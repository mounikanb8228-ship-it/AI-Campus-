"""Smart Campus Assistant - secured website.

Start:  python -m backend.app
Visitors can only see the landing page, sign-in, registration and the QR showcase page.
All college information needs an approved college account.
"""
import os

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

load_dotenv()

from . import admin, auth, guide, places  # noqa: E402
from .access import site_url  # noqa: E402
from .campus_data import rows  # noqa: E402
from .qr import make_svg  # noqa: E402
from .security import init_security  # noqa: E402

app = Flask(__name__)
init_security(app)  # <- the whole security layer is switched on by this one line
app.register_blueprint(auth.bp)
app.register_blueprint(admin.bp)
app.register_blueprint(guide.bp)  # visitor guide + tier-aware APIs (public, each route checks access.tier())


# ---------------------------------------------------------------- public pages
@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/showcase")
def showcase():
    base = site_url()
    return render_template("showcase.html", visit_url=base + "/visit?s=qr", login_url=base + "/login")


@app.route("/showcase/qr.svg")
def showcase_qr():
    # ?for=visit -> visitor guide, ?for=login -> students and staff sign-in
    target = "/login" if request.args.get("for") == "login" else "/visit?s=qr"
    return Response(make_svg(site_url() + target), mimetype="image/svg+xml")


# -------------------------------------------------- members only (guarded by security.py)
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/directory")
def directory():
    return render_template("directory.html")


@app.route("/buildings")
def buildings():
    return jsonify(rows("SELECT id, name, category, lat, lng, is_public, info FROM buildings ORDER BY name"))


@app.route("/departments")
def departments():
    return jsonify(rows("SELECT id, name, hod FROM departments ORDER BY name"))


@app.route("/rooms")
def rooms():
    return jsonify(rows(
        "SELECT r.number, b.name AS building, r.floor, r.kind FROM rooms r "
        "JOIN buildings b ON b.id = r.building_id ORDER BY r.number"))


@app.route("/faculty")
def faculty():
    return jsonify(rows(
        "SELECT f.name, d.name AS department, f.designation, f.cabin FROM faculty f "
        "JOIN departments d ON d.id = f.department_id ORDER BY f.name"))


@app.route("/timetable")
def timetable():
    return jsonify(rows(
        "SELECT t.day, t.start, t.end, t.subject, r.number AS room, f.name AS faculty "
        "FROM timetable t JOIN rooms r ON r.id = t.room_id JOIN faculty f ON f.id = t.faculty_id "
        "WHERE t.semester = 4 ORDER BY t.start"))


@app.route("/find-route")
def find_route():
    r = places.route(request.args.get("start", "")[:60], request.args.get("end", "")[:60], public_only=False)
    if r is None:
        return jsonify(error="No route found between those places."), 404
    return jsonify(r)


if __name__ == "__main__":
    from werkzeug.serving import WSGIRequestHandler

    WSGIRequestHandler.server_version = "campus"  # do not announce Werkzeug / Python versions
    WSGIRequestHandler.sys_version = ""
    # Never run with debug=True in front of other people: the debugger lets anyone run code.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
