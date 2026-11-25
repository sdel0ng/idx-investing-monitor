"""Index-rebalance flow watch: tells you when forced (passive) selling pressure on a stock has faded.

Free data has no foreign / broker flow, so "fading" is read from price + volume behaviour of
COMPLETED daily sessions around the index effective date:

  hold      - no close below support, and (after the event) no new low under the event-day low
  volume    - the last 3 sessions trade at <= 80% of the median 20-day volume (sellers drying up)
  closes    - 2 of the last 3 sessions close in the upper half of their range (no dumping into the close)
  reclaim   - the last close is above the reclaim level and above the 5-day average
  event     - the effective date's closing auction (when index funds must sell) has passed

Stages (each pops up once):
  event_day - reminder on the effective date itself
  early     - hold + volume + closes, before confirmation
  faded     - event passed (2+ sessions after) + hold + reclaim + (volume or closes)
  failed    - a daily close below support

Watches live in flow_watch.json. Education only - not financial advice.
"""
import json
import statistics
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import live_data

FOLDER = Path(__file__).resolve().parent
WATCH_FILE = FOLDER / "flow_watch.json"
VOLUME_RATIO = 0.8
DEFAULT_WATCHES = [
    {
        "code": "BRMS",
        "event": "MSCI review 12 Nov 2026: moved from Standard to Small Cap index",
        "effective": "2026-11-30",
        "support": 284,
        "reclaim": 298,
        "active": True,
        "fired": {},
    }
]
STAGE_TEXT = {
    "event_day": "Index rebalancing day: passive funds sell in today's closing auction. "
                 "Expect pressure into the close - do not panic-sell the auction; judge tomorrow's session.",
    "early": "Early sign: selling pressure is easing (support held, volume drying up, closes off the lows). "
             "Not confirmed until the effective date has passed and price reclaims the trigger.",
    "faded": "Selling pressure has FADED: the event is over, support held and price reclaimed the trigger. "
             "Plan: buy-back / add setup is valid (small size, stop just under support).",
    "failed": "Support broke on a daily close. Exit per plan - the index selling won.",
}
_lock = threading.Lock()


def load_watches() -> list:
    if not WATCH_FILE.exists():
        save_watches(DEFAULT_WATCHES)
    try:
        return json.loads(WATCH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_watches(watches: list) -> None:
    with _lock:
        WATCH_FILE.write_text(json.dumps(watches, indent=2), encoding="utf-8")


def _bar_date(bar: dict) -> date:
    return datetime.fromtimestamp(bar["time"], timezone.utc).date()


def completed_bars(code: str) -> list:
    """Daily bars of finished sessions only (drops today's bar while the market is still trading)."""
    bars = live_data.get_candles(code, "1D")["bars"]
    state = live_data.market_status()["state"]
    if bars and state in ("open", "pre", "break") and _bar_date(bars[-1]) == date.today():
        bars = bars[:-1]
    return bars


def _close_location(bar: dict) -> float:
    span = bar["high"] - bar["low"]
    return 0.5 if span <= 0 else (bar["close"] - bar["low"]) / span


def evaluate(watch: dict, bars: list = None, today: date = None) -> dict:
    """Condition checklist + stage for one watch. `bars`/`today` can be injected for testing."""
    today = today or date.today()
    bars = bars if bars is not None else completed_bars(watch["code"])
    effective = date.fromisoformat(watch["effective"])
    support, reclaim = float(watch["support"]), float(watch["reclaim"])
    if len(bars) < 25:
        return {"code": watch["code"], "stage": None, "conditions": [], "note": "Not enough daily data yet."}

    last = bars[-1]
    recent = bars[-3:]
    # the effective-date closing auction is forced, one-off volume: judge real selling on the other sessions
    judged = [bar for bar in recent if _bar_date(bar) != effective] or recent
    baseline = statistics.median(bar["volume"] for bar in bars[-23:-3])
    recent_volume = sum(bar["volume"] for bar in judged) / len(judged)
    strong_closes = sum(1 for bar in judged if _close_location(bar) >= 0.5)
    closes_needed = 2 if len(judged) >= 2 else 1
    sma5 = sum(bar["close"] for bar in bars[-5:]) / 5
    sessions_after = [bar for bar in bars if _bar_date(bar) > effective]
    event_bar = next((bar for bar in bars if _bar_date(bar) == effective), None)

    closes_hold = all(bar["close"] >= support for bar in recent)
    closes_text = ", ".join(f"{bar['close']:,.0f}" for bar in recent)
    no_new_low = event_bar is None or all(bar["low"] >= event_bar["low"] for bar in sessions_after)
    conditions = [
        {
            "key": "event",
            "label": f"Effective date {effective:%d %b} closing auction passed",
            "ok": len(sessions_after) >= 1,
            "detail": f"{len(sessions_after)} session(s) after the event" if sessions_after else "Not yet",
        },
        {
            "key": "hold",
            "label": f"Support {support:,.0f} held on closes" + (" + no new low after the event" if event_bar else ""),
            "ok": closes_hold and no_new_low,
            "detail": f"last 3 closes {closes_text}",
        },
        {
            "key": "volume",
            "label": f"Volume drying up (3-day avg <= {VOLUME_RATIO:.0%} of 20-day median, event day excluded)",
            "ok": recent_volume <= VOLUME_RATIO * baseline,
            "detail": f"{recent_volume / 1e6:,.0f}M vs median {baseline / 1e6:,.0f}M ({recent_volume / baseline:.0%})",
        },
        {
            "key": "closes",
            "label": "Closes off the lows (2 of last 3 in the upper half of the day's range)",
            "ok": strong_closes >= closes_needed,
            "detail": f"{strong_closes} of {len(judged)}",
        },
        {
            "key": "reclaim",
            "label": f"Reclaim: close above {reclaim:,.0f} and above the 5-day average",
            "ok": last["close"] > reclaim and last["close"] > sma5,
            "detail": f"close {last['close']:,.0f}, 5-day avg {sma5:,.0f}",
        },
    ]
    ok = {condition["key"]: condition["ok"] for condition in conditions}

    stage = None
    if last["close"] < support:
        stage = "failed"
    elif len(sessions_after) >= 2 and ok["hold"] and ok["reclaim"] and (ok["volume"] or ok["closes"]):
        stage = "faded"
    elif ok["hold"] and ok["volume"] and ok["closes"]:
        stage = "early"
    elif today == effective:
        stage = "event_day"

    return {
        "code": watch["code"],
        "event": watch.get("event", ""),
        "effective": watch["effective"],
        "last_session": _bar_date(last).isoformat(),
        "last_close": last["close"],
        "stage": stage,
        "stage_text": STAGE_TEXT.get(stage, "Pressure still on - no fading signal yet. Hold the plan, don't add."),
        "conditions": conditions,
    }


def status() -> list:
    results = []
    for watch in load_watches():
        if not watch.get("active", True):
            continue
        try:
            results.append(evaluate(watch))
        except Exception as error:  # data hiccup: show it instead of crashing the page
            results.append({"code": watch["code"], "stage": None, "conditions": [], "note": f"No data: {error}"})
    return results


def check(show_popup=None, fired_events: list = None) -> list:
    """Pop up once per stage per watch (called every 15 minutes by the alert checker)."""
    watches = load_watches()
    messages = []
    changed = False
    for watch in watches:
        if not watch.get("active", True):
            continue
        try:
            result = evaluate(watch)
        except Exception:
            continue
        stage = result.get("stage")
        fired = watch.setdefault("fired", {})
        if not stage or stage in fired:
            continue
        fired[stage] = datetime.now().strftime("%Y-%m-%d %H:%M")
        changed = True
        checklist = "\n".join(f"{'✔' if c['ok'] else '✘'} {c['label']} - {c['detail']}" for c in result["conditions"])
        message = (f"{watch['code']} - {watch.get('event', 'index rebalance')}\n{STAGE_TEXT[stage]}\n\n"
                   f"Last close {result['last_close']:,.0f} ({result['last_session']})\n{checklist}")
        messages.append(message)
        if fired_events is not None:
            fired_events.append({"id": f"flow-{watch['code']}-{stage}", "time": time.time(),
                                 "code": watch["code"], "message": message})
    if changed:
        save_watches(watches)
    if show_popup:
        for message in messages:
            threading.Thread(target=show_popup, args=("Index flow watch", message), daemon=True).start()
    return messages


if __name__ == "__main__":
    for item in status():
        print(item["code"], item["stage"], item.get("stage_text"))
        for condition in item["conditions"]:
            print("  ", "OK " if condition["ok"] else "-- ", condition["label"], "|", condition["detail"])
