"""Integration tests for the REST API.

Covers the API standards the brief names explicitly: status codes, the response
envelope, server-side validation, and soft delete.
"""
from datetime import date, timedelta


def _create(client, payload):
    return client.post("/patients", json=payload)


# --- Envelope + status codes ----------------------------------------------
def test_create_returns_201_with_envelope(client, valid_patient):
    resp = _create(client, valid_patient)
    assert resp.status_code == 201

    body = resp.json()
    assert set(body) == {"data", "error"}
    assert body["error"] is None
    assert body["data"]["patient_id"]
    assert resp.headers["Location"].endswith(body["data"]["patient_id"])


def test_values_are_normalized_on_write(client, valid_patient):
    """Input "(512) 555-0142" is stored canonically and returned in display form."""
    body = _create(client, valid_patient).json()["data"]
    assert body["phone_number"] == "(512) 555-0142"
    assert body["date_of_birth"] == "03/05/1985"
    assert body["state"] == "TX"
    assert body["preferred_language"] == "English"  # defaulted


def test_auto_fields_are_generated(client, valid_patient):
    body = _create(client, valid_patient).json()["data"]
    assert len(body["patient_id"]) == 36
    assert body["created_at"] and body["updated_at"]
    assert body["deleted_at"] is None


def test_missing_required_field_is_422(client, valid_patient):
    payload = dict(valid_patient)
    del payload["zip_code"]
    resp = _create(client, payload)
    assert resp.status_code == 422
    assert resp.json()["data"] is None
    assert "zip_code" in resp.json()["error"]["fields"]


def test_future_date_of_birth_is_422(client, valid_patient):
    payload = dict(valid_patient, date_of_birth=(date.today() + timedelta(days=1)).strftime("%m/%d/%Y"))
    resp = _create(client, payload)
    assert resp.status_code == 422
    assert "future" in resp.json()["error"]["fields"]["date_of_birth"].lower()


def test_short_phone_number_is_422(client, valid_patient):
    resp = _create(client, dict(valid_patient, phone_number="555"))
    assert resp.status_code == 422
    assert "phone_number" in resp.json()["error"]["fields"]


def test_malformed_json_is_400_not_422(client):
    """The brief distinguishes 400 from 422; FastAPI returns 422 for both by default."""
    resp = client.post(
        "/patients", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "malformed_json"


def test_unknown_id_is_404(client):
    resp = client.get("/patients/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --- Read + filters --------------------------------------------------------
def test_get_by_id(client, valid_patient):
    created = _create(client, valid_patient).json()["data"]
    resp = client.get(f"/patients/{created['patient_id']}")
    assert resp.status_code == 200
    assert resp.json()["data"]["patient_id"] == created["patient_id"]


def test_filter_by_last_name_is_case_insensitive(client, valid_patient):
    _create(client, dict(valid_patient, last_name="Okonkwo", phone_number="5125550777"))
    resp = client.get("/patients", params={"last_name": "okonkwo"})
    assert resp.status_code == 200
    names = [p["last_name"] for p in resp.json()["data"]["patients"]]
    assert names and all(n == "Okonkwo" for n in names)


def test_filter_by_phone_accepts_any_format(client, valid_patient):
    """?phone_number=(512) 555-0888 must match the stored bare digits."""
    _create(client, dict(valid_patient, phone_number="5125550888"))
    resp = client.get("/patients", params={"phone_number": "(512) 555-0888"})
    assert resp.json()["data"]["count"] == 1


def test_filter_by_dob_accepts_both_formats(client, valid_patient):
    _create(client, dict(valid_patient, date_of_birth="07/04/1976", phone_number="5125550999"))
    for fmt in ("07/04/1976", "1976-07-04"):
        resp = client.get("/patients", params={"date_of_birth": fmt})
        assert any(
            p["date_of_birth"] == "07/04/1976" for p in resp.json()["data"]["patients"]
        ), fmt


def test_bad_filter_value_is_400(client):
    resp = client.get("/patients", params={"phone_number": "abc"})
    assert resp.status_code == 400


# --- Update ---------------------------------------------------------------
def test_partial_update_touches_only_sent_fields(client, valid_patient):
    created = _create(client, dict(valid_patient, phone_number="5125551111")).json()["data"]
    resp = client.put(
        f"/patients/{created['patient_id']}", json={"city": "Dallas", "email": "j@example.com"}
    )
    assert resp.status_code == 200

    updated = resp.json()["data"]
    assert updated["city"] == "Dallas"
    assert updated["email"] == "j@example.com"
    assert updated["last_name"] == created["last_name"]  # untouched
    assert updated["updated_at"] >= created["updated_at"]


def test_update_revalidates(client, valid_patient):
    created = _create(client, dict(valid_patient, phone_number="5125552222")).json()["data"]
    resp = client.put(f"/patients/{created['patient_id']}", json={"state": "Ontario"})
    assert resp.status_code == 422


def test_update_unknown_id_is_404(client):
    resp = client.put(
        "/patients/00000000-0000-0000-0000-000000000000", json={"city": "Austin"}
    )
    assert resp.status_code == 404


# --- Soft delete ----------------------------------------------------------
def test_delete_is_soft(client, valid_patient):
    created = _create(client, dict(valid_patient, phone_number="5125553333")).json()["data"]
    pid = created["patient_id"]

    assert client.delete(f"/patients/{pid}").status_code == 200

    # Hidden from normal reads...
    assert client.get(f"/patients/{pid}").status_code == 404
    listed = client.get("/patients").json()["data"]["patients"]
    assert pid not in [p["patient_id"] for p in listed]

    # ...but the row is still there.
    all_rows = client.get("/patients", params={"include_deleted": "true"}).json()
    match = [p for p in all_rows["data"]["patients"] if p["patient_id"] == pid]
    assert match and match[0]["deleted_at"] is not None


def test_second_delete_is_404(client, valid_patient):
    created = _create(client, dict(valid_patient, phone_number="5125554444")).json()["data"]
    client.delete(f"/patients/{created['patient_id']}")
    assert client.delete(f"/patients/{created['patient_id']}").status_code == 404


# --- Ops ------------------------------------------------------------------
def test_health_reports_database_and_config(client):
    body = client.get("/health").json()["data"]
    assert body["status"] == "ok"
    assert body["database"]["reachable"] is True
    assert "VAPI_SERVER_SECRET_set" in body["config"]


def test_dashboard_renders(client, valid_patient):
    _create(client, dict(valid_patient, phone_number="5125555555"))
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Patient Registration Dashboard" in resp.text
