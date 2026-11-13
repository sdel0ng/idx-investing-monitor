# IDX Investing Monitor

Personal swing-trading dashboard for the Indonesia Stock Exchange (IHSG), built with Flask + pywebview.
**Education only - not financial advice.** Free data sources (TradingView scanner, Yahoo Finance) are delayed ~10 minutes.

## Features
- Home, Portfolio, Screener, Heatmap, Macro, Ideas, Dividends, Reports, Journal, Alerts
- Stock page: live chart (TradingView Lightweight Charts) with MAs, BB, RSI/MACD, click-to-alert
- **Auto technical analysis** overlay (`static/ta.js`): swings, support/resistance zones, trendlines,
  double bottom/top, Fibonacci, RSI divergence, plain-English summary
- **Queue plans**: staggered limit buys, exit on daily close, targets, breakout plan, auto alerts
- **Macro sync** (`macro_sync.py`): gold / IHSG / USD-IDR drivers, correlation with the stock, pause/confirm rules
- Morning brief (08:00) and closing report (16:20) via Windows Task Scheduler
- IDX tick-size and ARA/ARB rules, fees, position sizer, trade journal

## Setup (Windows)
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy portfolio.example.json portfolio.json        # then edit your holdings
copy alert_config.example.json alert_config.json  # optional e-mail alerts (Gmail app password)
.venv\Scripts\python desktop_app.py               # opens the app (http://127.0.0.1:8766)
```
`setup_new_pc.ps1` creates the venv, shortcuts and scheduled tasks on a new PC.

## Privacy
Your holdings, journal, plans, alerts and e-mail settings stay local - they are listed in `.gitignore`.
Never commit `alert_config.json`.
