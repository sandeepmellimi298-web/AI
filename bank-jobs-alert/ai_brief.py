#!/usr/bin/env python3
"""AI brief.

A small AI model that runs on this machine reads the newest digest in the folder
"out", picks the items that matter most by meaning, and writes one short line
about each of them at the top. It works for the AI Updates Digest and for the
Bank Jobs Alert.

The model runs through Ollama (https://ollama.com). There is no account, no key
and no outside AI service. If Ollama is not running, nothing is changed.

If the model of the settings is not on this machine, a model that Ollama already
has is used (setting "other_chat_models"). A small helper model (about 46 MB) is
downloaded without a question. Before a big download the program asks.

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

VERSION = "1.3"
HERE = pathlib.Path(__file__).resolve().parent
START, END = "<!-- ai-brief:start -->", "<!-- ai-brief:end -->"
DEFAULTS = {
    "enabled": True,
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "phi4-mini",
    "other_chat_models": ["llama3.2"],   # used when chat_model is not on this machine
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
SMALL = ("all-minilm",)   # small enough to download without a question
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
    code = 0   # the HTTP code, when the AI program answered with an error


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
            error = AiError("the AI program answered HTTP %s %s" % (exc.code, detail))
            error.code = exc.code
            raise error
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AiError("no answer from the AI program (%s)" % (getattr(exc, "reason", exc),))

    def installed(self):
        """The names of the models on this machine, such as "llama3.2:latest"."""
        return [m.get("name", "") for m in self.call("/api/tags").get("models", [])]

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
        payload = {
            "model": model, "stream": False, "format": SCHEMA,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.2, "num_ctx": 2048, "num_predict": 120},
        }
        try:
            answer = self.call("/api/chat", payload, timeout=900)
        except AiError as exc:
            if exc.code != 400:
                raise
            payload["format"] = "json"   # an older Ollama does not know a JSON schema here
            answer = self.call("/api/chat", payload, timeout=900)
        return (answer.get("message") or {}).get("content", "")


def find(names, model):
    """The name under which a model is on this machine, or "" when it is not there."""
    for name in names:
        if name in (model, model + ":latest"):
            return name
    for name in names:
        if name.split(":")[0] == model:
            return name   # another size of the same model, such as llama3.2:1b
    return ""


def plain(name):
    return name[:-len(":latest")] if name.endswith(":latest") else name


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

def pick_by_meaning(ai, model, cfg, items):
    texts = [cfg["reader"]] + ["%s. %s" % (item["title"], item["note"]) for item in items]
    vectors = ai.embed(model, [text[:600] for text in texts])
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


def explain(ai, model, item):
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
        answer = json.loads(ai.chat(model, system, user))
    except ValueError:
        return ""
    text = clean_sentence(answer.get("sentence", "") if isinstance(answer, dict) else "")
    if len(text) < 15 or not numbers_in(text) <= numbers_in(facts):
        return ""  # too short, or a number that is not in the item: do not trust it
    return text


def allowed_downloads(cfg, missing):
    """The missing models that may be downloaded now.

    Setting true: all of them. Setting false: none. Setting "ask": a small helper model
    is downloaded without a question, and for a big model a person has to say yes.
    """
    setting = cfg["download_missing_models"]
    if setting is True:
        return list(missing)
    if setting != "ask":
        return []
    big = [model for model in missing if model not in SMALL]
    yes = False
    if big and sys.stdin and sys.stdin.isatty():   # else nobody is there to answer
        say("The AI part needs a download, one time: %s."
            % ", ".join("%s (%s)" % (model, SIZES.get(model, "size not known")) for model in big))
        try:
            answer = input("Download now? Type y and press Enter. To skip, press only Enter: ")
        except (EOFError, OSError):
            answer = ""
        yes = answer.strip().lower() in ("y", "yes")
    return [model for model in missing if model in SMALL or yes]


def get_models(ai, cfg, notes):
    """Find the two models. Returns their names on this machine; "" for one that is not there.

    When the chat model is not there, another model that Ollama already has is used, so
    that nothing big has to be downloaded.
    """
    others = cfg["other_chat_models"] if isinstance(cfg["other_chat_models"], list) else []

    def look():
        names = ai.installed()
        chat = find(names, cfg["chat_model"])
        for other in others:
            chat = chat or find(names, str(other))
        return find(names, cfg["embed_model"]), chat

    embed, chat = look()
    missing = [model for model, found in ((cfg["embed_model"], embed), (cfg["chat_model"], chat))
               if not found]
    for model in allowed_downloads(cfg, missing):
        say("Downloading the AI model %s (%s), one time. Please wait..."
            % (model, SIZES.get(model, "size not known")))
        try:
            ai.pull(model)
        except AiError as exc:
            notes.append(str(exc))
    if missing:
        embed, chat = look()
    notes += ["the AI model %s is not on this machine" % model
              for model, found in ((cfg["embed_model"], embed), (cfg["chat_model"], chat))
              if not found]
    if chat and not find([chat], cfg["chat_model"]):
        say("AI part: %s is not on this machine, so the model %s is used."
            % (cfg["chat_model"], plain(chat)))
    return embed, chat


# --------------------------------------------------------------------------
# Writing the AI part into the files
# --------------------------------------------------------------------------

def footer(model, by_meaning, picks):
    did = [words for words, yes in (("picked these items by meaning", by_meaning),
                                    ("wrote the short lines", any(i.get("ai") for i in picks)))
           if yes]
    return ("A small AI model (%s) %s. It ran on this machine, with no outside AI service. "
            "It can make mistakes, so check the links." % (model, " and ".join(did)))


def block_markdown(picks, model, by_meaning):
    out = [START, "## AI picks", ""]
    for n, item in enumerate(picks, 1):
        title = item["title"].replace("[", "(").replace("]", ")")
        line = "%d. [%s](%s)" % (n, title, item["url"])
        out.append(line + (": " + item["ai"] if item.get("ai") else ""))
    out += ["", "*%s*" % footer(model, by_meaning, picks), END, "", ""]
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
               % esc(footer(model, by_meaning, picks)))
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

def run(cfg, out_dir, report):
    source = out_dir / "latest.md"
    if not source.exists():
        source = newest(out_dir, "*-20*.md")
    if source is None or not source.exists():
        raise AiError("there is no digest in %s yet" % out_dir)
    items = read_items(strip_block(source.read_text(encoding="utf-8")))
    if len(items) < 2:
        raise AiError("the digest has fewer than 2 items, so there is nothing to sort")

    ai = Ollama(cfg["ollama_url"])
    version = ai.call("/api/version", timeout=10)
    report["ollama"] = str(version.get("version", "") if isinstance(version, dict) else "")[:40]
    notes = []
    embed, chat = get_models(ai, cfg, notes)
    if not embed and not chat:
        raise AiError("; ".join(notes))
    say("AI part: the AI model is reading the digest...")
    picks, by_meaning, sentences = [], False, 0
    if embed:
        try:
            picks, by_meaning = pick_by_meaning(ai, embed, cfg, items), True
        except AiError as exc:
            notes.append("picks: %s" % exc)
    if not by_meaning:
        picks = items[:int(cfg["top_picks"])]
    if chat:
        try:
            for item in picks[:int(cfg["explain_top"])]:
                item["ai"] = explain(ai, chat, item)
                sentences += 1 if item["ai"] else 0
        except AiError as exc:
            notes.append("sentences: %s" % exc)
    if not picks or (not by_meaning and not sentences):
        raise AiError("; ".join(notes) or "the AI model gave nothing usable")
    model = plain(chat or embed)

    changed = []
    for path in {source, out_dir / "latest.md", newest(out_dir, "*-20*.md")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_markdown(picks, model, by_meaning), ["## "])
            write_text(path, text)
            changed.append(path.name)
    for path in {out_dir / "latest.html", newest(out_dir, "*-20*.html")}:
        if path and path.exists():
            text = insert_block(path.read_text(encoding="utf-8"),
                                block_html(picks, model, by_meaning),
                                ['<div style="background:#f0f6ff', "<h2 "])
            write_text(path, text)
            changed.append(path.name)
    return {"model": model, "items": len(items), "picks": len(picks), "sentences": sentences,
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
            report.update(run(cfg, out_dir, report), ok=True)
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
