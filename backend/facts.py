"""Turns the campus database into short fact passages so the assistant can answer open questions
("which rooms are in the CSE block?", "how many teachers are in ECE?") from the same retriever.

Visitors get facts about PUBLIC places only. Members get faculty, departments, rooms and timetable too.
"""
import re

from . import places
from .campus_data import rows


def dept_code(name):
    return "".join(w[0] for w in name.split() if w.lower() not in {"and", "of", "the"}).upper()


def build(tier):
    docs = []

    def add(title, body):
        docs.append({"id": -(len(docs) + 1), "title": title, "body": body})

    for b in places.buildings(public_only=(tier != "member")):
        add("Place: " + b["name"], "%s is a %s on the campus. %s" % (b["name"], b["category"] or "place", b["info"] or ""))
    if tier != "member":
        return docs

    fac = rows("SELECT f.name, f.designation, f.cabin, d.name AS dept, d.hod FROM faculty f JOIN departments d ON d.id = f.department_id ORDER BY d.name, f.name")
    by_dept = {}
    for f in fac:
        by_dept.setdefault((f["dept"], f["hod"]), []).append(f)
        add("Faculty: " + f["name"], "%s is %s in the %s department. Cabin %s." % (f["name"], f["designation"], f["dept"], f["cabin"]))
    for (dept, hod), people in by_dept.items():
        code = dept_code(dept)
        names = "; ".join("%s (%s, cabin %s)" % (p["name"], p["designation"], p["cabin"]) for p in people)
        add("Faculty of " + dept,
            "The teachers, faculty and staff members of the %s (%s) department are: %s. There are %d of them. "
            "The head of department (HOD) is %s." % (dept, code, names, len(people), hod))
    grouped = {}
    for r in rows("SELECT b.name AS building, r.number, r.floor, r.kind FROM rooms r JOIN buildings b ON b.id = r.building_id ORDER BY b.name, r.number"):
        grouped.setdefault(r["building"], []).append(r)
    for building, rs in grouped.items():
        add("Rooms in " + building, "Rooms, classrooms and labs in the %s: %s." % (
            building, "; ".join("%s (%s, floor %s)" % (r["number"], r["kind"], r["floor"]) for r in rs)))
    days = {}
    for t in rows("SELECT t.day, t.start, t.end, t.subject, r.number AS room, f.name AS faculty FROM timetable t "
                  "JOIN rooms r ON r.id = t.room_id JOIN faculty f ON f.id = t.faculty_id WHERE t.semester = 4 ORDER BY t.start"):
        days.setdefault(t["day"], []).append(t)
    for day, ts in days.items():
        add("Timetable " + day, "Classes on %s for semester 4: %s." % (day, "; ".join(
            "%s-%s %s in %s taught by %s" % (t["start"], t["end"], t["subject"], t["room"], t["faculty"]) for t in ts)))
    return docs