"""End-to-end HTTP journeys against the real stack.

login -> create conversation -> upload document -> ask -> retrieve evidence ->
extract facts -> reason -> verify -> answer -> citations -> provenance.
"""
from __future__ import annotations

import io
import json
import re

from tests.conftest import auth_headers, login, unique_email


def numbers(text: str) -> set[float]:
    return {float(t) for t in re.findall(r"(?<![\w.\-])\d+(?:\.\d+)?", text)}


def test_health_reports_every_subsystem(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm_provider"] and body["embedding_provider"]


def test_openapi_document_is_served(client):
    schema = client.get("/api/openapi.json").json()
    assert schema["info"]["title"] == "Praxis API"
    assert "/api/chat" in schema["paths"]


def test_request_id_is_echoed(client):
    response = client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_security_headers_present(client):
    headers = client.get("/api/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_full_journey_register_upload_ask_cite(client):
    # 1. A brand-new workspace with its own admin.
    email = unique_email("journey")
    registered = client.post("/api/auth/register", json={
        "email": email, "password": "Journey-Pass-2026!", "display_name": "Journey",
        "tenant_name": "Journey Corp"}).json()
    headers = {"Authorization": f"Bearer {registered['tokens']['access_token']}"}

    # 2. A conversation.
    conversation = client.post("/api/conversations", headers=headers,
                               json={"title": None}).json()
    assert conversation["title"] == "New conversation"

    # 3. Nothing is known yet, so the assistant must abstain.
    cold = client.post("/api/chat", headers=headers, json={
        "message": "What is our travel expense limit?",
        "conversation_id": conversation["id"]}).json()
    assert cold["gate_decision"] == "ABSTAIN"
    assert cold["citations"] == []

    # 4. Upload a document.
    document = client.post("/api/documents/upload", headers=headers, files={
        "file": ("travel.md", io.BytesIO(
            b"# Travel Policy\n\n## Expenses\n"
            b"The travel expense limit is 250 USD per day.\n"
            b"The mileage rate is 42 cents per mile.\n"), "text/markdown"),
    }, data={"collection": "policies", "sensitivity": "internal",
             "effective_date": "2026-04-01"}).json()
    assert document["status"] == "ready"
    assert document["chunk_count"] >= 1

    # 5. The same question is now answerable, with a citation.
    warm = client.post("/api/chat", headers=headers, json={
        "message": "What is our travel expense limit?",
        "conversation_id": conversation["id"]}).json()
    assert warm["gate_decision"] in ("ANSWER", "ANSWER_WITH_CAVEAT")
    assert 250.0 in numbers(warm["answer"])
    assert warm["citations"]
    # The title comes from the document's own heading, not the filename.
    assert warm["citations"][0]["document_title"] == "Travel Policy"

    # 6. The turn is persisted with its trace.
    messages = client.get(f"/api/conversations/{conversation['id']}/messages",
                          headers=headers).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assistant = messages[-1]
    assert assistant["verification_status"]
    assert assistant["trace"]["citations"]
    assert assistant["trace"]["gate"]["signals"]

    # 7. The conversation title was derived from the first question.
    listing = client.get("/api/conversations", headers=headers).json()
    assert listing[0]["title"].lower().startswith("what is our travel")
    assert listing[0]["message_count"] == 4

    # 8. Cleanup path works.
    assert client.delete(f"/api/documents/{document['id']}",
                         headers=headers).status_code == 204
    assert client.delete(f"/api/conversations/{conversation['id']}",
                         headers=headers).status_code == 204


def test_symbolic_journey_over_http(client):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "How many annual leave days am I entitled to?"}).json()

    assert body["route"] == "RAG_PLUS_SYMBOLIC"
    assert 20.0 in numbers(body["answer"])
    assert body["reasoning"]["conclusion"].startswith("self.annual_leave_days")
    assert any(step["rule_code"] == "R12" for step in body["reasoning"]["inferences"])
    assert body["claims"]
    assert body["stages"]
    assert body["latency_ms"] >= 0


def test_streaming_endpoint_emits_start_deltas_and_done(client):
    headers = auth_headers(client, "alice@acme.test")
    with client.stream("POST", "/api/chat/stream", headers=headers, json={
            "message": "What does the handbook say about the notice period?"}) as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        raw = "".join(stream.iter_text())

    events = re.findall(r"event: (\w+)\ndata: (.*)", raw)
    kinds = [e[0] for e in events]
    assert kinds[0] == "start"
    assert "delta" in kinds
    assert kinds[-1] == "done"

    final = json.loads(events[-1][1])
    assert final["citations"]
    assert final["verification_status"]
    streamed = "".join(json.loads(data)["text"]
                       for kind, data in events if kind == "delta")
    assert streamed.strip() == final["answer"].strip()


def test_abstention_journey_over_http(client):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "What is our policy on submarine maintenance?"}).json()
    assert body["gate_decision"] == "ABSTAIN"
    assert body["confidence"] == 0.0
    assert "don't have enough information" in body["answer"].lower()
    assert body["warnings"]


def test_conflict_journey_over_http(client):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "How long is the warranty on the Praxis Router X1?"}).json()
    assert body["gate_decision"] == "REPORT_CONFLICT"
    conflicts = body["reasoning"]["contradictions"]
    assert conflicts and conflicts[0]["predicate"] == "warranty_months"


def test_policy_journey_over_http(client):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "Can this user access salary records?"}).json()
    assert body["policy_decision"]["decision"] == "DENY"
    assert body["gate_decision"] == "DENY"


def test_facts_and_rules_endpoints(client):
    headers = auth_headers(client, "hr@acme.test")
    facts = client.get("/api/facts", headers=headers,
                       params={"predicate": "annual_leave_days"}).json()
    assert facts
    assert {f["kind"] for f in facts} <= {"DIRECT", "INFERRED"}

    created = client.post("/api/facts", headers=headers, json={
        "subject": "policy", "predicate": "parking_spaces", "object": 40})
    assert created.status_code == 201
    assert created.json()["kind"] == "DIRECT"

    rules = client.get("/api/rules", headers=auth_headers(client, "alice@acme.test"))
    assert rules.status_code == 200
    assert "R12" in {r["code"] for r in rules.json()}


def test_rule_lifecycle(client):
    headers = auth_headers(client, "admin@acme.test")
    payload = {
        "code": "TEST_R1", "name": "Test rule",
        "body": [{"type": "fact", "subject": "?e", "predicate": "years_of_service",
                  "value": "?y"},
                 {"type": "comparison", "left": "?y", "op": "gte",
                  "right": {"const": 10}}],
        "head": {"subject": "?e", "predicate": "sabbatical_weeks",
                 "value": {"const": 4}, "unit": "weeks"},
    }
    assert client.post("/api/rules", headers=headers, json=payload).status_code == 201
    assert "TEST_R1" in {r["code"] for r in
                         client.get("/api/rules", headers=headers).json()}
    assert client.delete("/api/rules/TEST_R1", headers=headers).status_code == 204
    active = {r["code"] for r in client.get("/api/rules", headers=headers).json()
              if r["is_active"]}
    assert "TEST_R1" not in active


def test_invalid_rule_is_rejected_with_a_useful_message(client):
    response = client.post("/api/rules", headers=auth_headers(client, "admin@acme.test"),
                           json={"code": "BAD_R", "body": [
                               {"type": "fact", "subject": "?e", "predicate": "a",
                                "value": "?v"}],
                               "head": {"subject": "?e", "predicate": "b",
                                        "value": "?unbound"}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_rule"
    assert "bound" in response.json()["error"]["message"].lower()


def test_chat_requires_a_non_empty_message(client):
    headers = auth_headers(client, "alice@acme.test")
    assert client.post("/api/chat", headers=headers,
                       json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", headers=headers,
                       json={"message": "x" * 9000}).status_code == 422


def test_conversation_can_be_renamed_and_archived(client):
    headers = auth_headers(client, "alice@acme.test")
    conversation_id = client.post("/api/conversations", headers=headers,
                                  json={"title": "Original"}).json()["id"]
    renamed = client.patch(f"/api/conversations/{conversation_id}", headers=headers,
                           json={"title": "Renamed"}).json()
    assert renamed["title"] == "Renamed"
    client.patch(f"/api/conversations/{conversation_id}", headers=headers,
                 json={"is_archived": True})
    listing = client.get("/api/conversations", headers=headers).json()
    assert conversation_id not in {c["id"] for c in listing}
