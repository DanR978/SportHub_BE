"""
Friends router tests — focus on the new /friends/of/{user_id} endpoint that
powers the profile "friends" section and "X mutual friends" indicator.
"""
from auth import hash_password, issue_token_pair
from models.db_user import DBUser


def _make_user(db, email: str, first_name: str = "Carol") -> DBUser:
    u = DBUser(
        email=email,
        first_name=first_name,
        last_name="Doe",
        hashed_password=hash_password("password123"),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _hdr(email: str) -> dict:
    return {"Authorization": f"Bearer {issue_token_pair(email)['access_token']}"}


def _befriend(client, headers_a, target_user_id, headers_b):
    """Send a request from A→B and accept it from B."""
    r = client.post("/friends/request", headers=headers_a,
                    json={"user_id": str(target_user_id)})
    assert r.status_code == 200, r.text
    # Find requester id from headers_a → /users/me
    me_a = client.get("/users/me", headers=headers_a).json()
    r = client.post(f"/friends/{me_a['user_id']}/accept", headers=headers_b)
    assert r.status_code == 200, r.text


class TestFriendsOf:
    def test_self_view_returns_full_friends_list_no_mutuals(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        _befriend(client, auth_headers, other_user.user_id, other_auth_headers)
        r = client.get(f"/friends/of/{user.user_id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        # Looking at your own profile → mutuals is empty by design.
        assert data["mutual_count"] == 0
        assert data["mutuals"] == []
        assert any(f["user_id"] == str(other_user.user_id) for f in data["friends"])

    def test_other_view_surfaces_mutuals(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        carol = _make_user(db, "carol@example.com")
        carol_h = _hdr(carol.email)
        # alice ↔ bob, alice ↔ carol, bob ↔ carol → all three are friends
        _befriend(client, auth_headers, other_user.user_id, other_auth_headers)
        _befriend(client, auth_headers, carol.user_id,      carol_h)
        _befriend(client, other_auth_headers, carol.user_id, carol_h)

        # Alice views Bob's profile — Carol is the mutual.
        r = client.get(f"/friends/of/{other_user.user_id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert data["mutual_count"] == 1
        assert data["mutuals"][0]["user_id"] == str(carol.user_id)
        # Mutuals are surfaced first in the friends array.
        assert data["friends"][0]["is_mutual"] is True

    def test_unknown_user_404s(self, client, auth_headers):
        r = client.get("/friends/of/00000000-0000-0000-0000-000000000000",
                       headers=auth_headers)
        assert r.status_code == 404

    def test_blocked_returns_empty(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        _befriend(client, auth_headers, other_user.user_id, other_auth_headers)
        # Alice blocks Bob → her view of Bob's friends is suppressed.
        r = client.post(f"/users/{other_user.user_id}/block", headers=auth_headers)
        assert r.status_code in (200, 201), r.text
        r = client.get(f"/friends/of/{other_user.user_id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["friends"] == []
        assert data["count"] == 0
