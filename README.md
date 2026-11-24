# Smart Campus Assistant: visitors and members

A Flask website for **VTU TechVista Campus** (sample data) with two kinds of users.

| | Visitor (scans the QR code, no account) | Member (student, faculty) | Admin |
|---|---|---|---|
| Opens | `/visit`, a place's QR page | `/dashboard`, `/directory` | `/admin/...` |
| Map and routes | public places only, with turn-by-turn steps | every building | every building |
| Assistant (RAG) | answers from **public** documents only | public + **members** documents, timetable, faculty, rooms | same as member |
| Timetable, faculty, rooms | refused | yes | yes |
| Extra | visitor pass (check-in) | | knowledge base, places, QR sheet, visitors, usage, security log |

## How the two groups are kept apart
1. `backend/access.py: tier()` is the **only** place that decides visitor or member, from the server-side session (an active, recent sign-in). Nothing the browser sends can change it.
2. Visitor data paths never load private data: `places.buildings(public_only=True)` filters in SQL, so a private building cannot appear in a list, a route or even be a waypoint; `knowledge` builds a **separate index from `public` documents only**.
3. Everything else is **deny by default** in `security.py`. Only `/`, `/visit...`, `/api/*`, login and register are open, and each of those calls `tier()`.
4. The test suite and the scanner both try to make a visitor leak member data.

## Setup
```
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                                 # set SITE_URL
python -m database.init_db                             # creates or UPGRADES campus.db (keeps your data)
python -m database.create_admin                        # first admin
python -m backend.app                                  # http://127.0.0.1:5000
```
If you already have a `campus.db`, `init_db` only adds the new columns and the documents table. Your places start as **members only**; open Admin > Places and tick the ones visitors may see.

## Using it
* **QR codes:** open `/showcase` and print it. One code is for visitors (`/visit?s=qr`), one for students (`/login`). Admin > QR codes prints a code for every building; scanning one shows that place and directions from there.
* **Knowledge base (RAG):** Admin > Knowledge base. Add notices and rules and choose **Public** or **Members**. The assistant uses them immediately.
* **Members:** Admin > USN list creates one-time activation codes, or approve requests in Admin > Members.
* **Visitor pass:** a visitor fills a short form and gets a code and QR valid for 8 hours. Admin > Visitors verifies a code.
* **LLM (optional):** set `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` in `.env`. The model receives only the passages that the person's tier may read. If the call fails, the assistant answers from the passages.
* **Scanner:** `python scanner/security_scan.py --url http://127.0.0.1:5000 --active` (only on sites you own). It also checks that visitors cannot see private places or member answers. Use `--private-words "Name1,Name2"` for your own data.
* **Tests:** `python -m unittest discover -s tests -t .` (uses temporary databases).

## Advanced features
RAG with BM25 ranking, chunking and sources shown; a small tool-using agent (routes, timetable, faculty, rooms, general questions); turn-by-turn walking directions with compass bearings; place aliases (canteen = Food Court); per-building QR codes; admin-managed knowledge base; visitor passes; usage statistics without storing questions; rate limits on chat and check-in; CSRF, hashed passwords, lockout, security headers, audit log.

## Before real use
HTTPS (`COOKIE_SECURE=1`, `TRUST_PROXY=1` behind a proxy), a production server such as gunicorn or waitress, never `debug=True`, keep `.env`, `.secret_key` and `*.db` out of git. The rate limiter is per process. Leaflet loads from unpkg.com. Sample documents, people and timetable are fictional.
