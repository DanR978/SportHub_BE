def test_privacy_reachable(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Privacy Policy" in r.text


def test_terms_reachable(client):
    r = client.get("/terms")
    assert r.status_code == 200
    assert "Terms of Service" in r.text


def test_accept_terms(client, auth_headers):
    status = client.get("/users/me/terms-status", headers=auth_headers).json()
    assert status["accepted"] is False

    r = client.post("/users/me/accept-terms", headers=auth_headers)
    assert r.status_code == 200

    status = client.get("/users/me/terms-status", headers=auth_headers).json()
    assert status["accepted"] is True
    assert status["accepted_at"] is not None
