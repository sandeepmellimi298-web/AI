#!/usr/bin/env python3
"""Bank Jobs Alert.

Reads news searches about bank recruitment in India, keeps the headlines that
are job notices from banks, and writes one short alert.

    python bank_jobs.py           make today's alert in the folder "out"
    python bank_jobs.py --open    the same, then open the alert in the browser
    python bank_jobs.py --all     also show items that were sent on earlier days

Only the Python standard library is used. All settings are in config.json.
"""
import argparse
import datetime as dt
import email.utils
import gzip
import html
import json
import pathlib
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

VERSION = "1.0"
UTC = dt.timezone.utc
HERE = pathlib.Path(__file__).resolve().parent
USER_AGENT = "bank-jobs-alert/%s (personal job alert; Python urllib)" % VERSION
GOOGLE_NEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
MAX_BYTES = 8 * 1024 * 1024
GROUPS = [("government", "Government banks"), ("private", "Private banks"),
          ("other", "Other banks")]
NUMBERS = {"window_hours": 36, "window_hours_monday": 84, "max_items_per_group": 12,
           "max_items_per_bank": 2, "repeat_days": 30, "bank_quiet_days": 5,
           "pause_seconds": 3, "show_other_banks": True}
SAFETY = ("These lines are news headlines. Nobody checked them on the bank's website. "
          "Before you apply, open the official website of the bank: official websites of "
          "Indian banks end in .bank.in. Apply and pay the fee only there. No bank asks "
          "for money to give a job.")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def say(text=""):
    """Print without crashing on characters the console cannot show."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def clean_text(value, limit=None):
    text = re.sub(r"(?s)<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.- ") + "..."
    return text


def parse_date(value):
    try:
        parsed = email.utils.parsedate_to_datetime(str(value or "").strip())
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def short_error(exc):
    text = re.sub(r"\s+", " ", "%s" % (exc,)).strip() or exc.__class__.__name__
    return text[:200]


def words_re(words):
    body = "|".join(re.escape(w) for w in sorted(words or [], key=len, reverse=True) if w)
    return re.compile(r"(?<![A-Za-z])(?:%s)(?![A-Za-z])" % (body or "(?!)"), re.I)


# --------------------------------------------------------------------------
# Network and feed reading
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


def http_get(url, timeout=25, retries=2, wait=time.sleep):
    """GET a URL and return the body as bytes. Uses the system proxy settings."""
    headers = {"User-Agent": USER_AGENT,
               "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    last = FetchError("request was not sent")
    for attempt in range(retries + 1):
        pause = 3
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FetchError("response is too large")
            return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        except urllib.error.HTTPError as exc:
            last = FetchError("HTTP %s %s" % (exc.code, exc.reason))
            if exc.code not in (429, 500, 502, 503, 504):
                break
            if exc.code == 429:  # the news service asks us to slow down
                pause = 20
        except urllib.error.URLError as exc:
            last = FetchError("network error: %s" % (exc.reason,))
        except OSError as exc:  # timeouts, resets, TLS problems
            last = FetchError("network error: %s" % (exc,))
        if attempt < retries:
            wait(pause)
    raise last


def parse_feed(data):
    """Return the entries of an RSS feed: title, url, published, source."""
    if b"<!ENTITY" in data[:50000]:
        raise ValueError("the feed defines its own XML entities; skipped for safety")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("not a readable RSS feed (%s)" % exc)
    entries = []
    for element in root.iter("item"):
        source = clean_text(element.findtext("source"))
        title = clean_text(element.findtext("title"))
        if source and title.endswith(" - " + source):  # "Headline - Publisher"
            title = title[: -len(" - " + source)]
        link = (element.findtext("link") or "").strip()
        if not re.match(r"(?i)^https?://[^\s<>\"']+$", link):
            link = ""
        entries.append({"title": title, "url": link, "source": source,
                        "published": parse_date(element.findtext("pubDate"))})
    return entries


# --------------------------------------------------------------------------
# Reading a headline: which bank, how many posts, which last date
# --------------------------------------------------------------------------

class BankFinder:
    """Finds the bank in a headline. Long names win over short ones."""

    def __init__(self, cfg):
        named = []
        for group in ("government", "private"):
            for names in cfg["banks"].get(group, []):
                named.extend((text, group, names[0]) for text in names)
        named.sort(key=lambda row: len(row[0]), reverse=True)
        self.named = [(self._whole(text, 0 if text.isupper() else re.I), group, name)
                      for text, group, name in named]
        self.kinds = []
        for group, _label in GROUPS:
            for kind in cfg.get("bank_kinds", {}).get(group, []):
                self.kinds.append((re.compile(r"((?:[A-Z][A-Za-z&.'-]*\s+){0,3})%s(?![A-Za-z0-9])"
                                              % re.escape(kind)), group, kind))
        self.skip = set(word.lower() for word in cfg.get("not_name_words", []))
        self.mask = [self._whole(text, re.I) for text in cfg.get("not_banks", [])]

    @staticmethod
    def _whole(text, flags):
        return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(text), flags)

    def find(self, title):
        """Return (group, bank name) or None."""
        for pattern in self.mask:
            title = pattern.sub(" ", title)
        for pattern, group, name in self.named:
            if pattern.search(title):
                return group, name
        for pattern, group, kind in self.kinds:
            for match in pattern.finditer(title):
                words = match.group(1).split()
                while words and words[0].lower().strip(".'") in self.skip:
                    words.pop(0)
                if words or kind != "Bank":
                    return group, " ".join(words + [kind])
        return None


_ROLE = r"(?:posts?|vacanc(?:y|ies)|apprentices?|officers?|managers?|clerks?|associates?|jobs)"
_POSTS_RE = re.compile(r"(?<![\w.,-])(\d{1,3}(?:,\d{2,3})+|\d+)\+?\s+((?:[A-Za-z&/.-]+\s+){0,4}?)%s"
                       r"(?![A-Za-z])" % _ROLE, re.I)
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_LAST_RE = re.compile(
    r"(?i)(?:last date|apply by|apply till|apply before|till|until|before|by)\b[^,;|]{0,30}?"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+%s(?:\s+\d{4})?|%s\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)"
    % (_MONTH, _MONTH))
_KINDS = [("apprentice", r"(?i:apprentice)"),
          ("clerk", r"(?i:clerk|customer service associate|junior associate|banking associate"
                    r"|office assistant)"),
          ("po", r"(?i:probationary officer)|\bPO\b"),
          ("so", r"(?i:specialist officer|specialist cadre)|\bSO\b|\bSCO\b"),
          ("manager", r"(?i:manager)"), ("officer", r"(?i:officer)")]


def find_posts(title):
    """The number of posts in a headline, or 0."""
    best = 0
    for match in _POSTS_RE.finditer(title):
        raw = match.group(1)
        number = int(raw.replace(",", ""))
        if "," not in raw and 1990 <= number <= 2100:
            continue  # a year, not a number of posts
        best = max(best, number)
    return best


def find_last_date(title):
    match = _LAST_RE.search(title)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def post_kind(title):
    for name, pattern in _KINDS:
        if re.search(pattern, title):
            return name
    return "notice"


# --------------------------------------------------------------------------
# Building the alert
# --------------------------------------------------------------------------

def load_config(path):
    try:
        cfg = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit("The settings file is missing: %s (%s)" % (path, exc))
    except ValueError as exc:
        raise SystemExit("The settings file has a mistake: %s (%s)" % (path, exc))
    for key, value in NUMBERS.items():
        cfg.setdefault(key, value)
    for key in ("queries", "job_words", "banks"):
        if not cfg.get(key):
            raise SystemExit('The settings file needs "%s": %s' % (key, path))
    return cfg


def build_alert(cfg, fetch, now, seen, show_all=False, hours=None, pause=time.sleep):
    local = now.astimezone()
    if not hours:  # Monday: also cover the weekend
        hours = cfg["window_hours_monday"] if local.weekday() == 0 else cfg["window_hours"]
    hours = float(hours)
    days = max(1, int(-(-hours // 24)))
    today = local.date().isoformat()
    oldest = now - dt.timedelta(hours=hours)
    finder = BankFinder(cfg)
    job_re, drop_re = words_re(cfg["job_words"]), words_re(cfg.get("drop_words"))
    soft_re = words_re(cfg.get("soft_words"))  # these count only next to a number of posts

    statuses, found, links = [], [], set()
    for number, query in enumerate(cfg["queries"], 1):
        text = query.replace("{year}", str(local.year)).replace("{days}", str(days))
        status = {"source": "News search %d" % number, "query": text, "ok": True,
                  "fetched": 0, "kept": 0}
        try:
            entries = parse_feed(fetch(GOOGLE_NEWS % urllib.parse.quote_plus(text)))
        except Exception as exc:  # one broken search must never stop the alert
            entries = []
            status["ok"] = False
            status["error"] = short_error(exc)
        status["fetched"] = len(entries)
        for entry in entries:
            title, published = entry["title"], entry["published"]
            if not title or not entry["url"] or entry["url"] in links:
                continue
            if published is None or published < oldest or published > now + dt.timedelta(hours=12):
                continue
            posts = find_posts(title)
            if drop_re.search(title):
                continue
            if not job_re.search(title) and not (posts and soft_re.search(title)):
                continue
            bank = finder.find(title)
            if not bank or (bank[0] == "other" and not cfg["show_other_banks"]):
                continue
            links.add(entry["url"])
            found.append({
                "group": bank[0], "bank": bank[1], "title": clean_text(title, 170),
                "url": entry["url"], "source": entry["source"], "published": published,
                "posts": posts, "last_date": find_last_date(title),
                "key": "%s|%s" % (bank[1].lower(), posts or post_kind(title)),
                "search": status["source"],
            })
        statuses.append(status)
        if number < len(cfg["queries"]):
            pause(float(cfg["pause_seconds"]))

    # 1. many sites report the same notice: keep the newest headline of each
    found.sort(key=lambda item: item["published"], reverse=True)
    unique = {}
    for item in found:
        unique.setdefault(item["key"], item)

    # 2. leave out what was sent on an earlier day
    fresh = []
    for item in unique.values():
        if not show_all:
            if seen["items"].get(item["key"], today) < today:
                continue
            last = seen["banks"].get(item["bank"].lower(), today)
            quiet = (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days
            if not item["posts"] and 0 < quiet < int(cfg["bank_quiet_days"]):
                continue  # this bank was in a recent alert and the headline has no new number
        fresh.append(item)

    # 3. the biggest recruitments first, and not too many lines for one bank
    fresh.sort(key=lambda item: (item["posts"], item["published"]), reverse=True)
    groups, per_bank = {key: [] for key, _label in GROUPS}, {}
    for item in fresh:
        count = per_bank.get(item["bank"], 0)
        chosen = groups[item["group"]]
        if count < int(cfg["max_items_per_bank"]) and len(chosen) < int(cfg["max_items_per_group"]):
            chosen.append(item)
            per_bank[item["bank"]] = count + 1
    for status in statuses:
        status["kept"] = sum(1 for items in groups.values() for item in items
                             if item["search"] == status["source"])
    return {"generated": now, "window_hours": hours, "groups": groups, "statuses": statuses,
            "total": sum(len(items) for items in groups.values())}


def remember(seen, alert, cfg):
    today = alert["generated"].astimezone().date()
    for items in alert["groups"].values():
        for item in items:
            seen["items"].setdefault(item["key"], today.isoformat())
            seen["banks"][item["bank"].lower()] = today.isoformat()
    oldest = (today - dt.timedelta(days=int(cfg["repeat_days"]))).isoformat()
    for part in ("items", "banks"):
        seen[part] = {key: day for key, day in seen[part].items() if day >= oldest}
    return seen


def load_seen(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        items, banks = data.get("items", {}), data.get("banks", {})
    except (OSError, ValueError, AttributeError):
        items, banks = {}, {}
    good = lambda part: {str(k): str(v) for k, v in part.items()
                         if re.match(r"^\d{4}-\d{2}-\d{2}$", str(v))}
    return {"items": good(items), "banks": good(banks)}


# --------------------------------------------------------------------------
# Writing the alert
# --------------------------------------------------------------------------

def alert_title(alert):
    return "Bank Jobs Alert - " + alert["generated"].astimezone().strftime("%A, %d %B %Y")


def summary_line(alert):
    if not alert["total"]:
        return "No new bank job notices in the last %d hours." % round(alert["window_hours"])
    return ("%d new items from the last %d hours. Items sent on earlier days are left out."
            % (alert["total"], round(alert["window_hours"])))


def meta_parts(item):
    parts = []
    if item["posts"]:
        parts.append("{:,} posts".format(item["posts"]))
    if item["last_date"]:
        parts.append("last date " + item["last_date"])
    if item["source"]:
        parts.append(item["source"])
    parts.append(item["published"].astimezone().strftime("%d %b").lstrip("0"))
    return parts


def source_line(alert):
    good = [s for s in alert["statuses"] if s["ok"]]
    line = "News searches that answered: %d of %d." % (len(good), len(alert["statuses"]))
    bad = ["%s (%s)" % (s["source"], s.get("error", "error")[:60])
           for s in alert["statuses"] if not s["ok"]]
    return line + (" Failed: %s." % "; ".join(bad) if bad else "")


def render_markdown(alert):
    out = ["# " + alert_title(alert), "", summary_line(alert), ""]
    for key, label in GROUPS:
        items = alert["groups"][key]
        if key == "other" and not items:
            continue
        out += ["## " + label, ""]
        if not items:
            out += ["No new notices.", ""]
        for item in items:
            title = item["title"].replace("[", "(").replace("]", ")")
            out.append("- **%s**: [%s](%s) (%s)" % (item["bank"], title, item["url"],
                                                    ", ".join(meta_parts(item))))
        if items:
            out.append("")
    out += ["---", "", source_line(alert), "", SAFETY, ""]
    return "\n".join(out)


def render_html(alert):
    esc = lambda value: html.escape(str(value), quote=True)
    out = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title></head>" % esc(alert_title(alert)),
           '<body style="margin:0;padding:0;background:#f4f5f7;">',
           '<div style="max-width:700px;margin:0 auto;padding:24px 18px;background:#ffffff;'
           'font-family:Segoe UI,Arial,Helvetica,sans-serif;color:#1f2328;line-height:1.5;">',
           '<h1 style="font-size:22px;margin:0 0 4px 0;">%s</h1>' % esc(alert_title(alert)),
           '<p style="margin:0 0 18px 0;color:#59636e;font-size:13px;">%s</p>'
           % esc(summary_line(alert))]
    for key, label in GROUPS:
        items = alert["groups"][key]
        if key == "other" and not items:
            continue
        out.append('<h2 style="font-size:17px;margin:22px 0 8px 0;padding-bottom:4px;'
                   'border-bottom:1px solid #d1d9e0;">%s</h2>' % esc(label))
        if not items:
            out.append('<p style="margin:0;color:#59636e;font-size:14px;">No new notices.</p>')
        for item in items:
            out.append('<div style="margin:0 0 12px 0;"><p style="margin:0;font-size:15px;">'
                       '<b>%s</b>: <a href="%s" style="color:#0969da;text-decoration:none;">%s</a>'
                       '</p><p style="margin:1px 0 0 0;color:#59636e;font-size:13px;">%s</p></div>'
                       % (esc(item["bank"]), esc(item["url"]), esc(item["title"]),
                          " &middot; ".join(esc(part) for part in meta_parts(item))))
    out.append('<p style="margin:26px 0 0 0;padding-top:10px;border-top:1px solid #d1d9e0;'
               'color:#59636e;font-size:12px;">%s %s Made by bank_jobs.py %s.</p>'
               % (esc(source_line(alert)), esc(SAFETY), esc(VERSION)))
    out.append("</div></body></html>")
    return "\n".join(out)


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --------------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Make today's Bank Jobs Alert.")
    parser.add_argument("--open", action="store_true", help="open the alert in the browser")
    parser.add_argument("--hours", type=float, help="look back this many hours (default: config)")
    parser.add_argument("--all", action="store_true", help="also show items sent on earlier days")
    parser.add_argument("--config", default=str(HERE / "config.json"), help="settings file")
    parser.add_argument("--out", default=str(HERE / "out"), help="folder for the alerts")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    out_dir = pathlib.Path(args.out)
    seen_path = out_dir / "seen.json"
    seen = load_seen(seen_path)
    say("Bank Jobs Alert %s - reading %d news searches..." % (VERSION, len(cfg["queries"])))
    alert = build_alert(cfg, http_get, dt.datetime.now(UTC), seen, show_all=args.all,
                        hours=args.hours)

    stamp = alert["generated"].astimezone().strftime("%Y-%m-%d")
    markdown, page = render_markdown(alert), render_html(alert)
    html_path = out_dir / ("alert-%s.html" % stamp)
    write_text(html_path, page)
    write_text(out_dir / ("alert-%s.md" % stamp), markdown)
    write_text(out_dir / "latest.md", markdown)
    write_text(out_dir / "latest.html", page)
    if not args.all:
        write_text(seen_path, json.dumps(remember(seen, alert, cfg), indent=1, sort_keys=True))
    report = {
        "version": VERSION, "finished": dt.datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(), "system": platform.platform(),
        "window_hours": alert["window_hours"], "total_items": alert["total"],
        "groups": {key: len(items) for key, items in alert["groups"].items()},
        "sources": alert["statuses"], "alert_file": str(html_path),
    }
    write_text(out_dir / "last_run.json", json.dumps(report, indent=2))

    for status in alert["statuses"]:
        say("  %-15s %s  read %3d, kept %d%s" % (
            status["source"], "ok    " if status["ok"] else "FAILED", status["fetched"],
            status["kept"], "" if status["ok"] else "  (%s)" % status.get("error", "")))
    say(summary_line(alert))
    for key, label in GROUPS:
        for item in alert["groups"][key]:
            say("  [%s] %s: %s" % (label, item["bank"], item["title"]))
    say("Saved: %s" % html_path)
    if args.open:
        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:
            pass
    if alert["statuses"] and not any(status["ok"] for status in alert["statuses"]):
        say("No news search answered. Check the internet connection and try again.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
