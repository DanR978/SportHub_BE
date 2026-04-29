from datetime import date, timedelta


def _event_payload(**overrides):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    base = {
        "title": "Saturday Pickup",
        "sport": "Basketball",
        "start_date": tomorrow,
        "start_time": "18:00:00",
        "end_time": "20:00:00",
        "location": "Central Park Court 1",
        "experience_level": "Intermediate",
        "description": "All levels welcome",
        "max_players": 4,
        "cost": 0,
        "latitude": 40.785,
        "longitude": -73.968,
    }
    base.update(overrides)
    return base


def test_create_event_happy(client, auth_headers):
    r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Saturday Pickup"
    assert body["sport"] == "basketball"  # lowercased
    assert body["is_organizer"] is True
    assert body["joined"] is True
    assert body["participant_count"] == 1


def test_create_event_rejects_over_4_hours(client, auth_headers):
    r = client.post(
        "/sports-events",
        headers=auth_headers,
        json=_event_payload(start_time="10:00:00", end_time="15:00:00"),
    )
    assert r.status_code == 400
    assert "4 hours" in r.json()["detail"]


def test_create_event_rejects_end_before_start(client, auth_headers):
    r = client.post(
        "/sports-events",
        headers=auth_headers,
        json=_event_payload(start_time="18:00:00", end_time="17:00:00"),
    )
    assert r.status_code == 400


def test_create_event_rejects_profanity(client, auth_headers):
    r = client.post(
        "/sports-events",
        headers=auth_headers,
        json=_event_payload(title="shit game"),
    )
    assert r.status_code == 400


def test_create_event_sets_cost(client, auth_headers):
    # Cost is no longer premium-gated; anyone can set it for future analytics
    r = client.post("/sports-events", headers=auth_headers, json=_event_payload(cost=10.0))
    assert r.status_code == 200
    assert float(r.json()["cost"]) == 10.0


def test_join_leave_flow(client, auth_headers, other_auth_headers):
    created = client.post("/sports-events", headers=auth_headers, json=_event_payload())
    event_id = created.json()["event_id"]

    r_join = client.post(f"/sports-events/{event_id}/join", headers=other_auth_headers)
    assert r_join.status_code == 200

    r_dup = client.post(f"/sports-events/{event_id}/join", headers=other_auth_headers)
    assert r_dup.status_code == 400
    assert "Already joined" in r_dup.json()["detail"]

    r_leave = client.delete(f"/sports-events/{event_id}/leave", headers=other_auth_headers)
    assert r_leave.status_code == 200


def test_organizer_cannot_leave_own_event(client, auth_headers):
    created = client.post("/sports-events", headers=auth_headers, json=_event_payload())
    event_id = created.json()["event_id"]
    r = client.delete(f"/sports-events/{event_id}/leave", headers=auth_headers)
    assert r.status_code == 400


def test_capacity_enforced(client, db, auth_headers, other_auth_headers):
    # Create a 2-person event. Organizer auto-joins, so only 1 slot left.
    created = client.post("/sports-events", headers=auth_headers, json=_event_payload(max_players=2))
    event_id = created.json()["event_id"]

    r = client.post(f"/sports-events/{event_id}/join", headers=other_auth_headers)
    assert r.status_code == 200

    # A third user would need a third auth set; fabricate one inline.
    from auth import hash_password, issue_token_pair
    from models.db_user import DBUser
    third = DBUser(email="charlie@example.com", first_name="C", last_name="D", hashed_password=hash_password("password123"))
    db.add(third); db.commit(); db.refresh(third)
    third_headers = {"Authorization": f"Bearer {issue_token_pair(third.email)['access_token']}"}

    r_full = client.post(f"/sports-events/{event_id}/join", headers=third_headers)
    assert r_full.status_code == 400
    assert "full" in r_full.json()["detail"]


def test_only_organizer_can_patch(client, auth_headers, other_auth_headers):
    created = client.post("/sports-events", headers=auth_headers, json=_event_payload())
    event_id = created.json()["event_id"]
    r = client.patch(f"/sports-events/{event_id}", headers=other_auth_headers, json={"title": "hijacked"})
    assert r.status_code == 403


def test_delete_event_archives_it(client, auth_headers, db):
    from uuid import UUID
    from models.db_archived_event import DBArchivedEvent
    created = client.post("/sports-events", headers=auth_headers, json=_event_payload())
    event_id = UUID(created.json()["event_id"])
    r = client.delete(f"/sports-events/{event_id}", headers=auth_headers)
    assert r.status_code == 200
    assert db.query(DBArchivedEvent).filter(DBArchivedEvent.event_id == event_id).first() is not None


# ── Block + event invisibility (both directions) ─────────────────────────────

class TestBlockHidesEvents:
    def test_blocked_organizer_event_hidden_from_blocker(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        # other_user organizes an event.
        r = client.post("/sports-events", headers=other_auth_headers, json=_event_payload())
        assert r.status_code == 200
        event_id = r.json()["event_id"]
        # alice blocks bob.
        r = client.post(f"/users/{other_user.user_id}/block", headers=auth_headers)
        assert r.status_code in (200, 201)
        # alice's event list should not include bob's event.
        r = client.get("/sports-events", headers=auth_headers)
        ids = [e["event_id"] for e in r.json()]
        assert event_id not in ids

    def test_blocked_user_cannot_see_blockers_event(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        # alice creates an event, then blocks bob.
        r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
        event_id = r.json()["event_id"]
        client.post(f"/users/{other_user.user_id}/block", headers=auth_headers)
        # bob shouldn't see alice's event.
        r = client.get("/sports-events", headers=other_auth_headers)
        ids = [e["event_id"] for e in r.json()]
        assert event_id not in ids

    def test_block_deletes_friendship(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        # Become friends first.
        r = client.post("/friends/request", headers=auth_headers,
                        json={"user_id": str(other_user.user_id)})
        assert r.status_code == 200
        me_a = client.get("/users/me", headers=auth_headers).json()
        r = client.post(f"/friends/{me_a['user_id']}/accept", headers=other_auth_headers)
        assert r.status_code == 200
        # Confirm friendship.
        r = client.get("/friends", headers=auth_headers)
        assert any(f["user_id"] == str(other_user.user_id) for f in r.json()["friends"])
        # Block.
        client.post(f"/users/{other_user.user_id}/block", headers=auth_headers)
        # Friendship gone.
        r = client.get("/friends", headers=auth_headers)
        assert not any(f["user_id"] == str(other_user.user_id) for f in r.json()["friends"])
        # Unblock should NOT auto-restore — Alice has to send a fresh request.
        client.delete(f"/users/{other_user.user_id}/block", headers=auth_headers)
        r = client.get("/friends", headers=auth_headers)
        assert not any(f["user_id"] == str(other_user.user_id) for f in r.json()["friends"])


# ── Organizer kick + ban ─────────────────────────────────────────────────────

class TestOrganizerKick:
    def _join(self, client, headers, event_id):
        r = client.post(f"/sports-events/{event_id}/join", headers=headers)
        assert r.status_code == 200, r.text

    def test_organizer_can_kick_participant(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
        event_id = r.json()["event_id"]
        self._join(client, other_auth_headers, event_id)

        r = client.delete(
            f"/sports-events/{event_id}/participants/{other_user.user_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        # Bob is no longer a participant.
        r = client.get(f"/sports-events/{event_id}/participants", headers=auth_headers)
        ids = [p["user_id"] for p in r.json()["participants"]]
        assert str(other_user.user_id) not in ids

    def test_kick_with_ban_blocks_rejoin(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
        event_id = r.json()["event_id"]
        self._join(client, other_auth_headers, event_id)

        r = client.delete(
            f"/sports-events/{event_id}/participants/{other_user.user_id}?ban=true",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json().get("banned") is True

        # Bob tries to re-join — should 403.
        r = client.post(f"/sports-events/{event_id}/join", headers=other_auth_headers)
        assert r.status_code == 403

    def test_lift_event_ban_allows_rejoin(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
        event_id = r.json()["event_id"]
        self._join(client, other_auth_headers, event_id)
        client.delete(
            f"/sports-events/{event_id}/participants/{other_user.user_id}?ban=true",
            headers=auth_headers,
        )
        r = client.delete(f"/sports-events/{event_id}/bans/{other_user.user_id}",
                          headers=auth_headers)
        assert r.status_code == 200

        r = client.post(f"/sports-events/{event_id}/join", headers=other_auth_headers)
        assert r.status_code == 200

    def test_non_organizer_cannot_kick(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        from auth import hash_password, issue_token_pair
        from models.db_user import DBUser
        carol = DBUser(
            email="carolkick@example.com", first_name="Carol", last_name="K",
            hashed_password=hash_password("p"),
        )
        db.add(carol); db.commit(); db.refresh(carol)
        carol_h = {"Authorization": f"Bearer {issue_token_pair(carol.email)['access_token']}"}

        r = client.post("/sports-events", headers=auth_headers, json=_event_payload())
        event_id = r.json()["event_id"]
        self._join(client, other_auth_headers, event_id)
        # Carol is not the organizer — she can't kick Bob.
        r = client.delete(
            f"/sports-events/{event_id}/participants/{other_user.user_id}",
            headers=carol_h,
        )
        assert r.status_code == 403
