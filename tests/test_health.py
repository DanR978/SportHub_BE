def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["message"].startswith("Game Radar")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
