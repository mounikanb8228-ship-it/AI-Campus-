"""Create campus.db with SAMPLE data for the demo campus (VTU TechVista Campus).

Safe to run more than once: it only fills an empty database and never deletes anything.
Run:  python -m database.init_db
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMPUS_DB = os.environ.get("CAMPUS_DB", os.path.join(ROOT, "campus.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, category TEXT, lat REAL, lng REAL);
CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, audience TEXT NOT NULL DEFAULT 'members', body TEXT NOT NULL, updated REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS paths (a_id INTEGER, b_id INTEGER);
CREATE TABLE IF NOT EXISTS departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL, hod TEXT);
CREATE TABLE IF NOT EXISTS rooms (id INTEGER PRIMARY KEY, number TEXT NOT NULL, building_id INTEGER, floor INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS faculty (id INTEGER PRIMARY KEY, name TEXT NOT NULL, department_id INTEGER, designation TEXT, cabin TEXT);
CREATE TABLE IF NOT EXISTS timetable (id INTEGER PRIMARY KEY, semester INTEGER, day TEXT, start TEXT, end TEXT, subject TEXT, room_id INTEGER, faculty_id INTEGER);
"""

BUILDINGS = [
    (1, "Main Gate", "gate", 12.9700, 77.5930), (2, "Administration Block", "admin", 12.9708, 77.5938),
    (3, "CSE Block", "academic", 12.9716, 77.5950), (4, "ECE Block", "academic", 12.9720, 77.5958),
    (5, "Central Library", "academic", 12.9712, 77.5944), (6, "Central Auditorium", "events", 12.9725, 77.5942),
    (7, "Medical Center", "health", 12.9704, 77.5955), (8, "Sports Ground", "sports", 12.9730, 77.5960),
    (9, "Girls Hostel", "hostel", 12.9735, 77.5948), (10, "Boys Hostel", "hostel", 12.9732, 77.5970),
    (11, "Food Court", "food", 12.9712, 77.5956), (12, "Parking Area", "parking", 12.9698, 77.5942),
]
PATHS = [(1, 2), (1, 12), (2, 5), (2, 12), (5, 3), (5, 6), (5, 11), (3, 4), (3, 11), (4, 8), (4, 11),
         (11, 7), (6, 9), (9, 8), (8, 10), (9, 3), (7, 2)]
DEPARTMENTS = [(1, "Computer Science and Engineering", "Dr. S. Kulkarni"), (2, "Electronics and Communication", "Dr. M. Hegde")]
ROOMS = [(1, "CSE-101", 3, 1, "classroom"), (2, "CSE-102", 3, 1, "classroom"), (3, "CSE-103", 3, 1, "classroom"),
         (4, "CSE-204", 3, 2, "classroom"), (5, "CSE Lab 2", 3, 2, "lab"), (6, "ECE-105", 4, 1, "classroom")]
FACULTY = [(1, "Dr. S. Kulkarni", 1, "Professor and HOD", "CSE-208"), (2, "Prof. R. Nayak", 1, "Associate Professor", "CSE-212"),
           (3, "Prof. A. Shetty", 1, "Assistant Professor", "CSE-210"), (4, "Dr. M. Hegde", 2, "Professor and HOD", "ECE-105")]
TIMETABLE = [
    (1, 4, "Friday", "09:00", "10:00", "Operating Systems", 1, 1), (2, 4, "Friday", "10:00", "11:00", "Design and Analysis of Algorithms", 2, 2),
    (3, 4, "Friday", "11:15", "12:15", "Computer Networks", 3, 4), (4, 4, "Friday", "14:00", "15:00", "Database Management", 4, 3),
    (5, 4, "Friday", "15:15", "16:15", "Database Lab", 5, 3),
]

# Places that visitors may see and route to. Everything else is for students and staff only.
PUBLIC_INFO = {
    "Main Gate": "Entry point. Visitors register here and receive a visitor pass.",
    "Administration Block": "Principal's office, admissions and enquiry desk.",
    "Parking Area": "Visitor parking. Vehicles are not allowed inside the academic area.",
    "Medical Center": "First aid and emergency care.",
    "Food Court": "Canteen, open to visitors.",
    "Central Library": "Visitor reading desk on the ground floor (photo ID needed).",
    "Central Auditorium": "Events and seminars; open to visitors on event days.",
}

# SAMPLE documents for the RAG assistant. audience: public = visitors and members, members = signed-in members only.
DOCUMENTS = [
    ("About VTU TechVista Campus", "public",
     "VTU TechVista Campus is a fictional demonstration campus used for the Smart Campus Assistant project. It offers engineering programmes in Computer Science and Engineering and in Electronics and Communication.\n\nThe campus has an Administration Block, a Central Library, a Central Auditorium, a Medical Center, a Food Court and sports and hostel facilities."),
    ("Visiting hours and entry rules", "public",
     "Visitors are welcome from 9:30 AM to 4:30 PM, Monday to Saturday. Every visitor must register at the Main Gate and collect a visitor pass. A photo ID is required.\n\nVisitors must park at the Parking Area near the Main Gate. Vehicles are not allowed inside the academic area. Visitors may not enter classrooms, laboratories or hostels."),
    ("Admission enquiries", "public",
     "The admission enquiry desk is on the ground floor of the Administration Block. It is open from 10:00 AM to 4:00 PM on working days.\n\nParents should carry the student's mark sheets, a photo ID and passport size photographs. Fee and seat details are given by the admission office."),
    ("Facilities open to visitors", "public",
     "Visitors may use the Food Court, the visitor reading desk in the Central Library and the Medical Center in an emergency. The Central Auditorium is open to visitors during public events and seminars."),
    ("Contact the college office", "public",
     "For help, contact the college office in the Administration Block. Sample contact details: office@techvista.example and 080-0000-0000. Office hours are 9:30 AM to 4:30 PM, Monday to Saturday. The principal's office is on the first floor of the Administration Block."),
    ("Examination rules", "members",
     "Students must carry the college ID card and hall ticket to every examination. Mobile phones and smart watches are not allowed inside the examination hall. Students must be seated 15 minutes before the exam starts.\n\nA student needs at least 75 percent attendance to be eligible for the semester examination."),
    ("Library rules for members", "members",
     "Students may borrow up to four books for 14 days. The Central Library is open from 8:30 AM to 7:00 PM on working days. Late returns are charged a small fine per day. Digital resources are available on the campus network."),
    ("Hostel rules", "members",
     "Hostel entry closes at 9:00 PM. Visitors are not allowed inside the hostel rooms. Residents must inform the warden before leaving the campus overnight. The mess serves breakfast, lunch, evening snacks and dinner."),
    ("Academic calendar sample", "members",
     "Internal assessment tests are held twice each semester. The semester examinations begin after the last teaching day. Project phase reviews are held with the project guide as scheduled by the department."),
]


def ensure_schema(con):
    """Add the visitor-tier columns to an existing campus.db without touching its data."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(buildings)")}
    fresh = "is_public" not in cols
    if fresh:
        con.execute("ALTER TABLE buildings ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
    if "info" not in cols:
        con.execute("ALTER TABLE buildings ADD COLUMN info TEXT NOT NULL DEFAULT ''")
    if fresh:
        mark_public(con)


def mark_public(con):
    for name, info in PUBLIC_INFO.items():
        con.execute("UPDATE buildings SET is_public = 1, info = ? WHERE name = ?", (info, name))


def main():
    con = sqlite3.connect(CAMPUS_DB)
    con.executescript(SCHEMA)
    ensure_schema(con)
    if con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        import time
        con.executemany("INSERT INTO documents (title, audience, body, updated) VALUES (?,?,?,?)",
                        [(t, a, b, time.time()) for t, a, b in DOCUMENTS])
    if con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0:
        con.executemany("INSERT INTO buildings (id, name, category, lat, lng) VALUES (?,?,?,?,?)", BUILDINGS)
        con.executemany("INSERT INTO paths VALUES (?,?)", PATHS)
        con.executemany("INSERT INTO departments VALUES (?,?,?)", DEPARTMENTS)
        con.executemany("INSERT INTO rooms VALUES (?,?,?,?,?)", ROOMS)
        con.executemany("INSERT INTO faculty VALUES (?,?,?,?,?)", FACULTY)
        con.executemany("INSERT INTO timetable VALUES (?,?,?,?,?,?,?,?)", TIMETABLE)
        mark_public(con)  # the sample public places
        print("Sample campus data added to", CAMPUS_DB)
    else:
        print("campus.db already has data; nothing changed.")
    con.commit()
    con.close()
    try:
        os.chmod(CAMPUS_DB, 0o600)
    except OSError:
        pass


if __name__ == "__main__":
    main()
