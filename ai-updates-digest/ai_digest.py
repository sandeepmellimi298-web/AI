#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Updates Digest (local version)
=================================

Collects current AI updates from public sources, keeps what is recent and
trending, writes a digest (HTML + Markdown) and, if Gmail is set up, emails it.

Standard library only: no "pip install" is needed. Works with Python 3.8+.

    python ai_digest.py               collect, save the digest, email it if set up
    python ai_digest.py --open        the same, then open the digest in the browser
    python ai_digest.py --no-email    collect and save only
    python ai_digest.py --test-email  send a short test email (checks Gmail setup)
    python ai_digest.py --selftest    check the program without using the internet

Files next to this program:
    config.json          sources, keywords and limits (safe to edit)
    email_settings.txt   Gmail address and app password (keep it private)
    out/                 digests, run report and the list of items already sent
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import gzip
import html
import json
import os
import platform
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from pathlib import Path

VERSION = "1.0"
APP_DIR = Path(__file__).resolve().parent
UTC = dt.timezone.utc
USER_AGENT = "ai-updates-digest/%s (personal news digest; Python urllib)" % VERSION
MAX_BYTES = 8 * 1024 * 1024

SECTIONS = [
    ("releases", "Releases"),
    ("tools", "Trending AI tools"),
    ("community", "What users are saying"),
    ("code", "Reusable code"),
    ("integration", "AI for integration"),
]

GOOGLE_NEWS = "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en"

DEFAULT_CONFIG = {
    "window_hours": 36,
    "window_hours_monday": 84,
    "max_items_per_section": 5,
    "max_items_by_section": {"integration": 6},
    "skip_items_sent_in_last_days": 30,
    "exclude_terms": {
        "acronyms": ["IPO"],
        "words": ["initial public offering", "funding round", "valuation", "share price",
                  "stock price", "Series A", "Series B", "Series C", "earnings call"],
    },
    "ai_terms": {
        "acronyms": ["AI", "A.I.", "LLM", "LLMs", "GPT", "RAG", "MCP", "AGI", "ML"],
        "words": [
            "OpenAI", "Anthropic", "Claude", "ChatGPT", "Gemini", "DeepMind", "Mistral",
            "Llama", "Qwen", "DeepSeek", "Copilot", "Codex", "Grok", "Hugging Face",
            "LangChain", "Ollama", "agent", "agents", "agentic", "language model",
            "language models", "machine learning", "deep learning", "neural network",
            "neural networks", "diffusion model", "fine-tuning", "fine-tune",
            "prompt injection", "prompt engineering", "embeddings", "inference",
            "chatbot", "chatbots", "generative", "vibe coding", "Stable Diffusion",
        ],
    },
    "hacker_news": {"enabled": True, "min_points": 80, "min_points_show_hn": 30},
    "devto": {"enabled": True, "tags": ["ai", "llm"], "min_reactions": 20},
    "github": {
        "enabled": True,
        "pause_seconds": 2,
        "queries": [
            {"section": "tools", "name": "GitHub (new and rising)", "rank": "stars_per_day",
             "min_age_days": 3,
             "q": "llm OR agent OR mcp in:name,description created:>{d30} stars:>500"},
            {"section": "tools", "name": "GitHub (new and rising)", "rank": "stars_per_day",
             "min_age_days": 3,
             "q": "topic:ai-agents created:>{d30} stars:>300"},
            {"section": "code", "name": "GitHub (new reusable code)", "rank": "stars_per_day",
             "min_age_days": 3,
             "q": "skills agent in:name,description created:>{d90} stars:>1000"},
            {"section": "code", "name": "GitHub (popular templates)", "rank": "stars",
             "q": "topic:llm template OR cookbook OR examples OR starter in:name,description pushed:>{d7} stars:>1000"},
            {"section": "integration", "name": "GitHub (Azure Logic Apps)", "rank": "stars",
             "q": "org:Azure logicapps in:name pushed:>{d30}"},
            {"section": "integration", "name": "GitHub (Azure AI samples)", "rank": "stars",
             "q": "org:Azure-Samples agent OR openai OR foundry in:name,description pushed:>{d14} stars:>200"},
        ],
    },
    "feeds": [
        {"name": "OpenAI", "section": "releases", "max_items": 2,
         "urls": ["https://openai.com/news/rss.xml"]},
        {"name": "Google AI", "section": "releases", "max_items": 2,
         "urls": ["https://blog.google/innovation-and-ai/technology/ai/rss/"]},
        {"name": "Google DeepMind", "section": "releases", "max_items": 2,
         "urls": ["https://deepmind.google/blog/rss.xml"]},
        {"name": "Microsoft AI news", "section": "releases", "max_items": 2,
         "urls": ["https://news.microsoft.com/source/topics/ai/feed/"]},
        {"name": "Mistral AI", "section": "releases", "max_items": 2,
         "urls": ["https://mistral.ai/news/rss"]},
        {"name": "Hugging Face", "section": "releases", "max_items": 2,
         "urls": ["https://huggingface.co/blog/feed.xml"]},
        {"name": "Anthropic news (community feed)", "section": "releases", "max_items": 2,
         "optional": True,
         "urls": ["https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml"]},
        {"name": "Simon Willison", "section": "community", "max_items": 2,
         "urls": ["https://simonwillison.net/atom/everything/"]},
        {"name": "Reddit r/LocalLLaMA", "section": "community", "max_items": 2, "optional": True,
         "urls": ["https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day"]},
        {"name": "Azure Integration Services blog", "section": "integration", "max_items": 3,
         "window_hours": 96,
         "urls": ["https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=IntegrationsonAzureBlog",
                  "https://techcommunity.microsoft.com/gxcuf89792/rss/board?board.id=IntegrationsonAzureBlog"]},
        {"name": "Azure updates", "section": "integration", "max_items": 3, "window_hours": 96,
         "include": ["Logic Apps", "API Management", "Service Bus", "Event Grid",
                     "Integration", "Foundry", "OpenAI"],
         "urls": ["https://www.microsoft.com/releasecommunications/api/v2/azure/rss"]},
        {"name": "News: Logic Apps and AI", "section": "integration", "max_items": 2,
         "window_hours": 96, "optional": True,
         "urls": [GOOGLE_NEWS % "%22Logic+Apps%22+AI+when:4d"]},
        {"name": "News: SAP Integration Suite and AI", "section": "integration", "max_items": 1,
         "window_hours": 168, "optional": True,
         "urls": [GOOGLE_NEWS % "%22SAP+Integration+Suite%22+AI+when:7d"]},
        {"name": "News: TIBCO, MuleSoft and AI", "section": "integration", "max_items": 1,
         "window_hours": 168, "optional": True,
         "urls": [GOOGLE_NEWS % "%28TIBCO+OR+MuleSoft%29+AI+agents+when:7d"]},
    ],
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def now_utc():
    return dt.datetime.now(UTC)


def say(text=""):
    """Print without ever crashing on characters the console cannot show."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def clean_text(value, limit=None):
    """Turn HTML into plain text, collapse spaces, optionally shorten."""
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", str(value))
    text = re.sub(r"(?is)</?(?:a|b|i|u|em|strong|span|code|sup|sub|small|mark)\b[^>]*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.- ") + "..."
    return text


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:[.,]\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_date(value):
    """Parse RSS (RFC 822) and Atom/JSON (ISO 8601) dates. Returns UTC or None."""
    if not value:
        return None
    text = str(value).strip()
    match = _ISO_RE.match(text)
    if match:
        year, month, day, hour, minute, second, zone = match.groups()
        try:
            base = dt.datetime(int(year), int(month), int(day),
                               int(hour or 0), int(minute or 0), int(second or 0))
        except ValueError:
            return None
        if zone and zone != "Z":
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            offset = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
            base = base - sign * offset
        return base.replace(tzinfo=UTC)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def safe_url(value):
    """Only http(s) links are allowed into the digest."""
    if not value:
        return ""
    url = str(value).strip()
    return url if re.match(r"(?i)^https?://[^\s<>\"']+$", url) else ""


def url_key(url):
    """Normalised form of a link, used to spot duplicates and repeats."""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not k.lower().startswith("utm_") and k.lower() not in ("ref", "source")]
    key = host + parts.path.rstrip("/")
    if query:
        key += "?" + urllib.parse.urlencode(query)
    return key.lower()


def title_key(title):
    return "".join(ch for ch in title.lower() if ch.isalnum())[:90]


def fmt_int(number):
    number = int(number)
    if number >= 10000:
        return "%.1fK" % (number / 1000.0)
    if number >= 1000:
        return "%.1fK" % (number / 1000.0)
    return str(number)


def short_error(exc):
    text = "%s" % (exc,)
    text = re.sub(r"\s+", " ", text).strip() or exc.__class__.__name__
    return text[:200]


class TermMatcher:
    """Finds AI words in a title. Acronyms are case-sensitive, words are not."""

    def __init__(self, words, acronyms):
        self._words = self._compile(words, re.I)
        self._acronyms = self._compile(acronyms, 0)

    @staticmethod
    def _compile(terms, flags):
        terms = [t for t in (terms or []) if t]
        if not terms:
            return None
        body = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % body, flags)

    def match(self, text):
        if not text:
            return False
        if self._acronyms and self._acronyms.search(text):
            return True
        return bool(self._words and self._words.search(text))


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


def http_get(url, headers=None, timeout=25, retries=1):
    """GET a URL and return the body as bytes. Uses the system proxy settings."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    last = FetchError("request was not sent")
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FetchError("response is too large")
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data
        except urllib.error.HTTPError as exc:
            last = FetchError("HTTP %s %s" % (exc.code, exc.reason))
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except urllib.error.URLError as exc:
            last = FetchError("network error: %s" % (exc.reason,))
        except OSError as exc:  # timeouts, resets, TLS problems
            last = FetchError("network error: %s" % (exc,))
        if attempt < retries:
            time.sleep(2)
    raise last


def fetch_json(fetch, url, headers=None):
    data = fetch(url, headers)
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        raise FetchError("the answer was not valid JSON")


# --------------------------------------------------------------------------
# Feed parsing (RSS 2.0, RSS 1.0 and Atom)
# --------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


_XML_NAMED = ("amp", "lt", "gt", "quot", "apos")


def _repair_xml(data):
    """Second try for feeds that are not strict XML (HTML entities, stray &)."""
    head = data[:300].decode("ascii", "replace")
    declared = re.search(r"encoding=[\"']([A-Za-z0-9._-]+)[\"']", head)
    try:
        text = data.decode(declared.group(1) if declared else "utf-8", "replace")
    except LookupError:
        text = data.decode("utf-8", "replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    def fix_entity(match):
        name = match.group(1)
        if name in _XML_NAMED:
            return match.group(0)
        value = html.unescape(match.group(0))
        if value == match.group(0):
            return "&amp;%s;" % name  # unknown entity: keep it as plain text
        return html.escape(value, quote=False)

    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", fix_entity, text)
    text = re.sub(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", text)
    return text.encode("utf-8")


def parse_feed(data):
    """Return a list of entries: title, url, published, summary, source."""
    if b"<!ENTITY" in data[:50000]:
        raise ValueError("the feed defines its own XML entities; skipped for safety")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:
            root = ET.fromstring(_repair_xml(data))
        except ET.ParseError as exc:
            raise ValueError("not a readable RSS or Atom feed (%s)" % exc)

    entries = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        fields = {}
        for child in element:
            fields.setdefault(_local(child.tag), []).append(child)

        def text_of(*names):
            for name in names:
                for node in fields.get(name, []):
                    value = "".join(node.itertext()).strip()
                    if value:
                        return value
            return ""

        link = ""
        links = fields.get("link", [])
        for node in links:  # Atom: <link rel="alternate" href="...">
            href = node.get("href")
            if href and node.get("rel", "alternate") == "alternate":
                link = href
                break
        if not link:
            for node in links:
                link = node.get("href") or (node.text or "").strip()
                if link:
                    break
        if not link:
            guid = text_of("guid", "id")
            link = guid if guid.lower().startswith("http") else ""

        source_nodes = fields.get("source", [])  # Google News: <source>Publisher</source>
        entries.append({
            "title": clean_text(text_of("title")),
            "url": safe_url(link),
            "published": parse_date(text_of("pubdate", "published", "updated", "date",
                                            "issued", "created")),
            "summary": clean_text(text_of("description", "summary", "encoded", "content")),
            "source": clean_text(source_nodes[0].text) if source_nodes
                      and len(source_nodes[0]) == 0 else "",
        })
    return entries


# --------------------------------------------------------------------------
# Collectors. Each returns a list of items (dicts).
# --------------------------------------------------------------------------

def make_item(section, group, title, url, published=None, summary="", meta=None,
              score=0.0, windowed=True, window_hours=None, group_cap=None):
    return {
        "section": section, "group": group, "title": clean_text(title, 160),
        "url": safe_url(url), "published": published, "summary": summary or "",
        "meta": meta or {}, "score": float(score), "windowed": windowed,
        "window_hours": window_hours, "group_cap": group_cap,
    }


def collect_hacker_news(cfg, fetch, since, matcher):
    """Popular Hacker News stories about AI: what practitioners talk about."""
    conf = cfg.get("hacker_news", {})
    base = "https://hn.algolia.com/api/v1/search?"

    def search(tag, min_points):
        params = {
            "tags": tag,
            "hitsPerPage": "200",
            "numericFilters": "created_at_i>%d,points>%d"
                              % (int(since.timestamp()), max(0, int(min_points) - 1)),
        }
        return fetch_json(fetch, base + urllib.parse.urlencode(params)).get("hits", [])

    hits = {}
    for hit in search("show_hn", conf.get("min_points_show_hn", 30)):
        hit["_show"] = True
        hits[str(hit.get("objectID"))] = hit
    for hit in search("story", conf.get("min_points", 80)):
        hits.setdefault(str(hit.get("objectID")), hit)

    items = []
    for object_id, hit in hits.items():
        title = hit.get("title") or hit.get("story_title") or ""
        if not matcher.match(title):
            continue
        tags = hit.get("_tags") or []
        is_show = bool(hit.get("_show")) or "show_hn" in tags or title.lower().startswith("show hn")
        discussion = "https://news.ycombinator.com/item?id=%s" % object_id
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        created = hit.get("created_at_i")
        published = dt.datetime.fromtimestamp(int(created), UTC) if created else None
        items.append(make_item(
            "tools" if is_show else "community", "Hacker News", title,
            hit.get("url") or discussion, published,
            meta={"points": points, "comments": comments, "discussion": discussion},
            score=points + comments))
    return items


def collect_devto(cfg, fetch, since, hours):
    """Well-received DEV Community posts: hands-on experience from developers."""
    conf = cfg.get("devto", {})
    days = max(1, int((hours + 23) // 24))
    items = []
    for tag in conf.get("tags", []):
        url = "https://dev.to/api/articles?" + urllib.parse.urlencode(
            {"tag": tag, "top": str(days), "per_page": "30"})
        for post in fetch_json(fetch, url) or []:
            if not isinstance(post, dict):
                continue
            reactions = int(post.get("public_reactions_count")
                            or post.get("positive_reactions_count") or 0)
            if reactions < int(conf.get("min_reactions", 20)):
                continue
            comments = int(post.get("comments_count") or 0)
            items.append(make_item(
                "community", "DEV Community", post.get("title") or "", post.get("url") or "",
                parse_date(post.get("published_at") or post.get("published_timestamp")),
                clean_text(post.get("description"), 180),
                meta={"reactions": reactions, "comments": comments},
                score=reactions + 2 * comments))
    return items


def collect_github(cfg, fetch, now):
    """New fast-rising AI repositories and reusable templates/examples."""
    conf = cfg.get("github", {})
    dates = {"d7": 7, "d14": 14, "d30": 30, "d90": 90}
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    items, errors, queries = [], [], conf.get("queries", [])
    for index, query in enumerate(queries):
        text = query.get("q", "")
        for key, days in dates.items():
            text = text.replace("{%s}" % key, (now - dt.timedelta(days=days)).date().isoformat())
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": text, "sort": "stars", "order": "desc", "per_page": "15"})
        try:
            repos = fetch_json(fetch, url, headers).get("items", [])
        except FetchError as exc:
            errors.append(short_error(exc))
            repos = []
        for repo in repos:
            if repo.get("fork") or repo.get("archived"):
                continue
            stars = int(repo.get("stargazers_count") or 0)
            created = parse_date(repo.get("created_at"))
            age_days = max(1.0, (now - created).total_seconds() / 86400.0) if created else 30.0
            if age_days < float(query.get("min_age_days", 0)):
                continue  # too new to judge: one-day star spikes are often not real interest
            license_id = (repo.get("license") or {}).get("spdx_id") or ""
            if license_id == "NOASSERTION":
                license_id = ""
            per_day = stars / age_days
            rank = query.get("rank", "stars")
            items.append(make_item(
                query.get("section", "code"), query.get("name", "GitHub"),
                repo.get("full_name") or "", repo.get("html_url") or "",
                created, clean_text(repo.get("description"), 180),
                meta={"stars": stars, "language": repo.get("language") or "",
                      "license": license_id,
                      "per_day": int(per_day) if rank == "stars_per_day" else None,
                      "created": created if rank == "stars_per_day" else None},
                score=per_day if rank == "stars_per_day" else stars,
                windowed=False))
        if index < len(queries) - 1:
            time.sleep(float(conf.get("pause_seconds", 2)))
    if errors and not items:
        raise FetchError(errors[0])
    return items


def collect_feed(feed, fetch, matcher):
    """One RSS/Atom feed from config.json."""
    last_error = None
    entries = None
    for url in feed.get("urls") or [feed.get("url")]:
        if not url:
            continue
        try:
            entries = parse_feed(fetch(url, {"Accept": "application/rss+xml, application/atom+xml, "
                                                       "application/xml, text/xml, */*"}))
            break
        except (FetchError, ValueError) as exc:
            last_error = exc
    if entries is None:
        raise last_error or FetchError("no feed address is configured")

    include = [w.lower() for w in feed.get("include", [])]
    exclude = [w.lower() for w in feed.get("exclude", [])]
    items = []
    for entry in entries:
        title = entry["title"]
        source = entry.get("source") or ""
        if source and title.endswith(" - " + source):  # Google News: "Headline - Publisher"
            title = title[: -len(" - " + source)]
        summary = entry["summary"]
        haystack = (title + " " + summary).lower()
        if include and not any(word in haystack for word in include):
            continue
        if exclude and any(word in haystack for word in exclude):
            continue
        if feed.get("require_ai") and not matcher.match(title + " " + summary):
            continue
        summary = re.split(r"\s+submitted by\s+/u/", summary)[0]  # Reddit boilerplate
        if title_key(summary).startswith(title_key(title)[:40]):
            summary = ""  # the summary only repeats the title
        published = entry["published"]
        meta = {"publisher": source} if source else {}
        items.append(make_item(
            feed.get("section", "releases"), feed.get("name", "Feed"), title, entry["url"],
            published, clean_text(summary, 180), meta=meta,
            score=published.timestamp() if published else 0.0,
            window_hours=feed.get("window_hours"), group_cap=feed.get("max_items")))
    return items


# --------------------------------------------------------------------------
# Building the digest
# --------------------------------------------------------------------------

def pick_window_hours(cfg, now, override=None):
    if override:
        return float(override)
    if now.astimezone().weekday() == 0:  # Monday: also cover the weekend
        return float(cfg.get("window_hours_monday", 84))
    return float(cfg.get("window_hours", 36))


def build_digest(cfg, fetch, now=None, seen=None, hours=None, ignore_seen=False):
    now = now or now_utc()
    seen = seen if seen is not None else {}
    hours = pick_window_hours(cfg, now, hours)
    since = now - dt.timedelta(hours=hours)
    matcher = TermMatcher(cfg["ai_terms"].get("words"), cfg["ai_terms"].get("acronyms"))
    excluded = TermMatcher(cfg.get("exclude_terms", {}).get("words"),
                           cfg.get("exclude_terms", {}).get("acronyms"))
    today = now.astimezone().date().isoformat()

    collected, statuses = [], []

    def run(name, func, optional=False):
        started = time.time()
        status = {"source": name, "ok": True, "optional": bool(optional), "fetched": 0, "kept": 0}
        try:
            got = func()
            status["fetched"] = len(got)
            collected.extend(got)
        except Exception as exc:  # one broken source must never stop the digest
            status["ok"] = False
            status["error"] = short_error(exc)
        status["seconds"] = round(time.time() - started, 1)
        statuses.append(status)

    if cfg.get("hacker_news", {}).get("enabled", True):
        run("Hacker News", lambda: collect_hacker_news(cfg, fetch, since, matcher))
    if cfg.get("devto", {}).get("enabled", True):
        run("DEV Community", lambda: collect_devto(cfg, fetch, since, hours))
    if cfg.get("github", {}).get("enabled", True):
        run("GitHub", lambda: collect_github(cfg, fetch, now))
    for feed in cfg.get("feeds", []):
        if feed.get("enabled", True):
            run(feed.get("name", "Feed"), lambda feed=feed: collect_feed(feed, fetch, matcher),
                optional=feed.get("optional", False))

    # 1. keep only complete, recent, not-yet-sent items
    usable = []
    for item in collected:
        if not item["title"] or not item["url"]:
            continue
        if item["windowed"] and excluded.match(item["title"]):
            continue  # funding and stock-market news is left out
        if item["windowed"]:
            limit = now - dt.timedelta(hours=float(item["window_hours"] or hours))
            if item["published"] is None or item["published"] < limit:
                continue
            if item["published"] > now + dt.timedelta(hours=12):
                continue
        key = url_key(item["url"])
        if not ignore_seen and seen.get(key, today) < today:
            continue  # it was in an earlier day's digest
        item["key"] = key
        usable.append(item)

    # 2. remove duplicates (same link or same headline), keeping the first seen
    unique, seen_links, seen_titles = [], set(), set()
    for item in usable:
        tkey = title_key(item["title"])
        if item["key"] in seen_links or (len(tkey) > 12 and tkey in seen_titles):
            continue
        seen_links.add(item["key"])
        seen_titles.add(tkey)
        unique.append(item)

    # 3. per section: take the best of each source in turn, so no source floods it
    default_cap = int(cfg.get("max_items_per_section", 5))
    sections = {}
    for key, _label in SECTIONS:
        cap = int(cfg.get("max_items_by_section", {}).get(key, default_cap))
        groups, order = {}, []
        for item in unique:
            if item["section"] != key:
                continue
            if item["group"] not in groups:
                groups[item["group"]] = []
                order.append(item["group"])
            groups[item["group"]].append(item)
        order.sort(key=lambda name: 0 if groups[name][0]["windowed"] else 1)  # news first
        for name in order:
            groups[name].sort(key=lambda it: it["score"], reverse=True)
            group_cap = groups[name][0].get("group_cap")
            if group_cap:
                groups[name] = groups[name][: int(group_cap)]
        chosen, rank = [], 0
        while len(chosen) < cap and any(rank < len(groups[name]) for name in order):
            for name in order:
                if rank < len(groups[name]) and len(chosen) < cap:
                    chosen.append(groups[name][rank])
            rank += 1
        sections[key] = chosen

    counts = {}
    for chosen in sections.values():
        for item in chosen:
            counts[item["group"]] = counts.get(item["group"], 0) + 1
    for status in statuses:
        status["kept"] = counts.get(status["source"], 0)
        if status["source"] == "GitHub":
            status["kept"] = sum(v for k, v in counts.items() if k.startswith("GitHub"))

    highlights = [sections[key][0] for key in ("community", "releases", "tools", "code",
                                               "integration") if sections.get(key)][:3]
    return {
        "generated": now, "window_hours": hours, "sections": sections,
        "highlights": highlights, "statuses": statuses,
        "total": sum(len(v) for v in sections.values()),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def local_day(moment):
    local = moment.astimezone()
    if abs((now_utc() - moment).days) > 300:
        return local.strftime("%b %Y")
    return local.strftime("%d %b").lstrip("0")


def meta_parts(item):
    meta = item["meta"]
    parts = [meta.get("publisher") or item["group"]]
    if meta.get("points") is not None:
        parts.append("%d points" % meta["points"])
    if meta.get("reactions") is not None:
        parts.append("%d reactions" % meta["reactions"])
    if meta.get("comments") is not None:
        parts.append("%d comments" % meta["comments"])
    if meta.get("stars") is not None:
        parts.append("%s stars" % fmt_int(meta["stars"]))
    if meta.get("per_day"):
        parts.append("about %s stars a day" % fmt_int(meta["per_day"]))
    if meta.get("language"):
        parts.append(meta["language"])
    if meta.get("license"):
        parts.append(meta["license"])
    if meta.get("created"):
        parts.append("created " + local_day(meta["created"]))
    elif item.get("published") and item.get("windowed", True):
        parts.append(local_day(item["published"]))
    return parts


def digest_title(digest):
    return "AI Updates Digest - " + digest["generated"].astimezone().strftime("%A, %d %B %Y")


def source_summary(digest):
    good = [s for s in digest["statuses"] if s["ok"]]
    bad = [s for s in digest["statuses"] if not s["ok"] and not s.get("optional")]
    skipped = [s for s in digest["statuses"] if not s["ok"] and s.get("optional")]
    return good, bad, skipped


def render_html(digest):
    esc = lambda value: html.escape(str(value), quote=True)
    font = "font-family:Segoe UI,Arial,Helvetica,sans-serif;"
    out = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title></head>" % esc(digest_title(digest)),
           '<body style="margin:0;padding:0;background:#f4f5f7;">',
           '<div style="max-width:700px;margin:0 auto;padding:24px 18px;background:#ffffff;'
           '%scolor:#1f2328;line-height:1.5;">' % font,
           '<h1 style="font-size:22px;margin:0 0 4px 0;">%s</h1>' % esc(digest_title(digest)),
           '<p style="margin:0 0 18px 0;color:#59636e;font-size:13px;">%d items from the last %d '
           "hours. Items sent on earlier days are left out.</p>"
           % (digest["total"], round(digest["window_hours"]))]

    if digest["highlights"]:
        out.append('<div style="background:#f0f6ff;border-left:4px solid #0969da;'
                   'padding:10px 14px;margin:0 0 18px 0;">')
        out.append('<p style="margin:0 0 6px 0;font-weight:600;">In 30 seconds</p>')
        out.append('<ul style="margin:0;padding-left:20px;">')
        for item in digest["highlights"]:
            out.append('<li style="margin:2px 0;"><a href="%s" style="color:#0969da;'
                       'text-decoration:none;">%s</a> <span style="color:#59636e;font-size:13px;">'
                       "(%s)</span></li>" % (esc(item["url"]), esc(item["title"]),
                                             esc(" - ".join(meta_parts(item)[:2]))))
        out.append("</ul></div>")

    for key, label in SECTIONS:
        out.append('<h2 style="font-size:17px;margin:22px 0 8px 0;padding-bottom:4px;'
                   'border-bottom:1px solid #d1d9e0;">%s</h2>' % esc(label))
        items = digest["sections"].get(key) or []
        if not items:
            out.append('<p style="margin:0;color:#59636e;font-size:14px;">Nothing notable '
                       "in this period.</p>")
        for item in items:
            out.append('<div style="margin:0 0 12px 0;">')
            out.append('<p style="margin:0;font-size:15px;font-weight:600;"><a href="%s" '
                       'style="color:#0969da;text-decoration:none;">%s</a></p>'
                       % (esc(item["url"]), esc(item["title"])))
            meta = " &middot; ".join(esc(part) for part in meta_parts(item))
            discussion = item["meta"].get("discussion")
            if discussion and discussion != item["url"]:
                meta += ' &middot; <a href="%s" style="color:#59636e;">discussion</a>' % esc(discussion)
            out.append('<p style="margin:1px 0 0 0;color:#59636e;font-size:13px;">%s</p>' % meta)
            if item["summary"]:
                out.append('<p style="margin:3px 0 0 0;font-size:14px;">%s</p>' % esc(item["summary"]))
            out.append("</div>")

    good, bad, skipped = source_summary(digest)
    out.append('<p style="margin:26px 0 0 0;padding-top:10px;border-top:1px solid #d1d9e0;'
               'color:#59636e;font-size:12px;">Sources that answered: %s.' %
               esc(", ".join(s["source"] for s in good) or "none"))
    if bad:
        out.append(" Sources that failed: %s." % esc(
            "; ".join("%s (%s)" % (s["source"], s.get("error", "error")[:70]) for s in bad)))
    if skipped:
        out.append(" Optional sources not available: %s." % esc(
            ", ".join(s["source"] for s in skipped)))
    out.append(" Made on this computer by ai_digest.py %s. Titles and short excerpts belong "
               "to their publishers; follow the links for the full text. Trending does not "
               "mean trusted: read a repository before you run its code.</p>" % esc(VERSION))
    out.append("</div></body></html>")
    return "\n".join(out)


def render_text(digest, markdown=False):
    lines = []
    title = digest_title(digest)
    lines.append(("# " + title) if markdown else title)
    lines.append("")
    lines.append("%d items from the last %d hours. Items sent on earlier days are left out."
                 % (digest["total"], round(digest["window_hours"])))
    if digest["highlights"]:
        lines += ["", "## In 30 seconds" if markdown else "IN 30 SECONDS"]
        for item in digest["highlights"]:
            if markdown:
                lines.append("- [%s](%s)" % (item["title"].replace("[", "(").replace("]", ")"),
                                             item["url"]))
            else:
                lines.append("- %s" % item["title"])
    for key, label in SECTIONS:
        lines += ["", ("## " + label) if markdown else label.upper()]
        items = digest["sections"].get(key) or []
        if not items:
            lines.append("Nothing notable in this period.")
        for item in items:
            meta = " | ".join(meta_parts(item))
            if markdown:
                lines.append("- **[%s](%s)**  " % (
                    item["title"].replace("[", "(").replace("]", ")"), item["url"]))
                lines.append("  %s" % meta)
                if item["summary"]:
                    lines.append("  %s" % item["summary"])
            else:
                lines.append("- %s" % item["title"])
                lines.append("  %s" % item["url"])
                lines.append("  %s" % meta)
                if item["summary"]:
                    lines.append("  %s" % item["summary"])
    good, bad, skipped = source_summary(digest)
    lines += ["", "Sources that answered: %s." % (", ".join(s["source"] for s in good) or "none")]
    if bad:
        lines.append("Sources that failed: %s." % "; ".join(
            "%s (%s)" % (s["source"], s.get("error", "error")[:70]) for s in bad))
    if skipped:
        lines.append("Optional sources not available: %s." % ", ".join(
            s["source"] for s in skipped))
    lines.append("Trending does not mean trusted: read a repository before you run its code.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Settings, state and email
# --------------------------------------------------------------------------

def load_config(path):
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise SystemExit("config.json is not valid JSON: %s" % exc)
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
    return config


def load_settings(path):
    """Read KEY=VALUE lines from email_settings.txt."""
    settings = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            settings[key.strip().upper()] = value.strip().strip('"').strip("'")
    return settings


def email_ready(settings):
    return bool(settings.get("GMAIL_ADDRESS") and settings.get("GMAIL_APP_PASSWORD"))


def send_email(settings, subject, text_body, html_body=None):
    address = settings["GMAIL_ADDRESS"].strip()
    password = settings["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipients = [a.strip() for a in re.split(r"[;,]", settings.get("SEND_TO") or address)
                  if a.strip()]
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = address
    message["To"] = ", ".join(recipients)
    message["Date"] = email.utils.formatdate(localtime=True)
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    host = settings.get("SMTP_HOST", "smtp.gmail.com")
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, 465, context=context, timeout=30) as server:
            server.login(address, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise
    except (OSError, smtplib.SMTPException):
        with smtplib.SMTP(host, 587, timeout=30) as server:  # second way in: STARTTLS
            server.starttls(context=context)
            server.login(address, password)
            server.send_message(message)
    return recipients


def explain_email_error(exc):
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("Gmail did not accept the sign-in. Use a 16-character Gmail APP PASSWORD "
                "(not your normal password) in email_settings.txt. App passwords need "
                "2-Step Verification on the Google account.")
    if isinstance(exc, (OSError, smtplib.SMTPException)):
        return ("Could not reach Gmail (%s). Some office networks block outgoing mail; "
                "try another network. The digest file was still saved." % short_error(exc))
    return short_error(exc)


def load_seen(path, keep_days):
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(seen, dict):
        return {}
    limit = (dt.date.today() - dt.timedelta(days=int(keep_days))).isoformat()
    return {k: v for k, v in seen.items() if isinstance(v, str) and v >= limit}


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --------------------------------------------------------------------------
# Self-test (no internet): proves parsing, filtering and rendering work
# --------------------------------------------------------------------------

def selftest():
    now = now_utc()
    recent = now - dt.timedelta(hours=3)
    old = now - dt.timedelta(days=9)
    rfc = email.utils.format_datetime
    iso = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    rss = ("<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><title>T</title>"
           "<item><title>Introducing Model X &amp; more</title><link>https://example.com/x?utm_source=a</link>"
           "<pubDate>%s</pubDate><description>&lt;p&gt;Model X is &lt;b&gt;here&lt;/b&gt;.&lt;/p&gt;</description></item>"
           "<item><title>Old post</title><link>https://example.com/old</link><pubDate>%s</pubDate></item>"
           "<item><title>No date</title><link>https://example.com/nodate</link></item>"
           "<item><title>Bad link</title><link>javascript:alert(1)</link><pubDate>%s</pubDate></item>"
           "</channel></rss>" % (rfc(recent), rfc(old), rfc(recent))).encode("utf-8")
    atom = ("<?xml version='1.0' encoding='utf-8'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            "<entry><title type='html'>Notes on running agents&nbsp;in production</title>"
            "<link rel='alternate' href='https://example.org/notes'/><updated>%s</updated>"
            "<summary type='html'>What worked &amp; what failed.</summary></entry></feed>"
            % iso(recent)).encode("utf-8")
    hn_story = {"hits": [
        {"objectID": "1", "title": "GPT-9 launch thread", "url": "https://example.com/gpt9",
         "points": 900, "num_comments": 400, "created_at_i": int(recent.timestamp()),
         "_tags": ["story"]},
        {"objectID": "2", "title": "A new bridge opens in Oslo", "url": "https://example.com/bridge",
         "points": 500, "num_comments": 100, "created_at_i": int(recent.timestamp()),
         "_tags": ["story"]}]}
    hn_show = {"hits": [
        {"objectID": "3", "title": "Show HN: An open-source LLM router", "url": None,
         "points": 120, "num_comments": 40, "created_at_i": int(recent.timestamp()),
         "_tags": ["story", "show_hn"]}]}
    devto = [{"title": "What I learned shipping an AI agent", "url": "https://dev.to/a/b",
              "description": "Lessons.", "published_at": iso(recent),
              "public_reactions_count": 80, "comments_count": 12},
             {"title": "Quiet post", "url": "https://dev.to/a/c", "published_at": iso(recent),
              "public_reactions_count": 2, "comments_count": 0}]
    github = {"items": [
        {"full_name": "acme/agent-kit", "html_url": "https://github.com/acme/agent-kit",
         "description": "Reusable agent templates", "stargazers_count": 4200, "language": "Python",
         "license": {"spdx_id": "MIT"}, "created_at": iso(now - dt.timedelta(days=10)),
         "fork": False, "archived": False}]}

    def fake_fetch(url, headers=None):
        if "hn.algolia.com" in url:
            return json.dumps(hn_show if "show_hn" in url else hn_story).encode()
        if "dev.to/api" in url:
            return json.dumps(devto).encode()
        if "api.github.com" in url:
            return json.dumps(github).encode()
        if "atom" in url:
            return atom
        if "broken" in url:
            raise FetchError("HTTP 500 test")
        return rss

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["github"]["pause_seconds"] = 0
    cfg["github"]["queries"] = cfg["github"]["queries"][:1] + cfg["github"]["queries"][2:3]
    cfg["feeds"] = [
        {"name": "Vendor", "section": "releases", "urls": ["https://example.com/rss"]},
        {"name": "Blogger", "section": "community", "urls": ["https://example.org/atom"]},
        {"name": "Down", "section": "releases", "urls": ["https://example.net/broken"]},
    ]
    digest = build_digest(cfg, fake_fetch, now=now, seen={}, hours=36)
    titles = lambda key: [item["title"] for item in digest["sections"][key]]
    page = render_html(digest)
    checks = [
        ("RSS: recent item kept, HTML cleaned",
         titles("releases") == ["Introducing Model X & more"]
         and digest["sections"]["releases"][0]["summary"] == "Model X is here."),
        ("RSS: old, undated and unsafe links dropped", "Old post" not in page
         and "No date" not in page and "javascript:" not in page),
        ("Atom: entry read", "Notes on running agents in production" in titles("community")),
        ("Hacker News: AI story kept, off-topic story dropped",
         "GPT-9 launch thread" in titles("community") and "bridge" not in page),
        ("Hacker News: Show HN goes to tools with a discussion link",
         any(i["title"].startswith("Show HN") and "item?id=3" in i["url"]
             for i in digest["sections"]["tools"])),
        ("DEV: popular post kept, quiet post dropped",
         "What I learned shipping an AI agent" in titles("community") and "Quiet post" not in page),
        ("GitHub: repository listed once in tools and not repeated in code",
         titles("tools").count("acme/agent-kit") == 1 and "acme/agent-kit" not in titles("code")),
        ("A failing source is reported, not fatal",
         any(s["source"] == "Down" and not s["ok"] for s in digest["statuses"])),
        ("Dates: RSS and ISO forms",
         parse_date("Fri, 20 Nov 2026 10:00:00 +0530") == dt.datetime(2026, 11, 20, 4, 30, tzinfo=UTC)
         and parse_date("2026-11-20T10:00:00.123+05:30") == dt.datetime(2026, 11, 20, 4, 30, tzinfo=UTC)
         and parse_date("not a date") is None),
        ("Repeat filter: an item sent on an earlier day is skipped",
         build_digest(cfg, fake_fetch, now=now, seen={"example.com/x": "2000-01-01"},
                      hours=36)["sections"]["releases"] == []),
        ("Text and Markdown versions render",
         "RELEASES" in render_text(digest) and "## Releases" in render_text(digest, True)),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        say("  %s  %s" % ("ok  " if ok else "FAIL", name))
    say("")
    say("SELF-TEST PASSED" if not failed else "SELF-TEST FAILED: %d check(s)" % len(failed))
    return 0 if not failed else 1


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="AI Updates Digest (local version)")
    parser.add_argument("--no-email", action="store_true", help="save the digest, do not email it")
    parser.add_argument("--open", action="store_true", help="open the digest in the browser")
    parser.add_argument("--hours", type=float, help="look back this many hours (default: config)")
    parser.add_argument("--all", action="store_true", help="also show items sent on earlier days")
    parser.add_argument("--out", help="folder for digests and run report (default: out)")
    parser.add_argument("--test-email", action="store_true", help="send a short test email")
    parser.add_argument("--selftest", action="store_true", help="check the program offline")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    settings = load_settings(APP_DIR / "email_settings.txt")
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        if settings.get(name):
            os.environ[name] = settings[name]

    if args.test_email:
        if not email_ready(settings):
            say("Email is not set up yet. Fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD in "
                "email_settings.txt first.")
            return 1
        try:
            sent_to = send_email(settings, "AI Updates Digest - test email",
                                 "This is a test from ai_digest.py. Email delivery works.\n")
        except Exception as exc:
            say("Test email failed. " + explain_email_error(exc))
            return 1
        say("Test email sent to %s." % ", ".join(sent_to))
        return 0

    config = load_config(APP_DIR / "config.json")
    out_dir = Path(args.out).expanduser().resolve() if args.out else APP_DIR / "out"
    seen_path = out_dir / "seen.json"
    seen = load_seen(seen_path, config.get("skip_items_sent_in_last_days", 14))

    say("AI Updates Digest %s - collecting (this takes about a minute)..." % VERSION)
    digest = build_digest(config, http_get, seen=seen, hours=args.hours, ignore_seen=args.all)

    for status in digest["statuses"]:
        if status["ok"]:
            say("  ok      %-36s %3d read, %d used" % (status["source"], status["fetched"],
                                                      status["kept"]))
        else:
            say("  %-7s %-36s %s" % ("skipped" if status.get("optional") else "FAILED",
                                     status["source"], status.get("error", "")))

    stamp = digest["generated"].astimezone().strftime("%Y-%m-%d")
    html_page = render_html(digest)
    html_path = out_dir / ("digest-%s.html" % stamp)
    write_text(html_path, html_page)
    write_text(out_dir / "latest.html", html_page)
    write_text(out_dir / ("digest-%s.md" % stamp), render_text(digest, markdown=True))

    report = {
        "version": VERSION, "finished": now_utc().isoformat(timespec="seconds"),
        "python": platform.python_version(), "system": platform.platform(),
        "window_hours": digest["window_hours"], "total_items": digest["total"],
        "sections": {key: len(digest["sections"][key]) for key, _ in SECTIONS},
        "sources": digest["statuses"], "digest_file": str(html_path),
        "email": {"attempted": False, "sent": False},
    }

    working = [s for s in digest["statuses"] if s["ok"]]
    exit_code = 0
    if not working:
        say("")
        say("No source could be reached. Check the internet connection. If this computer "
            "uses a proxy, add a line HTTPS_PROXY=http://host:port to email_settings.txt.")
        exit_code = 1
    elif digest["total"] == 0:
        say("")
        say("Nothing new since the last digest, so no email was sent.")
    elif args.no_email:
        pass
    elif not email_ready(settings):
        say("")
        say("Email is not set up, so the digest was only saved. See README.md, step 2.")
    else:
        report["email"]["attempted"] = True
        try:
            subject = "%s (%d items, local)" % (digest_title(digest), digest["total"])
            sent_to = send_email(settings, subject, render_text(digest), html_page)
            report["email"].update({"sent": True, "to": sent_to})
            say("")
            say("Email sent to %s." % ", ".join(sent_to))
        except Exception as exc:
            report["email"]["error"] = short_error(exc)
            say("")
            say("Email was NOT sent. " + explain_email_error(exc))

    # remember what was shown, so later days do not repeat it
    if working and not args.all:
        today = digest["generated"].astimezone().date().isoformat()
        for items in digest["sections"].values():
            for item in items:
                seen.setdefault(item["key"], today)
        write_text(seen_path, json.dumps(seen, indent=0, sort_keys=True))
    write_text(out_dir / "last_run.json", json.dumps(report, indent=2, default=str))

    say("")
    say("%d items saved to: %s" % (digest["total"], html_path))
    if args.open and digest["total"]:
        try:
            webbrowser.open(html_path.as_uri())
        except Exception:
            pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
