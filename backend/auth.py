"""Sign in, register and sign out."""
import time
from collections import defaultdict, deque

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import auth_db
from .security import (
    USN_RE,
    client_ip,
    is_safe_next,
    normalize_usn,
    password_problem,
    rate_limited,
)

bp = Blueprint("auth", __name__)

MAX_FAILED = 5
LOCK_SECONDS = 15 * 60
# used when a USN does not exist so that the timing looks the same as a wrong password
_DUMMY_HASH = generate_password_hash("not-a-real-password")
_code_failures = defaultdict(deque)  # usn -> times of wrong activation codes


def _code_blocked(usn):
    q = _code_failures[usn]
    while q and q[0] < time.time() - 3600:
        q.popleft()
    return len(q) >= 5


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if rate_limited("login", 10, 300):
            auth_db.audit("rate_limited", ip=client_ip(), detail="login")
            abort(429)
        usn = normalize_usn(request.form.get("usn"))
        password = request.form.get("password", "")[:200]
        now = time.time()
        con = auth_db.connect()
        row = con.execute("SELECT * FROM users WHERE usn = ?", (usn,)).fetchone()
        locked = bool(row and row["locked_until"] > now)
        good = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
        if row and good and not locked:
            con.execute("UPDATE users SET failed_attempts = 0, locked_until = 0 WHERE id = ?", (row["id"],))
            con.commit()
            con.close()
            session.clear()  # new session on every login (prevents session fixation)
            session["uid"] = row["id"]
            session["login_at"] = now
            session.permanent = True
            auth_db.audit("login_ok", usn=usn, ip=client_ip())
            target = request.args.get("next", "")
            if row["status"] != "active":
                return redirect(url_for("auth.pending"))
            return redirect(target if is_safe_next(target) else url_for("dashboard"))
        fails = 0
        if row and not locked:
            fails = row["failed_attempts"] + 1
            if fails >= MAX_FAILED:
                con.execute("UPDATE users SET failed_attempts = 0, locked_until = ? WHERE id = ?", (now + LOCK_SECONDS, row["id"]))
            else:
                con.execute("UPDATE users SET failed_attempts = ? WHERE id = ?", (fails, row["id"]))
            con.commit()
        con.close()
        if row and not locked and fails >= MAX_FAILED:
            auth_db.audit("account_locked", usn=usn, ip=client_ip())
        auth_db.audit("login_failed", usn=usn[:20], ip=client_ip(), detail="locked" if locked else "")
        flash("Invalid USN or password, or the account is temporarily locked.", "error")
    return render_template("login.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if rate_limited("register", 10, 3600):
            auth_db.audit("rate_limited", ip=client_ip(), detail="register")
            abort(429)
        usn = normalize_usn(request.form.get("usn"))
        name = " ".join((request.form.get("name") or "").split())[:80]
        password = request.form.get("password", "")[:200]
        confirm = request.form.get("confirm", "")[:200]
        code = (request.form.get("code") or "").strip().upper().replace(" ", "")[:20]

        error = None
        if not USN_RE.match(usn):
            error = "Enter a valid USN, for example 1UC24CS037."
        elif len(name) < 2 or not all(c.isalpha() or c in " .'-" for c in name):
            error = "Enter your full name (letters only)."
        elif password != confirm:
            error = "The two passwords do not match."
        else:
            error = password_problem(password, usn)
        if error:
            flash(error, "error")
            return render_template("register.html", usn=usn, name=name)

        con = auth_db.connect()
        generic = "We could not create this account. If you already registered, sign in or contact the college office."
        if con.execute("SELECT 1 FROM users WHERE usn = ?", (usn,)).fetchone():
            con.close()
            flash(generic, "error")
            return render_template("register.html", usn=usn, name=name)

        pw_hash = generate_password_hash(password)
        if code:  # path 1: USN on the college list + one-time activation code
            if _code_blocked(usn):
                con.close()
                abort(429)
            allowed = con.execute("SELECT * FROM allowed_usns WHERE usn = ? AND used = 0", (usn,)).fetchone()
            if not allowed or not check_password_hash(allowed["code_hash"], code):
                _code_failures[usn].append(time.time())
                con.close()
                auth_db.audit("register_bad_code", usn=usn, ip=client_ip())
                flash("Invalid USN or activation code.", "error")
                return render_template("register.html", usn=usn, name=name)
            con.execute(
                "INSERT INTO users (usn, name, role, status, password_hash, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (usn, name, allowed["role"], pw_hash, time.time()),
            )
            con.execute("UPDATE allowed_usns SET used = 1 WHERE usn = ?", (usn,))
            con.commit()
            con.close()
            auth_db.audit("register_active", usn=usn, ip=client_ip())
            flash("Account created. You can sign in now.", "ok")
        else:  # path 2: request access, an admin must approve
            con.execute(
                "INSERT INTO users (usn, name, role, status, password_hash, created_at) VALUES (?, ?, 'student', 'pending', ?, ?)",
                (usn, name, pw_hash, time.time()),
            )
            con.commit()
            con.close()
            auth_db.audit("register_pending", usn=usn, ip=client_ip())
            flash("Request received. You can sign in, but you will see college information only after an admin approves you.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("register.html")


@bp.route("/pending")
def pending():
    if g.user["status"] == "active":
        return redirect(url_for("dashboard"))
    return render_template("pending.html")


@bp.route("/logout", methods=["POST"])
def logout():
    auth_db.audit("logout", usn=g.user["usn"], ip=client_ip())
    session.clear()
    flash("You have been signed out.", "ok")
    return redirect(url_for("auth.login"))
