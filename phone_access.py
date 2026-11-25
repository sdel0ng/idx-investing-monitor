"""
Phone access for the IDX Investing Monitor (iPhone / Android via Safari or Chrome).

OFF by default. When switched on (Settings -> Phone access) the app also listens on the PC's
network address, so a phone on the same Wi-Fi (or on Tailscale) can open it. Every request that
does not come from the PC itself must log in with a PIN first; the PC keeps working without a PIN.

Also serves the bits that make "Add to Home Screen" look like a real app: manifest + icons.
Settings live in phone_access.json (PIN stored only as a salted hash). Education only.
"""

import hashlib
import hmac
import io
import json
import secrets
import socket
import time
from pathlib import Path

from flask import Response, redirect, request

FOLDER = Path(__file__).resolve().parent
CONFIG_FILE = FOLDER / "phone_access.json"
ICON_FILE = FOLDER / "app_icon.ico"
COOKIE = "idx_phone"
COOKIE_DAYS = 30
PUBLIC_PATHS = ("/phone-login", "/manifest.webmanifest", "/apple-touch-icon.png", "/app-icon-192.png", "/app-icon-512.png")
MAX_TRIES = 5
LOCK_SECONDS = 600

_failures = {}


# ---------------------------------------------------------------- settings

def load() -> dict:
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        config = {}
    config.setdefault("enabled", False)
    config.setdefault("secret", secrets.token_hex(32))
    return config


def _save(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 200_000).hex()


def update(enabled: bool, pin: str = "") -> dict:
    """Switch phone access on/off; a new PIN (4-12 digits) replaces the old one and logs out old phones."""
    config = load()
    pin = (pin or "").strip()
    if pin:
        if not pin.isdigit() or not 4 <= len(pin) <= 12:
            raise ValueError("PIN must be 4-12 digits.")
        config["salt"] = secrets.token_hex(16)
        config["pin_hash"] = _hash_pin(pin, config["salt"])
        config["secret"] = secrets.token_hex(32)   # invalidates existing phone logins
    if enabled and not config.get("pin_hash"):
        raise ValueError("Set a PIN first.")
    config["enabled"] = bool(enabled)
    _save(config)
    return config


def enabled() -> bool:
    config = load()
    return bool(config.get("enabled") and config.get("pin_hash"))


def bind_host() -> str:
    return "0.0.0.0" if enabled() else "127.0.0.1"


def lan_addresses() -> list:
    """IPv4 addresses of this PC that a phone could use (Wi-Fi / Ethernet / Tailscale)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            found.add(probe.getsockname()[0])
    except Exception:
        pass
    return sorted(ip for ip in found if not ip.startswith("127.") and not ip.startswith("169.254."))


# ---------------------------------------------------------------- login

def _token(config: dict) -> str:
    return hmac.new(config["secret"].encode(), b"phone-ok", hashlib.sha256).hexdigest()


def _is_local() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1", None)


def _logged_in(config: dict) -> bool:
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _token(config))


LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="IDX Monitor">
<meta name="theme-color" content="#0b1220"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest"><title>IDX Monitor · PIN</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;
color:#e2e8f0;font-family:-apple-system,Segoe UI,sans-serif}form{width:280px;text-align:center}
img{width:72px;height:72px;border-radius:16px}h1{font-size:20px;margin:14px 0 4px}p{color:#8b9bb4;font-size:13px}
input{width:100%;box-sizing:border-box;font-size:28px;letter-spacing:8px;text-align:center;padding:12px;border-radius:12px;
border:1px solid #2a3a5c;background:#111a2e;color:#fff;margin:14px 0}button{width:100%;padding:14px;border:0;border-radius:12px;
background:#22c55e;color:#04120a;font-size:17px;font-weight:700}.err{color:#f87171;min-height:18px}</style></head><body>
<form method="post"><img src="/apple-touch-icon.png" alt=""><h1>IDX Investing Monitor</h1><p>Enter your PIN</p>
<input name="pin" type="password" inputmode="numeric" autocomplete="current-password" autofocus required>
<div class="err">__ERROR__</div><button>Open</button>
<p>Runs on your PC. Prices ~10 min delayed. Education only.</p></form></body></html>"""


def _login_page(error: str = "", status: int = 200) -> Response:
    return Response(LOGIN_HTML.replace("__ERROR__", error), status=status, mimetype="text/html")


# ---------------------------------------------------------------- icons / manifest

def _icon_png(size: int) -> bytes:
    from PIL import Image

    with Image.open(ICON_FILE) as ico:
        sizes = sorted(ico.info.get("sizes", {(ico.width, ico.height)}), key=lambda s: -s[0])
        ico.size = sizes[0]
        image = ico.convert("RGBA").resize((size, size), Image.LANCZOS)
    background = Image.new("RGBA", (size, size), (11, 18, 32, 255))   # iOS shows transparency as black
    background.alpha_composite(image)
    buffer = io.BytesIO()
    background.convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


MANIFEST = {
    "name": "IDX Investing Monitor",
    "short_name": "IDX Monitor",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0b1220",
    "theme_color": "#0b1220",
    "icons": [
        {"src": "/app-icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/app-icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}

HEAD_TAGS = """<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="IDX Monitor">
<meta name="theme-color" content="#0b1220"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">"""


# ---------------------------------------------------------------- Flask wiring

def install(app) -> None:
    icon_cache = {}

    @app.before_request
    def require_pin():
        if _is_local() or request.path in PUBLIC_PATHS:
            return None
        config = load()
        if not (config.get("enabled") and config.get("pin_hash")):
            return Response("Phone access is switched off (Settings -> Phone access on the PC).", status=403)
        if _logged_in(config):
            return None
        if request.path.startswith("/api/") or request.method != "GET":
            return Response("PIN required", status=401)
        return redirect(f"/phone-login?next={request.full_path.rstrip('?')}")

    @app.route("/phone-login", methods=["GET", "POST"])
    def phone_login():
        config = load()
        target = request.args.get("next") or "/"
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        if request.method == "GET":
            return _login_page()
        client = request.remote_addr or "?"
        count, since = _failures.get(client, (0, 0))
        if count >= MAX_TRIES and time.time() - since < LOCK_SECONDS:
            return _login_page("Too many wrong PINs - wait 10 minutes.", 429)
        pin = (request.form.get("pin") or "").strip()
        good = bool(config.get("pin_hash")) and hmac.compare_digest(_hash_pin(pin, config.get("salt", "")), config["pin_hash"])
        if not good:
            _failures[client] = (count + 1 if time.time() - since < LOCK_SECONDS else 1, time.time())
            return _login_page("Wrong PIN.", 401)
        _failures.pop(client, None)
        response = redirect(target)
        response.set_cookie(COOKIE, _token(config), max_age=COOKIE_DAYS * 86400, httponly=True, samesite="Lax")
        return response

    @app.route("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), mimetype="application/manifest+json")

    def icon(size):
        if size not in icon_cache:
            icon_cache[size] = _icon_png(size)
        return Response(icon_cache[size], mimetype="image/png", headers={"Cache-Control": "max-age=86400"})

    app.add_url_rule("/apple-touch-icon.png", "apple_touch_icon", lambda: icon(180))
    app.add_url_rule("/app-icon-192.png", "app_icon_192", lambda: icon(192))
    app.add_url_rule("/app-icon-512.png", "app_icon_512", lambda: icon(512))
