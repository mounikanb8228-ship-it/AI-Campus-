"""Security layer for the Smart Campus website.

Call init_security(app) once. After that:
  * every page and API route needs a signed-in, ACTIVE college member (deny by default),
    except the few public endpoints listed in PUBLIC_ENDPOINTS;
  * every POST needs a valid CSRF token;
  * sessions use hardened cookies and expire;
  * every response carries security headers;
  * login and registration are rate limited.

To protect an EXISTING Flask app without rewriting it, copy this file, auth_db.py and
auth.py next to it and add two lines to app.py:

    from backend.security import init_security
    init_security(app)
"""
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import abort, g, jsonify, redirect, render_template, request, session, url_for

from . import auth_db

# Endpoints that visitors (not signed in) may open. Everything else is blocked.
PUBLIC_ENDPOINTS = {
    "static",
    "landing",
    "health",
    "showcase",
    "showcase_qr",
    "robots",
    "auth.login",
    "auth.register",
}
# Signed-in but not yet approved people may only open these.
PENDING_ENDPOINTS = {"auth.pending", "auth.logout", "static", "landing", "health"}

IDLE_TIMEOUT_SECONDS = 30 * 60
ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60

# --- tiny in-memory rate limiter (use Redis or Flask-Limiter when you scale out) ---
_hits = defaultdict(deque)


def rate_limited(bucket, limit, window_seconds):
    """Return True when this client already used `limit` hits in the last window."""
    now = time.time()
    key = (bucket, client_ip())
    q = _hits[key]
    while q and q[0] < now - window_seconds:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def client_ip():
    return request.remote_addr or "unknown"


def load_secret_key(root):
    """SECRET_KEY from the environment, else a random one saved to .secret_key (mode 600)."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(root, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def is_safe_next(target):
    """Only allow redirects to a path on this same site (blocks open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    parts = urlparse(target)
    return not parts.scheme and not parts.netloc


def wants_json():
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json" or request.is_json


def _blocked(reason):
    key = (client_ip(), request.path, int(time.time() // 60))
    if key not in _logged_blocks:
        _logged_blocks.add(key)
        if len(_logged_blocks) > 5000:
            _logged_blocks.clear()
        auth_db.audit("blocked_access", ip=client_ip(), detail="%s %s" % (request.method, request.path))
    if wants_json():
        return jsonify(error="authentication required"), 401
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


_logged_blocks = set()


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    con = auth_db.connect()
    row = con.execute("SELECT id, usn, name, role, status FROM users WHERE id = ?", (uid,)).fetchone()
    con.close()
    return row


def init_security(app):
    app.config.update(
        SECRET_KEY=load_secret_key(auth_db.ROOT),
        SESSION_COOKIE_NAME="campus_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=IDLE_TIMEOUT_SECONDS,
        MAX_CONTENT_LENGTH=256 * 1024,
        JSON_SORT_KEYS=False,
    )
    auth_db.init_auth_db()
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.context_processor
    def inject_user():
        u = getattr(g, "user", None) or current_user()
        return {"user": u if u is not None and u["status"] == "active" else None}

    if os.environ.get("TRUST_PROXY", "0") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.before_request
    def guard():
        endpoint = request.endpoint
        # 1. block cross-site requests that change data
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                abort(403)
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(sent, expected):
                auth_db.audit("csrf_rejected", ip=client_ip(), detail=request.path)
                abort(403)
        if endpoint is None:  # unknown URL, let the 404 handler answer
            return None
        # 2. public pages
        # the visitor guide (blueprint "guide") is public, and every route in it decides by access.tier()
        if endpoint in PUBLIC_ENDPOINTS or endpoint.startswith("guide."):
            return None
        # 3. everything else needs a signed-in, active member
        user = current_user()
        if user is None:
            session.clear()
            return _blocked("anonymous")
        now = time.time()
        if now - session.get("login_at", 0) > ABSOLUTE_TIMEOUT_SECONDS or user["status"] == "disabled":
            session.clear()
            return _blocked("expired")
        session.permanent = True
        g.user = user
        if user["status"] != "active":
            if endpoint in PENDING_ENDPOINTS:
                return None
            return redirect(url_for("auth.pending"))
        if endpoint.startswith("admin.") and user["role"] != "admin":
            auth_db.audit("forbidden", usn=user["usn"], ip=client_ip(), detail=request.path)
            abort(403)
        return None

    @app.after_request
    def headers(resp):
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' https://unpkg.com; "
            "style-src-attr 'unsafe-inline'; "
            "img-src 'self' data: https://unpkg.com https://*.tile.openstreetmap.org; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        resp.headers["Content-Security-Policy"] = csp
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=()"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Server"] = "campus"
        if request.is_secure or os.environ.get("COOKIE_SECURE", "0") == "1":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Pragma"] = "no-cache"
        return resp

    def error_page(code, title, message):
        if wants_json():
            return jsonify(error=title), code
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(403)
    def e403(_):
        return error_page(403, "Not allowed", "You do not have permission to open this page.")

    @app.errorhandler(404)
    def e404(_):
        return error_page(404, "Page not found", "That page does not exist.")

    @app.errorhandler(413)
    def e413(_):
        return error_page(413, "Request too large", "The data you sent was too large.")

    @app.errorhandler(429)
    def e429(_):
        return error_page(429, "Too many attempts", "Please wait a few minutes and try again.")

    @app.errorhandler(500)
    def e500(_):
        return error_page(500, "Something went wrong", "The error was recorded. Please try again later.")


# --- password and USN rules -------------------------------------------------------
# student USN like 1UC24CS037, or a staff ID like FAC001
USN_RE = re.compile(r"^(?:[0-9][A-Z]{2}[0-9]{2}[A-Z]{2,3}[0-9]{3}|[A-Z]{3}[0-9]{3,4})$")
_COMMON = {"password123", "1234567890", "qwerty12345", "welcome123", "admin12345", "college123"}


def normalize_usn(value):
    return re.sub(r"\s+", "", (value or "")).upper()


def password_problem(password, usn=""):
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        return "Password must contain letters and numbers."
    if password.lower() in _COMMON or (usn and usn.lower() in password.lower()):
        return "Choose a password that is harder to guess."
    return None
