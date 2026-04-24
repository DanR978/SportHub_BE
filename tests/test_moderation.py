def test_block_and_unblock(client, auth_headers, other_user):
    r = client.post(f"/users/{other_user.user_id}/block", headers=auth_headers)
    assert r.status_code == 200

    blocked = client.get("/users/me/blocked", headers=auth_headers).json()
    assert any(b["user_id"] == str(other_user.user_id) for b in blocked["blocked"])

    r = client.delete(f"/users/{other_user.user_id}/block", headers=auth_headers)
    assert r.status_code == 200


def test_cannot_block_self(client, auth_headers, user):
    r = client.post(f"/users/{user.user_id}/block", headers=auth_headers)
    assert r.status_code == 400


def test_report_user(client, auth_headers, other_user):
    r = client.post(
        "/reports",
        headers=auth_headers,
        json={"target_type": "user", "target_id": str(other_user.user_id), "reason": "harassment"},
    )
    assert r.status_code == 200


def test_duplicate_report_blocked(client, auth_headers, other_user):
    body = {"target_type": "user", "target_id": str(other_user.user_id), "reason": "spam"}
    r1 = client.post("/reports", headers=auth_headers, json=body)
    assert r1.status_code == 200
    r2 = client.post("/reports", headers=auth_headers, json=body)
    assert r2.status_code == 400


def test_invalid_reason_rejected(client, auth_headers, other_user):
    r = client.post(
        "/reports",
        headers=auth_headers,
        json={"target_type": "user", "target_id": str(other_user.user_id), "reason": "not_a_real_reason"},
    )
    assert r.status_code == 400


def test_admin_required_for_moderation_endpoints(client, auth_headers):
    r = client.get("/admin/reports", headers=auth_headers)
    assert r.status_code == 403


def test_admin_can_list_and_review(client, auth_headers, admin_auth_headers, other_user):
    # User files a report
    body = {"target_type": "user", "target_id": str(other_user.user_id), "reason": "spam"}
    r = client.post("/reports", headers=auth_headers, json=body)
    assert r.status_code == 200

    listing = client.get("/admin/reports", headers=admin_auth_headers)
    assert listing.status_code == 200
    reports = listing.json()["reports"]
    assert len(reports) == 1
    report_id = reports[0]["report_id"]

    review = client.post(
        f"/admin/reports/{report_id}/review",
        headers=admin_auth_headers,
        json={"status": "dismissed", "notes": "not a violation"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "dismissed"

    # Now pending list should be empty
    again = client.get("/admin/reports?status=pending", headers=admin_auth_headers)
    assert len(again.json()["reports"]) == 0

    stats = client.get("/admin/reports/stats", headers=admin_auth_headers)
    assert stats.json()["counts"]["dismissed"] == 1
