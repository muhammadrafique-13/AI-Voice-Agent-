"""Test fixtures.

Each test session runs against a throwaway SQLite file. DATABASE_URL is set *before*
importing the app, because config is read once at import time.
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="test_patients_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.replace(os.sep, '/')}"
os.environ["VAPI_SERVER_SECRET"] = "test-secret"
os.environ["APP_ENV"] = "test"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def valid_patient():
    """A complete, valid payload. Tests mutate copies of this."""
    return {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "03/05/1985",
        "sex": "Female",
        "phone_number": "(512) 555-0142",
        "address_line_1": "412 Oak Street",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78704",
    }
