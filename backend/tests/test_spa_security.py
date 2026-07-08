"""Regression tests for SPA fallback route security."""

from pathlib import Path
import sys
import os
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def client_with_spa(tmp_path, monkeypatch):
    """Create a test app with frontend/dist mocked to test SPA route."""
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("LOTSPOT_POS_API_KEY", "test-pos-key")

    # Create mock frontend/dist with index.html and assets
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>SPA</body></html>")

    assets_dir = dist_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('app');")

    # Patch FRONTEND_DIST to point to our mock dist
    from app import create_app
    from unittest.mock import patch

    with patch("app.FRONTEND_DIST", dist_dir):
        with TestClient(create_app()) as test_client:
            yield test_client


def test_spa_path_traversal_blocked(client_with_spa):
    """Path traversal attempts should be blocked from accessing files outside frontend/dist."""
    resp = client_with_spa.get("/../../../etc/passwd")
    assert resp.status_code == 200
    assert "SPA" in resp.text or resp.headers.get("content-type") == "text/html; charset=utf-8"


def test_spa_double_dot_blocked(client_with_spa):
    """Double-dot sequences should not allow traversal outside frontend/dist."""
    resp = client_with_spa.get("/../../backend/db.py")
    assert resp.status_code == 200
    assert "SPA" in resp.text or resp.headers.get("content-type") == "text/html; charset=utf-8"


def test_spa_url_encoded_traversal_blocked(client_with_spa):
    """URL-encoded traversal sequences should be blocked."""
    resp = client_with_spa.get("/%2e%2e/%2e%2e/etc/passwd")
    assert resp.status_code == 200
    assert "SPA" in resp.text or resp.headers.get("content-type") == "text/html; charset=utf-8"


def test_spa_valid_asset_served(client_with_spa):
    """Valid assets within dist should be served."""
    resp = client_with_spa.get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_spa_api_routes_blocked(client):
    """API routes should return 404, not fall through to SPA."""
    from conftest import client as base_client_fixture
    # Use the base client fixture which doesn't have SPA route
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["success"] is False
