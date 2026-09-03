"""Authentication: registration, login, rotation, replay, lockout, enumeration."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.auth import passwords, service as auth_service, tokens
from app.core.errors import InvalidCredentialsError, TokenError, ValidationError
from app.db.models.tenancy import RefreshToken, User
from app.db.session import SessionLocal, bind_tenant
from tests.conftest import DEMO_PASSWORD, auth_headers, login, unique_email


# --- password hashing -------------------------------------------------------

def test_password_hash_is_argon2id_and_verifies():
    hashed = passwords.hash_password("Correct-Horse-9!")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password("Correct-Horse-9!", hashed)
    assert not passwords.verify_password("correct-horse-9!", hashed)


def test_password_hash_is_salted():
    assert passwords.hash_password("Same-Password-1!") != passwords.hash_password(
        "Same-Password-1!")


@pytest.mark.parametrize("weak", ["short1!", "alllowercase123", "PASSWORD1234", "password"])
def test_weak_passwords_rejected(weak):
    with pytest.raises(ValidationError):
        passwords.validate_password_strength(weak)


def test_strong_password_accepted():
    passwords.validate_password_strength("Praxis-Demo-2026!")


# --- tokens -----------------------------------------------------------------

def test_access_token_round_trip(alice):
    token, ttl = tokens.create_access_token(
        user_id=alice.user_id, tenant_id=alice.tenant_id, email=alice.email,
        roles=["employee"], permissions=["chat:use"])
    claims = tokens.decode_access_token(token)
    assert claims.sub == alice.user_id
    assert claims.tenant_id == alice.tenant_id
    assert "chat:use" in claims.permissions
    assert ttl > 0


def test_tampered_access_token_rejected(alice):
    token, _ = tokens.create_access_token(
        user_id=alice.user_id, tenant_id=alice.tenant_id, email=alice.email,
        roles=[], permissions=[])
    header, payload, signature = token.split(".")
    with pytest.raises(TokenError):
        tokens.decode_access_token(f"{header}.{payload}.{signature[:-2]}xy")


def test_expired_access_token_rejected(alice):
    token, _ = tokens.create_access_token(
        user_id=alice.user_id, tenant_id=alice.tenant_id, email=alice.email,
        roles=[], permissions=[], ttl_seconds=-10)
    with pytest.raises(TokenError):
        tokens.decode_access_token(token)


def test_refresh_token_cannot_be_used_as_access_token(alice):
    material = tokens.issue_refresh_token(alice.tenant_id)
    with pytest.raises(TokenError):
        tokens.decode_access_token(material.token)


def test_refresh_token_prefix_is_only_a_routing_hint(alice):
    material = tokens.issue_refresh_token(alice.tenant_id)
    tenant_hint, token_hash = tokens.split_refresh_token(material.token)
    assert tenant_hint == alice.tenant_id
    assert token_hash == material.token_hash
    # The secret itself is never stored.
    assert material.token_hash not in material.token


# --- registration -----------------------------------------------------------

def test_register_creates_tenant_and_admin(client):
    email = unique_email("founder")
    response = client.post("/api/auth/register", json={
        "email": email, "password": "Str0ng-Passw0rd!", "display_name": "Founder",
        "tenant_name": "Test Workspace",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == email
    assert "admin" in body["user"]["roles"]
    assert body["tokens"]["access_token"]


def test_duplicate_email_conflicts(client):
    email = unique_email("dup")
    payload = {"email": email, "password": "Str0ng-Passw0rd!", "tenant_name": "Dup One"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_register_into_existing_workspace_gets_default_role(client):
    email = unique_email("joiner")
    response = client.post("/api/auth/register", json={
        "email": email, "password": "Str0ng-Passw0rd!", "tenant_slug": "acme"})
    assert response.status_code == 201
    assert response.json()["user"]["roles"] == ["employee"]


def test_register_unknown_workspace_does_not_reveal_existence(client):
    response = client.post("/api/auth/register", json={
        "email": unique_email(), "password": "Str0ng-Passw0rd!",
        "tenant_slug": "no-such-workspace"})
    assert response.status_code == 422
    assert "not available" in response.json()["error"]["message"].lower()


# --- login ------------------------------------------------------------------

def test_login_succeeds(client):
    body = login(client, "alice@acme.test")
    assert body["user"]["email"] == "alice@acme.test"
    assert "chat:use" in body["user"]["permissions"]


def test_login_response_identical_for_unknown_email_and_wrong_password(client):
    unknown = client.post("/api/auth/login", json={
        "email": "nobody@acme.test", "password": "Whatever-1234!"})
    wrong = client.post("/api/auth/login", json={
        "email": "alice@acme.test", "password": "Whatever-1234!"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_password_is_never_echoed(client):
    response = client.post("/api/auth/login", json={
        "email": "alice@acme.test", "password": "Sup3rSecret-Value!"})
    assert "Sup3rSecret-Value!" not in response.text


def test_account_locks_after_repeated_failures(client):
    email = unique_email("lockme")
    client.post("/api/auth/register", json={
        "email": email, "password": "Str0ng-Passw0rd!", "tenant_name": "Lock Test"})
    for _ in range(auth_service.MAX_FAILED_LOGINS):
        assert client.post("/api/auth/login", json={
            "email": email, "password": "wrong-password"}).status_code == 401
    # Correct credentials are now refused, with the same generic message.
    response = client.post("/api/auth/login", json={
        "email": email, "password": "Str0ng-Passw0rd!"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


# --- refresh rotation and replay -------------------------------------------

def test_refresh_rotates_and_old_token_is_dead(client):
    tokens_1 = login(client, "bob@acme.test")["tokens"]
    response = client.post("/api/auth/refresh",
                           json={"refresh_token": tokens_1["refresh_token"]})
    assert response.status_code == 200
    tokens_2 = response.json()["tokens"]
    assert tokens_2["refresh_token"] != tokens_1["refresh_token"]

    replay = client.post("/api/auth/refresh",
                         json={"refresh_token": tokens_1["refresh_token"]})
    assert replay.status_code == 401


def test_replay_revokes_the_whole_family(client):
    tokens_1 = login(client, "analyst@acme.test")["tokens"]
    tokens_2 = client.post("/api/auth/refresh", json={
        "refresh_token": tokens_1["refresh_token"]}).json()["tokens"]

    # Replaying the consumed token must burn the family...
    assert client.post("/api/auth/refresh", json={
        "refresh_token": tokens_1["refresh_token"]}).status_code == 401
    # ...including the currently-valid successor.
    assert client.post("/api/auth/refresh", json={
        "refresh_token": tokens_2["refresh_token"]}).status_code == 401


def test_refresh_with_forged_tenant_prefix_fails(client, alice, carol):
    tokens_1 = login(client, "alice@acme.test")["tokens"]
    _, secret = tokens_1["refresh_token"].split(".", 1)
    forged = f"{carol.tenant_id.hex}.{secret}"
    assert client.post("/api/auth/refresh",
                       json={"refresh_token": forged}).status_code == 401


def test_garbage_refresh_token_rejected(client):
    for value in ["", "not-a-token", "..", "x." + "y" * 40]:
        assert client.post("/api/auth/refresh",
                           json={"refresh_token": value}).status_code in (401, 422)


def test_logout_revokes_family(client):
    tokens_1 = login(client, "hr@acme.test")["tokens"]
    headers = {"Authorization": f"Bearer {tokens_1['access_token']}"}
    assert client.post("/api/auth/logout", headers=headers,
                       json={"refresh_token": tokens_1["refresh_token"]}).status_code == 204
    assert client.post("/api/auth/refresh", json={
        "refresh_token": tokens_1["refresh_token"]}).status_code == 401


def test_logout_cannot_revoke_someone_elses_session(client):
    victim = login(client, "alice@acme.test")["tokens"]
    attacker = login(client, "bob@acme.test")["tokens"]
    headers = {"Authorization": f"Bearer {attacker['access_token']}"}
    client.post("/api/auth/logout", headers=headers,
                json={"refresh_token": victim["refresh_token"]})
    # The victim's session still works.
    assert client.post("/api/auth/refresh", json={
        "refresh_token": victim["refresh_token"]}).status_code == 200


def test_sessions_listing_and_revoke_all(client):
    headers = auth_headers(client, "admin@acme.test")
    listing = client.get("/api/auth/sessions", headers=headers)
    assert listing.status_code == 200 and listing.json()
    assert client.post("/api/auth/sessions/revoke-all",
                       headers=headers).status_code == 204
    after = client.get("/api/auth/sessions", headers=headers).json()
    assert all(not s["active"] for s in after)


# --- protected endpoints ----------------------------------------------------

def test_missing_and_malformed_authorization_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": "Bearer"}).status_code == 401


def test_me_returns_resolved_permissions(client):
    body = client.get("/api/auth/me",
                      headers=auth_headers(client, "hr@acme.test")).json()
    assert body["tenant_slug"] == "acme"
    assert "document:read_hr" in body["permissions"]
