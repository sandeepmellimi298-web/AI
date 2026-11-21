#!/usr/bin/env python3
"""Forecast model of the Gold Rate Alert.

The model answers one question: how far can the gold price move in the next days?
It does not say whether the price goes up or down. In the tests on 5 years of prices
no model could do that better than chance. The test is in gold_lab.py and everybody
can run it again; it also tries advanced AI models.

How the model works
  1. It reads about 5 years of daily world gold prices in US dollars.
  2. It measures how big the daily moves are now. New days count more than old days:
     each day back counts 3% less. One very big day counts at most like a move of
     4 times the usual size, so that a single shock does not fool the model.
  3. It was tested on days it had not seen. For each past day it made a range with
     only what was known on that day, and then looked whether the real price came
     inside. The width of the range is set so that 8 of 10 real prices were inside.

Only the Python standard library is used.
"""
import bisect
import datetime
import json
import math

LEVEL = 0.8        # the share of real prices that should come inside the range
WEIGHT = 0.97      # how much of the old measure is kept each day
CAP = 4.0          # one day counts at most like a move of this many usual sizes
WARM = 20          # days used for the first measure
START = 500        # the test starts after this many days of prices
BURN = 100         # test days that only set the first width
Z80 = 1.2815515655446004   # in a normal bell curve, 8 of 10 values are closer to the middle than this


def parse_history(text, before=None):
    """Daily closing prices from the price service: [(date, price)], oldest first.

    before: a date as text. Days from this date on are left out, because the bar of a
    day that is not over yet is not a closing price.
    """
    data = json.loads(text)
    rows = {}
    for point in (data.get("points") if isinstance(data, dict) else None) or []:
        if not isinstance(point, dict):
            continue
        day, close = point.get("d"), point.get("c")
        if not isinstance(day, str) or isinstance(close, bool) or not isinstance(close, (int, float)) \
                or not 200 <= close <= 50000:
            continue
        try:
            date = datetime.date.fromisoformat(day[:10])
        except ValueError:
            continue
        if date.weekday() >= 5 or (before and date.isoformat() >= before):
            continue
        rows[date.isoformat()] = float(close)
    return sorted(rows.items())


def daily_variance(closes, weight=WEIGHT, cap=CAP, warm=WARM):
    """out[t]: the expected squared move of day t+1, with what is known at the end of day t."""
    count = len(closes)
    squares = [0.0] + [math.log(closes[i] / closes[i - 1]) ** 2 for i in range(1, count)]
    level = max(sum(squares[1:warm + 1]) / warm, 1e-10)
    out = [0.0] * count
    for t in range(1, count):
        new = squares[t]
        if cap and t > warm:
            new = min(new, cap * cap * level)
        level = max(weight * level + (1 - weight) * new, 1e-10)
        out[t] = level
    return out


def quantile(ordered, share):
    """The value below which this share of an ordered list lies."""
    place = (len(ordered) - 1) * share
    low, high = int(math.floor(place)), int(math.ceil(place))
    return ordered[low] + (ordered[high] - ordered[low]) * (place - low)


def test_ranges(closes, variance, days, level=LEVEL, start=START, burn=BURN):
    """Walk through the past and test the range for `days` trading days ahead.

    On each past day the width comes only from moves that were already over on that day.
    Returns the width to use now and how often the real price was inside in the test.
    """
    known, waiting, inside = [], [], []
    for t in range(start, len(closes) - days):
        while waiting and waiting[0][0] <= t:
            bisect.insort(known, waiting.pop(0)[1])
        usual = math.sqrt(variance[t] * days)
        move = abs(math.log(closes[t + days] / closes[t])) / usual
        if len(known) >= burn:
            inside.append(1 if move <= quantile(known, level) else 0)
        waiting.append((t + days, move))
    for _, move in waiting:
        bisect.insort(known, move)
    if len(known) < burn or not inside:
        raise ValueError("not enough days of prices for the test")
    return {"multiplier": quantile(known, level), "tested": len(inside), "inside": sum(inside),
            "tested_last_year": len(inside[-250:]), "inside_last_year": sum(inside[-250:])}


def forecast(points, days=5):
    """Build the model from daily prices [(date, price)] and test it. Returns a dictionary."""
    closes = [price for _, price in points]
    if len(closes) < START + BURN + 150:
        raise ValueError("only %d days of prices, the model needs %d" % (len(closes), START + BURN + 150))
    variance = daily_variance(closes)
    model = test_ranges(closes, variance, days)
    sizes = sorted(math.sqrt(value) for value in variance[100:])
    model.update({"days_ahead_tested": days, "level": LEVEL, "days_of_prices": len(closes),
                  "first_day": points[0][0], "last_day": points[-1][0],
                  "move_now": math.sqrt(variance[-1]), "move_normal": quantile(sizes, 0.5),
                  "change_week": closes[-1] / closes[-6] - 1, "change_month": closes[-1] / closes[-22] - 1})
    return model


def band(model, days):
    """The range after `days` trading days, as two factors of the price now: (low, high)."""
    half = model["multiplier"] * model["move_now"] * math.sqrt(max(days, 0.0))
    return math.exp(-half), math.exp(half)


def chance_below(model, days, distance):
    """Chance that the price after `days` trading days is below a level.

    distance: how far the level is above the price now, as a logarithm (below: negative).
    The model sees no direction, so the chance is 50% for the price now itself.
    """
    spread = model["move_now"] * math.sqrt(max(days, 1e-9)) * model["multiplier"] / Z80
    return 0.5 * (1.0 + math.erf(distance / spread / math.sqrt(2.0)))


def market_open_days(start, end):
    """Trading time between two moments, in days. The world gold market is closed from
    Friday 22:00 to Sunday 23:00 (UTC)."""
    utc = datetime.timezone.utc
    moment, end = start.astimezone(utc), end.astimezone(utc)
    step, total = datetime.timedelta(minutes=15), 0
    while moment < end:
        day, hour = moment.weekday(), moment.hour
        closed = day == 5 or (day == 4 and hour >= 22) or (day == 6 and hour < 23)
        total += 0 if closed else 1
        moment += step
    return total / 96.0
