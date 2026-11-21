#!/usr/bin/env python3
"""Say whether a scheduled start still has work to do.

GitHub sometimes starts a scheduled job late, or not at all. So each daily job has two
start times, and the second one is a backup. A scheduled start does nothing when the
last run finished a short time ago. A start by hand always runs.

Used only by the GitHub Actions workflows. It prints "go=true" or "go=false" and writes
the same line to the file that GitHub names in GITHUB_OUTPUT.
"""
import datetime
import json
import os
import pathlib
import sys

HOURS = 3.0   # a scheduled start is skipped when the last run is younger than this


def decide(event, report_path, now=None):
    """Return (go, reason)."""
    if event != "schedule":
        return True, "started by hand"
    try:
        report = json.loads(pathlib.Path(report_path).read_text(encoding="utf-8"))
        done = datetime.datetime.fromisoformat(str(report["finished"]))
        if done.tzinfo is None:
            done = done.replace(tzinfo=datetime.timezone.utc)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return True, "the report of the last run cannot be read (%s)" % exc.__class__.__name__
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age = (now - done).total_seconds() / 3600.0
    if 0 <= age < HOURS:
        return False, "the last run finished %.1f hours ago, so there is nothing to do" % age
    return True, "the last run finished %.1f hours ago" % age


def main():
    # LIKE_SCHEDULE is set by the box "act like a scheduled start" of a start by hand.
    by_schedule = os.environ.get("LIKE_SCHEDULE") == "true"
    event = "schedule" if by_schedule else os.environ.get("GITHUB_EVENT_NAME", "")
    report = pathlib.Path(os.environ.get("APP_DIR") or ".") / "out" / "last_run.json"
    go, reason = decide(event, report)
    line = "go=%s" % ("true" if go else "false")
    print("%s (%s)" % (line, reason))
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
