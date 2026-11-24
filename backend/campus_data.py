"""Read helpers for campus.db (shared by the member pages and the visitor guide)."""
import sqlite3

from database.init_db import CAMPUS_DB


def rows(sql, args=()):
    con = sqlite3.connect(CAMPUS_DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def execute(sql, args=()):
    con = sqlite3.connect(CAMPUS_DB)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()
