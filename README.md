# IDX Investing Monitor

Personal swing-trading dashboard for the Indonesia Stock Exchange (IHSG), built with Flask + pywebview.
**Education only - not financial advice.** Free data sources (TradingView scanner, Yahoo Finance) are delayed ~10 minutes.

## Download (latest release: v1.0.0)
Get the ready-made files from **[Releases](https://github.com/sdel0ng/idx-investing-monitor/releases/latest)**:
`IDX-Investing-Monitor-v1.0.0-windows.zip` (Windows app) and `IDX-Monitor-Android-1.0.0.apk` (Android remote screen).
What's new: see `CHANGELOG.md`.

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
2. Download `IDX-Investing-Monitor-v1.0.0-windows.zip` from Releases (or **Code -> Download ZIP**) and unzip it into its own folder, e.g. `C:\IDX\idx-investing-monitor`.
3. In that folder, right-click **`setup_new_pc.ps1` -> Run with PowerShell** (one time, 1-3 minutes).
   It installs the packages, makes the **IDX Investing Monitor** shortcuts and (optionally) the daily reports.
4. Open the app: **Start Menu -> IDX Investing Monitor**, or double-click **`IDX Investing Monitor.vbs`**.

To bring your own data from the old PC, copy `portfolio.json`, `transactions.json`, `trade_plans.json`,
`price_alerts.json` and `alert_config.json` into the app folder (they are never uploaded to GitHub).

## Privacy
Your holdings, journal, plans, alerts and e-mail settings stay local - they are listed in `.gitignore`.
Never commit `alert_config.json`.

## Use it on your phone (iPhone / Android)

Phone access is **off by default**. On your own PC (home network only):

1. Open the app → **Settings → 📱 Phone access**, tick *Allow my phone*, set a 4–12 digit PIN, Save, then close and reopen the app.
2. If Windows Firewall asks, allow Python on **Private** networks only.
3. On the phone (same Wi-Fi) open Safari at the address shown in Settings (e.g. `http://192.168.x.x:8766`), enter the PIN.
4. Share → **Add to Home Screen** – it opens full-screen with its own icon.
5. Away from home: install **Tailscale** on PC and phone and use the PC's `100.x.x.x` address. Never port-forward 8766 to the internet.

The PC must be on with the app open; the phone is only a screen for it. The PIN is stored as a salted hash in `phone_access.json` (git-ignored).

## Android app (APK)

Download the latest `IDX-Monitor-x.y.z.apk` from **Releases** (built automatically by GitHub Actions from `android/`).
It is a remote screen for the monitor running on your PC – the PC must be on with the app open and **Settings → Phone access** enabled.
Install: open the APK on the phone and allow "Install unknown apps" for your browser/files app. Then enter the PC address shown in Settings (e.g. `192.168.1.10:8766`) and your PIN.
Menu (⋮): Reload · Home · PC address. iPhone cannot install APKs – use Safari → Add to Home Screen instead.
