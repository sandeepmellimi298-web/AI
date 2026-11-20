#!/usr/bin/env python3
"""Run the digest program on GitHub Actions, with two small adjustments for the cloud."""
import os
import pathlib
import shutil
import sys

APP_DIR = pathlib.Path(os.environ.get("APP_DIR") or "ai-updates-digest").resolve()
sys.path.insert(0, str(APP_DIR))
import ai_digest  # noqa: E402

_load_config = ai_digest.load_config


def load_config(path):
    config = _load_config(path)
    # DEV Community and Reddit refuse automated readers, so they are left out here.
    config["devto"]["enabled"] = False
    config["feeds"] = [feed for feed in config["feeds"]
                       if "reddit.com" not in " ".join(feed.get("urls", []))]
    return config


ai_digest.load_config = load_config
code = ai_digest.main(["--no-email"])
digests = sorted((APP_DIR / "out").glob("digest-*.md"))
if digests:
    shutil.copyfile(digests[-1], APP_DIR / "out" / "latest.md")  # newest digest, fixed name
sys.exit(code)
