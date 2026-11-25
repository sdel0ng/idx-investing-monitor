# IDX Investing Monitor v1.0.0 – first release

Released 23 Nov 2026. **Education only – not financial advice.** Prices are free data, about 10 minutes delayed.

## Downloads
- **IDX-Investing-Monitor-v1.0.0-windows.zip** – the Windows desktop app (Python source + launchers + setup script).
- **IDX-Monitor-Android-1.0.0.apk** – Android remote screen for the PC app. GitHub Actions builds it and adds it to this release about 5 minutes after publishing.

## What's in v1.0.0
- **Home dashboard** – IHSG / LQ45 live, market breadth, top movers, market regime, sector heatmap.
- **Stock page** – live chart (1m to 1W) with MAs, RSI, Bollinger Bands and a **TA overlay**: swing points, support/resistance zones, trendlines, double bottom/top with neckline and target, Fibonacci, RSI divergence and a plain-English summary. Buy/sell ticket with position sizer.
- **Portfolio** with a max-loss guard and **Journal** with realized P/L, win rate and profit factor.
- **Queue plans** – multi-price entries, exit on daily close, targets, breakout trigger, and **macro rules** (gold, IHSG, USD/IDR) with popups.
- **Alerts** – price / % change popups, plus the **index flow watch** that tells you when index-rebalance (e.g. MSCI) selling pressure fades.
- **Screener, Macro, Ideas, Dividends, Reports** – daily rebuilt analysis and a daily recap.
- **Phone mode** – use the app from your phone on home Wi-Fi or Tailscale, PIN-protected, **off by default** (Settings → Phone access). Works as an iPhone home-screen app, plus the Android APK.

## Install (Windows)
1. Install Python 3.11 or newer from python.org (tick **Add python.exe to PATH**).
2. Unzip `IDX-Investing-Monitor-v1.0.0-windows.zip` to a folder you keep (for example `Documents\IDX-Investing-Monitor`).
3. Right-click `setup_new_pc.ps1` → **Run with PowerShell**. It creates the Python environment and the desktop shortcuts.
4. Open the **IDX Investing Monitor** shortcut. Add your holdings on the Portfolio page.

## Install (Android)
Download the APK from this release, open it and allow "Install unknown apps". On the PC enable Settings → Phone access, then type the PC address shown there into the app.

## Known limitations
- Free data sources (TradingView scanner, Yahoo Finance), about 10 minutes delayed. No foreign / broker-flow data.
- Popups and alerts only work while the app is open on the PC. No exchange-holiday calendar.
- Email alerts need your own Gmail app password in `alert_config.json` (never uploaded).
- The Android app is a remote screen: the PC must be on with the app open. This build is signed with a temporary key, so uninstall it before installing a later version.

## Privacy
Your portfolio, journal, plans, alerts, phone PIN and email settings stay on your PC and are never uploaded to GitHub.
