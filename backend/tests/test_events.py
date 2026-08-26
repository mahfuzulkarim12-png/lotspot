"""Real-time layer tests.

NOTE: httpx's TestClient buffers entire response bodies, so an infinite SSE
stream cannot be consumed through client.stream(). Instead we test the three
layers separately:
  1. Broadcaster mechanics (pure asyncio),
  2. the /api/events generator wire format (called directly, closed explicitly),
  3. that API mutations publish the right events (queue subscribed around
     real TestClient requests).
"""

import asyncio
import json

from events import Broadcaster


def test_broadcaster_delivers_to_all_subscribers():
    async def scenario():
        broadcaster = Broadcaster()
        q1 = broadcaster.subscribe()
        q2 = broadcaster.subscribe()

        broadcaster.publish({"type": "inventory", "action": "updated", "product": {"id": 1}})

        e1 = await asyncio.wait_for(q1.get(), timeout=1)
        e2 = await asyncio.wait_for(q2.get(), timeout=1)
        assert e1 == e2
        assert e1["type"] == "inventory"

        broadcaster.unsubscribe(q1)
        broadcaster.publish({"type": "inventory", "action": "deleted", "product_id": 1})
        e2b = await asyncio.wait_for(q2.get(), timeout=1)
        assert e2b["action"] == "deleted"
        assert q1.empty()

    asyncio.run(scenario())


def test_broadcaster_drops_events_for_stalled_subscriber():
    async def scenario():
        broadcaster = Broadcaster(max_queue_size=2)
        q = broadcaster.subscribe()
        for i in range(5):
            broadcaster.publish({"type": "inventory", "seq": i})
        # queue capped at maxsize: oldest events kept, newest dropped, no exception
        assert q.qsize() == 2

    asyncio.run(scenario())


def test_sse_endpoint_wire_format_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "sse.db"))
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")

    from app import create_app
    from auth import TokenStore

    app = create_app()
    token = app.state.tokens.create("admin")["token"]
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/events")

    async def scenario():
        response = await route.endpoint(token=token)
        assert response.media_type == "text/event-stream"
        chunks = response.body_iterator

        first = await asyncio.wait_for(anext(chunks), timeout=1)
        assert first == 'data: {"type": "connected"}\n\n'
        assert app.state.broadcaster.subscriber_count == 1

        app.state.broadcaster.publish({"type": "inventory", "action": "deleted", "product_id": 7})
        second = await asyncio.wait_for(anext(chunks), timeout=1)
        assert second.startswith("data: ")
        payload = json.loads(second[len("data: "):])
        assert payload == {"type": "inventory", "action": "deleted", "product_id": 7}

        await chunks.aclose()
        assert app.state.broadcaster.subscriber_count == 0  # unsubscribed on close

    asyncio.run(scenario())


def test_sse_emits_keepalive_after_heartbeat_timeout(tmp_path, monkeypatch):
    """A subscriber that receives no events must still see a ': keepalive'
    comment once the configured heartbeat interval elapses, and the stream
    must keep delivering real events afterwards. Uses a tiny interval so the
    test stays fast and deterministic instead of waiting the real 15s
    default."""
    monkeypatch.setenv("LOTSPOT_DB", str(tmp_path / "sse-heartbeat.db"))
    monkeypatch.setenv("LOTSPOT_ADMIN_PASSWORD", "testpass123")
    monkeypatch.setenv("LOTSPOT_ADMIN_USER", "admin")
    monkeypatch.setenv("LOTSPOT_SSE_HEARTBEAT", "0.05")

    from app import create_app

    app = create_app()
    token = app.state.tokens.create("admin")["token"]
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/events")

    async def scenario():
        response = await route.endpoint(token=token)
        chunks = response.body_iterator

        first = await asyncio.wait_for(anext(chunks), timeout=1)
        assert first == 'data: {"type": "connected"}\n\n'

        # No events published: next chunk must be the heartbeat keepalive,
        # not a dropped/hung connection.
        second = await asyncio.wait_for(anext(chunks), timeout=1)
        assert second == ": keepalive\n\n"

        # A second silent interval produces another keepalive (it repeats,
        # not a one-shot).
        third = await asyncio.wait_for(anext(chunks), timeout=1)
        assert third == ": keepalive\n\n"

        # Once real activity arrives, it is still delivered normally after
        # the heartbeats.
        app.state.broadcaster.publish({"type": "inventory", "action": "updated", "product": {"id": 1}})
        fourth = await asyncio.wait_for(anext(chunks), timeout=1)
        assert fourth.startswith("data: ")
        payload = json.loads(fourth[len("data: "):])
        assert payload["type"] == "inventory"

        await chunks.aclose()

    asyncio.run(scenario())


def _drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def test_product_create_publishes_inventory_event(client, admin_headers):
    queue = client.app.state.broadcaster.subscribe()
    resp = client.post(
        "/api/products",
        json={"sku": "EVT-1", "name": "Event Item", "qty": 4, "price_cents": 100},
        headers=admin_headers,
    )
    assert resp.status_code == 201

    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "inventory"
    assert events[0]["action"] == "created"
    assert events[0]["product"]["sku"] == "EVT-1"


def test_sale_publishes_inventory_and_sale_events(client, admin_headers, sample_product):
    queue = client.app.state.broadcaster.subscribe()
    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201

    events = _drain(queue)
    assert [e["type"] for e in events] == ["inventory", "sale"]
    assert events[0]["action"] == "updated"
    assert events[0]["product"]["qty"] == sample_product["qty"] - 1
    assert events[1]["sale"]["qty"] == 1


def test_pos_checkout_publishes_inventory_and_sale_events(client, admin_headers):
    coke = client.post(
        "/api/products",
        json={"sku": "EVT-COKE", "name": "Event Coke", "qty": 10, "price_cents": 250},
        headers=admin_headers,
    ).json()["data"]
    bread = client.post(
        "/api/products",
        json={"sku": "EVT-BREAD", "name": "Event Bread", "qty": 10, "price_cents": 400},
        headers=admin_headers,
    ).json()["data"]

    queue = client.app.state.broadcaster.subscribe()
    resp = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "card",
            "items": [
                {"product_id": coke["id"], "qty": 2},
                {"product_id": bread["id"], "qty": 1},
            ],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201

    events = _drain(queue)
    assert [e["type"] for e in events] == ["inventory", "inventory", "sale", "sale"]
    assert {e["sale"]["transaction_id"] for e in events if e["type"] == "sale"} == {
        resp.json()["data"]["transaction_id"]
    }


def test_product_delete_publishes_deleted_event(client, admin_headers, sample_product):
    queue = client.app.state.broadcaster.subscribe()
    resp = client.delete(f"/api/products/{sample_product['id']}", headers=admin_headers)
    assert resp.status_code == 200

    events = _drain(queue)
    assert len(events) == 1
    assert events[0] == {
        "type": "inventory",
        "action": "deleted",
        "product_id": sample_product["id"],
    }


def test_sse_endpoint_requires_auth(client):
    """GET /api/events without a token must return 401."""
    resp = client.get("/api/events")
    assert resp.status_code == 401
    assert "Missing bearer token" in resp.text or "401" in resp.text


def test_sse_endpoint_rejects_invalid_token(client):
    """GET /api/events with an invalid token must return 401."""
    resp = client.get("/api/events?token=invalid-token-12345")
    assert resp.status_code == 401


def test_sse_does_not_broadcast_sensitive_sale_fields(client, admin_headers, sample_product):
    """Sale broadcasts must NOT include cashier, void_reason, or voided_by fields."""
    token = admin_headers["Authorization"].split(" ")[1]
    queue = client.app.state.broadcaster.subscribe()

    resp = client.post(
        "/api/sales",
        json={"product_id": sample_product["id"], "qty": 1},
        headers=admin_headers,
    )
    assert resp.status_code == 201

    events = _drain(queue)
    sale_events = [e for e in events if e["type"] == "sale"]
    assert len(sale_events) >= 1

    for event in sale_events:
        sale = event.get("sale", {})
        assert "cashier" not in sale, "Sensitive field 'cashier' must not be in SSE broadcasts"
        assert "void_reason" not in sale
        assert "voided_by" not in sale


def test_sse_does_not_broadcast_void_reason_on_void(client, admin_headers, sample_product):
    """When a sale is voided, the void_reason must NOT be in the SSE broadcast."""
    coke = client.post(
        "/api/products",
        json={"sku": "SSE-VOID-COKE", "name": "SSE Void Coke", "qty": 10, "price_cents": 250},
        headers=admin_headers,
    ).json()["data"]

    checkout = client.post(
        "/api/pos/checkout",
        json={
            "payment_method": "cash",
            "items": [{"product_id": coke["id"], "qty": 1}],
        },
        headers=admin_headers,
    ).json()["data"]

    sale_id = checkout["line_items"][0]["id"]
    queue = client.app.state.broadcaster.subscribe()

    resp = client.post(
        f"/api/sales/{sale_id}/void",
        json={"reason": "Customer returned item"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    rest_response = resp.json()["data"]

    assert rest_response["void_reason"] == "Customer returned item"
    assert rest_response["voided_by"] == "admin"

    events = _drain(queue)
    void_events = [e for e in events if e.get("action") == "voided"]
    assert len(void_events) >= 1

    for event in void_events:
        sale = event.get("sale", {})
        assert "void_reason" not in sale, "void_reason must not be in SSE broadcast"
        assert "voided_by" not in sale, "voided_by must not be in SSE broadcast"
        assert sale.get("id") == sale_id
