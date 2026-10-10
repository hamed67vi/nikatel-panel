"""Arvan Radar (radar.arvancloud.ir): how ArvanCloud's points of presence reach eight well-known sites, per data centre, minute by minute.
It describes data centres in Iran and abroad, not home or mobile internet. The public JSON of the page is read from the panel server and cached."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..log import get_logger

log = get_logger("radar")
URL = "https://radar.arvancloud.ir/api/v1/internet-monitoring"
SITES = (("github", "GitHub"), ("google", "Google"), ("bing", "Bing"), ("wikipedia", "Wikipedia"), ("playstation", "PlayStation"),
         ("aparat", "Aparat"), ("digikala", "Digikala"), ("divar", "Divar"))
DCS = (("mci", "MCI (Hamrah-e Avval)", "iran"), ("irancell", "Irancell", "iran"), ("parsonline", "ParsOnline", "iran"), ("hostiran", "HostIran", "iran"),
       ("bertina-thr", "Bertina Tehran", "iran"), ("tebyan", "Tebyan", "iran"), ("tehran-3", "Tehran 3", "iran"),
       ("istanbul", "Istanbul", "foreign"), ("frankfort", "Frankfurt", "foreign"), ("bertina-xrx", "Bertina XRX", "foreign"), ("paris", "Paris", "foreign"),
       ("los-angles", "Los Angeles", "foreign"), ("washington", "Washington", "foreign"), ("singapore", "Singapore", "foreign"),
       ("moscow", "Moscow", "foreign"), ("saint-petersburg", "Saint Petersburg", "foreign"), ("hong-kong", "Hong Kong", "foreign"))
TTL = 120          # seconds the answer is kept: the page itself moves once a minute
RECENT = 15        # minutes that decide the colour of a site
STRIP = 60         # minutes shown as the small history strip
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def level(avg: float | None) -> str:
    """One site seen from one data centre: nothing wrong / some probes failed / failing."""
    if avg is None:
        return "none"

    return "ok" if avg <= 0 else "warn" if avg < 1 else "bad"


def summarize(key: str, name: str, group: str, raw: dict | None) -> dict:
    """Pure: the page's series of one data centre -> colours. The series run oldest first, one value per minute, 0 = fine, up to 4 = failing, null = no data."""
    sites, strip_src = [], []

    for sk, label in SITES:
        series = (raw or {}).get(sk)

        if not isinstance(series, list):
            sites.append({"key": sk, "name": label, "state": "none", "avg": None})
            continue

        tail = [v for v in series[-RECENT:] if v is not None]
        avg = sum(tail) / len(tail) if tail else None
        sites.append({"key": sk, "name": label, "state": level(avg), "avg": None if avg is None else round(avg, 2)})
        strip_src.append(series[-STRIP:])

    known = [s for s in sites if s["state"] != "none"]
    bad, warn = sum(1 for s in known if s["state"] == "bad"), sum(1 for s in known if s["state"] == "warn")
    state = "none" if not known else "bad" if bad >= 2 else "warn" if bad else "ok"      # a site that fails only now and then (below level 1) colours its own square, not the data centre
    strip = []

    for i in range(STRIP // 2):        # 30 cells of two minutes: the worst value of any site in that time
        cells = [v for src in strip_src for v in src[i * 2:i * 2 + 2] if v is not None]
        strip.append(None if not cells else round(max(cells), 1))

    return {"key": key, "name": name, "group": group, "state": state, "bad": bad, "warn": warn, "sites": sites, "strip": strip}


def _one(item) -> dict:
    key, name, group = item

    try:
        r = httpx.get(URL, params={"isp": key}, timeout=15, headers={"user-agent": "nikatel-panel"})
        r.raise_for_status()
        return summarize(key, name, group, r.json())
    except (httpx.HTTPError, ValueError) as e:
        return {**summarize(key, name, group, None), "error": str(e)[:80]}


def fetch(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["at"] < TTL:
            return _cache["data"]

    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(_one, DCS))

    data = {"at": int(time.time()), "source": "https://radar.arvancloud.ir/", "rows": rows,
            "error": "the radar does not answer" if all(r.get("error") for r in rows) else ""}

    with _lock:
        if not data["error"]:
            _cache.update({"at": time.time(), "data": data})
        elif _cache["data"] is not None:
            return {**_cache["data"], "stale": True}

    return data