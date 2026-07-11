from tests.conftest import TEST_EMPLOYEE_PIN


def _clock_in(client, headers, employee, pin=TEST_EMPLOYEE_PIN):
    return client.post(
        "/api/timeclock/clock-in",
        json={"employee_id": employee["id"], "pin": pin},
        headers=headers,
    )


def _clock_out(client, headers, employee, pin=TEST_EMPLOYEE_PIN):
    return client.post(
        "/api/timeclock/clock-out",
        json={"employee_id": employee["id"], "pin": pin},
        headers=headers,
    )


def test_create_employee_hides_pin_hash(client, admin_headers):
    resp = client.post(
        "/api/employees",
        json={"name": "Alex Chen", "pin": "1234"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    employee = resp.json()["data"]
    assert employee["name"] == "Alex Chen"
    assert "pin_hash" not in employee
    assert "pin" not in employee


def test_create_employee_requires_auth(client):
    resp = client.post("/api/employees", json={"name": "Alex Chen", "pin": "1234"})
    assert resp.status_code == 401


def test_create_employee_rejects_non_numeric_pin(client, admin_headers):
    resp = client.post(
        "/api/employees",
        json={"name": "Alex Chen", "pin": "abcd"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_list_employees(client, admin_headers, sample_employee):
    resp = client.get("/api/employees", headers=admin_headers)
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["data"]]
    assert sample_employee["name"] in names


def test_clock_in_opens_a_shift(client, admin_headers, sample_employee):
    resp = _clock_in(client, admin_headers, sample_employee)
    assert resp.status_code == 201, resp.text
    entry = resp.json()["data"]
    assert entry["employee_id"] == sample_employee["id"]
    assert entry["employee_name"] == sample_employee["name"]
    assert entry["clock_in_at"]
    assert entry["clock_out_at"] is None


def test_clock_in_wrong_pin_rejected(client, admin_headers, sample_employee):
    resp = _clock_in(client, admin_headers, sample_employee, pin="0000")
    assert resp.status_code == 401


def test_clock_in_unknown_employee_404(client, admin_headers):
    resp = client.post(
        "/api/timeclock/clock-in",
        json={"employee_id": 999, "pin": TEST_EMPLOYEE_PIN},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_clock_in_requires_auth(client, sample_employee):
    resp = client.post(
        "/api/timeclock/clock-in",
        json={"employee_id": sample_employee["id"], "pin": TEST_EMPLOYEE_PIN},
    )
    assert resp.status_code == 401


def test_double_clock_in_rejected(client, admin_headers, sample_employee):
    first = _clock_in(client, admin_headers, sample_employee)
    assert first.status_code == 201

    second = _clock_in(client, admin_headers, sample_employee)
    assert second.status_code == 409
    assert sample_employee["name"] in second.json()["error"]


def test_clock_out_closes_the_shift_and_reports_duration(
    client, admin_headers, sample_employee, monkeypatch
):
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T09:00:00")
    resp = _clock_in(client, admin_headers, sample_employee)
    assert resp.status_code == 201

    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T17:30:00")
    resp = _clock_out(client, admin_headers, sample_employee)
    assert resp.status_code == 200, resp.text
    entry = resp.json()["data"]
    assert entry["clock_in_at"] == "2026-07-01T09:00:00"
    assert entry["clock_out_at"] == "2026-07-01T17:30:00"
    assert entry["duration_minutes"] == 510


def test_clock_out_without_open_shift_rejected(client, admin_headers, sample_employee):
    resp = _clock_out(client, admin_headers, sample_employee)
    assert resp.status_code == 409


def test_clock_out_wrong_pin_rejected(client, admin_headers, sample_employee):
    _clock_in(client, admin_headers, sample_employee)
    resp = _clock_out(client, admin_headers, sample_employee, pin="0000")
    assert resp.status_code == 401


def test_clock_out_requires_auth(client, sample_employee):
    resp = client.post(
        "/api/timeclock/clock-out",
        json={"employee_id": sample_employee["id"], "pin": TEST_EMPLOYEE_PIN},
    )
    assert resp.status_code == 401


def test_clock_in_again_after_clock_out_succeeds(client, admin_headers, sample_employee):
    assert _clock_in(client, admin_headers, sample_employee).status_code == 201
    assert _clock_out(client, admin_headers, sample_employee).status_code == 200
    assert _clock_in(client, admin_headers, sample_employee).status_code == 201


def test_status_for_employee_with_no_open_shift(client, admin_headers, sample_employee):
    resp = client.get(
        "/api/timeclock/status",
        params={"employee_id": sample_employee["id"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["employee_id"] == sample_employee["id"]
    assert data["open_shift"] is None


def test_status_for_employee_with_open_shift(client, admin_headers, sample_employee):
    _clock_in(client, admin_headers, sample_employee)
    resp = client.get(
        "/api/timeclock/status",
        params={"employee_id": sample_employee["id"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["open_shift"]["employee_id"] == sample_employee["id"]
    assert data["open_shift"]["clock_out_at"] is None


def test_status_without_employee_id_lists_all_open_shifts(client, admin_headers, sample_employee):
    _clock_in(client, admin_headers, sample_employee)
    resp = client.get("/api/timeclock/status", headers=admin_headers)
    assert resp.status_code == 200
    open_shifts = resp.json()["data"]["open_shifts"]
    assert len(open_shifts) == 1
    assert open_shifts[0]["employee_name"] == sample_employee["name"]


def test_status_requires_auth(client):
    assert client.get("/api/timeclock/status").status_code == 401


def test_history_query_returns_closed_and_open_shifts(
    client, admin_headers, sample_employee, monkeypatch
):
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T09:00:00")
    _clock_in(client, admin_headers, sample_employee)
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T17:00:00")
    _clock_out(client, admin_headers, sample_employee)

    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-02T09:00:00")
    _clock_in(client, admin_headers, sample_employee)

    resp = client.get("/api/timeclock/history", headers=admin_headers)
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert len(entries) == 2
    # newest first
    assert entries[0]["clock_in_at"] == "2026-07-02T09:00:00"
    assert entries[0]["clock_out_at"] is None
    assert entries[0]["duration_minutes"] is None
    assert entries[1]["duration_minutes"] == 480


def test_history_filters_by_employee(client, admin_headers, sample_employee):
    other = client.post(
        "/api/employees",
        json={"name": "Morgan Lee", "pin": "5678"},
        headers=admin_headers,
    ).json()["data"]

    _clock_in(client, admin_headers, sample_employee)
    _clock_in(client, admin_headers, other, pin="5678")

    resp = client.get(
        "/api/timeclock/history",
        params={"employee_id": sample_employee["id"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["employee_id"] == sample_employee["id"]


def test_history_filters_by_date_range(client, admin_headers, sample_employee, monkeypatch):
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T09:00:00")
    _clock_in(client, admin_headers, sample_employee)
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-07-01T17:00:00")
    _clock_out(client, admin_headers, sample_employee)

    monkeypatch.setattr("db.local_now_iso", lambda: "2026-08-01T09:00:00")
    _clock_in(client, admin_headers, sample_employee)
    monkeypatch.setattr("db.local_now_iso", lambda: "2026-08-01T17:00:00")
    _clock_out(client, admin_headers, sample_employee)

    resp = client.get(
        "/api/timeclock/history",
        params={"start": "2026-07-01", "end": "2026-07-31"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    entries = resp.json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["clock_in_at"] == "2026-07-01T09:00:00"


def test_history_rejects_partial_date_range(client, admin_headers):
    resp = client.get(
        "/api/timeclock/history",
        params={"start": "2026-07-01"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_history_requires_auth(client):
    assert client.get("/api/timeclock/history").status_code == 401
