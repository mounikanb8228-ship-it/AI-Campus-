"""Visitor guide + shared tier-aware APIs. EVERY route here is public, so each one must call
access.tier() and return only what that tier may see."""
import re
import secrets
import time

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for

from . import assistant, auth_db, places
from .access import MEMBER, VISITOR, tier
from .qr import make_svg
from .security import client_ip, rate_limited
from flask import Response

bp = Blueprint("guide", __name__)
PURPOSES = ["Admission enquiry", "Meet an official", "Meet a student or staff member", "Event or seminar", "Delivery or service", "Other"]
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASS_HOURS = 8


@bp.route("/visit")
def portal():
    t = tier()
    auth_db.audit("visit_open", ip=client_ip(), detail="qr" if request.args.get("s") == "qr" else "direct")
    return render_template("visit.html", tier=t, purposes=PURPOSES)


@bp.route("/visit/place/<int:pid>")
def place(pid):
    t = tier()
    blds = places.buildings(public_only=(t == VISITOR))
    b = next((x for x in blds if x["id"] == pid), None)
    if b is None:  # private and missing look identical to a visitor
        return render_template("visit_locked.html", next_url=url_for("guide.place", pid=pid)), 404
    return render_template("visit_place.html", b=b, others=[x for x in blds if x["id"] != pid], tier=t)


@bp.route("/visit/checkin", methods=["GET", "POST"])
def checkin():
    if request.method == "POST":
        if rate_limited("checkin", 10, 3600):
            abort(429)
        name = " ".join((request.form.get("name") or "").split())[:60]
        purpose = request.form.get("purpose", "")
        host = " ".join((request.form.get("host") or "").split())[:60]
        error = None
        if len(name) < 2 or not re.fullmatch(r"[A-Za-z .'-]+", name):
            error = "Enter your name (letters only)."
        elif purpose not in PURPOSES:
            error = "Choose the purpose of your visit."
        elif host and not re.fullmatch(r"[A-Za-z0-9 .,'()-]+", host):
            error = "Use letters and numbers only for who you are meeting."
        if error:
            return render_template("visit_checkin.html", purposes=PURPOSES, error=error, name=name, host=host, purpose=purpose)
        code = "V" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
        now = time.time()
        con = auth_db.connect()
        con.execute("INSERT INTO visitors (code, name, purpose, host, created_at, valid_until) VALUES (?,?,?,?,?,?)",
                    (code, name, purpose, host, now, now + PASS_HOURS * 3600))
        con.commit()
        con.close()
        session["pass"] = code
        auth_db.audit("visitor_checkin", ip=client_ip(), detail=purpose)
        return redirect(url_for("guide.visitor_pass"))
    return render_template("visit_checkin.html", purposes=PURPOSES)


def _my_pass():
    code = session.get("pass")
    if not code:
        return None
    con = auth_db.connect()
    row = con.execute("SELECT * FROM visitors WHERE code = ?", (code,)).fetchone()
    con.close()
    return row


@bp.route("/visit/pass")
def visitor_pass():
    row = _my_pass()
    if not row:
        return redirect(url_for("guide.checkin"))
    return render_template("visit_pass.html", p=row, hours=PASS_HOURS,
                           valid=row["valid_until"] > time.time(),
                           until=time.strftime("%d %b %Y, %I:%M %p", time.localtime(row["valid_until"])))


@bp.route("/visit/pass.svg")
def visitor_pass_svg():
    row = _my_pass()
    if not row:
        abort(404)
    return Response(make_svg("VISITOR-PASS:" + row["code"]), mimetype="image/svg+xml")


# ---------------------------------------------------------------- tier-aware JSON APIs
@bp.route("/api/places")
def api_places():
    t = tier()
    return jsonify(tier=t, places=[
        {"id": b["id"], "name": b["name"], "category": b["category"], "lat": b["lat"], "lng": b["lng"], "info": b["info"]}
        for b in places.buildings(public_only=(t == VISITOR))])


@bp.route("/api/route")
def api_route():
    t = tier()
    start, end = request.args.get("start", "")[:60], request.args.get("end", "")[:60]
    r = places.route(start, end, public_only=(t == VISITOR))
    if r is None:
        return jsonify(error="No route found between those places."), 404
    return jsonify(r)


@bp.route("/api/chat", methods=["POST"])
def api_chat():
    t = tier()
    if rate_limited("chat_" + t, 60 if t == MEMBER else 20, 300):
        return jsonify(error="Too many questions. Please wait a few minutes."), 429
    data = request.get_json(silent=True) or {}
    q = data.get("q", "") if isinstance(data, dict) else ""
    if not isinstance(q, str) or not q.strip():
        return jsonify(error="Type a question first."), 400
    result = assistant.answer(q, t)
    auth_db.audit("chat_" + t, ip=client_ip(), detail=result["intent"] + "/" + result["mode"])  # the question text is not stored
    return jsonify(result)
