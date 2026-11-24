"""Visitor vs member separation, the RAG assistant and the admin tools.
Run:  python -m unittest tests.test_tiers -v"""
import re
import sqlite3
import time
import unittest
from unittest import mock

from tests.common import app, auth_db, init_db, security  # noqa: F401
from werkzeug.security import generate_password_hash

from backend import knowledge, places
from backend.campus_data import execute, rows

PW = "Strong-pass-123"
PRIVATE = ["CSE Block", "ECE Block", "Girls Hostel", "Boys Hostel", "Sports Ground", "Kulkarni", "CSE-101"]


def meta_token(c, path="/visit"):
    return re.search(r'name="csrf" content="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)


def add_user(usn, role="student", status="active"):
    con = auth_db.connect()
    con.execute("INSERT OR REPLACE INTO users (usn,name,role,status,password_hash,created_at) VALUES (?,?,?,?,?,?)",
                (usn, "Test " + usn, role, status, generate_password_hash(PW), time.time()))
    con.commit(); con.close()


def login(c, usn):
    t = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
    return c.post("/login", data={"usn": usn, "password": PW, "csrf_token": t})


def ask(c, q, token=None):
    return c.post("/api/chat", json={"q": q}, headers={"X-CSRF-Token": token or meta_token(c)})


class Base(unittest.TestCase):
    def setUp(self):
        security._hits.clear()
        con = auth_db.connect()
        for t in ("users", "allowed_usns", "audit_log", "visitors"):
            con.execute("DELETE FROM " + t)
        con.commit(); con.close()
        execute("DELETE FROM documents WHERE title LIKE 'TEST %'")
        for name, info in init_db.PUBLIC_INFO.items():
            execute("UPDATE buildings SET is_public = 1, info = ? WHERE name = ?", (info, name))
        execute("UPDATE buildings SET is_public = 0 WHERE name IN ('CSE Block','ECE Block','Girls Hostel','Boys Hostel','Sports Ground')")
        self.v = app.test_client()
        add_user("1UC24CS101")
        self.m = app.test_client(); login(self.m, "1UC24CS101")
        add_user("ADM001", role="admin")
        self.a = app.test_client(); login(self.a, "ADM001")


class VisitorTier(Base):
    def test_places_api_has_only_public(self):
        names = {p["name"] for p in self.v.get("/api/places").get_json()["places"]}
        self.assertIn("Main Gate", names)
        self.assertFalse(names & {"CSE Block", "Girls Hostel", "ECE Block"})

    def test_member_places_api_has_all(self):
        names = {p["name"] for p in self.m.get("/api/places").get_json()["places"]}
        self.assertIn("CSE Block", names)

    def test_visitor_route_public_ok_with_steps(self):
        r = self.v.get("/api/route?start=Main%20Gate&end=Central%20Library")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertGreater(j["distance_m"], 0)
        self.assertTrue(j["steps"][-1]["text"].startswith("You have arrived"))

    def test_visitor_route_to_private_is_refused(self):
        for end in ("CSE%20Block", "Girls%20Hostel", "nonsense"):
            self.assertEqual(self.v.get("/api/route?start=Main%20Gate&end=" + end).status_code, 404, end)
        self.assertEqual(self.m.get("/api/route?start=Main%20Gate&end=CSE%20Block").status_code, 200)

    def test_private_building_cannot_be_a_waypoint(self):
        execute("UPDATE buildings SET is_public = 0 WHERE name = 'Central Library'")  # now a cut-vertex for the auditorium
        self.assertEqual(self.v.get("/api/route?start=Main%20Gate&end=Central%20Auditorium").status_code, 404)
        self.assertEqual(self.m.get("/api/route?start=Main%20Gate&end=Central%20Auditorium").status_code, 200)

    def test_place_pages(self):
        self.assertEqual(self.v.get("/visit/place/1").status_code, 200)
        priv = rows("SELECT id FROM buildings WHERE name = 'CSE Block'")[0]["id"]
        r = self.v.get("/visit/place/%d" % priv)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.status_code, self.v.get("/visit/place/9999").status_code)  # private looks like missing
        self.assertNotIn("CSE Block", r.get_data(as_text=True))
        self.assertEqual(self.m.get("/visit/place/%d" % priv).status_code, 200)

    def test_visitor_pages_contain_no_private_names(self):
        for p in ("/visit", "/api/places", "/visit/place/1", "/showcase", "/"):
            body = self.v.get(p).get_data(as_text=True)
            for w in PRIVATE:
                self.assertNotIn(w, body, p)

    def test_member_apis_still_blocked_for_visitors(self):
        for p in ("/buildings", "/find-route?start=Main%20Gate&end=Central%20Library", "/timetable", "/faculty", "/rooms", "/departments"):
            self.assertIn(self.v.get(p).status_code, (302, 401), p)

    def test_old_session_is_a_visitor_again(self):
        with self.m.session_transaction() as s:
            s["login_at"] = 1
        names = {p["name"] for p in self.m.get("/api/places").get_json()["places"]}
        self.assertNotIn("CSE Block", names)

    def test_disabled_and_pending_are_visitors(self):
        add_user("1UC24CS102", status="pending")
        c = app.test_client(); login(c, "1UC24CS102")
        self.assertNotIn("CSE Block", {p["name"] for p in c.get("/api/places").get_json()["places"]})
        con = auth_db.connect(); con.execute("UPDATE users SET status='disabled' WHERE usn='1UC24CS101'"); con.commit(); con.close()
        self.assertNotIn("CSE Block", {p["name"] for p in self.m.get("/api/places").get_json()["places"]})

    def test_qr_variants(self):
        for q in ("visit", "login", "bogus"):
            r = self.v.get("/showcase/qr.svg?for=" + q)
            self.assertEqual(r.mimetype, "image/svg+xml")


class Assistant(Base):
    def test_chat_needs_csrf_and_text(self):
        self.assertEqual(self.v.post("/api/chat", json={"q": "hi"}).status_code, 403)
        self.assertEqual(ask(self.v, "   ").status_code, 400)
        self.assertEqual(self.v.post("/api/chat", json={"q": 5}, headers={"X-CSRF-Token": meta_token(self.v)}).status_code, 400)

    def test_visitor_gets_public_answer(self):
        j = ask(self.v, "What are the visiting hours?").get_json()
        self.assertIn("9:30", j["answer"]); self.assertEqual(j["tier"], "visitor")
        self.assertTrue(j["sources"])

    def test_visitor_route_by_chat(self):
        j = ask(self.v, "How do I reach the library from the main gate?").get_json()
        self.assertEqual(j["intent"], "route"); self.assertTrue(j["route"]["steps"])

    def test_alias_words(self):
        j = ask(self.v, "how do I get to the canteen").get_json()
        self.assertEqual(j["route"]["path"][-1]["name"], "Food Court")

    def test_visitor_cannot_get_member_topics(self):
        for q in ("Show the timetable", "Who is the HOD of CSE?", "Where is CSE-101?", "faculty list"):
            j = ask(self.v, q).get_json()
            self.assertEqual(j["intent"], "members_only", q)
            for w in PRIVATE:
                self.assertNotIn(w, j["answer"])
        for q in ("What are the examination rules?", "hall ticket", "route to CSE block"):
            j = ask(self.v, q).get_json()
            self.assertNotIn("hall ticket", j["answer"].lower() if q != "hall ticket" else "")
            self.assertNotIn("CSE Block", j["answer"])

    def test_member_documents_never_reach_visitors(self):
        execute("INSERT INTO documents (title, audience, body, updated) VALUES ('TEST secret', 'members', 'The zebraquartz protocol opens the vault at midnight.', ?)", (time.time(),))
        v = ask(self.v, "what is the zebraquartz protocol").get_json()
        self.assertNotIn("zebraquartz", v["answer"].lower()); self.assertEqual(v["intent"], "unknown")
        m = ask(self.m, "what is the zebraquartz protocol").get_json()
        self.assertIn("zebraquartz", m["answer"].lower())

    def test_admin_changing_audience_applies_at_once(self):
        execute("INSERT INTO documents (title, audience, body, updated) VALUES ('TEST fee', 'members', 'The quokkafee counter is open on Mondays.', ?)", (time.time(),))
        self.assertEqual(ask(self.v, "quokkafee counter").get_json()["intent"], "unknown")
        doc = rows("SELECT id FROM documents WHERE title = 'TEST fee'")[0]["id"]
        t = meta_token(self.a, "/admin/knowledge")
        r = self.a.post("/admin/knowledge", data={"id": doc, "title": "TEST fee", "audience": "public",
                        "body": "The quokkafee counter is open on Mondays.", "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', self.a.get("/admin/knowledge").get_data(as_text=True)).group(1)})
        self.assertEqual(r.status_code, 302)
        self.assertIn("quokkafee", ask(self.v, "quokkafee counter").get_json()["answer"].lower())

    def test_member_tools(self):
        t = ask(self.m, "show friday timetable").get_json()
        self.assertEqual(t["intent"], "timetable"); self.assertIn("Operating Systems", t["answer"])
        f = ask(self.m, "Where is Prof. Nayak?").get_json()
        self.assertEqual(f["intent"], "faculty"); self.assertIn("CSE-212", f["answer"])
        h = ask(self.m, "Who is the HOD of CSE?").get_json()
        self.assertIn("Kulkarni", h["answer"])
        r = ask(self.m, "Where is CSE-101?").get_json()
        self.assertEqual(r["intent"], "room"); self.assertTrue(r["route"])
        rt = ask(self.m, "route from library to girls hostel").get_json()
        self.assertEqual([p["name"] for p in rt["route"]["path"]][0], "Central Library")
        self.assertEqual(rt["route"]["path"][-1]["name"], "Girls Hostel")
        self.assertIn("seated", ask(self.m, "exam rules").get_json()["answer"])

    def test_visitor_chat_rate_limit(self):
        t = meta_token(self.v)
        codes = [ask(self.v, "hello there", t).status_code for _ in range(25)]
        self.assertIn(429, codes)

    def test_question_text_is_not_stored(self):
        ask(self.v, "my secret question zzzyyyxxx")
        con = auth_db.connect()
        n = con.execute("SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%zzzyyyxxx%'").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_llm_gets_only_allowed_passages(self):
        execute("INSERT INTO documents (title, audience, body, updated) VALUES ('TEST priv', 'members', 'The visiting zebraquartz secret code is orange.', ?)", (time.time(),))
        sent = []

        class Resp:
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "LLM says hello."}}]}

        def fake_post(url, **kw):
            sent.append(kw["json"]); return Resp()

        with mock.patch.dict("os.environ", {"LLM_PROVIDER": "openai", "LLM_API_KEY": "k"}), mock.patch("backend.llm.requests.post", fake_post):
            j = ask(self.v, "What are the visiting hours?").get_json()
            self.assertEqual(j["mode"], "llm"); self.assertEqual(j["answer"], "LLM says hello.")
            self.assertNotIn("zebraquartz", str(sent[-1]).lower())
            j2 = ask(self.m, "visiting zebraquartz secret code").get_json()
            self.assertIn("zebraquartz", str(sent[-1]).lower())

    def test_llm_failure_falls_back(self):
        import requests as rq
        with mock.patch.dict("os.environ", {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "k"}), \
             mock.patch("backend.llm.requests.post", side_effect=rq.ConnectionError()):
            j = ask(self.v, "What are the visiting hours?").get_json()
        self.assertEqual(j["mode"], "retrieval"); self.assertIn("9:30", j["answer"])


class Retrieval(unittest.TestCase):
    def test_bm25_ranks_relevant_first(self):
        idx = knowledge.Index([{"id": 1, "title": "Parking", "body": "Cars park near the gate."},
                               {"id": 2, "title": "Library", "body": "Books can be borrowed for fourteen days."}])
        self.assertEqual(idx.search("how long can I borrow books")[0]["title"], "Library")

    def test_chunking_keeps_sentences(self):
        body = " ".join("Sentence number %d is here." % i for i in range(60))
        parts = knowledge.chunk(body, 30)
        self.assertGreater(len(parts), 3)
        self.assertTrue(all(p.endswith(".") for p in parts))

    def test_mentions(self):
        blds = places.buildings(public_only=False)
        ms, words, pos = places.find_mentions("from the library to girls hostel", blds)
        self.assertEqual([m["name"] for m in ms], ["Central Library", "Girls Hostel"])
        ms, _, _ = places.find_mentions("where is the hostel", blds)
        self.assertEqual(ms, [])  # 'hostel' alone is ambiguous


class CheckinAndAdmin(Base):
    def _post_checkin(self, **kw):
        data = {"name": "Ravi Kumar", "purpose": "Admission enquiry", "host": "Admissions", "csrf_token": meta_token(self.v, "/visit/checkin")}
        data.update(kw)
        return self.v.post("/visit/checkin", data=data)

    def test_checkin_and_pass(self):
        r = self._post_checkin()
        self.assertEqual(r.status_code, 302)
        page = self.v.get("/visit/pass").get_data(as_text=True)
        code = re.search(r'class="big">(V[A-Z0-9]{6})<', page).group(1)
        self.assertEqual(self.v.get("/visit/pass.svg").mimetype, "image/svg+xml")
        res = self.a.get("/admin/visitors?code=" + code).get_data(as_text=True)
        self.assertIn("VALID", res); self.assertIn("Ravi Kumar", res)

    def test_checkin_validation(self):
        for bad in ({"name": "<script>alert(1)</script>"}, {"purpose": "Hack"}, {"host": "<b>x</b>"}, {"name": ""}):
            r = self._post_checkin(**bad)
            self.assertEqual(r.status_code, 200); self.assertIn("flash error", r.get_data(as_text=True))
        self.assertEqual(self.v.get("/visit/pass").status_code, 302)  # no pass was created

    def test_pass_is_private_to_the_browser(self):
        self._post_checkin()
        other = app.test_client()
        self.assertEqual(other.get("/visit/pass").status_code, 302)
        self.assertEqual(other.get("/visit/pass.svg").status_code, 404)

    def test_expired_pass(self):
        self._post_checkin()
        con = auth_db.connect(); con.execute("UPDATE visitors SET valid_until = 1"); con.commit(); con.close()
        self.assertIn("EXPIRED", self.a.get("/admin/visitors?code=" + re.search(r'class="big">(V[A-Z0-9]{6})<', self.v.get("/visit/pass").get_data(as_text=True)).group(1)).get_data(as_text=True))

    def test_checkin_rate_limit(self):
        codes = [self._post_checkin().status_code for _ in range(14)]
        self.assertIn(429, codes)

    def test_admin_places_toggle(self):
        pid = rows("SELECT id FROM buildings WHERE name = 'Sports Ground'")[0]["id"]
        t = re.search(r'name="csrf_token" value="([^"]+)"', self.a.get("/admin/places").get_data(as_text=True)).group(1)
        self.a.post("/admin/places", data={"id": pid, "is_public": "1", "info": "Open ground", "csrf_token": t})
        names = {p["name"] for p in self.v.get("/api/places").get_json()["places"]}
        self.assertIn("Sports Ground", names)
        self.a.post("/admin/places", data={"id": pid, "info": "", "csrf_token": t})
        self.assertNotIn("Sports Ground", {p["name"] for p in self.v.get("/api/places").get_json()["places"]})

    def test_admin_pages_forbidden_to_members(self):
        for p in ("/admin/knowledge", "/admin/places", "/admin/visitors", "/admin/analytics", "/admin/qr-sheet", "/admin/qr/1.svg"):
            self.assertEqual(self.m.get(p).status_code, 403, p)
            self.assertIn(self.v.get(p).status_code, (302, 401), p)
            self.assertEqual(self.a.get(p).status_code, 200, p)

    def test_admin_document_is_escaped(self):
        t = re.search(r'name="csrf_token" value="([^"]+)"', self.a.get("/admin/knowledge").get_data(as_text=True)).group(1)
        self.a.post("/admin/knowledge", data={"title": "TEST <b>x</b>", "audience": "public", "body": "<script>alert(1)</script> some long enough text", "csrf_token": t})
        page = self.a.get("/admin/knowledge").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_usage_page_counts_events(self):
        ask(self.v, "What are the visiting hours?"); self.v.get("/visit?s=qr")
        page = self.a.get("/admin/analytics").get_data(as_text=True)
        self.assertIn("from the QR code", page)


class Migration(unittest.TestCase):
    def test_old_database_gets_new_columns_and_keeps_data(self):
        con = sqlite3.connect(":memory:")
        con.executescript("CREATE TABLE buildings (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, category TEXT, lat REAL, lng REAL);"
                          "INSERT INTO buildings VALUES (1,'Main Gate','gate',1,2),(2,'My Own Lab','lab',3,4);")
        init_db.ensure_schema(con)
        got = {r[0]: r[1] for r in con.execute("SELECT name, is_public FROM buildings")}
        self.assertEqual(got, {"Main Gate": 1, "My Own Lab": 0})  # unknown places stay private
        init_db.ensure_schema(con)  # running twice is harmless


if __name__ == "__main__":
    unittest.main()
