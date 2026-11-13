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

## How to open the app (new PC)
1. Install **Python 3.11+** from python.org - tick **"Add python.exe to PATH"** in the installer.
2. On GitHub click **Code -> Download ZIP** and unzip it into its own folder, e.g. `C:\IDX\idx-investing-monitor`.
3. In that folder, right-click **`setup_new_pc.ps1` -> Run with PowerShell** (one time, 1-3 minutes).
   It installs the packages, makes the **IDX Investing Monitor** shortcuts and (optionally) the daily reports.
4. Open the app: **Start Menu -> IDX Investing Monitor**, or double-click **`IDX Investing Monitor.vbs`**.

To bring your own data from the old PC, copy `portfolio.json`, `transactions.json`, `trade_plans.json`,
`price_alerts.json` and `alert_config.json` into the app folder (they are never uploaded to GitHub).

## Privacy
Your holdings, journal, plans, alerts and e-mail settings stay local - they are listed in `.gitignore`.
Never commit `alert_config.json`.
