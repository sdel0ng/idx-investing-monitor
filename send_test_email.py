"""Send one real test email using alert_config.json (no popup, password never printed)."""

import sys

import bmri_daily_check as core
import ihsg_analysis

for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

config = core.load_config()
config["popup"]["enabled"] = False
config["whatsapp"]["enabled"] = False

summary = ihsg_analysis.load_summary()
body = (
    "This is a test from your BMRI / IHSG monitor. If you can read this, email alerts work.\n\n"
    "You will get emails for: IHSG rebound/retrace call changes, IHSG level breaks, BMRI plan alerts, "
    "BBCA/BBRI/BBNI entry alerts, and the daily IHSG call after the 18:00 broksum run.\n\n"
    "Current IHSG call (intraday, for illustration):\n"
    + (ihsg_analysis.call_summary_text(summary) if summary else "not available")
)
try:
    core.send_email(config["email"], "BMRI / IHSG monitor — test email", body, core.CHART_FILE)
    print("RESULT: email sent to", ", ".join(config["email"]["recipients"]))
except Exception as error:
    print("RESULT: FAILED", type(error).__name__, str(error)[:300])
