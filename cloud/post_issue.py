#!/usr/bin/env python3
"""Post today's digest as a GitHub issue, so GitHub emails it to the repository owner.

Used only by the GitHub Actions workflow. It does nothing when the digest is empty,
when the digest was already emailed through Gmail, or when the issue of today is
already there and holds every link of the new digest (a second run on one day).
"""
import json
import os
import pathlib
import re
import subprocess
import sys

OUT = pathlib.Path(os.environ.get("APP_DIR") or ".") / "out"
ZERO_WIDTH_SPACE = chr(0x200B)
LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")


def build_issue(report, markdown, owner):
    """Return (title, body), or (None, reason) when no issue should be opened."""
    if not report.get("total_items"):
        return None, "Nothing new today, so no issue was opened."
    if report.get("email", {}).get("sent"):
        return None, "The digest was sent by Gmail, so no issue was opened."
    lines = markdown.splitlines()
    title = (lines[0].lstrip("# ").strip() if lines else "") or "AI Updates Digest"
    body = "\n".join(lines[1:]).strip()
    # A title in the digest can contain "@name" or "#12". On GitHub these would notify
    # other people or link to other issues, so they are made harmless here.
    body = body.replace("@", "@" + ZERO_WIDTH_SPACE)
    body = re.sub(r"#(?=\d)", "#" + ZERO_WIDTH_SPACE, body)
    if owner:
        body += "\n\ncc @%s" % owner  # this mention is what makes GitHub email you
    return title, body + "\n"


def repo_args():
    name = os.environ.get("GITHUB_REPOSITORY")
    return ["--repo", name] if name else []


def already_posted(title, body):
    """True when an issue with this title is there and holds every link of the new text."""
    command = ["gh", "issue", "list", "--state", "all", "--limit", "30", "--json", "title,body"]
    try:
        done = subprocess.run(command + repo_args(), capture_output=True, text=True, timeout=120)
        issues = json.loads(done.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False  # when in doubt, post: an email twice is better than no email
    links = set(LINK_RE.findall(body))
    return any(isinstance(issue, dict) and issue.get("title") == title
               and links <= set(LINK_RE.findall(issue.get("body") or "")) for issue in issues)


def main():
    try:
        report = json.loads((OUT / "last_run.json").read_text(encoding="utf-8"))
        markdown = (OUT / "latest.md").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        print("No digest to post: %s" % exc)
        return 0
    title, body = build_issue(report, markdown, os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip())
    if title is None:
        print(body)
        return 0
    if already_posted(title, body):
        print("The issue of today is already there and nothing new came in, "
              "so no second issue was opened.")
        return 0
    path = pathlib.Path(os.environ.get("RUNNER_TEMP") or ".") / "digest_issue.md"
    path.write_text(body, encoding="utf-8")
    command = ["gh", "issue", "create", "--title", title, "--body-file", str(path)]
    return subprocess.run(command + repo_args()).returncode


if __name__ == "__main__":
    sys.exit(main())
