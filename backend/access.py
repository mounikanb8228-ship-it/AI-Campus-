"""One place that decides who is asking: a visitor or a signed-in member.

visitor = anyone not signed in (for example someone who scanned the QR code)
member  = an ACTIVE student, faculty or admin account
Every route that serves both groups must call tier() and never trust anything the browser sends.
"""
import os

import time

from flask import request, session

from .security import ABSOLUTE_TIMEOUT_SECONDS, current_user

VISITOR, MEMBER = "visitor", "member"


def tier():
    user = current_user()
    if user is None or user["status"] != "active":
        return VISITOR
    if time.time() - session.get("login_at", 0) > ABSOLUTE_TIMEOUT_SECONDS:
        return VISITOR  # the sign-in is too old, so treat the person as a visitor again
    return MEMBER


def audiences(t):
    """Which document audiences this tier may read in the knowledge base."""
    return ("public", "members") if t == MEMBER else ("public",)


def site_url():
    """Address that goes into QR codes. Set SITE_URL in .env (for a demo: http://<your-PC-LAN-IP>:5000)."""
    return (os.environ.get("SITE_URL") or request.host_url).rstrip("/")
