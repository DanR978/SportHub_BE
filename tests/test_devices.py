def test_register_and_unregister_device(client, auth_headers):
    token = "a" * 64
    r = client.post("/users/me/devices", headers=auth_headers, json={"token": token, "platform": "ios"})
    assert r.status_code == 200

    r = client.delete(f"/users/me/devices/{token}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


def test_register_rejects_short_token(client, auth_headers):
    r = client.post("/users/me/devices", headers=auth_headers, json={"token": "short"})
    assert r.status_code == 400


def test_register_reassigns_on_duplicate(client, auth_headers, other_auth_headers):
    token = "b" * 64
    # First user registers
    r1 = client.post("/users/me/devices", headers=auth_headers, json={"token": token})
    assert r1.status_code == 200
    # Second user registers the same token (device resale / reinstall) — reassigns
    r2 = client.post("/users/me/devices", headers=other_auth_headers, json={"token": token})
    assert r2.status_code == 200
    # First user can no longer delete it — it's not theirs anymore
    r3 = client.delete(f"/users/me/devices/{token}", headers=auth_headers)
    assert r3.json()["deleted"] == 0
