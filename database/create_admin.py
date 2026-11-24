"""Create the first admin account.  Run:  python -m database.create_admin"""
import getpass
import sys
import time

from werkzeug.security import generate_password_hash

from backend import auth_db
from backend.security import USN_RE, normalize_usn, password_problem


def main():
    auth_db.init_auth_db()
    usn = normalize_usn(input("Admin USN or staff ID (example 1UC24CS001): "))
    if not USN_RE.match(usn):
        sys.exit("That does not look like a valid USN / ID.")
    name = input("Full name: ").strip()
    password = getpass.getpass("Password (min 10 characters, letters and numbers): ")
    problem = password_problem(password, usn)
    if problem:
        sys.exit(problem)
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    con = auth_db.connect()
    if con.execute("SELECT 1 FROM users WHERE usn = ?", (usn,)).fetchone():
        sys.exit("That USN already has an account.")
    con.execute(
        "INSERT INTO users (usn, name, role, status, password_hash, created_at) VALUES (?, ?, 'admin', 'active', ?, ?)",
        (usn, name, generate_password_hash(password), time.time()),
    )
    con.commit()
    con.close()
    auth_db.audit("admin_created", usn=usn, detail="created from command line")
    print("Admin account created. Sign in at /login.")


if __name__ == "__main__":
    main()
