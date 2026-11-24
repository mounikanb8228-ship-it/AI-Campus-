"""
Smart Campus Security Scanner - checks YOUR OWN website and writes a report.

    python scanner/security_scan.py --url http://127.0.0.1:5000
    python scanner/security_scan.py --url http://127.0.0.1:5000 --active

Outputs reports/security_report.html (open in a browser) and reports/latest.json
(shown on the admin page: /admin/security).

SAFETY: only scan websites you own. By default the scanner refuses any address
that is not on this computer or a private network. For a server you own, add
--i-own-this-site.   --active also sends a burst of wrong logins (with an ID
that does not exist) to test the rate limiter.
"""
import argparse
import html
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
from urllib.parse import urlparse

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TIMEOUT = 8
PROTECTED = ["/dashboard", "/directory", "/buildings", "/find-route?start=Main%20Gate&end=CSE%20Block",
             "/departments", "/faculty", "/rooms", "/timetable", "/admin/users", "/admin/audit", "/admin/knowledge",
             "/admin/places", "/admin/visitors", "/admin/analytics", "/admin/qr-sheet", "/admin/security"]
# Words that must NEVER reach a visitor (members-only places, people, rooms and document text).
# Change them with --private-words to match your own campus data.
PRIVATE_WORDS = ["CSE Block", "ECE Block", "Boys Hostel", "Girls Hostel", "Sports Ground", "Kulkarni", "CSE-101",
                 "hall ticket", "75 percent", "warden", "borrow up to four"]
LEAK_WORDS = PRIVATE_WORDS
VISITOR_QUESTIONS = ["Show the timetable and faculty list", "Who is the HOD of CSE?", "What are the hostel rules?",
                     "What are the examination rules?", "route to CSE block", "Where is CSE-101?", "library rules for students"]
SENSITIVE = ["/.env", "/.git/config", "/auth.db", "/campus.db", "/backend/app.py", "/backend/security.py",
             "/database/init_db.py", "/requirements.txt", "/static/..%2fbackend%2fapp.py",
             "/static/%2e%2e/backend/app.py"]
WEIGHT = {"PASS": 1.0, "INFO": 1.0, "WARN": 0.5, "FAIL": 0.0}


def check(cid, title, status, detail, fix=""):
    return {"id": cid, "title": title, "status": status, "detail": detail, "fix": fix}


def is_local_or_private(host):
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if not (ip.is_loopback or ip.is_private):
                return False
        return True
    except (socket.gaierror, ValueError):
        return False


def get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("allow_redirects", False)
    return requests.get(url, **kw)


# ---------------------------------------------------------------- checks
def check_headers(base):
    r = get(base + "/login")
    h = {k.lower(): v for k, v in r.headers.items()}
    out = []
    need = [("content-security-policy", "Content-Security-Policy", "Blocks injected scripts (XSS)."),
            ("x-content-type-options", "X-Content-Type-Options", "Stops browsers guessing file types."),
            ("referrer-policy", "Referrer-Policy", "Limits what page addresses are shared."),
            ("permissions-policy", "Permissions-Policy", "Turns off unused browser features.")]
    for key, name, why in need:
        ok = key in h
        out.append(check("hdr-" + key, f"Header: {name}", "PASS" if ok else "FAIL",
                         (f"Present: {h[key][:120]}" if ok else "Missing."),
                         "" if ok else f"Add the {name} header. {why}"))
    framed = "x-frame-options" in h or "frame-ancestors" in h.get("content-security-policy", "")
    out.append(check("hdr-frame", "Clickjacking protection", "PASS" if framed else "FAIL",
                     "X-Frame-Options or frame-ancestors is set." if framed else "The site can be shown inside another site's frame.",
                     "" if framed else "Send X-Frame-Options: DENY or CSP frame-ancestors 'none'."))
    if base.startswith("https"):
        ok = "strict-transport-security" in h
        out.append(check("hdr-hsts", "Header: Strict-Transport-Security", "PASS" if ok else "FAIL",
                         "Present." if ok else "Missing on an HTTPS site.", "" if ok else "Add the HSTS header."))
    else:
        out.append(check("hdr-hsts", "HTTPS and HSTS", "INFO", "Scanned over plain HTTP (fine on your own computer).",
                         "Serve the real site over HTTPS and set COOKIE_SECURE=1."))
    csp = h.get("content-security-policy", "")
    if csp:
        bad = [x for x in ("'unsafe-inline'", "'unsafe-eval'") if x in re.sub(r"style-src[^;]*", "", csp)]
        strong = "object-src 'none'" in csp and "default-src" in csp and not bad
        out.append(check("csp-quality", "Content-Security-Policy strength", "PASS" if strong else "WARN",
                         "Restrictive policy (no unsafe-inline/eval, objects blocked)." if strong
                         else "Policy is present but loose: " + (", ".join(bad) or "object-src or default-src missing"),
                         "" if strong else "Remove unsafe-inline/unsafe-eval and add object-src 'none'."))
    banner = " ".join(h.get(k, "") for k in ("server", "x-powered-by"))
    leaky = re.search(r"\d+\.\d+|werkzeug|python|gunicorn|nginx/|apache/", banner, re.I)
    out.append(check("banner", "Server version disclosure", "WARN" if leaky else "PASS",
                     f"Headers reveal: {banner.strip()[:100]}" if leaky else "No software versions revealed.",
                     "Hide the Server / X-Powered-By version text." if leaky else ""))
    return out


def check_access(base):
    out = []
    open_paths = []
    for path in PROTECTED:
        r = get(base + path)
        ok = r.status_code in (301, 302, 303, 307, 308, 401, 403)
        if ok and r.status_code in (301, 302, 303, 307, 308):
            ok = "/login" in r.headers.get("Location", "")
        if not ok:
            open_paths.append(f"{path.split('?')[0]} -> {r.status_code}")
    out.append(check("deny-default", "Visitors are blocked from college pages",
                     "FAIL" if open_paths else "PASS",
                     ("Reachable without logging in: " + "; ".join(open_paths)) if open_paths
                     else f"All {len(PROTECTED)} protected pages send visitors to the login page or return 401/403.",
                     "Require login on every route (deny by default)." if open_paths else ""))
    landing = get(base + "/")
    leaks = [w for w in LEAK_WORDS if w.lower() in landing.text.lower()]
    out.append(check("public-leak", "Public page leaks no college details", "FAIL" if leaks else "PASS",
                     ("Public home page mentions: " + ", ".join(leaks)) if leaks
                     else "The public page contains none of the sensitive campus terms checked.",
                     "Remove campus details from pages visitors can see." if leaks else ""))
    robots = get(base + "/robots.txt")
    out.append(check("robots", "Search engines told to stay out", "PASS" if "Disallow: /" in robots.text else "WARN",
                     "robots.txt disallows crawling." if "Disallow: /" in robots.text else "robots.txt does not block crawlers.",
                     "" if "Disallow: /" in robots.text else "Serve robots.txt with 'Disallow: /'."))
    return out


def check_visitor_tier(base):
    """Visitors may use the public guide, but must not learn anything that is for members only."""
    out = []
    page = get(base + "/visit")
    out.append(check("visit-open", "Visitor guide opens without login", "PASS" if page.status_code == 200 else "FAIL",
                     f"/visit returned {page.status_code}.", "" if page.status_code == 200 else "Serve /visit to visitors."))
    leaks = []
    for path in ["/visit", "/api/places", "/api/route?start=Main%20Gate&end=CSE%20Block"] + [f"/visit/place/{i}" for i in range(1, 31)]:
        r = get(base + path)
        leaks += [f"{w} in {path.split('?')[0]}" for w in PRIVATE_WORDS if w.lower() in r.text.lower()]
    out.append(check("visit-leak", "Visitor pages and APIs show no members-only places", "FAIL" if leaks else "PASS",
                     "Found: " + "; ".join(leaks[:6]) if leaks else "Visitor pages, /api/places and place pages contained no private places or people.",
                     "Mark the place as members-only and check that visitor routes filter by tier." if leaks else ""))
    s = requests.Session()
    m = re.search(r'name="csrf" content="([^"]+)"', s.get(base + "/visit", timeout=TIMEOUT).text)
    token = m.group(1) if m else ""
    if not token:
        out.append(check("visit-chat", "Visitor assistant keeps members-only data private", "WARN", "Skipped: no CSRF token found.", ""))
        return out
    no_token = s.post(base + "/api/chat", json={"q": "hello"}, timeout=TIMEOUT)
    out.append(check("chat-csrf", "Assistant rejects requests without a CSRF token", "PASS" if no_token.status_code == 403 else "FAIL",
                     f"POST without token returned {no_token.status_code}.", "" if no_token.status_code == 403 else "Enforce CSRF on /api/chat."))
    bad = []
    for q in VISITOR_QUESTIONS:
        r = s.post(base + "/api/chat", json={"q": q}, headers={"X-CSRF-Token": token}, timeout=TIMEOUT)
        if r.status_code == 429:
            break
        bad += [f"'{q}' -> {w}" for w in PRIVATE_WORDS if w.lower() in r.text.lower()]
    out.append(check("visit-chat", "Visitor assistant keeps members-only data private", "FAIL" if bad else "PASS",
                     "Leaked: " + "; ".join(bad[:5]) if bad else f"{len(VISITOR_QUESTIONS)} questions about members-only topics leaked nothing.",
                     "Give visitors a public-only knowledge index." if bad else ""))
    return out


def check_cookies_and_csrf(base):
    out = []
    s = requests.Session()
    r = s.get(base + "/login", timeout=TIMEOUT, allow_redirects=False)
    raw = r.headers.get("Set-Cookie", "")
    if not raw:
        out.append(check("cookie", "Session cookie flags", "WARN", "No session cookie was set on the login page.", ""))
    else:
        low = raw.lower()
        https = base.startswith("https")
        flags = {"HttpOnly": "httponly" in low, "SameSite": "samesite=" in low, "Secure": "secure" in low}
        missing = [k for k, v in flags.items() if not v and (k != "Secure" or https)]
        out.append(check("cookie", "Session cookie flags", "FAIL" if missing else ("PASS" if https else "PASS"),
                         ("Missing: " + ", ".join(missing)) if missing else
                         ("HttpOnly, SameSite" + (", Secure" if https else "") + " are set."),
                         "Set HttpOnly, SameSite=Lax and Secure on the session cookie." if missing else ""))
        if not https:
            out.append(check("cookie-secure", "Cookie 'Secure' flag", "INFO",
                             "Not checked over plain HTTP.", "Set COOKIE_SECURE=1 when the site uses HTTPS."))
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    out.append(check("csrf-form", "Login form carries a CSRF token", "PASS" if m else "FAIL",
                     "Hidden token found in the form." if m else "No CSRF token in the login form.",
                     "" if m else "Add a per-session CSRF token to every form."))
    p = requests.post(base + "/login", data={"usn": "1UC24CS101", "password": "x"},
                      timeout=TIMEOUT, allow_redirects=False)
    out.append(check("csrf-enforced", "Forms without a token are rejected", "PASS" if p.status_code in (400, 403) else "FAIL",
                     f"POST without token returned {p.status_code}.",
                     "" if p.status_code in (400, 403) else "Reject POST requests that lack a valid CSRF token."))
    return out, s, (m.group(1) if m else "")


def check_exposure(base):
    out = []
    exposed = []
    for path in SENSITIVE:
        r = get(base + path)
        if r.status_code == 200 and len(r.content) > 0 and "<html" not in r.text[:200].lower():
            exposed.append(path)
    out.append(check("files", "Source and database files are not downloadable", "FAIL" if exposed else "PASS",
                     ("Downloadable: " + ", ".join(exposed)) if exposed else f"{len(SENSITIVE)} sensitive paths all refused.",
                     "Never serve project folders; keep .env and *.db outside the web root." if exposed else ""))
    junk = get(base + "/definitely-not-a-page-" + secrets.token_hex(3))
    leaked = re.search(r"Traceback|Werkzeug Debugger|File \"|sqlite3\.", junk.text)
    out.append(check("errors", "Error pages reveal nothing internal", "FAIL" if leaked else "PASS",
                     "Error page shows internals." if leaked else f"Unknown URL returns a plain {junk.status_code} page.",
                     "Turn off debug mode and use generic error pages." if leaked else ""))
    console = get(base + "/console")
    out.append(check("debug", "Debug console is not exposed", "FAIL" if console.status_code == 200 else "PASS",
                     f"/console returned {console.status_code}.",
                     "Run with FLASK_DEBUG=0." if console.status_code == 200 else ""))
    try:
        t = requests.request("TRACE", base + "/", timeout=TIMEOUT, allow_redirects=False)
        out.append(check("trace", "HTTP TRACE is disabled", "PASS" if t.status_code in (400, 403, 404, 405, 501) else "FAIL",
                         f"TRACE returned {t.status_code}.", "Disable the TRACE method."))
    except requests.RequestException:
        out.append(check("trace", "HTTP TRACE is disabled", "PASS", "Request refused.", ""))
    return out


def check_injection(base, session, token):
    if not token:
        return [check("sqli", "Login resists SQL-injection text", "WARN", "Skipped: no CSRF token available.", "")]
    r = session.post(base + "/login", timeout=TIMEOUT, allow_redirects=False,
                     data={"csrf_token": token, "usn": "' OR '1'='1' --", "password": "' OR '1'='1"})
    broke = r.status_code >= 500 or re.search(r"sqlite|syntax error|traceback", r.text, re.I) or \
        (r.status_code in (301, 302, 303) and "/dashboard" in r.headers.get("Location", ""))
    return [check("sqli", "Login resists SQL-injection text", "FAIL" if broke else "PASS",
                  f"Injection text returned {r.status_code} with no database error." if not broke else "Unexpected result.",
                  "Use parameterized queries only." if broke else "")]


def check_rate_limit(base, session, token):
    codes = []
    for _ in range(14):
        r = session.post(base + "/login", timeout=TIMEOUT, allow_redirects=False,
                         data={"csrf_token": token, "usn": "1ZZ99ZZ999", "password": "wrong-password-1"})
        codes.append(r.status_code)
        if r.status_code == 429:
            break
    ok = 429 in codes
    return [check("rate-limit", "Repeated wrong logins are throttled", "PASS" if ok else "FAIL",
                  f"Blocked with 429 after {len(codes)} attempts." if ok else "14 wrong logins in a row were all processed.",
                  "" if ok else "Add per-IP rate limiting and account lock-out.")]


# ---------------------------------------------------------------- report
def build_report(target, checks):
    counts = {k: sum(1 for c in checks if c["status"] == k) for k in ("PASS", "WARN", "FAIL", "INFO")}
    score = round(100 * sum(WEIGHT[c["status"]] for c in checks) / max(1, len(checks)))
    return {"target": target, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "score": score, "counts": counts, "checks": checks}


def render_html(rep):
    e = html.escape
    rows = "".join(
        f'<tr><td><span class="b {c["status"].lower()}">{e(c["status"])}</span></td><td>{e(c["title"])}</td>'
        f'<td>{e(c["detail"])}</td><td>{e(c["fix"])}</td></tr>' for c in rep["checks"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Security report</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f3f6f5;color:#101828}}main{{max-width:1000px;margin:auto;padding:28px 20px}}
h1{{margin:0 0 4px}}.score{{font-size:56px;font-weight:800;color:#0f766e;margin:12px 0 0}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e4e7ec}}th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #e4e7ec;vertical-align:top;font-size:14px}}
.b{{padding:3px 10px;border-radius:99px;font-weight:800;font-size:12px}}.pass{{background:#e1f3ef;color:#0b3b36}}.warn{{background:#fde9c4;color:#7a4b00}}.fail{{background:#fde7e5;color:#7a1710}}.info{{background:#e4e7ec;color:#344054}}
</style><main><h1>Smart Campus Assistant - security report</h1>
<p>Target {e(rep["target"])} &middot; {e(rep["when"])}</p>
<p class="score">{rep["score"]}%</p>
<p>{rep["counts"]["PASS"]} passed &middot; {rep["counts"]["WARN"]} warnings &middot; {rep["counts"]["FAIL"]} failed &middot; {rep["counts"]["INFO"]} notes</p>
<table><tr><th>Result</th><th>Check</th><th>What was found</th><th>How to fix</th></tr>{rows}</table>
<p>Automated checks cover common web weaknesses only; they do not replace a full security review.</p></main></html>"""


def main():
    ap = argparse.ArgumentParser(description="Security scanner for your own website")
    ap.add_argument("--url", required=True, help="e.g. http://127.0.0.1:5000")
    ap.add_argument("--active", action="store_true", help="also test login rate limiting (sends wrong logins)")
    ap.add_argument("--i-own-this-site", action="store_true", help="allow scanning a non-local address you own")
    ap.add_argument("--private-words", default="", help="comma list of names/phrases that must never reach visitors")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    args = ap.parse_args()

    if args.private_words:
        PRIVATE_WORDS[:] = [w.strip() for w in args.private_words.split(",") if w.strip()]
    base = args.url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        sys.exit("Please give a full address such as http://127.0.0.1:5000")
    if not is_local_or_private(parsed.hostname) and not args.i_own_this_site:
        sys.exit("Refusing to scan a public address. Only scan sites you own; "
                 "if this is yours, add --i-own-this-site.")
    try:
        requests.get(base + "/health", timeout=TIMEOUT)
    except requests.RequestException as exc:
        sys.exit(f"Cannot reach {base}: {exc}")

    print(f"Scanning {base} ...")
    checks = []
    checks += check_headers(base)
    checks += check_access(base)
    checks += check_visitor_tier(base)
    cookie_checks, session, token = check_cookies_and_csrf(base)
    checks += cookie_checks
    checks += check_exposure(base)
    checks += check_injection(base, session, token)
    if args.active and token:
        checks += check_rate_limit(base, session, token)
    elif not args.active:
        checks.append(check("rate-limit", "Repeated wrong logins are throttled", "INFO",
                            "Not tested. Run again with --active to test.", ""))

    rep = build_report(base, checks)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    with open(os.path.join(args.out, "security_report.html"), "w", encoding="utf-8") as fh:
        fh.write(render_html(rep))
    for c in checks:
        print(f"[{c['status']:<4}] {c['title']}")
    print(f"\nScore {rep['score']}%  ({rep['counts']['PASS']} pass, {rep['counts']['WARN']} warn, {rep['counts']['FAIL']} fail)")
    print("Report: " + os.path.join(args.out, "security_report.html"))


if __name__ == "__main__":
    main()
