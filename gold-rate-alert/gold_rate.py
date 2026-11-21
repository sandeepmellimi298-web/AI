#!/usr/bin/env python3
"""Gold Rate Alert.

Shows the price of 22K gold for 1 gram in Indian rupees, how it changed, a simple
estimate of the next official rate, and a tested price range for the next 7 days.

    python gold_rate.py           make the alert in the folder "out"
    python gold_rate.py --open    the same, then open the page in the browser

Where the numbers come from:
  - The official rates of India are read from the public page of IBJA
    (India Bullion and Jewellers Association, https://ibjarates.com/). IBJA gives an
    opening rate at about 12 noon and a closing rate after 5 PM, Monday to Friday.
    The rates are for 10 grams and are without GST.
  - The world gold price in US dollars comes from https://xaus.com/ (free, no key).

The estimate: the last official rate, moved by the change of the world gold price
since that rate was made. The 7-day range comes from the model in gold_ai.py.
Both are calculations, not advice to buy or sell.

Only the Python standard library is used. Settings are in config.json.
"""
import argparse
import datetime
import html
import json
import math
import pathlib
import re
import sys
import urllib.error
import urllib.request
import webbrowser

try:
    import gold_ai
except ImportError:   # the alert also works without the forecast model
    gold_ai = None

VERSION = "1.1"
HERE = pathlib.Path(__file__).resolve().parent
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
RUPEE, UP, DOWN = chr(0x20B9), chr(0x25B2), chr(0x25BC)   # the rupee sign and the two arrows
DEFAULTS = {
    "ibja_url": "https://ibjarates.com/",
    "spot_url": "https://xaus.com/api/v1/spot?currency=INR&unit=gram",
    "intraday_url": "https://xaus.com/api/v1/intraday?symbol=xau&hours=48",
    "backup_spot_url": "https://api.gold-api.com/price/XAU",
    "history_url": "https://xaus.com/api/v1/history",
    "main_purity": "916",
    "main_name": "22K",
    "grams": [1, 8, 10],
    "gst_percent": 3,
    "opening_window": ["11:30", "12:00"],   # India time: when IBJA collects the opening rate
    "closing_window": ["16:30", "17:00"],   # India time: when IBJA collects the closing rate
    "first_range_percent": 1.0,             # range of the estimate until there is a record
    "forecast": True,                       # show the tested price range of the model
    "forecast_days": 7,
    "email_when_nothing_changed": True,
    "timeout_seconds": 40,
    "user_agent": "gold-rate-alert/1.0 (personal daily alert)",
}
NAMES = {"999": "24K (999)", "995": "995", "916": "22K (916)", "750": "18K (750)", "585": "14K (585)"}
ROW_RE = re.compile(r'data-label="(AM|PM)">\s*(?:<strong>)?\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*(?:</strong>)?\s*</td>'
                    r'(.*?)</tr>', re.S)
CELL_RE = re.compile(r'data-label="Gold (\d{3})">\s*([0-9][0-9,]*)')
CAUTION = "Nobody can know the price in advance."
SHOPS = "Making charges are extra. Shop prices in your city can differ a little."


def say(text=""):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:   # a console that cannot show the rupee sign
        plain = text.replace(RUPEE, "Rs. ").replace(UP, "up").replace(DOWN, "down")
        print(plain.encode("ascii", "replace").decode("ascii"), flush=True)


# --------------------------------------------------------------------------
# Reading the sources
# --------------------------------------------------------------------------
def fetch(url, cfg):
    request = urllib.request.Request(url, headers={"User-Agent": cfg["user_agent"], "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=float(cfg["timeout_seconds"])) as response:
        return response.read().decode("utf-8", "replace")


def parse_ibja(text):
    """Read the table of the last days. Returns {"2026-11-20": {"AM": {"999": 123584, ...}, "PM": {...}}}."""
    fixes = {}
    for session, day, month, year, cells in ROW_RE.findall(text):
        values = {purity: int(number.replace(",", "")) for purity, number in CELL_RE.findall(cells)}
        fine, main = values.get("999", 0), values.get("916", 0)
        if not 10000 <= fine <= 5000000 or not 0.90 * fine <= main <= 0.93 * fine:
            continue   # an empty or strange row, for example a rate that is not published yet
        try:
            date = datetime.date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
        fixes.setdefault(date, {})[session] = values
    return fixes


def parse_spot(text):
    """World price now, in US dollars for one troy ounce."""
    data = json.loads(text)
    price = data.get("spot_usd_oz", data.get("price")) if isinstance(data, dict) else None
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not 200 <= price <= 50000:
        raise ValueError("no usable price")
    return float(price)


def parse_intraday(text):
    """World price of the last two days: a list of (seconds, US dollars), oldest first."""
    data = json.loads(text)
    points = []
    for point in (data.get("points") if isinstance(data, dict) else None) or []:
        if isinstance(point, dict) and isinstance(point.get("t"), (int, float)) \
                and isinstance(point.get("p"), (int, float)) and 200 <= point["p"] <= 50000:
            points.append((float(point["t"]), float(point["p"])))
    return sorted(points)


def read_sources(cfg, now):
    """Read the sources. A source that fails is left out and named in the report."""
    sources, report = {}, []

    def read(name, key, url, parse):
        try:
            sources[key] = parse(fetch(url, cfg))
            report.append({"source": name, "ok": bool(sources[key])})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            report.append({"source": name, "ok": False, "error": str(getattr(exc, "reason", exc))[:120]})

    read("official rates", "fixes", cfg["ibja_url"], parse_ibja)
    read("world price", "spot", cfg["spot_url"], parse_spot)
    read("world price, last 2 days", "intraday", cfg["intraday_url"], parse_intraday)
    if not sources.get("spot"):
        read("world price, second source", "spot", cfg["backup_spot_url"], parse_spot)
    if cfg["forecast"] and gold_ai:
        today = now.astimezone(datetime.timezone.utc).date().isoformat()
        read("world price, 5 years", "history", cfg["history_url"],
             lambda text: gold_ai.parse_history(text, before=today))
    return sources, report


# --------------------------------------------------------------------------
# Official rates and the world price at the time of a rate
# --------------------------------------------------------------------------
def fix_id(date, session):
    return "%s %s" % (date, session)


def ordered(fixes, main="916"):
    """All official rates, oldest first: [(date, session, values), ...]."""
    out = []
    for date in sorted(fixes):
        try:
            real_day = datetime.date.fromisoformat(date).isoformat() == date
        except ValueError:
            real_day = False
        for session in ("AM", "PM"):
            values = fixes[date].get(session) if real_day and isinstance(fixes[date], dict) else None
            if isinstance(values, dict) and isinstance(values.get(main), int) and values[main] > 0:
                out.append((date, session, values))
    return out


def window(cfg, date, session):
    """Start and end (as seconds) of the time in which IBJA collected that rate."""
    first, last = cfg["opening_window"] if session == "AM" else cfg["closing_window"]
    day = datetime.date.fromisoformat(date)
    stamps = []
    for clock in (first, last):
        hour, minute = (int(part) for part in clock.split(":"))
        stamps.append(datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).timestamp())
    return stamps[0], stamps[1]


def spot_in_window(points, start, end):
    """Mean world price inside a time window, or None when there are not enough points."""
    inside = [price for stamp, price in points if start <= stamp <= end]
    if len(inside) >= 2:
        return sum(inside) / len(inside)
    middle = (start + end) / 2
    near = [(abs(stamp - middle), price) for stamp, price in points if abs(stamp - middle) <= 45 * 60]
    return min(near)[1] if near else None


def next_fix(date, session):
    """The official rate that should come after this one: (date, session)."""
    if session == "AM":
        return date, "PM"
    day = datetime.date.fromisoformat(date) + datetime.timedelta(days=1)
    while day.weekday() >= 5:   # no rates on Saturday and Sunday
        day += datetime.timedelta(days=1)
    return day.isoformat(), "AM"


def coming_fix(now):
    """The first official rate that can still come after this moment: (date, session)."""
    day, minutes = now.date(), now.hour * 60 + now.minute
    if day.weekday() < 5 and minutes < 12 * 60 + 30:
        return day.isoformat(), "AM"
    if day.weekday() < 5 and minutes < 18 * 60:
        return day.isoformat(), "PM"
    day += datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day.isoformat(), "AM"


# --------------------------------------------------------------------------
# Words and numbers
# --------------------------------------------------------------------------
def rupees(amount):
    """Whole rupees in the Indian way of writing: 113486 -> 1,13,486 (with the rupee sign)."""
    number = int(round(abs(amount)))
    digits = str(number)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join(([head] if head else []) + groups + [tail])
    return ("-" if amount < 0 and number else "") + RUPEE + digits


def percent(value):
    text = "%+.2f%%" % value
    return "0.00%" if text in ("+0.00%", "-0.00%") else text


def day_name(date, now):
    """'today', 'yesterday', 'tomorrow', or the name of the day with the date."""
    day = datetime.date.fromisoformat(date)
    gap = (now.date() - day).days
    if gap in (0, 1, -1):
        return {0: "today", 1: "yesterday", -1: "tomorrow"}[gap]
    return "%s %d %s" % (day.strftime("%A"), day.day, day.strftime("%b"))


def on_day(date, now):
    name = day_name(date, now)
    return name if name in ("today", "yesterday", "tomorrow") else "on " + name


def session_name(session):
    return "opening rate" if session == "AM" else "closing rate"


def session_time(session):
    return "about 12 noon" if session == "AM" else "after 5 PM"


def rate_words(fix, now):
    """For example 'closing rate of yesterday'."""
    return "%s of %s" % (session_name(fix[1]), day_name(fix[0], now))


def slot_name(now):
    """The name of this run for the title: one of the two planned times, or the clock time."""
    minutes = now.hour * 60 + now.minute
    for planned, name in ((9 * 60, "9 AM"), (16 * 60, "4 PM")):
        if abs(minutes - planned) <= 90:
            return name
    return now.strftime("%I:%M %p").lstrip("0")


def change(new, old):
    """Difference for 1 gram and in percent, from two rates for 10 grams."""
    return (new - old) / 10.0, (new - old) * 100.0 / old


def change_words(new, old, short=False):
    amount, share = change(new, old)
    if int(round(abs(amount))) == 0:
        return "no change"
    words = "%s %s" % (UP if amount > 0 else DOWN, rupees(abs(amount)))
    return words if short else "%s (%s)" % (words, percent(share))


# --------------------------------------------------------------------------
# The work of one run
# --------------------------------------------------------------------------
def week_forecast(cfg, now, points, official, estimate):
    """The price range of the model for about 7 days ahead, in rupees for 10 grams."""
    age = (now.date() - datetime.date.fromisoformat(points[-1][0])).days
    if age > 6:
        raise ValueError("the price history ends on %s" % points[-1][0])
    model = gold_ai.forecast(points)
    target = now.date() + datetime.timedelta(days=int(cfg["forecast_days"]))
    while target.weekday() >= 5:   # a Saturday or Sunday: take the Friday before
        target -= datetime.timedelta(days=1)
    closing = datetime.datetime(target.year, target.month, target.day, 17, 0, tzinfo=IST)
    days = gold_ai.market_open_days(now, closing)
    middle = float(estimate["value"]) if estimate else float(official)
    low, high = gold_ai.band(model, days)
    return {"made": now.isoformat(timespec="seconds"), "target": target.isoformat(), "trading_days": round(days, 2),
            "middle": round(middle), "low": round(middle * low), "high": round(middle * high),
            "official": official,
            "chance_lower": round(gold_ai.chance_below(model, days, math.log(official / middle)), 3),
            "level": model["level"], "move_now": round(model["move_now"] * 100, 2),
            "move_normal": round(model["move_normal"] * 100, 2),
            "change_week": round(model["change_week"] * 100, 1),
            "change_month": round(model["change_month"] * 100, 1),
            "tested": model["tested"], "inside": model["inside"], "prices_until": model["last_day"]}


def load_history(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("fixes", "spot_at_fix", "estimates", "week"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def build(cfg, history, now, sources):
    """Put the new readings into the history and work out everything the alert shows."""
    notes = []
    main = cfg["main_purity"]

    # 1. the official rates
    for date, sessions in (sources.get("fixes") or {}).items():
        if not isinstance(history["fixes"].get(date), dict):
            history["fixes"][date] = {}
        history["fixes"][date].update(sessions)
    fixes = ordered(history["fixes"], main)
    if not fixes:
        return None
    date, session, values = fixes[-1]
    latest = fix_id(date, session)
    if not sources.get("fixes"):
        notes.append("The official page could not be read. These are the last saved rates.")
    elif now.weekday() >= 5:
        notes.append("Today is %s. There is no new official rate on Saturday and Sunday, and the world "
                     "gold market is closed. The next official rate comes %s at %s."
                     % (now.strftime("%A"), on_day(coming_fix(now)[0], now), session_time("AM")))
    elif date < now.date().isoformat() and now.hour >= 13:
        notes.append("There is no new official rate today so far. IBJA gives no rate on public holidays.")

    # 2. the world price at the time of each official rate (kept, because the source only
    #    goes back two days)
    points = sources.get("intraday") or []
    for fix_date, fix_session, _ in fixes[-8:]:
        key = fix_id(fix_date, fix_session)
        if key not in history["spot_at_fix"]:
            found = spot_in_window(points, *window(cfg, fix_date, fix_session))
            if found:
                history["spot_at_fix"][key] = round(found, 2)
    spot_now = sources.get("spot") or (points[-1][1] if points else None)
    spot_then = history["spot_at_fix"].get(latest)

    # 3. check the earlier estimates against the rates that came after them
    known = [fix_id(d, s) for d, s, _ in fixes]
    number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    for base, record in history["estimates"].items():
        if isinstance(record, dict) and number(record.get("value")) and record.get("actual") is None \
                and base in known and known.index(base) + 1 < len(known):
            d, s, v = fixes[known.index(base) + 1]
            record["actual"] = v[main]
            record["actual_fix"] = fix_id(d, s)
            record["error"] = record["value"] - v[main]
    checked = [r for _, r in sorted(history["estimates"].items()) if isinstance(r, dict)
               and number(r.get("value")) and number(r.get("error")) and number(r.get("actual")) and r["actual"] > 0]
    record_text = ""
    if checked:
        last, recent = checked[-1], checked[-20:]
        record_text = "the last estimate said %s and the official rate came at %s (%s apart)." % (
            rupees(last["value"] / 10.0), rupees(last["actual"] / 10.0), rupees(abs(last["error"]) / 10.0))
        if len(recent) >= 2:
            record_text += " The last %d estimates were %s apart on average." % (
                len(recent), rupees(sum(abs(r["error"]) for r in recent) / len(recent) / 10.0))

    # 4. the estimate of the next official rate
    estimate = None
    target = max(next_fix(date, session), coming_fix(now))
    if spot_now and spot_then and 0.85 <= spot_now / spot_then <= 1.15:
        move = spot_now / spot_then
        errors = sorted(abs(r["error"]) * 100.0 / r["actual"] for r in checked[-20:])
        spread = errors[int(0.8 * (len(errors) - 1))] if len(errors) >= 8 else float(cfg["first_range_percent"])
        spread = min(max(spread, 0.2), 3.0)
        value = values[main] * move
        estimate = {"made": now.isoformat(timespec="seconds"), "value": round(value),
                    "low": round(value * (1 - spread / 100.0)), "high": round(value * (1 + spread / 100.0)),
                    "move_percent": round((move - 1) * 100.0, 2), "spread_percent": round(spread, 2),
                    "spot_then": spot_then, "spot_now": round(spot_now, 2),
                    "target": fix_id(*target), "actual": None}
        history["estimates"][latest] = estimate
    elif not spot_now:
        notes.append("The world gold price could not be read, so there is no estimate this time.")
    else:
        notes.append("The world gold price at the time of the last official rate is not known, "
                     "so there is no estimate this time.")

    # 5. the range of the model for about 7 days ahead, and the check of its earlier ranges
    outlook = None
    if sources.get("history"):
        try:
            outlook = week_forecast(cfg, now, sources["history"], values[main], estimate)
        except (ValueError, ArithmeticError, LookupError) as exc:
            notes.append("The forecast model could not run this time (%s)." % exc)
    elif cfg["forecast"] and gold_ai:
        notes.append("The price history could not be read, so there is no 7-day forecast this time.")
    for when, saved in history.setdefault("week", {}).items():
        if not isinstance(saved, dict) or "inside" in saved or not number(saved.get("low")) \
                or not number(saved.get("high")):
            continue
        rates = history["fixes"].get(when)
        closing = rates.get("PM") if isinstance(rates, dict) else None
        actual = closing.get(main) if isinstance(closing, dict) else None
        if number(actual):
            saved["actual"] = actual
            saved["inside"] = bool(saved["low"] <= actual <= saved["high"])
        elif when < (now.date() - datetime.timedelta(days=5)).isoformat():
            saved["inside"] = None   # no closing rate came on that day, for example a holiday
    judged = [r for r in history["week"].values() if isinstance(r, dict) and isinstance(r.get("inside"), bool)]
    if outlook:
        outlook["live_total"], outlook["live_inside"] = len(judged), sum(1 for r in judged if r["inside"])
        if outlook["target"] not in history["week"]:   # the first range made for a day is the one that is checked
            history["week"][outlook["target"]] = {"made": outlook["made"], "low": outlook["low"],
                                                  "high": outlook["high"], "middle": outlook["middle"]}

    # 6. keep the history small
    for key, keep in (("fixes", 400), ("spot_at_fix", 60), ("estimates", 200), ("week", 200)):
        for old in sorted(history[key])[:-keep]:
            del history[key][old]

    # 7. the earlier rates to compare with
    earlier = fixes[:-1]
    previous = earlier[-1] if earlier else None
    closes = [fix for fix in earlier if fix[1] == "PM" and fix[0] < date]

    def back(days):
        limit = (datetime.date.fromisoformat(date) - datetime.timedelta(days=days)).isoformat()
        older = [fix for fix in earlier if fix[0] <= limit]
        return older[-1] if older else None

    week = back(7)
    fresh = history.get("last_shown") != latest
    history["last_shown"] = latest
    return {"now": now, "fix": fixes[-1], "date": date, "session": session, "values": values,
            "latest": latest, "fresh": fresh, "day_before": closes[-1] if closes else previous,
            "previous": previous, "week": week, "month": back(30),
            "oldest": earlier[0] if len(earlier) >= 3 and not week else None,
            "estimate": estimate, "outlook": outlook, "target": target, "record_text": record_text,
            "notes": notes,
            "count": len(fixes), "spot_now": spot_now}


def change_lines(cfg, view):
    """Sentences about how the main price changed, the most important first."""
    main, new = cfg["main_purity"], view["values"][cfg["main_purity"]]
    lines, used = [], []
    for other in (view["day_before"], view["previous"], view["week"], view["month"], view["oldest"]):
        if other and other not in used:
            used.append(other)
            lines.append("From the %s: %s" % (rate_words(other, view["now"]), change_words(new, other[2][main])))
    return lines


def estimate_lines(cfg, view):
    estimate = view["estimate"]
    if not estimate:
        return []
    main = view["values"][cfg["main_purity"]]
    when = "%s, %s" % (day_name(view["target"][0], view["now"]), session_time(view["target"][1]))
    move = estimate["move_percent"]
    if abs(move) < 0.05:
        why = "the world gold price did not change after the last official rate"
    else:
        why = "the world gold price %s %.1f%% after the last official rate" % (
            "rose" if move > 0 else "fell", abs(move))
    return ["Next official rate (%s): about %s for 1 gram of %s (%s), likely between %s and %s."
            % (when, rupees(estimate["value"] / 10.0), cfg["main_name"],
               change_words(estimate["value"], main, short=True),
               rupees(estimate["low"] / 10.0), rupees(estimate["high"] / 10.0)),
            "Why: %s." % why,
            "Check: %s" % (view["record_text"] or "there is no record yet. The first estimates are rough.")]


def fell_or_rose(value):
    return "%s %.1f%%" % ("rose" if value > 0 else "fell", abs(value))


def forecast_lines(cfg, view):
    """The lines about the price range of the model for about 7 days ahead."""
    outlook = view["outlook"]
    if not outlook:
        return []
    official = rupees(outlook["official"] / 10.0)
    lines = ["Closing rate of %s: %d times out of 10 between %s and %s for 1 gram of %s." % (
        day_name(outlook["target"], view["now"]), round(outlook["level"] * 10), rupees(outlook["low"] / 10.0),
        rupees(outlook["high"] / 10.0), cfg["main_name"])]
    lower = outlook["chance_lower"]
    if 0.4 < lower < 0.6:
        lines.append("Higher or lower than the last official rate (%s): the chances are about equal." % official)
    else:
        share = min(95, int(round(max(lower, 1 - lower) * 20)) * 5)
        moved = view["estimate"]["move_percent"] if view["estimate"] else 0
        why = ""
        if (lower >= 0.6 and moved < 0) or (lower <= 0.4 and moved > 0):
            why = " The world price already %s after that rate." % ("fell" if moved < 0 else "rose")
        lines.append("Chance that it is %s than the last official rate (%s): about %d%%.%s" % (
            "lower" if lower >= 0.6 else "higher", official, share, why))
    size = outlook["move_now"] / outlook["move_normal"] if outlook["move_normal"] else 1
    lines.append("Market now: %s. The world price moves about %.1f%% a day; a normal day is %.1f%%. "
                 "In one week it %s, in one month it %s." % (
                     "big swings" if size >= 1.3 else ("calm" if size <= 0.8 else "normal swings"),
                     outlook["move_now"], outlook["move_normal"], fell_or_rose(outlook["change_week"]),
                     fell_or_rose(outlook["change_month"])))
    test = "Test: on %d past days that the model had not seen, the real price was inside the range %d%% of " \
           "the time." % (outlook["tested"], round(100.0 * outlook["inside"] / outlook["tested"]))
    if outlook["live_total"]:
        test += " In real use so far: inside in %d of %d." % (outlook["live_inside"], outlook["live_total"])
    lines.append(test)
    lines.append("The model gives a range, not a direction: in tests on 5 years of prices no model could say "
                 "up or down better than chance.")
    return lines


def caution(view):
    """The plain warning under the estimate and the forecast, or '' when there is neither."""
    shown = [name for name, part in (("estimate", view["estimate"]), ("forecast", view["outlook"])) if part]
    if len(shown) == 2:
        return "The estimate and the forecast are calculations from world gold prices. %s They are not " \
               "advice to buy or sell." % CAUTION
    if shown:
        return "The %s is a calculation from world gold prices. %s It is not advice to buy or sell." % (
            shown[0], CAUTION)
    return ""


def headline_change(cfg, view, short=False):
    """The change from the closing rate of the day before, or '' when there is no earlier rate."""
    other = view["day_before"]
    if not other:
        return ""
    words = change_words(view["values"][cfg["main_purity"]], other[2][cfg["main_purity"]], short)
    return words if short else "%s from the %s" % (words, rate_words(other, view["now"]))


def title_of(cfg, view):
    now = view["now"]
    move = headline_change(cfg, view, short=True) if view["fresh"] else "no new rate"
    return "Gold %s: %s for 1 gram%s (%s %d %s, %s)" % (
        cfg["main_name"], rupees(view["values"][cfg["main_purity"]] / 10.0), ", " + move if move else "",
        now.strftime("%a"), now.day, now.strftime("%b"), slot_name(now))


def gram_list(cfg):
    grams = [g for g in cfg["grams"] if isinstance(g, (int, float)) and not isinstance(g, bool) and g > 0]
    return grams or [1]


def gst_line(cfg, view):
    gst = float(cfg["gst_percent"])
    return "With %g%% GST, 1 gram of %s costs about %s." % (
        gst, cfg["main_name"], rupees(view["values"][cfg["main_purity"]] / 10.0 * (1 + gst / 100.0)))


def table_rows(cfg, view):
    """The rows of the price table: [(name, [price for each weight])]."""
    rows = []
    for purity in dict.fromkeys((cfg["main_purity"], "999")):
        if purity in view["values"]:
            rows.append((NAMES.get(purity, purity),
                         [rupees(view["values"][purity] / 10.0 * g) for g in gram_list(cfg)]))
    return rows


def make_markdown(cfg, view):
    main, values, now = cfg["main_purity"], view["values"], view["now"]
    grams = gram_list(cfg)
    move = headline_change(cfg, view)
    out = ["# " + title_of(cfg, view), "",
           "**%s gold, 1 gram: %s**%s" % (cfg["main_name"], rupees(values[main] / 10.0),
                                          ", " + move if move else ""), "",
           "Official rate of IBJA: the %s. Without GST." % rate_words(view["fix"], now), "",
           "| Gold | " + " | ".join("%g gram%s" % (g, "" if g == 1 else "s") for g in grams) + " |",
           "| --- | " + " | ".join("---:" for _ in grams) + " |"]
    out += ["| %s | %s |" % (name, " | ".join(prices)) for name, prices in table_rows(cfg, view)]
    out += ["", "%s %s" % (gst_line(cfg, view), SHOPS)]
    lines = change_lines(cfg, view)
    if lines:
        out += ["", "**Change** (%s, 1 gram)" % cfg["main_name"], ""] + ["- " + line for line in lines]
    lines = estimate_lines(cfg, view)
    if lines:
        out += ["", "**Estimate**", ""] + ["- " + line for line in lines]
    lines = forecast_lines(cfg, view)
    if lines:
        out += ["", "**Forecast for %d days** (tested model)" % cfg["forecast_days"], ""] \
            + ["- " + line for line in lines]
    for note in view["notes"]:
        out += ["", note]
    if caution(view):
        out += ["", caution(view)]
    out += ["", "Sources: [IBJA](https://ibjarates.com/) for the official rates of India, "
            "[xaus.com](https://xaus.com/) for the world gold price.", ""]
    return "\n".join(out)


def make_html(cfg, view):
    def esc(value):
        return html.escape(str(value), quote=True).replace(RUPEE, "&#8377;") \
            .replace(UP, "&#9650;").replace(DOWN, "&#9660;")

    main, values, now = cfg["main_purity"], view["values"], view["now"]
    grams = gram_list(cfg)
    other = view["day_before"]
    amount = change(values[main], other[2][main])[0] if other else 0
    colour = "#59636e" if int(round(abs(amount))) == 0 else ("#1a7f37" if amount > 0 else "#cf222e")
    head = "padding:8px 12px;border-bottom:2px solid #c9a227;"
    cell = "padding:8px 12px;border-bottom:1px solid #eee;"
    out = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title></head>" % esc(title_of(cfg, view)),
           '<body style="margin:0;background:#f6f3ea;font-family:Segoe UI,Arial,sans-serif;color:#1f2328;">',
           '<div style="max-width:640px;margin:0 auto;padding:20px;">',
           '<div style="background:#fff;border-radius:14px;padding:22px 24px;border-top:6px solid #c9a227;">',
           '<div style="color:#8a6d0b;font-size:13px;font-weight:600;letter-spacing:1px;">GOLD RATE ALERT</div>',
           '<div style="color:#59636e;font-size:15px;margin-top:10px;">%s gold, 1 gram</div>'
           % esc(cfg["main_name"]),
           '<div style="font-size:48px;font-weight:700;line-height:1.2;">%s</div>'
           % esc(rupees(values[main] / 10.0))]
    if other:
        out.append('<div style="font-size:17px;font-weight:600;color:%s;">%s <span style="color:#59636e;'
                   'font-weight:400;">from the %s</span></div>'
                   % (colour, esc(change_words(values[main], other[2][main])), esc(rate_words(other, now))))
    out.append('<div style="color:#59636e;font-size:13px;margin-top:8px;">Official rate of IBJA: the %s. '
               "Without GST.</div>" % esc(rate_words(view["fix"], now)))
    out.append('<table style="border-collapse:collapse;width:100%;margin-top:16px;font-size:15px;">')
    out.append('<tr><th style="%stext-align:left;">Gold</th>' % head
               + "".join('<th style="%stext-align:right;">%g gram%s</th>' % (head, g, "" if g == 1 else "s")
                         for g in grams) + "</tr>")
    for name, prices in table_rows(cfg, view):
        out.append('<tr><td style="%sfont-weight:600;">%s</td>' % (cell, esc(name))
                   + "".join('<td style="%stext-align:right;">%s</td>' % (cell, esc(price))
                             for price in prices) + "</tr>")
    out.append("</table>")
    out.append('<p style="font-size:14px;color:#59636e;">%s %s</p>' % (esc(gst_line(cfg, view)), esc(SHOPS)))
    lines = change_lines(cfg, view)
    if lines:
        out.append('<div style="font-weight:600;margin:18px 0 6px 0;">Change (%s, 1 gram)</div>'
                   '<ul style="margin:0;padding-left:20px;font-size:15px;">' % esc(cfg["main_name"]))
        out += ['<li style="margin:3px 0;">%s</li>' % esc(line) for line in lines]
        out.append("</ul>")
    lines = estimate_lines(cfg, view)
    if lines:
        out.append('<div style="background:#fff8e1;border-left:4px solid #c9a227;padding:10px 14px;'
                   'margin-top:18px;"><div style="font-weight:600;margin-bottom:4px;">Estimate</div>')
        out += ['<div style="font-size:15px;margin:4px 0;">%s</div>' % esc(line) for line in lines]
        out.append("</div>")
    lines = forecast_lines(cfg, view)
    if lines:
        out.append('<div style="background:#eef6ff;border-left:4px solid #0969da;padding:10px 14px;'
                   'margin-top:14px;"><div style="font-weight:600;margin-bottom:4px;">Forecast for %d days '
                   '<span style="font-weight:400;color:#59636e;">(tested model)</span></div>'
                   % int(cfg["forecast_days"]))
        out += ['<div style="font-size:15px;margin:4px 0;">%s</div>' % esc(line) for line in lines]
        out.append("</div>")
    for note in view["notes"]:
        out.append('<p style="font-size:14px;color:#9a6700;">%s</p>' % esc(note))
    if caution(view):
        out.append('<p style="font-size:12px;color:#59636e;margin-top:18px;">%s</p>' % esc(caution(view)))
    out.append('<p style="font-size:12px;color:#59636e;">Sources: <a href="https://ibjarates.com/" '
               'style="color:#0969da;">IBJA</a> for the official rates of India, '
               '<a href="https://xaus.com/" style="color:#0969da;">xaus.com</a> for the world gold price. '
               "Made on %s India time.</p>" % esc(now.strftime("%A %d %B %Y, %I:%M %p")))
    out.append("</div></div></body></html>")
    return "\n".join(out) + "\n"


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gold rate of India in rupees, with change and estimate.")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--out", default=str(HERE / "out"), help="folder for the results")
    parser.add_argument("--config", default=str(HERE / "config.json"), help="settings file")
    parser.add_argument("--now", help=argparse.SUPPRESS)   # a fixed time, only for tests
    args = parser.parse_args(argv)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = dict(DEFAULTS)
    try:
        loaded = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg.update(loaded)
    except OSError:
        pass
    except ValueError as exc:
        say("The settings file has a mistake (%s). The standard settings are used." % exc)

    now = datetime.datetime.fromisoformat(args.now).astimezone(IST) if args.now \
        else datetime.datetime.now(IST)
    say("Gold Rate Alert %s, %s India time" % (VERSION, now.strftime("%d %b %Y %I:%M %p")))
    sources, source_report = read_sources(cfg, now)
    for line in source_report:
        say("  %-28s %s" % (line["source"], "ok" if line["ok"] else "FAILED " + line.get("error", "")))

    history = load_history(out_dir / "history.json")
    view = build(cfg, history, now, sources)
    report = {"program": "Gold Rate Alert " + VERSION,
              "finished": now.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"),
              "sources": source_report, "total_items": 0}
    if view is None:
        say("No official rate could be read, and none is saved yet. Nothing was made.")
        write_text(out_dir / "last_run.json", json.dumps(report, indent=2) + "\n")
        return 1

    title = title_of(cfg, view)
    state = "%s|%s" % (view["latest"], round(view["spot_now"] or 0))
    changed = history.get("last_emailed") != state
    history["last_emailed"] = state
    report.update({"title": title, "latest_rate": view["latest"], "new_rate": view["fresh"],
                   "rates_saved": view["count"],
                   "price_1_gram": int(round(view["values"][cfg["main_purity"]] / 10.0)),
                   "estimate": view["estimate"], "forecast": view["outlook"], "notes": view["notes"],
                   "total_items": 1 if (changed or cfg["email_when_nothing_changed"]) else 0})
    write_text(out_dir / "latest.md", make_markdown(cfg, view))
    write_text(out_dir / "latest.html", make_html(cfg, view))
    write_text(out_dir / "history.json", json.dumps(history, indent=1, sort_keys=True) + "\n")
    write_text(out_dir / "last_run.json", json.dumps(report, indent=2) + "\n")
    say("")
    say(title)
    for line in change_lines(cfg, view) + estimate_lines(cfg, view) + forecast_lines(cfg, view) + view["notes"]:
        say("  " + line)
    say("")
    say("Saved in %s" % out_dir)
    if args.open:
        try:
            webbrowser.open((out_dir / "latest.html").resolve().as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
