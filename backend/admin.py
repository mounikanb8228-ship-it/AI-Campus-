"""Admin pages: approve people, load the USN list, read the audit log and scan report."""
import csv
import io
import json
import os
import secrets
import time

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import auth_db
from .security import USN_RE, client_ip, normalize_usn

bp = Blueprint("admin", __name__, url_prefix="/admin")
REPORT_JSON = os.path.join(auth_db.ROOT, "reports", "latest.json")
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid mistakes


def new_code(length=10):
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


@bp.route("/users")
def users():
    con = auth_db.connect()
    rows = con.execute(
        "SELECT id, usn, name, role, status, created_at FROM users "
        "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, created_at DESC"
    ).fetchall()
    con.close()
    return render_template("admin_users.html", users=rows)


@bp.route("/users/<int:user_id>/<action>", methods=["POST"])
def user_action(user_id, action):
    if action not in ("approve", "disable", "enable"):
        abort(404)
    if user_id == g.user["id"]:
        flash("You cannot change your own account here.", "error")
        return redirect(url_for("admin.users"))
    con = auth_db.connect()
    row = con.execute("SELECT usn FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        con.close()
        abort(404)
    if action == "approve":
        role = request.form.get("role", "student")
        if role not in ("student", "faculty"):
            role = "student"
        con.execute("UPDATE users SET status = 'active', role = ? WHERE id = ?", (role, user_id))
    elif action == "disable":
        con.execute("UPDATE users SET status = 'disabled' WHERE id = ?", (user_id,))
    else:
        con.execute("UPDATE users SET status = 'active', failed_attempts = 0, locked_until = 0 WHERE id = ?", (user_id,))
    con.commit()
    con.close()
    auth_db.audit("admin_" + action, usn=g.user["usn"], ip=client_ip(), detail=row["usn"])
    flash("Done: %s %s." % (action, row["usn"]), "ok")
    return redirect(url_for("admin.users"))


@bp.route("/usns", methods=["GET", "POST"])
def usns():
    issued = None
    if request.method == "POST":
        text = request.form.get("rows", "")[:50000]
        issued, skipped = [], []
        con = auth_db.connect()
        for rec in csv.reader(io.StringIO(text)):
            if not rec or not rec[0].strip() or rec[0].strip().lower() == "usn":
                continue
            usn = normalize_usn(rec[0])
            name = rec[1].strip()[:80] if len(rec) > 1 else ""
            role = rec[2].strip().lower() if len(rec) > 2 else "student"
            if role not in ("student", "faculty"):
                role = "student"
            exists = con.execute(
                "SELECT 1 FROM allowed_usns WHERE usn = ? UNION SELECT 1 FROM users WHERE usn = ?", (usn, usn)
            ).fetchone()
            if not USN_RE.match(usn) or exists:
                skipped.append(usn or "(blank)")
                continue
            code = new_code()
            con.execute(
                "INSERT INTO allowed_usns (usn, name, role, code_hash) VALUES (?, ?, ?, ?)",
                (usn, name, role, generate_password_hash(code)),
            )
            issued.append((usn, name, role, code))
        con.commit()
        con.close()
        auth_db.audit("admin_import_usns", usn=g.user["usn"], ip=client_ip(), detail="%d added, %d skipped" % (len(issued), len(skipped)))
        if skipped:
            flash("Skipped (invalid or already known): " + ", ".join(skipped[:20]), "error")
    con = auth_db.connect()
    total = con.execute("SELECT COUNT(*) FROM allowed_usns").fetchone()[0]
    unused = con.execute("SELECT COUNT(*) FROM allowed_usns WHERE used = 0").fetchone()[0]
    con.close()
    return render_template("admin_usns.html", issued=issued, total=total, unused=unused)


@bp.route("/audit")
def audit():
    con = auth_db.connect()
    rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    con.close()
    return render_template("admin_audit.html", rows=rows, fmt=lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)))


@bp.route("/security")
def security():
    report = None
    if os.path.exists(REPORT_JSON):
        with open(REPORT_JSON) as f:
            report = json.load(f)
    return render_template("admin_security.html", report=report)


# ------------------------------------------------------------------ knowledge base (RAG documents)
from flask import Response  # noqa: E402

from .access import site_url  # noqa: E402
from .campus_data import execute, rows  # noqa: E402
from .qr import make_svg  # noqa: E402

AUDIENCES = ("public", "members")


@bp.route("/knowledge", methods=["GET", "POST"])
def knowledge():
    if request.method == "POST":
        title = " ".join(request.form.get("title", "").split())[:120]
        audience = request.form.get("audience", "members")
        body = request.form.get("body", "").strip()[:8000]
        doc_id = request.form.get("id", "")
        if not title or len(body) < 20 or audience not in AUDIENCES:
            flash("Give a title, an audience and at least 20 characters of text.", "error")
        elif doc_id.isdigit():
            execute("UPDATE documents SET title=?, audience=?, body=?, updated=? WHERE id=?", (title, audience, body, time.time(), int(doc_id)))
            auth_db.audit("admin_doc_edit", usn=g.user["usn"], ip=client_ip(), detail=title[:60])
            flash("Document updated. The assistant uses it right away.", "ok")
        else:
            execute("INSERT INTO documents (title, audience, body, updated) VALUES (?,?,?,?)", (title, audience, body, time.time()))
            auth_db.audit("admin_doc_add", usn=g.user["usn"], ip=client_ip(), detail=title[:60])
            flash("Document added. The assistant uses it right away.", "ok")
        return redirect(url_for("admin.knowledge"))
    edit = None
    if request.args.get("edit", "").isdigit():
        found = rows("SELECT * FROM documents WHERE id = ?", (int(request.args["edit"]),))
        edit = found[0] if found else None
    docs = rows("SELECT id, title, audience, LENGTH(body) AS size FROM documents ORDER BY audience, title")
    return render_template("admin_knowledge.html", docs=docs, edit=edit)


@bp.route("/knowledge/<int:doc_id>/delete", methods=["POST"])
def knowledge_delete(doc_id):
    execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    auth_db.audit("admin_doc_delete", usn=g.user["usn"], ip=client_ip(), detail=str(doc_id))
    flash("Document deleted.", "ok")
    return redirect(url_for("admin.knowledge"))


# ------------------------------------------------------------------ which places visitors may see
@bp.route("/places", methods=["GET", "POST"])
def places_admin():
    if request.method == "POST":
        pid = request.form.get("id", "")
        if pid.isdigit():
            public = 1 if request.form.get("is_public") == "1" else 0
            info = " ".join(request.form.get("info", "").split())[:300]
            execute("UPDATE buildings SET is_public = ?, info = ? WHERE id = ?", (public, info, int(pid)))
            auth_db.audit("admin_place", usn=g.user["usn"], ip=client_ip(), detail="%s public=%d" % (pid, public))
            flash("Place updated.", "ok")
        return redirect(url_for("admin.places_admin"))
    return render_template("admin_places.html", places=rows("SELECT id, name, category, is_public, info FROM buildings ORDER BY is_public DESC, name"))


@bp.route("/qr-sheet")
def qr_sheet():
    return render_template("admin_qr_sheet.html", places=rows("SELECT id, name, is_public FROM buildings ORDER BY is_public DESC, name"))


@bp.route("/qr/<int:pid>.svg")
def qr_place(pid):
    if not rows("SELECT 1 FROM buildings WHERE id = ?", (pid,)):
        abort(404)
    return Response(make_svg("%s/visit/place/%d" % (site_url(), pid)), mimetype="image/svg+xml")


# ------------------------------------------------------------------ visitors and usage
@bp.route("/visitors")
def visitors():
    code = request.args.get("code", "").strip().upper()[:12]
    con = auth_db.connect()
    checked = con.execute("SELECT * FROM visitors WHERE code = ?", (code,)).fetchone() if code else None
    recent = con.execute("SELECT * FROM visitors ORDER BY id DESC LIMIT 100").fetchall()
    con.close()
    fmt = lambda t: time.strftime("%d %b %H:%M", time.localtime(t))
    return render_template("admin_visitors.html", recent=recent, checked=checked, code=code, fmt=fmt, now=time.time())


@bp.route("/analytics")
def analytics():
    con = auth_db.connect()
    since = time.time() - 7 * 86400
    data = con.execute(
        "SELECT date(ts, 'unixepoch', 'localtime') AS day, event, COUNT(*) AS n FROM audit_log "
        "WHERE ts > ? GROUP BY day, event ORDER BY day DESC", (since,)).fetchall()
    scans = con.execute("SELECT detail, COUNT(*) AS n FROM audit_log WHERE event = 'visit_open' AND ts > ? GROUP BY detail", (since,)).fetchall()
    chats = con.execute("SELECT detail, COUNT(*) AS n FROM audit_log WHERE event IN ('chat_visitor','chat_member') AND ts > ? "
                        "GROUP BY detail ORDER BY n DESC LIMIT 8", (since,)).fetchall()
    con.close()
    events = ["visit_open", "chat_visitor", "chat_member", "visitor_checkin", "login_ok", "login_failed", "blocked_access"]
    days = {}
    for r in data:
        days.setdefault(r["day"], {})[r["event"]] = r["n"]
    return render_template("admin_analytics.html", events=events, days=days,
                           scans={r["detail"]: r["n"] for r in scans}, chats=chats)
