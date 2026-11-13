"""
IDX Investing Monitor - Windows desktop app.

Opens the monitor (Watchlist, Portfolio, Market, Ideas, Stocks) in its own native window
(Microsoft Edge WebView2) instead of a browser tab. Closing the window closes the app.

Start it with "IDX Monitor.exe" (or: pythonw desktop_app.py).
"""

import ctypes
import socket
import sys
import threading
import time
from pathlib import Path

import webview
from werkzeug.serving import make_server

FOLDER = Path(__file__).resolve().parent
ICON_FILE = FOLDER / "app_icon.ico"
TITLE = "IDX Investing Monitor"
PORT = 8766
APP_ID = "IDX.InvestingMonitor"
MUTEX_NAME = "Local\\IDXInvestingMonitorDesktop"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def bring_existing_window_to_front() -> bool:
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def set_window_icon() -> None:
    """Give the native window (and its taskbar button) the app icon."""
    for _ in range(50):
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            break
        time.sleep(0.1)
    else:
        return
    image_icon, load_from_file = 1, 0x10
    for size, kind in ((16, 0), (32, 1)):  # ICON_SMALL, ICON_BIG
        handle = user32.LoadImageW(None, str(ICON_FILE), image_icon, size, size, load_from_file)
        if handle:
            user32.SendMessageW(hwnd, 0x0080, kind, handle)  # WM_SETICON


def message_box(text: str) -> None:
    user32.MessageBoxW(None, text, TITLE, 0x10)


class ServerThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.server = make_server("127.0.0.1", PORT, app, threaded=True)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()


def main():
    # One window only: a second launch just brings the open window to the front
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        bring_existing_window_to_front()
        return

    # Own taskbar group (not "Python")
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    server = None
    if not port_in_use(PORT):
        try:
            import dividend_app
        except Exception as error:
            message_box(f"Could not load the monitor:\n{error!r}")
            return
        server = ServerThread(dividend_app.app)
        server.start()
        # Refresh market data / ideas / watchlist in the background if they are old
        try:
            age = dividend_app.summary_age_hours()
            market_age = dividend_app.market_age_hours()
            if age is None or age > 20 or market_age is None or market_age > 2:
                dividend_app.start_refresh()
        except Exception:
            pass
    # else: the browser version is already serving on this port - reuse it

    for _ in range(40):
        if port_in_use(PORT):
            break
        time.sleep(0.15)
    else:
        message_box("The monitor did not start (port 8766 busy or blocked).")
        return

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(
        TITLE,
        f"http://127.0.0.1:{PORT}/",
        width=1440,
        height=920,
        min_size=(1000, 650),
        text_select=True,
    )
    window.events.shown += lambda: threading.Thread(target=set_window_icon, daemon=True).start()
    try:
        webview.start(gui="edgechromium", private_mode=False)
    finally:
        if server:
            server.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # never die silently under pythonw
        message_box(f"{TITLE} crashed:\n{error!r}")
        sys.exit(1)
