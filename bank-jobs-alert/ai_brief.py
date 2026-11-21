#!/usr/bin/env python3
"""AI brief.

A small AI model that runs on this machine reads the newest digest in the folder
"out", picks the items that matter most by meaning, and writes one short line
about the first of them at the top. It works for the AI Updates Digest and for the Bank Jobs Alert.

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

VERSION = "1.1"
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
    "explain_top": 3,
    "reader": "A reader who wants the most important and most useful items of today.",
}
SKIP_SECTIONS = ("in 30 seconds", "ai picks")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SCHEMA = {"type": "object", "properties": {"sentence": {"type": "string"}},
          "required": ["sentence"]}


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
            "options": {"temperature": 0.2, "num_ctx": 2048, "num_predict": 120},
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
# The two AI jobs: pick by meaning, and explain a pick in one line
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


def clean_sentence(text):
    text = re.sub(r"https?://\S+", "", str(text))
    text = re.sub(r"[(\[]\s*\d+(?:\s*,\s*\d+)*\s*[)\]]", " ", text)  # item numbers such as (1)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"[\[\]()<>*_#|\"]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text[:240]


def explain(ai, cfg, item):
    """One plain sentence about one item, or "" when the answer cannot be trusted."""
    system = ("You explain one news item in one short sentence in simple English. Use only what "
              "the item says. Never add a fact, a number, a date, a link or advice.")
    user = ("Item: %s\nDetails: %s\n\nWrite one sentence of at most 25 words that says in plain "
            "words what this item is about.\n"
            'Answer as JSON: {"sentence": "..."}' % (item["title"], item["note"] or "none"))
    try:
        answer = json.loads(ai.chat(cfg["chat_model"], system, user))
    except ValueError:
        return ""
    text = clean_sentence(answer.get("sentence", "") if isinstance(answer, dict) else "")
    known = re.sub(r"[,.]", "", "%s %s" % (item["title"], item["note"]))
    numbers = re.findall(r"\d[\d,.]*\d|\d", text)
    if len(text) < 15 or any(re.sub(r"[,.]", "", number) not in known for number in numbers):
        return ""  # too short, or a number that is not in the item: do not trust it
    return text


# --------------------------------------------------------------------------
# Writing the AI part into the files
# --------------------------------------------------------------------------

def footer(model, by_meaning):
    how = ("picked these items by meaning and wrote the short lines" if by_meaning
           else "wrote the short lines")
    return ("A small AI model (%s) %s. It ran on this machine, with no outside AI service. "
            "It can make mistakes, so check the links." % (model, how))


def block_markdown(picks, model, by_meaning):
    out = [START, "## AI picks", ""]
    for n, item in enumerate(picks, 1):
        title = item["title"].replace("[", "(").replace("]", ")")
        line = "%d. [%s](%s)" % (n, title, item["url"])
        out.append(line + (": " + item["ai"] if item.get("ai") else ""))
    out += ["", "*%s*" % footer(model, by_meaning), END, "", ""]
    return "\n".join(out)


def block_html(picks, model, by_meaning):
    esc = lambda value: html.escape(str(value), quote=True)
    out = [START, '<div style="background:#f6f8fa;border-left:4px solid #8250df;'
           'padding:10px 14px;margin:0 0 18px 0;">',
           '<p style="margin:0 0 6px 0;font-weight:600;">AI picks</p>',
           '<ol style="margin:0;padding-left:20px;">']
    for item in picks:
        out.append('<li style="margin:3px 0;"><a href="%s" style="color:#0969da;'
                   'text-decoration:none;">%s</a>%s</li>'
                   % (esc(item["url"]), esc(item["title"]),
                      ": " + esc(item["ai"]) if item.get("ai") else ""))
    out.append('</ol><p style="margin:8px 0 0 0;color:#59636e;font-size:12px;">%s</p></div>'
               % esc(footer(model, by_meaning)))
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
    picks, notes, by_meaning, sentences = [], [], True, 0
    try:
        ai.need(cfg["embed_model"], cfg["download_missing_models"])
        picks = pick_by_meaning(ai, cfg, items)
    except AiError as exc:
        notes.append("picks: %s" % exc)
        picks, by_meaning = items[:int(cfg["top_picks"])], False
    try:
        ai.need(cfg["chat_model"], cfg["download_missing_models"])
        for item in picks[:int(cfg["explain_top"])]:
            item["ai"] = explain(ai, cfg, item)
            sentences += 1 if item["ai"] else 0
    except AiError as exc:
        notes.append("sentences: %s" % exc)
    if not by_meaning and not sentences:
        raise AiError("; ".join(notes) or "the AI model gave nothing usable")

    changed = []
    for path in {source, out_dir / "latest.md", newest(out_dir, "*-20*.md")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_markdown(picks, cfg["chat_model"], by_meaning), ["## "])
            write_text(path, text)
            changed.append(path.name)
    for path in {out_dir / "latest.html", newest(out_dir, "*-20*.html")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_html(picks, cfg["chat_model"], by_meaning),
                                ['<div style="background:#f0f6ff', "<h2 "])
            write_text(path, text)
            changed.append(path.name)
    return {"items": len(items), "picks": len(picks), "sentences": sentences,
            "by_meaning": by_meaning, "files": sorted(changed), "notes": notes}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Add the AI picks to the newest digest.")
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
        say("AI part: done in %s seconds. %d picks with %d AI lines were added to %s."
            % (report["seconds"], report["picks"], report["sentences"], ", ".join(report["files"])))
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
