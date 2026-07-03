"""Shared fixtures: each test gets a fresh SQLite DB and a fresh app instance."""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_ADMIN_USER = "admin"
TEST_ADMIN_PASSWORD = "testpass123"
TEST_POS_API_KEY = "test-pos-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", TEST_ADMIN_USER)
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("LOTSPOT_POS_API_KEY", TEST_POS_API_KEY)

    from app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def sample_product(client, admin_headers):
    resp = client.post(
        "/api/products",
        json={"sku": "COKE-330", "name": "Coca-Cola 330ml", "qty": 24, "price_cents": 250},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]
