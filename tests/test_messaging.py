"""
Tests for /messaging/* — DMs, group chats (with admin role), event chats,
incremental message fetch, unread counts, and the notifications summary
endpoint that powers the in-app banner.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from auth import hash_password, issue_token_pair
from models.db_user import DBUser
from models.db_friendship import DBFriendship
from models.db_event import DBEvent
from models.db_event_participant import DBEventParticipant
from models.db_conversation import DBConversation, DBConversationMember
from models.db_message import DBMessage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_extra_user(db, email: str, first_name: str = "Carol") -> DBUser:
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


def _headers_for(email: str) -> dict:
    return {"Authorization": f"Bearer {issue_token_pair(email)['access_token']}"}


def _start_dm(client, headers, recipient_id) -> dict:
    r = client.post("/messaging/direct", headers=headers, json={"recipient_id": str(recipient_id)})
    assert r.status_code == 200, r.text
    return r.json()


def _send(client, headers, conv_id, body="hi") -> dict:
    r = client.post(
        f"/messaging/conversations/{conv_id}/messages",
        headers=headers,
        json={"kind": "text", "body": body},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _start_group(client, headers, member_ids, title=None) -> dict:
    r = client.post(
        "/messaging/group",
        headers=headers,
        json={"member_ids": [str(i) for i in member_ids], "title": title},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── Direct messages ───────────────────────────────────────────────────────────

class TestDirectMessages:
    def test_start_dm_creates_conversation(self, client, user, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        assert conv["kind"] == "direct"
        assert {m["user_id"] for m in conv["members"]} == {str(user.user_id), str(other_user.user_id)}
        assert conv["unread_count"] == 0

    def test_start_dm_is_idempotent(self, client, user, other_user, auth_headers):
        a = _start_dm(client, auth_headers, other_user.user_id)
        b = _start_dm(client, auth_headers, other_user.user_id)
        assert a["conversation_id"] == b["conversation_id"]

    def test_dm_returns_existing_when_started_from_either_side(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        a = _start_dm(client, auth_headers, other_user.user_id)
        b = _start_dm(client, other_auth_headers, user.user_id)
        assert a["conversation_id"] == b["conversation_id"]

    def test_cannot_dm_self(self, client, user, auth_headers):
        r = client.post("/messaging/direct", headers=auth_headers,
                        json={"recipient_id": str(user.user_id)})
        assert r.status_code == 400

    def test_cannot_dm_unknown_user(self, client, auth_headers):
        r = client.post("/messaging/direct", headers=auth_headers,
                        json={"recipient_id": "00000000-0000-0000-0000-000000000000"})
        assert r.status_code == 404


# ── Sending + reading messages ────────────────────────────────────────────────

class TestSendAndReceive:
    def test_send_message_round_trip(self, client, user, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        sent = _send(client, auth_headers, conv["conversation_id"], "hello there")
        assert sent["body"] == "hello there"
        assert sent["sender"]["user_id"] == str(user.user_id)
        assert sent["kind"] == "text"

    def test_blank_message_rejected(self, client, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers, json={"kind": "text", "body": "   "},
        )
        assert r.status_code == 400

    def test_unknown_kind_rejected(self, client, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers, json={"kind": "shout", "body": "hi"},
        )
        assert r.status_code == 400

    def test_too_long_message_rejected(self, client, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers, json={"kind": "text", "body": "x" * 2001},
        )
        assert r.status_code == 400

    def test_non_member_cannot_send(self, client, user, other_user, db, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        intruder = _make_extra_user(db, "intruder@example.com")
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=_headers_for(intruder.email),
            json={"kind": "text", "body": "leak"},
        )
        assert r.status_code == 403

    def test_list_messages_returns_chronological(self, client, user, other_user, auth_headers, other_auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        _send(client, auth_headers, conv["conversation_id"], "first")
        _send(client, other_auth_headers, conv["conversation_id"], "second")
        _send(client, auth_headers, conv["conversation_id"], "third")
        r = client.get(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers,
        )
        assert r.status_code == 200
        bodies = [m["body"] for m in r.json()["messages"]]
        assert bodies == ["first", "second", "third"]


# ── Incremental fetch (the speed win) ────────────────────────────────────────

class TestIncrementalFetch:
    def test_since_returns_only_new_messages(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        m1 = _send(client, auth_headers, conv["conversation_id"], "old")
        # Now ask for messages strictly newer than m1.created_at — should be empty.
        r = client.get(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers,
            params={"since": m1["created_at"]},
        )
        assert r.status_code == 200
        assert r.json()["messages"] == []

        # Send one more from the other side; it should now show up.
        m2 = _send(client, other_auth_headers, conv["conversation_id"], "new")
        r = client.get(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers,
            params={"since": m1["created_at"]},
        )
        assert r.status_code == 200
        bodies = [m["body"] for m in r.json()["messages"]]
        assert bodies == ["new"]
        assert r.json()["messages"][0]["message_id"] == m2["message_id"]


# ── Unread + read markers ────────────────────────────────────────────────────

class TestUnread:
    def test_unread_count_grows_until_read(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        for i in range(3):
            _send(client, other_auth_headers, conv["conversation_id"], f"msg-{i}")

        # Viewer's conversation list should show 3 unread.
        r = client.get("/messaging/conversations", headers=auth_headers)
        convs = r.json()["conversations"]
        assert len(convs) == 1
        assert convs[0]["unread_count"] == 3

        # Summary endpoint agrees.
        r = client.get("/messaging/unread-summary", headers=auth_headers)
        assert r.json()["unread"] == 3

        # Open the message list (which bumps last_read_at).
        client.get(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=auth_headers,
        )

        r = client.get("/messaging/unread-summary", headers=auth_headers)
        assert r.json()["unread"] == 0

    def test_own_messages_dont_count_as_unread(
        self, client, user, other_user, auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        _send(client, auth_headers, conv["conversation_id"], "talking to myself")
        r = client.get("/messaging/unread-summary", headers=auth_headers)
        assert r.json()["unread"] == 0

    def test_explicit_read_endpoint(self, client, user, other_user, auth_headers, other_auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        _send(client, other_auth_headers, conv["conversation_id"], "hi")
        client.post(
            f"/messaging/conversations/{conv['conversation_id']}/read",
            headers=auth_headers,
        )
        r = client.get("/messaging/unread-summary", headers=auth_headers)
        assert r.json()["unread"] == 0


# ── Group chats + admin role ─────────────────────────────────────────────────

class TestGroupChat:
    def test_create_group_marks_creator_as_admin(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Squad")
        assert conv["kind"] == "group"
        assert conv["title"] == "Squad"
        admins = [m for m in conv["members"] if m["is_admin"]]
        assert len(admins) == 1
        assert admins[0]["user_id"] == str(user.user_id)

    def test_group_requires_at_least_one_other(self, client, auth_headers, user):
        r = client.post("/messaging/group", headers=auth_headers,
                        json={"member_ids": [str(user.user_id)], "title": "lonely"})
        assert r.status_code == 400

    def test_admin_can_rename_group(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Old Name")
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}",
            headers=auth_headers, json={"title": "New Name"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "New Name"

    def test_non_admin_cannot_rename(self, client, user, other_user, auth_headers, other_auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Original")
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}",
            headers=other_auth_headers, json={"title": "Hacked"},
        )
        assert r.status_code == 403

    def test_admin_can_add_member(self, client, db, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        carol = _make_extra_user(db, "carol@example.com")
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/members",
            headers=auth_headers,
            json={"member_ids": [str(carol.user_id)]},
        )
        assert r.status_code == 200
        member_ids = {m["user_id"] for m in r.json()["members"]}
        assert str(carol.user_id) in member_ids

    def test_non_admin_cannot_add_members(self, client, db, user, other_user, auth_headers, other_auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        carol = _make_extra_user(db, "carol@example.com")
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/members",
            headers=other_auth_headers,
            json={"member_ids": [str(carol.user_id)]},
        )
        assert r.status_code == 403

    def test_admin_can_promote_and_demote(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        # Promote bob
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}",
            headers=auth_headers,
            json={"is_admin": True},
        )
        assert r.status_code == 200
        assert r.json()["is_admin"] is True

        # Bob can now rename
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}",
            headers={"Authorization": f"Bearer {issue_token_pair(other_user.email)['access_token']}"},
            json={"title": "Bob's group now"},
        )
        assert r.status_code == 200

        # Demote bob (alice is still admin so this is allowed)
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}",
            headers=auth_headers,
            json={"is_admin": False},
        )
        assert r.status_code == 200
        assert r.json()["is_admin"] is False

    def test_cannot_demote_last_admin(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}/members/{user.user_id}",
            headers=auth_headers,
            json={"is_admin": False},
        )
        assert r.status_code == 400

    def test_admin_can_kick_member(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200

        # Bob can no longer send.
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers={"Authorization": f"Bearer {issue_token_pair(other_user.email)['access_token']}"},
            json={"kind": "text", "body": "lemme back in"},
        )
        assert r.status_code == 403

    def test_self_can_leave(self, client, user, other_user, auth_headers, other_auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        # Bob leaves
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}",
            headers=other_auth_headers,
        )
        assert r.status_code == 200

    def test_last_admin_cannot_leave(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{user.user_id}",
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_non_admin_cannot_kick(self, client, db, user, other_user, auth_headers, other_auth_headers):
        carol = _make_extra_user(db, "carol@example.com")
        conv = _start_group(client, auth_headers, [other_user.user_id, carol.user_id])
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{carol.user_id}",
            headers=other_auth_headers,
        )
        assert r.status_code == 403


# ── Event chats (organizer is implicit admin) ─────────────────────────────────

@pytest.fixture
def event_with_chat(client, db, user, other_user, auth_headers):
    """Create an event organized by `user`, with `other_user` as a participant."""
    payload = {
        "title": "Saturday Pickup",
        "sport": "Basketball",
        "start_date": (date.today() + timedelta(days=1)).isoformat(),
        "start_time": "18:00:00",
        "end_time": "20:00:00",
        "location": "Court 1",
        "experience_level": "Intermediate",
        "max_players": 4,
        "cost": 0,
        "latitude": 40.785,
        "longitude": -73.968,
    }
    r = client.post("/sports-events", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text
    event_id = r.json()["event_id"]

    # Bob joins.
    r = client.post(
        f"/sports-events/{event_id}/join",
        headers={"Authorization": f"Bearer {issue_token_pair(other_user.email)['access_token']}"},
    )
    assert r.status_code == 200, r.text

    # Open the event chat as Alice.
    r = client.post(f"/messaging/event/{event_id}/open", headers=auth_headers)
    assert r.status_code == 200, r.text
    return event_id, r.json()


class TestEventChat:
    def test_event_chat_seeded_with_participants_and_organizer_admin(self, event_with_chat):
        _event_id, conv = event_with_chat
        assert conv["kind"] == "event"
        assert len(conv["members"]) >= 2
        admins = [m for m in conv["members"] if m["is_admin"]]
        assert len(admins) >= 1

    def test_non_participant_cannot_open_event_chat(self, client, db, event_with_chat):
        event_id, _ = event_with_chat
        outsider = _make_extra_user(db, "outsider@example.com")
        r = client.post(f"/messaging/event/{event_id}/open",
                        headers=_headers_for(outsider.email))
        assert r.status_code == 403


# ── Notifications summary (powers the in-app banner) ─────────────────────────

class TestNotificationsSummary:
    def test_empty_state(self, client, user, auth_headers):
        r = client.get("/messaging/notifications/summary", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["unread_messages"] == 0
        assert body["pending_friend_requests"] == 0
        assert body["next_event"] is None

    def test_counts_unread_and_friend_requests(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        # Bob → Alice: friend request.
        r = client.post(
            "/friends/request",
            headers=other_auth_headers,
            json={"user_id": str(user.user_id)},
        )
        assert r.status_code == 200, r.text

        # Bob DMs Alice twice.
        conv = _start_dm(client, other_auth_headers, user.user_id)
        _send(client, other_auth_headers, conv["conversation_id"], "yo")
        _send(client, other_auth_headers, conv["conversation_id"], "yo again")

        r = client.get("/messaging/notifications/summary", headers=auth_headers)
        body = r.json()
        assert body["unread_messages"] == 2
        assert body["pending_friend_requests"] == 1

    def test_surfaces_imminent_event(self, client, user, auth_headers):
        payload = {
            "title": "Tomorrow's Game",
            "sport": "Soccer",
            "start_date": (date.today() + timedelta(days=1)).isoformat(),
            "start_time": "10:00:00",
            "end_time": "11:30:00",
            "location": "Field A",
            "experience_level": "Beginner",
            "max_players": 6,
            "cost": 0,
            "latitude": 40.0,
            "longitude": -73.0,
        }
        r = client.post("/sports-events", headers=auth_headers, json=payload)
        assert r.status_code == 200, r.text
        event_id = r.json()["event_id"]

        r = client.get("/messaging/notifications/summary", headers=auth_headers)
        body = r.json()
        assert body["next_event"] is not None
        assert body["next_event"]["event_id"] == event_id
        assert body["next_event"]["title"] == "Tomorrow's Game"

    def test_distant_event_is_ignored(self, client, user, auth_headers):
        # > 48h out → not surfaced.
        payload = {
            "title": "Next Week",
            "sport": "Soccer",
            "start_date": (date.today() + timedelta(days=10)).isoformat(),
            "start_time": "10:00:00",
            "end_time": "11:30:00",
            "location": "Field A",
            "experience_level": "Beginner",
            "max_players": 6,
            "cost": 0,
            "latitude": 40.0,
            "longitude": -73.0,
        }
        r = client.post("/sports-events", headers=auth_headers, json=payload)
        assert r.status_code == 200

        r = client.get("/messaging/notifications/summary", headers=auth_headers)
        assert r.json()["next_event"] is None


# ── Conversation listing — bulk-loaded payload shape ──────────────────────────

class TestConversationList:
    def test_lists_all_kinds_with_unread_counts(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        # DM with one unread
        dm = _start_dm(client, other_auth_headers, user.user_id)
        _send(client, other_auth_headers, dm["conversation_id"], "ping")

        # Group with no unread for alice
        carol = _make_extra_user(db, "carol@example.com")
        _start_group(client, auth_headers, [other_user.user_id, carol.user_id], title="The Crew")

        r = client.get("/messaging/conversations", headers=auth_headers)
        assert r.status_code == 200
        convs = r.json()["conversations"]
        kinds = sorted(c["kind"] for c in convs)
        assert kinds == ["direct", "group"]

        dm_row = next(c for c in convs if c["kind"] == "direct")
        group_row = next(c for c in convs if c["kind"] == "group")
        assert dm_row["unread_count"] == 1
        assert group_row["unread_count"] == 0
        assert group_row["title"] == "The Crew"
        # Members carry the is_admin flag for group-membership UI.
        assert any(m["is_admin"] for m in group_row["members"])

    def test_list_is_ordered_by_recent_activity(
        self, client, db, user, other_user, auth_headers,
    ):
        carol = _make_extra_user(db, "carol@example.com")
        dm1 = _start_dm(client, auth_headers, other_user.user_id)
        dm2 = _start_dm(client, auth_headers, carol.user_id)
        _send(client, auth_headers, dm1["conversation_id"], "to bob")
        _send(client, auth_headers, dm2["conversation_id"], "to carol")
        # dm2 had the most recent message, so it should be first.
        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert ids[0] == dm2["conversation_id"]
        assert ids[1] == dm1["conversation_id"]


# ── Group images + roster shape ──────────────────────────────────────────────

class TestGroupImagery:
    def test_group_create_accepts_image_url(self, client, user, other_user, auth_headers):
        r = client.post("/messaging/group", headers=auth_headers, json={
            "member_ids": [str(other_user.user_id)],
            "title": "Hoops",
            "image_url": "https://cdn.example.com/group.jpg",
        })
        assert r.status_code == 200
        assert r.json()["image_url"] == "https://cdn.example.com/group.jpg"

    def test_admin_can_update_image_and_clear(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Hoops")
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}",
            headers=auth_headers,
            json={"image_url": "https://cdn.example.com/new.jpg"},
        )
        assert r.status_code == 200
        assert r.json()["image_url"] == "https://cdn.example.com/new.jpg"

        # Empty string clears.
        r = client.patch(
            f"/messaging/conversations/{conv['conversation_id']}",
            headers=auth_headers,
            json={"image_url": ""},
        )
        assert r.status_code == 200
        assert r.json()["image_url"] is None


# ── Read receipts ────────────────────────────────────────────────────────────

class TestReadReceipts:
    def test_receipts_endpoint_lists_other_members_only(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        r = client.get(
            f"/messaging/conversations/{conv['conversation_id']}/read-receipts",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        ids = [m["user_id"] for m in data["members"]]
        assert str(other_user.user_id) in ids
        assert str(user.user_id) not in ids

    def test_read_state_after_other_reads(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        _send(client, auth_headers, conv["conversation_id"], "you up?")
        # Other user opens the chat, which bumps last_read_at.
        client.get(
            f"/messaging/conversations/{conv['conversation_id']}/messages",
            headers=other_auth_headers,
        )
        r = client.get("/messaging/conversations", headers=auth_headers)
        convs = r.json()["conversations"]
        rs = next(c for c in convs if c["conversation_id"] == conv["conversation_id"])["read_state"]
        assert rs["read_by_count"] == 1
        assert rs["members_total"] == 1


# ── Admin moderation: delete + ban ───────────────────────────────────────────

class TestModeration:
    def test_admin_can_delete_anyones_message(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        msg = _send(client, other_auth_headers, conv["conversation_id"], "hello")
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/messages/{msg['message_id']}",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

    def test_sender_can_delete_own_message_in_dm(
        self, client, user, other_user, auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        msg = _send(client, auth_headers, conv["conversation_id"], "oops")
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/messages/{msg['message_id']}",
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_non_admin_non_sender_cannot_delete(
        self, client, db, user, other_user, auth_headers, other_auth_headers,
    ):
        carol = _make_extra_user(db, "carol@example.com")
        conv = _start_group(client, auth_headers, [other_user.user_id, carol.user_id])
        msg = _send(client, auth_headers, conv["conversation_id"], "mine")
        # Bob is a member but not admin; he can't delete Alice's message.
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/messages/{msg['message_id']}",
            headers=other_auth_headers,
        )
        assert r.status_code == 403

    def test_kick_with_ban_blocks_re_add(
        self, client, user, other_user, auth_headers,
    ):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        # Admin kicks + bans Bob.
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}?ban=true",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json().get("banned") is True

        # Try to re-add Bob — request should succeed but Bob should NOT be back.
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/members",
            headers=auth_headers,
            json={"member_ids": [str(other_user.user_id)]},
        )
        assert r.status_code == 200
        ids = {m["user_id"] for m in r.json()["members"]}
        assert str(other_user.user_id) not in ids

    def test_lift_ban_allows_re_add(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{other_user.user_id}?ban=true",
            headers=auth_headers,
        )

        # Confirm the ban shows up.
        r = client.get(
            f"/messaging/conversations/{conv['conversation_id']}/bans",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert any(b["user_id"] == str(other_user.user_id) for b in r.json()["bans"])

        # Lift it.
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/bans/{other_user.user_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200

        # Now re-adding should work.
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/members",
            headers=auth_headers,
            json={"member_ids": [str(other_user.user_id)]},
        )
        assert r.status_code == 200
        ids = {m["user_id"] for m in r.json()["members"]}
        assert str(other_user.user_id) in ids

    def test_self_cannot_self_ban(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id])
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/members/{user.user_id}?ban=true",
            headers=auth_headers,
        )
        assert r.status_code == 400


# ── Creator-as-admin fallback ────────────────────────────────────────────────

class TestCreatorAdminFallback:
    def test_creator_is_admin_in_serializer(self, client, user, other_user, auth_headers):
        """Creator should always show as admin even if the column wasn't set
        (e.g. row predates migration 0005)."""
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Crew")
        creator_member = next(m for m in conv["members"] if m["user_id"] == str(user.user_id))
        assert creator_member["is_admin"] is True

    def test_created_by_field_present(self, client, user, other_user, auth_headers):
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Crew")
        assert conv["created_by"] == str(user.user_id)

    def test_creator_admin_fallback_when_column_false(
        self, client, db, user, other_user, auth_headers,
    ):
        """If we manually clear is_admin on the creator row, the serializer
        still surfaces them as admin via the created_by fallback."""
        conv = _start_group(client, auth_headers, [other_user.user_id], title="Crew")
        # Force the creator's is_admin off — simulates a pre-migration row.
        from uuid import UUID as _UUID
        cm = db.query(DBConversationMember).filter_by(
            conversation_id=_UUID(conv["conversation_id"]),
            user_id=user.user_id,
        ).first()
        cm.is_admin = False
        db.commit()
        r = client.get("/messaging/conversations", headers=auth_headers)
        convs = r.json()["conversations"]
        match = next(c for c in convs if c["conversation_id"] == conv["conversation_id"])
        creator_row = next(m for m in match["members"] if m["user_id"] == str(user.user_id))
        assert creator_row["is_admin"] is True


# ── Archive + favorite (per-user state) ──────────────────────────────────────

class TestArchiveFavorite:
    def test_archive_hides_from_default_list(
        self, client, user, other_user, auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        r = client.post(
            f"/messaging/conversations/{conv['conversation_id']}/archive",
            headers=auth_headers,
        )
        assert r.status_code == 200

        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert conv["conversation_id"] not in ids

        # Show up when explicitly requested.
        r = client.get("/messaging/conversations?include_archived=true", headers=auth_headers)
        match = next((c for c in r.json()["conversations"] if c["conversation_id"] == conv["conversation_id"]), None)
        assert match is not None
        assert match["is_archived"] is True

    def test_unarchive_restores_visibility(self, client, user, other_user, auth_headers):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        client.post(f"/messaging/conversations/{conv['conversation_id']}/archive",
                    headers=auth_headers)
        r = client.delete(
            f"/messaging/conversations/{conv['conversation_id']}/archive",
            headers=auth_headers,
        )
        assert r.status_code == 200

        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert conv["conversation_id"] in ids

    def test_archive_is_per_user(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        # Alice archives — Bob should still see it.
        client.post(f"/messaging/conversations/{conv['conversation_id']}/archive",
                    headers=auth_headers)
        r = client.get("/messaging/conversations", headers=other_auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert conv["conversation_id"] in ids

    def test_favorites_pinned_to_top(self, client, db, user, other_user, auth_headers):
        carol = _make_extra_user(db, "carol@example.com")
        # dm1 has the most recent message but dm2 will be favorited.
        dm1 = _start_dm(client, auth_headers, other_user.user_id)
        dm2 = _start_dm(client, auth_headers, carol.user_id)
        _send(client, auth_headers, dm2["conversation_id"], "to carol")
        _send(client, auth_headers, dm1["conversation_id"], "to bob")
        # dm1 is now most recent. Without favoriting, dm1 would be first.
        client.post(f"/messaging/conversations/{dm2['conversation_id']}/favorite",
                    headers=auth_headers)

        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert ids[0] == dm2["conversation_id"]

    def test_unfavorite_returns_to_recency_order(
        self, client, db, user, other_user, auth_headers,
    ):
        carol = _make_extra_user(db, "carol@example.com")
        dm1 = _start_dm(client, auth_headers, other_user.user_id)
        dm2 = _start_dm(client, auth_headers, carol.user_id)
        _send(client, auth_headers, dm2["conversation_id"], "to carol")
        _send(client, auth_headers, dm1["conversation_id"], "to bob")

        client.post(f"/messaging/conversations/{dm2['conversation_id']}/favorite",
                    headers=auth_headers)
        client.delete(f"/messaging/conversations/{dm2['conversation_id']}/favorite",
                      headers=auth_headers)

        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert ids[0] == dm1["conversation_id"]

    def test_new_message_unarchives_conversation(
        self, client, user, other_user, auth_headers, other_auth_headers,
    ):
        conv = _start_dm(client, auth_headers, other_user.user_id)
        # Alice archives.
        r = client.post(f"/messaging/conversations/{conv['conversation_id']}/archive",
                        headers=auth_headers)
        assert r.status_code == 200
        # Bob sends a new message.
        _send(client, other_auth_headers, conv["conversation_id"], "yo!")
        # Conversation should be back in Alice's default inbox.
        r = client.get("/messaging/conversations", headers=auth_headers)
        ids = [c["conversation_id"] for c in r.json()["conversations"]]
        assert conv["conversation_id"] in ids

