"""The chat assistant: a small tool-using agent.

1. work out what the person wants (route, timetable, faculty, room, or a general question);
2. call the matching tool, or the RAG retriever (+ optional LLM) for general questions;
3. return text plus, for routes, the path so the map can draw it.

The tier decides what every tool may read: a visitor can never reach member data, because the
member tools are not even called for a visitor and the visitor index holds public documents only.
"""
import datetime
import re

from . import facts, knowledge, llm, places
from .access import MEMBER, VISITOR, audiences
from .campus_data import rows

ROUTE_WORDS = ("route", "way to", "how to reach", "how do i get", "how to get", "directions", "navigate",
               "path to", "reach", "go to", "walk to", "where is", "where's", "how far", "distance", "take me")
TIMETABLE_WORDS = ("timetable", "time table", "schedule", "classes", "class today", "lecture", "period")
FACULTY_WORDS = ("faculty", "professor", "hod", "head of", "head of department", "cabin", "teacher", "lecturer", "who teaches")
ROOM_RE = re.compile(r"\b([a-z]{2,4})[- ]?(\d{3})\b", re.I)
GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you)\b", re.I)
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TITLES = {"dr", "prof", "mr", "mrs", "ms", "the"}

VISITOR_ONLY_MSG = ("Timetables, faculty and room details are for students and staff. Please log in to see them. "
                    "As a visitor you can ask me about visiting hours, admissions, facilities and directions to public places.")
NO_INFO = {VISITOR: "I do not have that information. Please ask at the Main Gate or contact the college office.",
           MEMBER: "I do not have that information. Please contact your department or the college office."}


def _reply(text, intent, mode="tool", sources=None, route=None, tier=VISITOR):
    return {"answer": text, "intent": intent, "mode": mode, "sources": sources or [], "route": route, "tier": tier}


def answer(question, tier):
    q = " ".join(question.split())[:300]
    low = q.lower()
    public_only = tier == VISITOR
    blds = places.buildings(public_only)
    mentions, words, pos = places.find_mentions(q, blds)

    if GREETING.match(q) and len(words) <= 4:
        return _reply("Hello! Ask me about the college, or for directions, for example: how do I reach the library from the main gate?",
                      "greeting", tier=tier)

    wants_table = any(w in low for w in TIMETABLE_WORDS)
    wants_faculty = any(w in low for w in FACULTY_WORDS)
    room = ROOM_RE.search(q) if re.search(r"\b(room|lab|class|classroom)\b", low) or ROOM_RE.search(q) else None
    if tier == VISITOR and (wants_table or wants_faculty or (room and room.group(1).lower() not in ("km", "ph"))):
        return _reply(VISITOR_ONLY_MSG, "members_only", tier=tier)

    is_route = any(w in low for w in ROUTE_WORDS) or (" from " in " " + low + " " and " to " in low and len(mentions) >= 2)
    if tier == MEMBER and room and re.search(r"\d{3}", room.group(0)):
        return _room(room, tier)
    if is_route and mentions:
        return _route_answer(mentions, words, pos, blds, public_only, tier)

    if tier == MEMBER:
        if ("teaches" in words or "taught" in words or "teach" in words) and _subject_rows(words):
            return _who_teaches(words, tier)
        if wants_table:
            return _timetable(words, tier)
        if wants_faculty or _faculty_hits(words):
            return _faculty(words, tier)

    passages = knowledge.search(q, audiences(tier), 6 if llm.enabled() else 3, facts=tier)
    if passages and passages[0]["score"] >= 1.0 and passages[0]["coverage"] >= 0.5:
        text = llm.generate(q, passages)
        mode = "llm"
        if not text:
            mode = "retrieval"
            text = _extractive(q, passages)
        sources = list(dict.fromkeys(p["title"] for p in passages[:2]))
        return _reply(text, "general", mode, sources, tier=tier)
    if is_route:
        return _route_answer(mentions, words, pos, blds, public_only, tier)
    return _reply(NO_INFO[tier], "unknown", tier=tier)


def _extractive(q, passages):
    """No-LLM answer: for a list-type fact passage return it whole, otherwise the best sentences."""
    top = passages[0]
    if top["title"].startswith(("Rooms in", "Timetable ")) and ": " in top["text"]:
        head, rest = top["text"].split(": ", 1)  # keep only the list items that match the question
        items = rest.rstrip(".").split("; ")
        want = {t for t in knowledge.tokenize(q)} - set(knowledge.tokenize(top["title"]))
        best = [i for i in items if want & set(knowledge.tokenize(i))]
        return head + ": " + "; ".join(best or items) + "."
    if top["title"].startswith("Faculty of"):
        return top["text"]
    return knowledge.best_sentences(q, top["text"], 2)


def _route_answer(mentions, words, pos, blds, public_only, tier):
    names = ", ".join(b["name"] for b in blds)
    if not mentions:
        if public_only:
            return _reply("I could not find that place on the visitor map. Visitors can go to: %s." % names, "route", tier=tier)
        return _reply("Which places do you mean? Available places: %s." % names, "route", tier=tier)
    default_start = next((b for b in blds if b["name"] == "Main Gate"), None)
    if len(mentions) >= 2:
        fi = words.index("from") if "from" in words else None
        after = [m for m in mentions if fi is not None and pos[m["id"]] > fi]
        start = after[0] if after else mentions[0]
        end = next(m for m in mentions if m is not start)
        note = ""
    else:
        end, start = mentions[0], default_start
        note = " (starting from the Main Gate; say 'from <place>' to change the start)"
    if start is None or start["name"] == end["name"]:
        return _reply("%s: %s" % (end["name"], end["info"] or "You are already there or the start point is unknown."), "route", tier=tier)
    r = places.route(start["name"], end["name"], public_only)
    if not r:
        return _reply("I could not find a walking route from %s to %s." % (start["name"], end["name"]), "route", tier=tier)
    text = "From %s to %s it is about %d m, around %d min on foot%s." % (start["name"], end["name"], r["distance_m"], r["minutes"], note)
    if end["info"]:
        text += " %s: %s" % (end["name"], end["info"])
    return _reply(text, "route", route=r, tier=tier)


def _subject_rows(words):
    data = rows("SELECT t.day, t.start, t.end, t.subject, r.number AS room, f.name AS faculty FROM timetable t "
                "JOIN rooms r ON r.id = t.room_id JOIN faculty f ON f.id = t.faculty_id WHERE t.semester = 4")
    return [d for d in data if {w for w in re.findall(r"[a-z]+", d["subject"].lower()) if len(w) > 3} <= set(words)]


def _who_teaches(words, tier):
    return _reply("\n".join("%s is taught by %s on %s %s-%s in %s." % (d["subject"], d["faculty"], d["day"], d["start"], d["end"], d["room"])
                            for d in _subject_rows(words)), "timetable", tier=tier)


def _timetable(words, tier):
    today = datetime.date.today()
    day = None
    if "today" in words:
        day = today.strftime("%A")
    elif "tomorrow" in words:
        day = (today + datetime.timedelta(days=1)).strftime("%A")
    else:
        day = next((d.capitalize() for d in DAYS if d in words), None)
    sql = ("SELECT t.day, t.start, t.end, t.subject, r.number AS room, f.name AS faculty FROM timetable t "
           "JOIN rooms r ON r.id = t.room_id JOIN faculty f ON f.id = t.faculty_id WHERE t.semester = 4")
    args = ()
    if day:
        sql += " AND t.day = ?"; args = (day,)
    data = rows(sql + " ORDER BY t.day, t.start", args)
    subj = [d for d in data if {w for w in re.findall(r"[a-z]+", d["subject"].lower()) if len(w) > 3} <= set(words)]
    if subj:
        data = subj
    if not data:
        days = [r["day"] for r in rows("SELECT DISTINCT day FROM timetable WHERE semester = 4")]
        hint = " Classes are listed for: %s." % ", ".join(days) if days else ""
        return _reply("No classes found%s.%s" % (" on %s" % day if day else "", hint), "timetable", tier=tier)
    lines = ["%s %s-%s  %s (%s, %s)" % (d["day"][:3], d["start"], d["end"], d["subject"], d["room"], d["faculty"]) for d in data]
    return _reply("Timetable (semester 4)%s:\n%s" % (" for " + day if day else "", "\n".join(lines)), "timetable", tier=tier)


def _faculty_hits(words):
    ws = {w for w in words if len(w) > 3 and w not in TITLES}
    people = rows("SELECT f.name, f.designation, f.cabin, d.name AS department FROM faculty f JOIN departments d ON d.id = f.department_id")
    return [p for p in people if ws & {t for t in re.findall(r"[a-z]+", p["name"].lower()) if len(t) > 3 and t not in TITLES}]


def _departments():
    out = []
    for d in rows("SELECT id, name, hod FROM departments"):
        cabins = [r["cabin"] for r in rows("SELECT cabin FROM faculty WHERE department_id = ?", (d["id"],))]
        codes = {facts.dept_code(d["name"]).lower()} | {c.split("-")[0].lower() for c in cabins if "-" in c}
        words = {w for w in re.findall(r"[a-z]+", d["name"].lower()) if len(w) > 3}
        out.append({**d, "codes": codes, "words": words})
    return out


def _faculty(words, tier):
    ws = {w for w in words if len(w) > 2 and w not in TITLES}
    hits = _faculty_hits(words)
    if hits:
        return _reply("\n".join("%s: %s, %s. Cabin %s." % (p["name"], p["designation"], p["department"], p["cabin"]) for p in hits[:4]),
                      "faculty", tier=tier)
    depts = _departments()
    asked = [d for d in depts if ws & (d["codes"] | d["words"])]
    if ("hod" in ws or "head" in ws) and not (ws & {"teachers", "faculty", "staff", "professors", "teacher", "members"}):
        return _reply("\n".join("HOD of %s: %s" % (d["name"], d["hod"]) for d in (asked or depts)), "faculty", tier=tier)
    people = rows("SELECT f.name, f.designation, f.cabin, d.name AS department FROM faculty f JOIN departments d ON d.id = f.department_id ORDER BY d.name, f.name")
    keep = {d["name"] for d in (asked or depts)}
    people = [p for p in people if p["department"] in keep]
    if "professors" in ws or "professor" in ws:
        only = [p for p in people if "professor" in p["designation"].lower()]
        people = only or people
    if not people:
        return _reply("I could not find faculty for that department.", "faculty", tier=tier)
    lines = ["%s: %s, cabin %s" % (p["name"], p["designation"], p["cabin"]) for p in people]
    head = "%d faculty member(s)%s:" % (len(people), " in " + ", ".join(sorted(keep)) if asked else "")
    if asked:
        head += "  (HOD: %s)" % "; ".join(d["hod"] for d in asked)
    return _reply(head + "\n" + "\n".join(lines), "faculty", tier=tier)


def _room(m, tier):
    code = (m.group(1) + "-" + m.group(2)).upper()
    r = rows("SELECT r.number, r.floor, r.kind, b.name AS building FROM rooms r JOIN buildings b ON b.id = r.building_id "
             "WHERE UPPER(REPLACE(r.number, ' ', '-')) = ?", (code,))
    if not r:
        return _reply("I could not find room %s." % code, "room", tier=tier)
    r = r[0]
    route = places.route("Main Gate", r["building"], False)
    return _reply("%s is a %s on floor %s of the %s." % (r["number"], r["kind"], r["floor"], r["building"]), "room", route=route, tier=tier)