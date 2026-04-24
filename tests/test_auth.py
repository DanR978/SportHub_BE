from datetime import date, timedelta

from auth import create_refresh_token, issue_token_pair


def _signup_payload(**overrides):
    base = {
        "email": "newuser@example.com",
        "password": "strongpass1",
        "first_name": "New",
        "last_name": "User",
    }
    base.update(overrides)
    return base


def test_signup_happy_path(client):
    r = client.post("/users/signup", json=_signup_payload())
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "newuser@example.com"
    assert r.json()["is_admin"] is False


def test_signup_password_too_short(client):
    r = client.post("/users/signup", json=_signup_payload(password="short"))
    assert r.status_code == 400
    assert "8 characters" in r.json()["detail"]


def test_signup_duplicate_email(client, user):
    r = client.post("/users/signup", json=_signup_payload(email=user.email))
    assert r.status_code == 400
    assert "already registered" in r.json()["detail"]


def test_signup_underage_rejected(client):
    too_young = (date.today() - timedelta(days=365 * 10)).isoformat()
    r = client.post("/users/signup", json=_signup_payload(date_of_birth=too_young))
    assert r.status_code == 400
    assert "13 years old" in r.json()["detail"]


def test_signup_exactly_13_accepted(client):
    thirteen = (date.today() - timedelta(days=365 * 13 + 5)).isoformat()
    r = client.post("/users/signup", json=_signup_payload(date_of_birth=thirteen))
    assert r.status_code == 200


def test_login_and_refresh_flow(client, user):
    r = client.post(
        "/users/login",
        data={"username": user.email, "password": "password123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]

    # Refresh returns a new pair
    r2 = client.post("/users/token/refresh", json={"refresh_token": body["refresh_token"]})
    assert r2.status_code == 200
    assert r2.json()["access_token"]


def test_login_wrong_password(client, user):
    r = client.post("/users/login", data={"username": user.email, "password": "wrong"})
    assert r.status_code == 401


def test_refresh_rejects_access_token(client, user):
    # Using an access token as a refresh token must fail
    pair = issue_token_pair(user.email)
    r = client.post("/users/token/refresh", json={"refresh_token": pair["access_token"]})
    assert r.status_code == 401


def test_access_token_rejects_refresh_token(client, user):
    refresh = create_refresh_token({"sub": user.email})
    r = client.get("/users/me", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    r = client.get("/users/me")
    assert r.status_code == 401


def test_me_returns_current_user(client, auth_headers, user):
    r = client.get("/users/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["email"] == user.email


def test_patch_me_enforces_age(client, auth_headers):
    too_young = (date.today() - timedelta(days=365 * 8)).isoformat()
    r = client.patch("/users/me", headers=auth_headers, json={"date_of_birth": too_young})
    assert r.status_code == 400
