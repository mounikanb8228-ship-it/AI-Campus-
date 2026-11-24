"""Shared test set-up: temporary databases, so your real campus.db and auth.db are never touched."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.setdefault("CAMPUS_DB", os.path.join(_tmp, "campus.db"))
os.environ.setdefault("AUTH_DB", os.path.join(_tmp, "auth.db"))
os.environ.setdefault("SECRET_KEY", "test-key")
os.environ["LLM_PROVIDER"] = "none"

from database import init_db  # noqa: E402

init_db.main()

from backend import auth_db, security  # noqa: E402
from backend.app import app  # noqa: E402,F401
