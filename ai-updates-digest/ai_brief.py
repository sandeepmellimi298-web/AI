#!/usr/bin/env python3
"""AI brief.

A small AI model that runs on this machine reads the newest digest in the folder
"out", picks the items that matter most by meaning, and writes one short line
about each of them at the top. It works for the AI Updates Digest and for the
Bank Jobs Alert.

The model runs through Ollama (https://ollama.com). There is no account, no key
and no outside AI service. If Ollama is not running, nothing is changed. If a
model is not on this machine yet, the program asks before it downloads it.

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

VERSION = "1.2"
HERE = pathlib.Path(__file__).resolve().parent
START, END = "<!-- ai-brief:start -->", "<!-- ai-brief:end -->"
DEFAULTS = {
    "enabled": True,
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "phi4-mini",
    "embed_model": "all-minilm",
    "download_missing_models": "ask",   # true, false or "ask"
    "top_picks": 5,
    "max_picks_per_section": 2,
    "explain_top": 3,
    "reader": "A reader who wants the most important and most useful items of today.",
}
SKIP_SECTIONS = ("in 30 seconds", "ai picks")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SCHEMA = {"type": "object", "properties": {"sentence": {"type": "string"}},
          "required": ["sentence"]}
SIZES = {"phi4-mini": "about 2.5 GB", "all-minilm": "about 46 MB"}
NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")
DAY_RE = re.compile(r",\s*\d{1,2} [A-Z][a-z]{2}\)")   # the day in "(20 posts, Adda247, 20 Nov)"


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

    def missing(self, models):
        """The models of the list that are not on this machine."""
        names = [m.get("name", "") for m in self.call("/api/tags").get("models", [])]
        return [model for model in models
                if not any(name == model or name.split(":")[0] == model for name in names)]

    def pull(self, model):
        """Download a model and show how far it is."""
        data = json.dumps({"model": model, "stream": True}).encode("utf-8")
        request = urllib.request.Request(self.base + "/api/pull", data=data,
                                         headers={"Content-Type": "application/json"})
        shown = {}
        try:
            with self.opener.open(request, timeout=600) as response:
                for raw in response:
                    try:
                        step = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        continue
                    if not isinstance(step, dict):
                        continue
                    if step.get("error"):
                        raise AiError("the download of %s failed (%s)"
                                      % (model, str(step["error"])[:200]))
                    total, done = step.get("total"), step.get("completed") or 0
                    if not isinstance(total, (int, float)) or not isinstance(done, (int, float)):
                        continue
                    part = int(10 * done / total) if total > 50000000 else -1
                    if part > shown.get(step.get("digest"), -1):
                        shown[step.get("digest")] = part
                        say("  %s: %d%% of %.1f GB" % (model, part * 10, total / 1e9))
        except urllib.error.HTTPError as exc:
            raise AiError("the download of %s failed (HTTP %s)" % (model, exc.code))
        except (urllib.error.URLError, OSError) as exc:
            raise AiError("the download of %s stopped (%s)" % (model, getattr(exc, "reason", exc)))

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
    """Find the items of a digest: section, title, link, where it is from and what it says."""
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
        parts = [re.sub(r"\s+", " ", re.sub(r"[*\[\]]", "", part)).strip(" :") for part in around]
        parts = [part for part in parts if part]
        # A part such as "GitHub | 425 stars | ABAP | MIT" tells where the item is from; the
        # model only gets its first piece, so that it cannot mix up stars, language and licence.
        items.append({"section": section, "title": match.group(1).strip(), "url": match.group(2),
                      "note": " ".join(parts)[:300],
                      "source": " ".join(p.split(" | ")[0] for p in parts if " | " in p)[:80],
                      "text": " ".join(p for p in parts if " | " not in p)[:300]})
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
    text = re.sub(r"[\[\]()<>*_#|~\"\x60]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text[:240]


def numbers_in(text):
    return {re.sub(r"[,.]", "", number) for number in NUMBER_RE.findall(text)}


def explain(ai, cfg, item):
    """One plain sentence about one item, or "" when the answer cannot be trusted."""
    facts = "Item: %s\n" % item["title"]
    if item.get("source"):
        facts += "Source: %s\n" % item["source"]
    if item.get("text"):
        # Without the day, so that a day such as "20 Nov" cannot pass as a number of posts.
        facts += "Details: %s\n" % DAY_RE.sub(")", item["text"])
    system = ("You explain one news item in one short sentence in simple English. Use only what "
              "the item says. Never add a fact, a number, a date, a link or advice.")
    user = (facts + "\nWrite one sentence of at most 25 words that says in plain words what this "
            'item is about.\nAnswer as JSON: {"sentence": "..."}')
    try:
        answer = json.loads(ai.chat(cfg["chat_model"], system, user))
    except ValueError:
        return ""
    text = clean_sentence(answer.get("sentence", "") if isinstance(answer, dict) else "")
    if len(text) < 15 or not numbers_in(text) <= numbers_in(facts):
        return ""  # too short, or a number that is not in the item: do not trust it
    return text


def may_download(cfg, missing):
    """May the missing models be downloaded? With the setting "ask" a person has to say yes."""
    setting = cfg["download_missing_models"]
    if setting != "ask":
        return setting is True
    if not (sys.stdin and sys.stdin.isatty()):
        return False  # nobody is there to answer, for example in a scheduled run
    say("The AI part needs a download, one time: %s."
        % ", ".join("%s (%s)" % (model, SIZES.get(model, "size not known")) for model in missing))
    try:
        answer = input("Download now? Type y and press Enter. To skip, press only Enter: ")
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in ("y", "yes")


def get_models(ai, cfg, notes):
    """The models that are ready to use. Missing ones are downloaded when that is allowed."""
    wanted = [cfg["embed_model"], cfg["chat_model"]]
    missing = ai.missing(wanted)
    if missing and may_download(cfg, missing):
        for model in missing:
            say("Downloading the AI model %s, one time. Please wait..." % model)
            try:
                ai.pull(model)
            except AiError as exc:
                notes.append(str(exc))
        missing = ai.missing(wanted)
    notes += ["the AI model %s is not on this machine" % model for model in missing]
    return [model for model in wanted if model not in missing]


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
    notes = []
    ready = get_models(ai, cfg, notes)
    if not ready:
        raise AiError("; ".join(notes))
    say("AI part: the AI model is reading the digest...")
    picks, by_meaning, sentences = [], False, 0
    if cfg["embed_model"] in ready:
        try:
            picks, by_meaning = pick_by_meaning(ai, cfg, items), True
        except AiError as exc:
            notes.append("picks: %s" % exc)
    if not by_meaning:
        picks = items[:int(cfg["top_picks"])]
    if cfg["chat_model"] in ready:
        try:
            for item in picks[:int(cfg["explain_top"])]:
                item["ai"] = explain(ai, cfg, item)
                sentences += 1 if item["ai"] else 0
        except AiError as exc:
            notes.append("sentences: %s" % exc)
    if not picks or (not by_meaning and not sentences):
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
