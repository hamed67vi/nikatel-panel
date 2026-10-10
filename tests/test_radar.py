from nikatel.services import radar


def test_radar_summary_colours_each_site_and_the_data_centre():
    n = 120
    raw = {"github": [0.5] * n, "google": [0] * n, "bing": [0] * (n - 3) + [3, 3, 3], "wikipedia": [None] * n, "playstation": [0] * n,
           "aparat": [0] * n, "digikala": [0] * n, "divar": [0] * n}
    d = radar.summarize("x", "X", "iran", raw)
    states = {s["key"]: s["state"] for s in d["sites"]}
    assert states["github"] == "warn" and states["google"] == "ok" and states["bing"] == "warn" and states["wikipedia"] == "none"
    assert d["state"] == "warn" and d["bad"] == 0 and len(d["strip"]) == 30 and d["strip"][-1] == 3
    raw["google"] = [4] * n
    raw["bing"] = [4] * n
    assert radar.summarize("x", "X", "iran", raw)["state"] == "bad"
    assert radar.summarize("x", "X", "iran", None)["state"] == "none"          # a data centre the page has no data for
    assert radar.summarize("x", "X", "iran", {k: [0] * n for k, _ in radar.SITES})["state"] == "ok"


def test_radar_api_keeps_the_last_good_answer(client, monkeypatch):
    calls = {"n": 0}

    def fake(item):
        calls["n"] += 1

        if calls["n"] > len(radar.DCS):
            return {**radar.summarize(*item, None), "error": "down"}

        return radar.summarize(*item, {k: [0] * 30 for k, _ in radar.SITES})

    monkeypatch.setattr(radar, "_one", fake)
    radar._cache.update({"at": 0.0, "data": None})
    j = client.get("/api/radar").json()
    assert len(j["rows"]) == len(radar.DCS) and j["error"] == "" and all(r["state"] == "ok" for r in j["rows"])
    j = client.get("/api/radar?refresh=true").json()          # the radar is gone: the cached answer is returned, marked stale
    assert j["stale"] is True and all(r["state"] == "ok" for r in j["rows"])