#!/usr/bin/env python3
"""The clock. It starts the daily jobs at the right time.

Why it is here: GitHub has its own timer for jobs ("schedule"). In this repository that
timer did not start the jobs. So this small clock does it.

How it works:
  1. A clock run first waits. GitHub does the waiting, in an "environment" with a wait
     timer (the environment "wait-418" waits 418 minutes). No machine is used while it
     waits.
  2. After the wait, the run starts the daily jobs that are due. Then it starts the next
     clock run, which waits until the next start time. So the clock goes on by itself.
  3. There are two lines of clock runs. The "main" line wakes at the start times. The
     "guard" line wakes 10 minutes later. When one line is gone, the other one starts it
     again. The guard also starts a job that the main line did not start, and it starts
     a job one more time when its first run failed.

The daily jobs themselves make sure that they do not do their work twice (see
cloud/should_run.py), so one start too many does no harm.

If the clock ever stops: open "Actions", then "Clock", and press "Run workflow". One
start is enough, it brings back both lines.

Used by the workflow .github/workflows/clock.yml. The workflow clock-test.yml runs it
with "--test", to check by hand that the clock can do its work.
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UTC = datetime.timezone.utc
INDIA = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# The start times of the daily jobs, in UTC. India time (IST) is 5 hours 30 minutes later.
JOBS = (
    ("ai-digest.yml", ("02:23",)),            # 7:53 AM in India
    ("bank-jobs.yml", ("02:37",)),            # 8:07 AM in India
    ("gold-rate.yml", ("03:27", "10:27")),    # 8:57 AM and 3:57 PM in India
)

# The two lines, and how many minutes after a start time each one wakes.
LINES = (("main", 0), ("guard", 10))

# The waits, in minutes. For each one the repository has an environment "wait-<minutes>"
# whose wait timer is that number of minutes (Settings, Environments). They fit the
# gaps between the start times above: 14, 50, 420 and 956 minutes, each less 2 minutes.
WAITS = (954, 418, 48, 12)

CLOCK = "clock.yml"   # the file name of the clock workflow

SPARE = 1.0       # minutes that a wait must leave before the time to wake
NAP = 15.0        # minutes: a shorter rest than this is slept on the machine itself
LATE = 180        # minutes: a job is still started this long after its start time
EARLY = 30        # minutes: a run of a job made this long before its start time counts
GRACE = 90        # minutes: a clock run that is this late is seen as lost
GIVE_UP = 300     # minutes: so long a run tries to start the next clock run
MOST_RUNS = 20    # more clock runs than this in one hour means that something is wrong
BAD = ("failure", "timed_out", "startup_failure")

TITLE = re.compile(r"^Clock (main|guard) wait-(\d+)$")


def parse_time(text):
    """Turn a time from GitHub ("2026-11-21T13:36:12Z") into a date and time in UTC."""
    return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)


def minutes_of(text):
    """Turn "HH:MM" into minutes after midnight."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def show(moment):
    """Write a moment for the log, in UTC and in India time."""
    india = moment.astimezone(INDIA)
    hour = india.hour % 12 or 12
    half = "AM" if india.hour < 12 else "PM"
    return "%s UTC (%d:%02d %s in India)" % (moment.strftime("%H:%M"), hour, india.minute, half)


def due(now):
    """Return [(workflow, start time)] for each start time of the last LATE minutes."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    found = []
    for name, times in JOBS:
        for text in times:
            for back in (0, 1):
                at = day + datetime.timedelta(days=-back, minutes=minutes_of(text))
                if at <= now and now - at <= datetime.timedelta(minutes=LATE):
                    found.append((name, at))
    found.sort(key=lambda item: item[1])
    return found


def next_wake(line, now):
    """Return the next moment after now at which the line wakes."""
    after = dict(LINES)[line]
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    best = None
    for _name, times in JOBS:
        for text in times:
            for ahead in (-1, 0, 1, 2):
                at = day + datetime.timedelta(days=ahead, minutes=minutes_of(text) + after)
                if at > now and (best is None or at < best):
                    best = at
    return best


def line_of(run):
    """Return the line of a clock run ("main" or "guard"), or None when it is not clear."""
    found = TITLE.match(str(run.get("display_title") or ""))
    return found.group(1) if found else None


def wait_of(run):
    """Return the minutes that a clock run waits, or the longest wait when it is not clear."""
    found = TITLE.match(str(run.get("display_title") or ""))
    return int(found.group(2)) if found else max(WAITS)


def alive(run, now):
    """Say whether a clock run is still waiting or working, and is not lost."""
    if run.get("status") == "completed":
        return False
    try:
        age = (now - parse_time(run["created_at"])).total_seconds() / 60.0
    except (KeyError, ValueError):
        return True
    return age <= wait_of(run) + GRACE


def others_alive(runs, me, now):
    """Return the other clock runs that are alive."""
    return [run for run in runs if run.get("id") != me and alive(run, now)]


def replaced(me, line, event, others):
    """Say whether another run of the same line does the work of this run.

    A run started by GitHub's own timer (the safety net) steps back for any run of its
    line. Any other run steps back only for a newer run of its line. So when two runs of
    one line are there by mistake, the older one stops and exactly one goes on.
    """
    same = [run for run in others if line_of(run) == line]
    if event == "schedule":
        return bool(same)
    return any(run.get("id", 0) > me for run in same)


def too_many(runs, now):
    """Say whether more clock runs were made in the last hour than is normal."""
    hour = datetime.timedelta(hours=1)
    count = 0
    for run in runs:
        try:
            if now - parse_time(run["created_at"]) <= hour:
                count += 1
        except (KeyError, ValueError):
            pass
    return count > MOST_RUNS


class GitHub:
    """The few calls to GitHub that the clock needs."""

    def __init__(self, repo, token, base="https://api.github.com", ref="main", pause=time.sleep):
        self.repo = repo
        self.token = token
        self.base = base.rstrip("/")
        self.ref = ref
        self.pause = pause

    def call(self, method, path, body=None):
        """Return (status, data). The status is 0 when GitHub could not be reached."""
        url = "%s/repos/%s%s" % (self.base, self.repo, path)
        data = None if body is None else json.dumps(body).encode("utf-8")
        status, answer = 0, None
        for wait in (2, 5, 10, None):
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("Accept", "application/vnd.github+json")
            request.add_header("User-Agent", "clock")
            if self.token:
                request.add_header("Authorization", "Bearer " + self.token)
            if data is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=30) as reply:
                    status, raw = reply.status, reply.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
            except (urllib.error.URLError, OSError, ValueError):
                status, raw = 0, b""
            try:
                answer = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                answer = None
            if status not in (0, 429) and status < 500:
                break
            if wait is None:
                break
            self.pause(wait)
        return status, answer

    def run(self, run_id):
        """Return one run, or None."""
        status, answer = self.call("GET", "/actions/runs/%d" % run_id)
        return answer if status == 200 and isinstance(answer, dict) else None

    def runs(self, workflow, count):
        """Return the newest runs of a workflow, or None when they cannot be read."""
        status, answer = self.call("GET", "/actions/workflows/%s/runs?per_page=%d" % (workflow, count))
        if status != 200 or not isinstance(answer, dict):
            return None
        found = answer.get("workflow_runs")
        return found if isinstance(found, list) else None

    def start(self, workflow, inputs):
        """Start a workflow. Return True when GitHub took the start."""
        status, _answer = self.call("POST", "/actions/workflows/%s/dispatches" % workflow,
                                    {"ref": self.ref, "inputs": inputs})
        return 200 <= status < 300

    def timer(self, name):
        """Return the wait timer of an environment in minutes.

        0 means that the environment has no wait timer or is not there. None means that
        GitHub did not say.
        """
        status, answer = self.call("GET", "/environments/%s" % name)
        if status == 404:
            return 0
        if status != 200 or not isinstance(answer, dict):
            return None
        for rule in answer.get("protection_rules") or []:
            if isinstance(rule, dict) and rule.get("type") == "wait_timer":
                try:
                    return int(rule.get("wait_timer") or 0)
                except (TypeError, ValueError):
                    return None
        return 0


class Clock:
    """One clock run. The method steps() does the work.

    steps() is written so that it never sleeps itself. Each time it has to wait, it hands
    out the number of seconds, and main() sleeps. So the tests can run many days of the
    clock in a moment.
    """

    def __init__(self, hub, now, me, line, wait, event, say):
        self.hub = hub
        self.now = now          # a function that returns the time now, in UTC
        self.me = me            # the number of this run
        self.line = line        # "main" or "guard"
        self.wait = wait        # the minutes that this run waited, 0 when not known
        self.event = event      # "workflow_dispatch" or "schedule"
        self.say = say
        self.other = [name for name, _after in LINES if name != line][0]
        self.timers = {}        # what is known about the environments
        self.started = set()    # the job starts that this run has made
        self.problems = 0

    # -- the daily jobs ------------------------------------------------------------------

    def start_due_jobs(self):
        """Start each daily job that is due and has no run yet."""
        now = self.now()
        for name, at in due(now):
            if (name, at) in self.started:
                continue
            since = at - datetime.timedelta(minutes=EARLY)
            runs = self.hub.runs(name, 20)
            if runs is None:
                reason = "its runs cannot be read, so it is started to be safe"
            else:
                recent = []
                for run in runs:
                    try:
                        if parse_time(run["created_at"]) >= since:
                            recent.append(run)
                    except (KeyError, ValueError):
                        recent.append(run)
                if not recent:
                    reason = "it has no run yet"
                elif (len(recent) == 1 and recent[0].get("status") == "completed"
                      and recent[0].get("conclusion") in BAD):
                    reason = "its first run failed, so it gets one more try"
                else:
                    continue
            if self.hub.start(name, {"like_schedule": "true"}):
                self.started.add((name, at))
                self.say("Started %s for %s: %s." % (name, show(at), reason), True)
            else:
                self.problems += 1
                self.say("PROBLEM: %s could not be started for %s." % (name, show(at)),
                         self.problems <= 3)

    # -- the waits -----------------------------------------------------------------------

    def usable(self, minutes):
        """Say whether the environment of a wait really waits that long."""
        if minutes not in self.timers:
            found = self.hub.timer("wait-%d" % minutes)
            self.timers[minutes] = found is None or found == minutes
            if not self.timers[minutes]:
                self.problems += 1
                self.say("PROBLEM: the environment wait-%d does not wait %d minutes "
                         "(GitHub says %d). It is not used." % (minutes, minutes, found), True)
        return self.timers[minutes]

    def pick(self, minutes):
        """Return the longest usable wait that ends in time, or None."""
        for wait in sorted(WAITS, reverse=True):
            if wait + SPARE <= minutes and self.usable(wait):
                return wait
        return None

    def shortest(self):
        """Return the shortest usable wait, or None."""
        for wait in sorted(WAITS):
            if self.usable(wait):
                return wait
        return None

    # -- the next clock runs -------------------------------------------------------------

    def start_clock(self, line, wait):
        """Start a clock run of a line. Hands out seconds to sleep. Returns True or False."""
        title = "Clock %s wait-%d" % (line, wait)
        inputs = {"line": line, "wait": "wait-%d" % wait}
        since = self.now() - datetime.timedelta(seconds=60)
        for attempt in (1, 2):
            if not self.hub.start(CLOCK, inputs):
                if attempt == 1:
                    return False
                break
            # GitHub took the start. Look until the new run is in the list.
            for _look in range(10):
                yield 4
                for run in self.hub.runs(CLOCK, 30) or []:
                    try:
                        fresh = parse_time(run["created_at"]) >= since
                    except (KeyError, ValueError):
                        fresh = False
                    name = str(run.get("display_title") or "")
                    if (fresh and run.get("id") != self.me and run.get("status") != "completed"
                            and (name == title or not TITLE.match(name))):
                        self.say("Next clock run: line %s, it waits %d minutes." % (line, wait), True)
                        return True
            if attempt == 1:
                self.say("The new run '%s' is not in the list yet. One more start." % title)
        self.say("GitHub took the start of '%s', but the run is not in the list. The clock "
                 "goes on." % title, True)
        return True

    def bring_back(self, others):
        """Start the other line again when no run of it is alive."""
        if any(line_of(run) in (self.other, None) for run in others):
            return
        now = self.now()
        at = next_wake(self.other, now)
        wait = self.pick((at - now).total_seconds() / 60.0) or self.shortest()
        if wait is None:
            self.problems += 1
            self.say("PROBLEM: no environment waits as it should, so the line %s cannot "
                     "be started." % self.other, True)
            return
        self.say("No run of the line %s is alive. It is started again." % self.other, True)
        started = yield from self.start_clock(self.other, wait)
        if not started:
            self.problems += 1
            self.say("PROBLEM: the line %s could not be started." % self.other, True)

    # -- the work of one run -------------------------------------------------------------

    def steps(self):
        """Do the work of this run. Hands out seconds to sleep. Returns the exit code."""
        now = self.now()
        self.say("Clock run %d, line %s, at %s." % (self.me, self.line, show(now)))

        # Did the wait really happen? If not, this run must not start another run:
        # runs without a wait would follow each other without an end.
        waited = True
        mine = self.hub.run(self.me)
        if mine and self.wait:
            try:
                minutes = (now - parse_time(mine["created_at"])).total_seconds() / 60.0
                self.say("This run waited %.1f minutes. The plan was %d." % (minutes, self.wait), True)
                waited = minutes >= 0.8 * self.wait
            except (KeyError, ValueError):
                pass

        runs = self.hub.runs(CLOCK, 100)
        others = others_alive(runs or [], self.me, now)

        if not waited:
            self.start_due_jobs()
            self.say("PROBLEM: the wait did not happen. This run stops here and starts no "
                     "other clock run.", True)
            return 1
        if runs is not None and too_many(runs, now):
            self.start_due_jobs()
            self.say("PROBLEM: more than %d clock runs in one hour. This run stops here and "
                     "starts no other clock run." % MOST_RUNS, True)
            return 1
        if runs is None:
            self.say("The list of clock runs cannot be read. This run goes on with care.")
        elif replaced(self.me, self.line, self.event, others):
            self.say("Another run of the line %s does the work. This run stops here." % self.line, True)
            yield from self.bring_back(others)
            return 1 if self.problems else 0

        self.start_due_jobs()
        if runs is not None:
            yield from self.bring_back(others)

        # From here on this run cares for its own line: it sleeps a short rest itself, or
        # it starts the next run, which waits for a long rest. If GitHub does not take the
        # start, it tries again, for up to GIVE_UP minutes.
        began = self.now()
        tries = 0
        again = False
        while True:
            if again:
                runs = self.hub.runs(CLOCK, 100)
                if runs is not None and replaced(self.me, self.line, self.event,
                                                 others_alive(runs, self.me, self.now())):
                    self.say("Another run of the line %s does the work. This run stops here."
                             % self.line, True)
                    return 1 if self.problems else 0
                self.start_due_jobs()
            again = True
            now = self.now()
            at = next_wake(self.line, now)
            minutes = (at - now).total_seconds() / 60.0
            wait = self.pick(minutes)
            if wait is None:
                if minutes > NAP:
                    self.problems += 1
                    self.say("PROBLEM: no environment waits as it should. The line %s stops "
                             "here." % self.line, True)
                    return 1
                self.say("The next start time is %s. This run sleeps %d seconds until then."
                         % (show(at), int(minutes * 60) + 1))
                yield max(1.0, (at - now).total_seconds() + 1.0)
                continue
            self.say("The line %s wakes next at %s." % (self.line, show(at)))
            started = yield from self.start_clock(self.line, wait)
            if started:
                return 1 if self.problems else 0
            tries += 1
            self.problems += 1
            if (self.now() - began).total_seconds() > GIVE_UP * 60:
                self.say("PROBLEM: the next clock run could not be started for %d minutes. "
                         "The line %s stops here." % (GIVE_UP, self.line), True)
                return 1
            rest = 180 if tries <= 10 else 600
            self.say("PROBLEM: GitHub did not take the start of the next clock run (try %d). "
                     "One more try in %d minutes at the most." % (tries, rest // 60), tries == 1)
            # Do not sleep over a start time of a job.
            yield max(5.0, min(rest, (at - self.now()).total_seconds() + 1.0))


def self_test(hub, me, say):
    """Check the calls that the clock needs, and start one job in the way of the clock.

    Used by the workflow "Clock test (manual)". The job that it starts is the last one in
    JOBS. That job does nothing when its last run finished less than 3 hours ago.
    """
    good = True
    mine = hub.run(me)
    good = good and bool(mine)
    say("This run can read its own data: %s." % ("yes" if mine else "NO"), True)
    runs = hub.runs(CLOCK, 5)
    good = good and runs is not None
    say("The clock runs can be read: %s."
        % ("NO" if runs is None else "yes, %d found" % len(runs)), True)
    timers = [(wait, hub.timer("wait-%d" % wait)) for wait in sorted(WAITS)]
    good = good and all(found == wait for wait, found in timers)
    say("The wait timers (wanted: %s) are: %s." % (
        ", ".join(str(wait) for wait, _found in timers),
        ", ".join("not known" if found is None else str(found) for _wait, found in timers)), True)
    name = JOBS[-1][0]
    runs = hub.runs(name, 5)
    good = good and runs is not None
    say("The runs of %s can be read: %s."
        % (name, "NO" if runs is None else "yes, %d found" % len(runs)), True)
    taken = hub.start(name, {"like_schedule": "true"})
    say("GitHub %s the start of %s." % ("took" if taken else "REFUSED", name), True)
    if good and taken:
        say("All is well. The clock can do its work.", True)
        return 0
    say("PROBLEM: something that the clock needs does not work.", True)
    return 1


def main(now=None, sleep=time.sleep, test=None):
    now = now or (lambda: datetime.datetime.now(UTC))
    test = ("--test" in sys.argv[1:]) if test is None else test
    notes = []

    def say(text, important=False):
        if important:
            safe = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::notice title=Clock::%s" % safe)
            notes.append(text)
        else:
            print(text)
        sys.stdout.flush()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    line = os.environ.get("CLOCK_LINE") or "main"
    if line not in dict(LINES):
        line = "main"
    found = re.fullmatch(r"wait-(\d+)", os.environ.get("CLOCK_WAIT") or "")
    wait = int(found.group(1)) if found else 0
    try:
        me = int(os.environ.get("GITHUB_RUN_ID") or 0)
    except ValueError:
        me = 0
    hub = GitHub(repo, os.environ.get("GITHUB_TOKEN", ""),
                 os.environ.get("GITHUB_API_URL") or "https://api.github.com",
                 os.environ.get("GITHUB_REF_NAME") or "main", sleep)
    if test:
        code = self_test(hub, me, say)
    else:
        clock = Clock(hub, now, me, line, wait, os.environ.get("GITHUB_EVENT_NAME", ""), say)
        steps = clock.steps()
        code = 0
        while True:
            try:
                seconds = next(steps)
            except StopIteration as stop:
                code = stop.value or 0
                break
            sleep(seconds)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and notes:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join("- " + note for note in notes) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
