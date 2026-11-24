"""Buildings, routes and plain-language directions. public_only=True is the visitor view:
private buildings are never loaded, so they cannot appear in a route, a list or an answer."""
import math
import re

from navigation.pathfinding import build_graph, haversine_m, shortest_path

from .campus_data import rows

_DIRS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
_GENERIC = {"the", "block", "central", "area", "center", "centre", "of", "and"}


def buildings(public_only):
    sql = "SELECT id, name, category, lat, lng, is_public, info FROM buildings"
    if public_only:
        sql += " WHERE is_public = 1"
    return rows(sql + " ORDER BY name")


def _graph(blds):
    ids = {b["id"] for b in blds}
    paths = [p for p in rows("SELECT a_id, b_id FROM paths") if p["a_id"] in ids and p["b_id"] in ids]
    return build_graph(blds, paths)


def _bearing(a, b):
    y = math.sin(math.radians(b["lng"] - a["lng"])) * math.cos(math.radians(b["lat"]))
    x = math.cos(math.radians(a["lat"])) * math.sin(math.radians(b["lat"])) - \
        math.sin(math.radians(a["lat"])) * math.cos(math.radians(b["lat"])) * math.cos(math.radians(b["lng"] - a["lng"]))
    return _DIRS[int(((math.degrees(math.atan2(y, x)) + 360) % 360 + 22.5) // 45) % 8]


def route(start, goal, public_only):
    """Return a dict with path, steps, distance_m and minutes, or None when it is unknown or unreachable."""
    blds = buildings(public_only)
    by_name = {b["name"]: b for b in blds}
    if start not in by_name or goal not in by_name or start == goal:
        return None
    ids, metres = shortest_path(_graph(blds), by_name[start]["id"], by_name[goal]["id"])
    if ids is None:
        return None
    by_id = {b["id"]: b for b in blds}
    stops = [by_id[i] for i in ids]
    steps = []
    for a, b in zip(stops, stops[1:]):
        d = round(haversine_m(a["lat"], a["lng"], b["lat"], b["lng"]))
        steps.append({"text": "Walk %s from %s to %s (about %d m)." % (_bearing(a, b), a["name"], b["name"], d), "metres": d})
    steps.append({"text": "You have arrived at %s." % stops[-1]["name"], "metres": 0})
    return {
        "distance_m": round(metres),
        "minutes": max(1, round(metres / 80)),  # about 80 m per minute walking
        "path": [{"name": s["name"], "lat": s["lat"], "lng": s["lng"]} for s in stops],
        "steps": steps,
    }


def _tokens(name):
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _GENERIC}


ALIASES = {"admission": "Administration Block", "admissions": "Administration Block", "principal": "Administration Block",
           "office": "Administration Block", "canteen": "Food Court", "cafeteria": "Food Court", "clinic": "Medical Center",
           "hospital": "Medical Center", "entrance": "Main Gate", "seminar": "Central Auditorium", "playground": "Sports Ground"}


def find_mentions(text, blds):
    """Buildings named in a sentence, in the order they appear. A word like 'hostel' that fits two
    buildings only counts when the other word ('girls') is there too."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    owners = {}
    for b in blds:
        for t in _tokens(b["name"]):
            owners.setdefault(t, set()).add(b["id"])
    found = {}
    for b in blds:
        toks = _tokens(b["name"])
        hit = [i for i, w in enumerate(words) if w in toks]
        unique_hit = [i for i, w in enumerate(words) if w in toks and len(owners[w]) == 1]
        if toks and (toks <= set(words) or unique_hit):
            found[b["id"]] = min(unique_hit or hit)
    by_name = {b["name"]: b for b in blds}
    for i, w in enumerate(words):
        target = by_name.get(ALIASES.get(w, ""))
        if target and target["id"] not in found:
            found[target["id"]] = i
    order = sorted(found, key=lambda i: found[i])
    by_id = {b["id"]: b for b in blds}
    return [by_id[i] for i in order], words, found
