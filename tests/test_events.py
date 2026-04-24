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
