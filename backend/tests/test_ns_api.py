"""The neuro-symbolic assistant over HTTP: routing by workspace, persistence, follow-ups."""
from __future__ import annotations

import pytest

from app.hybrid.knowledge_pack import load_pack
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _semantic_encoder():
    if not load_pack(None, "semantic").encoder.semantic:
        pytest.skip("semantic encoder unavailable")


def test_a_knowledge_pack_workspace_is_answered_by_the_neurosymbolic_controller(client):
    headers = auth_headers(client, "dev@meridian.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "How much will I be reimbursed if my business trip cost ₹8,750?"}).json()
    ns = body["neurosymbolic"]
    assert ns["pipeline"] == "D"
    assert "₹8,000" in body["answer"]
    assert ns["explanation"], "the 'how was this determined' account is part of the envelope"
    assert ns["trace"]["pipeline_reason"]
    assert body["route"] == "NEUROSYMBOLIC_D"


def test_other_workspaces_keep_their_own_pipeline(client):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "How many annual leave days am I entitled to?"}).json()
    assert body["neurosymbolic"] is None
    assert not body["route"].startswith("NEUROSYMBOLIC")


def test_a_follow_up_in_the_same_conversation_resolves_it(client):
    headers = auth_headers(client, "priya@meridian.test")
    first = client.post("/api/chat", headers=headers,
                        json={"message": "Where is my laptop?"}).json()
    follow = client.post("/api/chat", headers=headers, json={
        "message": "Can I cancel it?", "conversation_id": first["conversation_id"]}).json()
    assert follow["neurosymbolic"]["goal"] == "cancellation_allowed"
    assert "no longer be cancelled" in follow["answer"]


def test_a_customer_cannot_reason_about_another_customers_order(client):
    headers = auth_headers(client, "priya@meridian.test")
    body = client.post("/api/chat", headers=headers,
                       json={"message": "Can I cancel order 22003?"}).json()
    assert body["neurosymbolic"]["outcome"] == "denied"
    assert body["gate_decision"] == "DENY"
