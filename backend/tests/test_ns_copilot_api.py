"""The copilot administration API: permission-aware, scoped to the knowledge-pack workspace."""
from __future__ import annotations

import pytest

from app.hybrid.knowledge_pack import load_pack
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _semantic_encoder():
    if not load_pack(None, "semantic").encoder.semantic:
        pytest.skip("semantic encoder unavailable")


def test_an_administrator_can_read_the_knowledge_and_analytics(client):
    headers = auth_headers(client, "admin@meridian.test")
    overview = client.get("/api/copilot/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["counts"]["rules"] > 0
    assert client.get("/api/copilot/analytics", headers=headers).status_code == 200
    assert client.get("/api/copilot/gaps", headers=headers).status_code == 200
    policies = client.get("/api/copilot/policies", headers=headers).json()
    assert any(d["policy"] == "returns" and not d["in_force"] for d in policies["documents"])


def test_a_rule_can_be_tested_without_asking_a_question(client):
    headers = auth_headers(client, "admin@meridian.test")
    body = client.post("/api/copilot/rules/test", headers=headers, json={
        "goal": "return_allowed", "subject": "ORD-33002",
        "facts": {"return_reason": "damaged_on_arrival"}}).json()
    assert body["value"] == "no"
    assert body["policy_versions"][0]["version"] == "RETURNS-v1"


def test_customers_and_agents_cannot_change_knowledge(client):
    for email in ("priya@meridian.test", "support@meridian.test"):
        headers = auth_headers(client, email)
        response = client.put("/api/copilot/files/rules/04_support.rules", headers=headers,
                              json={"content": "rule X \"x\" [SUP-1]\nend"})
        assert response.status_code == 403, email


def test_customers_cannot_read_decision_traces_or_analytics(client):
    headers = auth_headers(client, "priya@meridian.test")
    assert client.get("/api/copilot/analytics", headers=headers).status_code == 403


def test_other_workspaces_have_no_copilot_administration(client):
    headers = auth_headers(client, "alice@acme.test")
    response = client.get("/api/copilot/overview", headers=headers)
    assert response.status_code in (403, 404)


def test_an_invalid_change_is_reported_without_being_written(client):
    headers = auth_headers(client, "admin@meridian.test")
    body = client.post("/api/copilot/validate", headers=headers, json={
        "folder": "rules", "name": "04_support.rules", "content": "rule broken"}).json()
    assert body["ok"] is False and body["errors"]


def test_the_chat_envelope_carries_the_decision_view(client):
    headers = auth_headers(client, "support@meridian.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "Can order 22003 still be cancelled?"}).json()
    ns = body["neurosymbolic"]
    assert ns["decision_label"] == "Yes"
    assert ns["verification"]["status"] == "passed"
    assert ns["recommended_action"]
    assert ns["audience"] == "staff"
