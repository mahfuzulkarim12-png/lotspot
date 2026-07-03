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
