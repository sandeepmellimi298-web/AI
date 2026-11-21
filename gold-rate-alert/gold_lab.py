#!/usr/bin/env python3
"""Model test for the Gold Rate Alert ("the lab").

It answers two questions with about 5 years of real daily gold prices:
  1. Ranges: how often does the real price come inside the range of the model?
  2. Direction: can any model say "up" or "down" better than chance? It tries simple
     rules and advanced AI models: logistic regression, boosted trees, a random forest
     and neural networks.

Every model is tested only on days it has not seen: it learns from the days before a
date, says something about the days after it, and is trained again every 21 days.

    python gold_lab.py              print the scores
    python gold_lab.py --notices    also print them as notices for GitHub

The lab needs numpy and scikit-learn. The alert itself (gold_rate.py) does not use them.
"""
import datetime
import json
import math
import pathlib
import sys
import urllib.request
import warnings

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier

import gold_ai

HERE = pathlib.Path(__file__).resolve().parent
LOOK_BACK = 100     # days of prices a row of inputs needs
FIRST = 600         # the first test day
AGAIN = 21          # train again after this many days


def read_prices(text):
    """[(date, close, high, low)] from the price service, oldest first, working days only."""
    rows = {}
    for point in json.loads(text).get("points") or []:
        try:
            day = datetime.date.fromisoformat(str(point["d"])[:10])
            close, high, low = float(point["c"]), float(point["h"]), float(point["l"])
        except (KeyError, TypeError, ValueError):
            continue
        if day.weekday() < 5 and 200 <= close <= 50000 and high >= low > 0:
            rows[day.isoformat()] = (close, high, low)
    return [(day,) + rows[day] for day in sorted(rows)]


def inputs(rows):
    """One row of 16 numbers for each day, made only from that day and the days before it."""
    count = len(rows)
    close = np.array([r[1] for r in rows])
    high = np.array([r[2] for r in rows])
    low = np.array([r[3] for r in rows])
    move = np.zeros(count)
    move[1:] = np.log(close[1:] / close[:-1])
    square = move ** 2
    wide = np.log(high / low) ** 2 / (4 * math.log(2))
    table = np.full((count, 16), np.nan)
    for t in range(LOOK_BACK, count):
        v5 = math.sqrt(square[t - 4:t + 1].mean()) + 1e-5
        v20 = math.sqrt(square[t - 19:t + 1].mean()) + 1e-5
        v60 = math.sqrt(square[t - 59:t + 1].mean()) + 1e-5
        last14 = move[t - 13:t + 1]
        gain, loss = last14[last14 > 0].sum(), -last14[last14 <= 0].sum()
        weekday = datetime.date.fromisoformat(rows[t][0]).weekday()
        table[t] = [
            move[t] / v20,                                                      # the move of the day
            math.log(close[t] / close[t - 5]) / (v20 * math.sqrt(5)),           # the last 5 days
            math.log(close[t] / close[t - 20]) / (v20 * math.sqrt(20)),         # the last 20 days
            math.log(close[t] / close[t - 60]) / (v60 * math.sqrt(60)),         # the last 60 days
            math.log(close[t] / close[t - 19:t + 1].mean()) / (v20 * math.sqrt(20)),   # against the 20-day average
            math.log(close[t] / close[t - 49:t + 1].mean()) / (v60 * math.sqrt(50)),   # against the 50-day average
            math.log(close[t] / close[t - 99:t + 1].mean()) / (v60 * math.sqrt(100)),  # against the 100-day average
            math.log(v5 / v20),                                                 # are the moves growing
            math.log(v20 / v60),
            math.log(wide[t - 4:t + 1].mean() / wide[t - 19:t + 1].mean()),     # the same from highs and lows
            gain / (gain + loss + 1e-9) - 0.5,                                  # share of gains in 14 days
            math.log(close[t] / close[t - 59:t + 1].max()) / (v60 * math.sqrt(60)),    # below the 60-day high
            math.log(close[t] / close[t - 59:t + 1].min()) / (v60 * math.sqrt(60)),    # above the 60-day low
            (close[t] - low[t]) / (high[t] - low[t] + 1e-9) - 0.5,              # where the day closed in its range
            1.0 if weekday == 0 else 0.0,                                       # Monday
            1.0 if weekday == 4 else 0.0,                                       # Friday
        ]
    past = np.full((count, 20), np.nan)     # the last 20 daily moves, for the second neural network
    for t in range(LOOK_BACK, count):
        past[t] = move[t - 19:t + 1] / (math.sqrt(square[t - 19:t + 1].mean()) + 1e-5)
    return close, table, past


def makers():
    """The AI models of the test. Each one gives the chance that the price is higher later."""
    return {
        "logistic regression": lambda: LogisticRegression(C=0.01, max_iter=5000),
        "boosted trees": lambda: GradientBoostingClassifier(n_estimators=150, max_depth=2, learning_rate=0.03,
                                                            subsample=0.7, random_state=0),
        "random forest": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=25, random_state=0,
                                                        n_jobs=-1),
        "neural network": lambda: MLPClassifier(hidden_layer_sizes=(16, 8), alpha=1.0, max_iter=1500,
                                                random_state=0),
    }


def walk(close, table, past, days):
    """Test every model on days it has not seen. Returns the real answers and the chances."""
    count = len(close)
    answers, chances = [], {}
    names = list(makers()) + ["neural network on 20 days"]
    fitted, scale = {}, None
    for t in range(FIRST, count - days):
        if (t - FIRST) % AGAIN == 0:
            learn = np.arange(LOOK_BACK, t - days + 1)
            target = (close[learn + days] > close[learn]).astype(int)
            mean, spread = table[learn].mean(axis=0), table[learn].std(axis=0) + 1e-9
            scale = (mean, spread, float(target.mean()))
            x = np.clip((table[learn] - mean) / spread, -5, 5)
            for name, make in makers().items():
                fitted[name] = make().fit(x, target)
            fitted[names[-1]] = MLPClassifier(hidden_layer_sizes=(16,), alpha=1.0, max_iter=1500,
                                              random_state=0).fit(np.clip(past[learn], -5, 5), target)
        mean, spread, base = scale
        x = np.clip((table[t:t + 1] - mean) / spread, -5, 5)
        answers.append(1 if close[t + days] > close[t] else 0)
        chances.setdefault("always the usual share (no model)", []).append(base)
        for name in names[:-1]:
            chances.setdefault(name, []).append(float(fitted[name].predict_proba(x)[0, 1]))
        chances.setdefault(names[-1], []).append(
            float(fitted[names[-1]].predict_proba(np.clip(past[t:t + 1], -5, 5))[0, 1]))
        chances.setdefault("all AI models together", []).append(
            float(np.mean([chances[name][-1] for name in names])))
        for name, column, sign in (("rule: follow 20 days", 2, 1), ("rule: follow 60 days", 3, 1),
                                   ("rule: against 5 days", 1, -1), ("rule: against 1 day", 0, -1)):
            chances.setdefault(name, []).append(0.55 if sign * table[t, column] > 0 else 0.45)
    return np.array(answers), {name: np.array(values) for name, values in chances.items()}


def score(answers, chance, base):
    """Right calls in percent, and the gain against 'no model' (above 0 is better than no model)."""
    right = ((chance > 0.5).astype(int) == answers).mean() * 100
    gain = (1 - ((chance - answers) ** 2).mean() / ((base - answers) ** 2).mean()) * 100
    return right, gain


def direction_report(close, table, past, days):
    answers, chances = walk(close, table, past, days)
    base = chances["always the usual share (no model)"]
    half = len(answers) // 2
    parts = (("all", slice(None)), ("first half", slice(0, half)), ("second half", slice(half, None)),
             ("last 250", slice(-250, None)))
    lines = ["%d trading day%s ahead, %d test days (%s to now). Share of days with a higher price: %.0f%%."
             % (days, "" if days == 1 else "s", len(answers), "about %.1f years" % (len(answers) / 252.0),
                answers.mean() * 100),
             "right calls %% / gain against no model %%, for: %s" % ", ".join(name for name, _ in parts)]
    doubt = 2 * math.sqrt(0.25 / (len(answers) / float(days))) * 100
    base_right = score(answers, base, base)[0]
    winners = []
    for name, chance in chances.items():
        cells = [score(answers[part], chance[part], base[part]) for _, part in parts]
        lines.append("%-34s %s" % (name, "   ".join("%5.1f /%5.1f" % cell for cell in cells)))
        if name != "always the usual share (no model)" and cells[0][0] > base_right + doubt \
                and all(cell[1] > 0 for cell in cells[:3]):
            winners.append(name)
    lines.append("A model is only better than no model when it has more right calls than %.1f%% (no model %.1f%% "
                 "plus the doubt of the test, %.1f points) and a gain above 0 in both halves."
                 % (base_right + doubt, base_right, doubt))
    lines.append("RESULT: " + ("better than no model: " + ", ".join(winners) if winners
                               else "no model is better than no model."))
    return lines


def range_report(points):
    closes = [price for _, price in points]
    lines = ["share of real prices inside the range (goal 80%): all test days / last 250; width of the range; "
             "the factor that sets the width"]
    kinds = (("the model of the alert (weight 0.97, big days capped)", gold_ai.daily_variance(closes)),
             ("the same without the cap", gold_ai.daily_variance(closes, cap=0)),
             ("faster (weight 0.94, big days capped)", gold_ai.daily_variance(closes, weight=0.94)),
             ("plain average of the last 60 days", [0.0] * 60 + [
                 sum(math.log(closes[i] / closes[i - 1]) ** 2 for i in range(t - 59, t + 1)) / 60
                 for t in range(60, len(closes))]))
    for days in (1, 5, 21):
        for name, variance in kinds:
            test = gold_ai.test_ranges(closes, variance, days)
            width = np.mean([2 * test["multiplier"] * math.sqrt(variance[t] * days)
                             for t in range(gold_ai.START, len(closes) - days)]) * 100
            lines.append("%2d day%s  %-52s %5.1f%% of %d / %5.1f%%   width %5.2f%%   factor %.2f" % (
                days, " " if days == 1 else "s", name, 100.0 * test["inside"] / test["tested"], test["tested"],
                100.0 * test["inside_last_year"] / test["tested_last_year"], width, test["multiplier"]))
    return lines


def main():
    notices = "--notices" in sys.argv[1:]
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    request = urllib.request.Request(config.get("history_url", "https://xaus.com/api/v1/history"),
                                     headers={"User-Agent": config["user_agent"], "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8", "replace")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    rows = [row for row in read_prices(text) if row[0] < today]
    points = gold_ai.parse_history(text, before=today)
    close, table, past = inputs(rows)
    moves = np.diff(np.log(close))
    model = gold_ai.forecast(points)
    low, high = gold_ai.band(model, 5)
    reports = [("Gold lab 1 prices", [
        "%d working days, %s to %s. Same days as the model of the alert: %s." % (
            len(rows), rows[0][0], rows[-1][0], "yes" if [r[0] for r in rows] == [p[0] for p in points] else "NO"),
        "usual daily move %.2f%%, biggest moves: %s" % (moves.std() * 100, ", ".join(
            "%s %+.1f%%" % (rows[i + 1][0], moves[i] * 100) for i in np.argsort(-np.abs(moves))[:5])),
        "the model of the alert today: daily move now %.2f%%, normal %.2f%%, factor %.3f, 5-day range %.2f%% to "
        "%+.2f%%, tested on %d days, inside %d, last year %d of %d" % (
            model["move_now"] * 100, model["move_normal"] * 100, model["multiplier"], (low - 1) * 100,
            (high - 1) * 100, model["tested"], model["inside"], model["inside_last_year"],
            model["tested_last_year"]),
        "change of the world price: 5 days %+.1f%%, 21 days %+.1f%%" % (
            model["change_week"] * 100, model["change_month"] * 100)]),
        ("Gold lab 2 ranges", range_report(points))]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        for number, days in ((3, 1), (4, 5)):
            reports.append(("Gold lab %d direction %d day%s" % (number, days, "" if days == 1 else "s"),
                            direction_report(close, table, past, days)))
    for title, lines in reports:
        body = "\n".join(lines)
        print("== %s\n%s\n" % (title, body), flush=True)
        if notices:
            print("::notice title=%s::%s" % (title, body.replace("%", "%25").replace("\r", "").replace("\n", "%0A")),
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
