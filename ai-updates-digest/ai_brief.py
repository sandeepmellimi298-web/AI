#!/usr/bin/env python3
"""AI brief.

A small AI model that runs on this machine reads the newest digest in the folder
"out", picks the items that matter most by meaning, and writes a short summary
at the top. It works for the AI Updates Digest and for the Bank Jobs Alert.

The model runs through Ollama (https://ollama.com). There is no account, no key
and no outside AI service. If Ollama or the model is missing, nothing is changed.

    python ai_brief.py           add the AI part to the newest digest
    python ai_brief.py --open    the same, then open the page in the browser

Only the Python standard library is used. Settings are in ai_settings.json.
"""
import argparse
import html
import json
import math
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
import webbrowser

VERSION = "1.0"
HERE = pathlib.Path(__file__).resolve().parent
START, END = "<!-- ai-brief:start -->", "<!-- ai-brief:end -->"
DEFAULTS = {
    "enabled": True,
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "phi4-mini",
    "embed_model": "all-minilm",
    "download_missing_models": True,
    "top_picks": 5,
    "max_picks_per_section": 2,
    "max_items_for_summary": 30,
    "reader": "A reader who wants the most important and most useful items of today.",
}
SKIP_SECTIONS = ("in 30 seconds", "ai summary")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
        "type": "object",
        "properties": {"text": {"type": "string"},
                       "items": {"type": "array", "items": {"type": "integer"}}},
        "required": ["text", "items"]}}},
    "required": ["bullets"],
}


def say(text=""):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


# --------------------------------------------------------------------------
# Talking to the local AI model (Ollama)
# --------------------------------------------------------------------------

class AiError(Exception):
    pass


class Ollama:
    def __init__(self, base):
        self.base = base.rstrip("/")
        # The model is on this machine, so a proxy must never be used for it.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, payload=None, timeout=60):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise AiError("the AI program answered HTTP %s %s" % (exc.code, detail))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AiError("no answer from the AI program (%s)" % (getattr(exc, "reason", exc),))

    def has(self, model):
        names = [m.get("name", "") for m in self.call("/api/tags").get("models", [])]
        return any(name == model or name.split(":")[0] == model for name in names)

    def need(self, model, download):
        if self.has(model):
            return
        if not download:
            raise AiError("the AI model %s is not on this machine" % model)
        say("The AI model %s is not on this machine yet. Downloading it now, one time. "
            "This can take some minutes..." % model)
        self.call("/api/pull", {"model": model, "stream": False}, timeout=3600)

    def embed(self, model, texts):
        answer = self.call("/api/embed", {"model": model, "input": texts}, timeout=300)
        vectors = answer.get("embeddings") or []
        if len(vectors) != len(texts):
            raise AiError("the AI model gave %d vectors for %d texts" % (len(vectors), len(texts)))
        return vectors

    def chat(self, model, system, user):
        answer = self.call("/api/chat", {
            "model": model, "stream": False, "format": SCHEMA,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 400},
        }, timeout=900)
        return (answer.get("message") or {}).get("content", "")


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


# --------------------------------------------------------------------------
# Reading the digest
# --------------------------------------------------------------------------

def strip_block(text):
    """Remove an AI part that an earlier run added."""
    pattern = re.escape(START) + r".*?" + re.escape(END) + r"\n*"
    return re.sub(pattern, "", text, flags=re.S)


def read_items(markdown):
    """Find the items of a digest: section, title, link and a short note."""
    items, section, links = [], "", set()
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("- ") or section.lower() in SKIP_SECTIONS:
            continue
        match = LINK_RE.search(line)
        if not match or match.group(2) in links:
            continue
        links.add(match.group(2))
        around = [line[2:match.start()], line[match.end():]]
        around += [extra for extra in lines[index + 1:index + 3] if extra.startswith("  ")]
        note = " ".join(re.sub(r"[*\[\]]", "", part).strip(" :") for part in around)
        items.append({"section": section, "title": match.group(1).strip(),
                      "url": match.group(2), "note": re.sub(r"\s+", " ", note).strip()[:300]})
    return items


# --------------------------------------------------------------------------
# The two AI jobs: pick by meaning, and write the summary
# --------------------------------------------------------------------------

def pick_by_meaning(ai, cfg, items):
    texts = [cfg["reader"]] + ["%s. %s" % (item["title"], item["note"]) for item in items]
    vectors = ai.embed(cfg["embed_model"], [text[:600] for text in texts])
    reader, rest = vectors[0], vectors[1:]
    order = sorted(range(len(items)), key=lambda i: cosine(reader, rest[i]), reverse=True)
    picks, per_section = [], {}
    for i in order:
        section = items[i]["section"]
        if per_section.get(section, 0) >= int(cfg["max_picks_per_section"]):
            continue
        if any(cosine(rest[i], rest[j]) > 0.86 for j in picks):
            continue  # the same news again, from another source
        picks.append(i)
        per_section[section] = per_section.get(section, 0) + 1
        if len(picks) >= int(cfg["top_picks"]):
            break
    return [items[i] for i in picks]


def clean_bullet(text):
    text = re.sub(r"https?://\S+", "", str(text))
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"[\[\]()<>*_#|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text[:240]


def write_summary(ai, cfg, items):
    items = items[:int(cfg["max_items_for_summary"])]
    listing = "\n".join("%d. [%s] %s. %s" % (n, item["section"], item["title"], item["note"])
                        for n, item in enumerate(items, 1))
    system = ("You write very short news summaries in simple English. Use only facts from the "
              "list. Never add a fact, a number, a date or a link that is not in the list.")
    user = ("Reader: %s\n\nItems of today:\n%s\n\nWrite 3 short sentences for this reader about "
            "the most important items. Each sentence has at most 25 words and is plain text "
            "without links. For each sentence give the numbers of the items you used.\n"
            'Answer as JSON: {"bullets": [{"text": "...", "items": [1]}]}'
            % (cfg["reader"], listing))
    try:
        answer = json.loads(ai.chat(cfg["chat_model"], system, user))
    except ValueError:
        raise AiError("the AI model did not answer in the asked form")
    bullets = []
    for bullet in (answer.get("bullets") or [])[:3]:
        if not isinstance(bullet, dict):
            continue
        used = [items[n - 1] for n in bullet.get("items") or []
                if isinstance(n, int) and 1 <= n <= len(items)][:2]
        text = clean_bullet(bullet.get("text", ""))
        source = " ".join("%s %s" % (item["title"], item["note"]) for item in used)
        numbers = re.findall(r"\d[\d,.]*\d|\d", text)
        known = re.sub(r"[,.]", "", source)
        if len(text) < 15 or not used:
            continue
        if any(re.sub(r"[,.]", "", number) not in known for number in numbers):
            continue  # a number that is not in the cited items: do not trust this sentence
        bullets.append({"text": text, "items": used})
    return bullets


# --------------------------------------------------------------------------
# Writing the AI part into the files
# --------------------------------------------------------------------------

def block_markdown(bullets, picks, model):
    out = [START, "## AI summary", ""]
    for bullet in bullets:
        links = ", ".join("[source](%s)" % item["url"] for item in bullet["items"])
        out.append("- %s (%s)" % (bullet["text"], links))
    if picks:
        out += ["", "**Top picks by meaning**", ""]
        out += ["%d. [%s](%s)" % (n, item["title"].replace("[", "(").replace("]", ")"), item["url"])
                for n, item in enumerate(picks, 1)]
    out += ["", "*Written by a small AI model (%s) that ran inside this job, with no outside "
            "AI service. It can make mistakes, so check the links.*" % model, END, "", ""]
    return "\n".join(out)


def block_html(bullets, picks, model):
    esc = lambda value: html.escape(str(value), quote=True)
    link = '<a href="%s" style="color:#0969da;text-decoration:none;">%s</a>'
    out = [START, '<div style="background:#f6f8fa;border-left:4px solid #8250df;'
           'padding:10px 14px;margin:0 0 18px 0;">',
           '<p style="margin:0 0 6px 0;font-weight:600;">AI summary</p>']
    if bullets:
        out.append('<ul style="margin:0;padding-left:20px;">')
        for bullet in bullets:
            links = ", ".join(link % (esc(item["url"]), "source") for item in bullet["items"])
            out.append('<li style="margin:2px 0;">%s (%s)</li>' % (esc(bullet["text"]), links))
        out.append("</ul>")
    if picks:
        out.append('<p style="margin:8px 0 4px 0;font-weight:600;">Top picks by meaning</p>')
        out.append('<ol style="margin:0;padding-left:20px;">')
        out += ['<li style="margin:2px 0;">%s</li>' % (link % (esc(item["url"]), esc(item["title"])))
                for item in picks]
        out.append("</ol>")
    out.append('<p style="margin:8px 0 0 0;color:#59636e;font-size:12px;">Written by a small AI '
               "model (%s) that ran on this machine, with no outside AI service. It can make "
               "mistakes, so check the links.</p></div>" % esc(model))
    out.append(END)
    return "\n".join(out) + "\n"


def insert_block(text, block, marks):
    """Put the block in front of the first line that starts with one of the marks."""
    text = strip_block(text)
    found = [m.start() for m in (re.search(r"(?m)^" + re.escape(mark), text) for mark in marks) if m]
    if not found:
        return text.rstrip("\n") + "\n\n" + block
    return text[:min(found)] + block + text[min(found):]


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def newest(folder, pattern):
    files = sorted(folder.glob(pattern))
    return files[-1] if files else None


# --------------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------------

def run(cfg, out_dir):
    source = out_dir / "latest.md"
    if not source.exists():
        source = newest(out_dir, "*-20*.md")
    if source is None or not source.exists():
        raise AiError("there is no digest in %s yet" % out_dir)
    items = read_items(strip_block(source.read_text(encoding="utf-8")))
    if len(items) < 2:
        raise AiError("the digest has fewer than 2 items, so there is nothing to sort")

    ai = Ollama(cfg["ollama_url"])
    ai.call("/api/version", timeout=10)
    picks, bullets, notes = [], [], []
    try:
        ai.need(cfg["embed_model"], cfg["download_missing_models"])
        picks = pick_by_meaning(ai, cfg, items)
    except AiError as exc:
        notes.append("picks: %s" % exc)
    try:
        ai.need(cfg["chat_model"], cfg["download_missing_models"])
        bullets = write_summary(ai, cfg, items)
    except AiError as exc:
        notes.append("summary: %s" % exc)
    if not picks and not bullets:
        raise AiError("; ".join(notes) or "the AI model gave nothing usable")

    changed = []
    for path in {source, out_dir / "latest.md", newest(out_dir, "*-20*.md")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_markdown(bullets, picks, cfg["chat_model"]), ["## "])
            write_text(path, text)
            changed.append(path.name)
    for path in {out_dir / "latest.html", newest(out_dir, "*-20*.html")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_html(bullets, picks, cfg["chat_model"]),
                                ['<div style="background:#f0f6ff', "<h2 "])
            write_text(path, text)
            changed.append(path.name)
    return {"items": len(items), "picks": len(picks), "bullets": len(bullets),
            "files": sorted(changed), "notes": notes}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Add a short AI summary to the newest digest.")
    parser.add_argument("--open", action="store_true", help="open the newest page in the browser")
    parser.add_argument("--out", default=str(HERE / "out"), help="folder with the digests")
    parser.add_argument("--settings", default=str(HERE / "ai_settings.json"), help="settings file")
    args = parser.parse_args(argv)
    out_dir = pathlib.Path(args.out)

    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(pathlib.Path(args.settings).read_text(encoding="utf-8")))
    except OSError:
        pass
    except ValueError as exc:
        say("AI part: the settings file has a mistake (%s). Using the standard settings." % exc)

    started, report = time.time(), {"ok": False, "model": cfg["chat_model"]}
    if not cfg["enabled"]:
        report["reason"] = "switched off in ai_settings.json"
    else:
        try:
            say("AI part: the model %s is reading the digest..." % cfg["chat_model"])
            report.update(run(cfg, out_dir), ok=True)
        except AiError as exc:
            report["reason"] = str(exc)
        except Exception as exc:  # the AI part must never break the digest
            report["reason"] = "%s: %s" % (exc.__class__.__name__, exc)
    report["seconds"] = round(time.time() - started, 1)
    if report["ok"]:
        say("AI part: done in %s seconds. %d sentences and %d picks were added to %s."
            % (report["seconds"], report["bullets"], report["picks"], ", ".join(report["files"])))
    else:
        say("AI part: skipped (%s). The digest is unchanged." % report["reason"])
        if "no answer from the AI program" in report["reason"]:
            say("To use AI on this computer, install Ollama from https://ollama.com and start it.")
    try:
        path = out_dir / "last_run.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["ai"] = report
        write_text(path, json.dumps(data, indent=2))
    except (OSError, ValueError):
        pass
    if args.open:
        page = newest(out_dir, "*-20*.html") or out_dir / "latest.html"
        if page.exists():
            try:
                webbrowser.open(page.resolve().as_uri())
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
