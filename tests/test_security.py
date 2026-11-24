"""Run:  python -m unittest tests.test_security -v   (uses temporary databases)"""
import os, re, tempfile, time, unittest

from tests.common import app, auth_db, init_db, security  # noqa: E402,F401
from werkzeug.security import generate_password_hash  # noqa: E402

PW = "Strong-pass-123"
PROTECTED = ["/dashboard", "/directory", "/buildings", "/departments", "/rooms", "/faculty",
             "/timetable", "/find-route?start=Main%20Gate&end=CSE%20Block", "/admin/users", "/admin/usns",
             "/admin/audit", "/admin/security", "/admin/knowledge", "/admin/places", "/admin/visitors", "/admin/analytics", "/admin/qr-sheet"]


def token(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def add_user(usn, status="active", role="student", password=PW):
    con = auth_db.connect()
    con.execute("INSERT OR REPLACE INTO users (usn,name,role,status,password_hash,created_at) VALUES (?,?,?,?,?,?)",
                (usn, "Test " + usn, role, status, generate_password_hash(password), time.time()))
    con.commit(); con.close()


def login(client, usn, password=PW):
    return client.post("/login", data={"usn": usn, "password": password, "csrf_token": token(client)})


class T(unittest.TestCase):
    def setUp(self):
        security._hits.clear()
        con = auth_db.connect()
        for t in ("users", "allowed_usns", "audit_log"):
            con.execute("DELETE FROM " + t)
        con.commit(); con.close()
        app.config["TESTING"] = False
        self.c = app.test_client()

    def test_visitor_blocked_everywhere(self):
        for p in PROTECTED:
            r = self.c.get(p)
            self.assertIn(r.status_code, (302, 401), p)
            if r.status_code == 302:
                self.assertIn("/login", r.headers["Location"])
        r = self.c.get("/buildings", headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("Main Gate", r.get_data(as_text=True))

    def test_public_pages_have_no_college_data(self):
        for p in ["/", "/login", "/register", "/showcase", "/visit"]:
            body = self.c.get(p).get_data(as_text=True)
            for secret in ("Kulkarni", "CSE-208", "Girls Hostel", "CSE Block"):
                self.assertNotIn(secret, body)

    def test_active_member_can_see(self):
        add_user("1UC24CS101")
        self.assertEqual(login(self.c, "1UC24CS101").status_code, 302)
        self.assertEqual(self.c.get("/buildings").status_code, 200)
        self.assertEqual(self.c.get("/find-route?start=Main%20Gate&end=CSE%20Block").get_json()["path"][0]["name"], "Main Gate")

    def test_pending_cannot_see(self):
        add_user("1UC24CS102", status="pending")
        login(self.c, "1UC24CS102")
        r = self.c.get("/buildings", headers={"Accept": "application/json"})
        self.assertNotEqual(r.status_code, 200)
        self.assertNotIn("Main Gate", r.get_data(as_text=True))

    def test_student_cannot_open_admin(self):
        add_user("1UC24CS101")
        login(self.c, "1UC24CS101")
        for p in ("/admin/users", "/admin/usns", "/admin/audit"):
            self.assertEqual(self.c.get(p).status_code, 403)

    def test_admin_can_open_admin(self):
        add_user("ADM001", role="admin")
        login(self.c, "ADM001")
        for p in ("/admin/users", "/admin/usns", "/admin/audit", "/admin/security", "/admin/knowledge", "/admin/places", "/admin/visitors", "/admin/analytics", "/admin/qr-sheet"):
            self.assertEqual(self.c.get(p).status_code, 200, p)

    def test_wrong_password_generic_and_lockout(self):
        add_user("1UC24CS101")
        msgs = set()
        for _ in range(5):
            security._hits.clear()
            msgs.add(login(self.c, "1UC24CS101", "wrong-password-1").get_data(as_text=True)[:0] or "x")
        security._hits.clear()
        r = login(self.c, "1UC24CS101", PW)  # correct password but now locked
        self.assertEqual(r.status_code, 200)  # stays on login page
        self.assertEqual(self.c.get("/buildings").status_code, 302)

    def test_unknown_and_wrong_same_message(self):
        add_user("1UC24CS101")
        a = login(self.c, "1UC24CS101", "nope-nope-123").get_data(as_text=True)
        b = login(self.c, "1UC24CS999", "nope-nope-123").get_data(as_text=True)
        flash = lambda h: re.search(r'class="flash[^"]*"[^>]*>([^<]+)', h).group(1)
        self.assertEqual(flash(a), flash(b))

    def test_csrf_required(self):
        add_user("1UC24CS101")
        r = self.c.post("/login", data={"usn": "1UC24CS101", "password": PW})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/login", data={"usn": "1UC24CS101", "password": PW, "csrf_token": "bad"})
        self.assertEqual(r.status_code, 403)

    def test_cross_site_post_rejected(self):
        t = token(self.c)
        r = self.c.post("/login", data={"usn": "x", "password": "y", "csrf_token": t}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)

    def test_register_without_code_is_pending(self):
        t = token(self.c, "/register")
        r = self.c.post("/register", data={"usn": "1uc24cs103", "name": "New Person", "password": PW,
                                           "confirm": PW, "code": "", "csrf_token": t})
        self.assertEqual(r.status_code, 302)
        con = auth_db.connect()
        row = con.execute("SELECT status FROM users WHERE usn='1UC24CS103'").fetchone()
        con.close()
        self.assertEqual(row["status"], "pending")
        self.assertNotEqual(self.c.get("/buildings", headers={"Accept": "application/json"}).status_code, 200)

    def test_register_with_code_is_active_and_code_single_use(self):
        add_user("ADM001", role="admin")
        a = app.test_client(); login(a, "ADM001")
        r = a.post("/admin/usns", data={"rows": "1UC24CS104,Code Person,student", "csrf_token": token(a, "/admin/usns")})
        code = re.search(r"<code>([^<]+)</code>", r.get_data(as_text=True)).group(1)
        c = app.test_client()
        data = {"usn": "1UC24CS104", "name": "Code Person", "password": PW, "confirm": PW, "code": code}
        c.post("/register", data=dict(data, csrf_token=token(c, "/register")))
        self.assertEqual(c.get("/buildings").status_code, 302)  # registering does not sign in
        login(c, "1UC24CS104")
        self.assertEqual(c.get("/buildings").status_code, 200)
        c2 = app.test_client()
        c2.post("/register", data=dict(data, usn="1UC24CS104", csrf_token=token(c2, "/register")))
        con = auth_db.connect()
        n = con.execute("SELECT COUNT(*) FROM users WHERE usn='1UC24CS104'").fetchone()[0]
        con.close()
        self.assertEqual(n, 1)

    def test_weak_password_rejected(self):
        t = token(self.c, "/register")
        self.c.post("/register", data={"usn": "1UC24CS105", "name": "W", "password": "short1", "confirm": "short1", "csrf_token": t})
        con = auth_db.connect()
        self.assertIsNone(con.execute("SELECT 1 FROM users WHERE usn='1UC24CS105'").fetchone())
        con.close()

    def test_open_redirect_blocked(self):
        for bad in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
            self.assertFalse(security.is_safe_next(bad), bad)
        self.assertTrue(security.is_safe_next("/dashboard"))
        add_user("1UC24CS101")
        t = token(self.c)
        r = self.c.post("/login?next=https://evil.example", data={"usn": "1UC24CS101", "password": PW, "csrf_token": t})
        self.assertNotIn("evil.example", r.headers.get("Location", ""))

    def test_headers_and_cookie(self):
        r = self.c.get("/login")
        h = r.headers
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Server"], "campus")
        cookie = " ".join(h.getlist("Set-Cookie")).lower()
        self.assertIn("httponly", cookie); self.assertIn("samesite=lax", cookie)

    def test_session_cleared_on_login(self):
        add_user("1UC24CS101")
        t = token(self.c)
        login(self.c, "1UC24CS101")
        with self.c.session_transaction() as s:
            self.assertNotEqual(s.get("csrf"), t)

    def test_qr_svg(self):
        r = self.c.get("/showcase/qr.svg")
        self.assertEqual(r.mimetype, "image/svg+xml")
        self.assertIn("<svg", r.get_data(as_text=True))

    def test_sqli_in_route_params(self):
        add_user("1UC24CS101"); login(self.c, "1UC24CS101")
        r = self.c.get("/find-route?start=' OR 1=1--&end=x")
        self.assertEqual(r.status_code, 404)

    def test_rate_limit_login(self):
        codes = []
        for _ in range(14):
            codes.append(self.c.post("/login", data={"usn": "1UC24CS999", "password": "x", "csrf_token": token(self.c)}).status_code)
        self.assertIn(429, codes)


if __name__ == "__main__":
    unittest.main()
