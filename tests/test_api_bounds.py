"""The hours of the sample routes are bounded, 1 to 720 (the API-first review, 2026-10-16): an unbounded number was a full-table scan."""


def test_hours_are_bounded(client):
    for path in ("/api/servers/1/samples", "/api/tunnels/1/checks", "/api/configs/1/samples"):
        assert client.get(path + "?hours=0").status_code == 422, path
        assert client.get(path + "?hours=721").status_code == 422, path
        assert client.get(path + "?hours=x").status_code == 422, path
