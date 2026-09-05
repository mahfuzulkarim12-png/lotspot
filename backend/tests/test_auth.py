from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USER


def test_login_returns_token(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["token"]
    assert body["data"]["username"] == TEST_ADMIN_USER


def test_login_wrong_password_rejected(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["error"]


def test_login_unknown_user_rejected(client):
    resp = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "whatever123"}
    )
    assert resp.status_code == 401


def test_me_returns_current_user(client, admin_headers):
    resp = client.get("/api/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["username"] == TEST_ADMIN_USER


def test_me_without_token_rejected(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_garbage_token_rejected(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert resp.status_code == 401


def test_logout_invalidates_token(client, admin_headers):
    resp = client.post("/api/auth/logout", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/auth/me", headers=admin_headers)
    assert resp.status_code == 401


def _fail_login(client, username=TEST_ADMIN_USER, password="wrong-password"):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def test_login_locks_out_after_repeated_failures(client):
    from auth import LOGIN_MAX_ATTEMPTS

    for _ in range(LOGIN_MAX_ATTEMPTS - 1):
        resp = _fail_login(client)
        assert resp.status_code == 401

    resp = _fail_login(client)
    assert resp.status_code == 401

    # Locked out now, even with the correct password.
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 429


def test_login_lockout_is_per_ip_across_usernames(client):
    """TestClient always presents the same client IP, so hammering distinct
    usernames from one client proves the per-IP half of the throttle."""
    from auth import LOGIN_MAX_ATTEMPTS

    for i in range(LOGIN_MAX_ATTEMPTS):
        resp = _fail_login(client, username=f"ghost-{i}")
        assert resp.status_code == 401

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 429


def test_login_success_resets_the_failure_count(client):
    from auth import LOGIN_MAX_ATTEMPTS

    for _ in range(LOGIN_MAX_ATTEMPTS - 1):
        resp = _fail_login(client)
        assert resp.status_code == 401

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200

    # Fresh window: one failure short of the max must not lock the account out.
    for _ in range(LOGIN_MAX_ATTEMPTS - 1):
        resp = _fail_login(client)
        assert resp.status_code == 401

    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
