"""The Costs page (services/costs.py, api/costs.py, web/costs.js): what the typed prices add up to in a month, what one GB costs, the history, and the wiring of the page. No panel and no server is asked:
the traffic is a stub, the arithmetic is pure."""
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from nikatel.db import session
from nikatel.models import Panel
from nikatel.services import costs
from nikatel.services.panels import PanelError

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"
D = dt.date
UTC = dt.timezone.utc


def item(**kw):
    base = {"name": "x", "group": "foreign", "kind": "monthly", "amount": 3100, "cur": "IRT"}
    it = costs.clean_item({**base, **kw})
    it["id"] = kw.get("id", 1)
    return it


LAST = D(2026, 10, 31)


def run(items, key="2026-10", today=LAST, traffic=None, rates=None, ok=True, servers=None, known=None):
    return costs.compute(items, rates or {"USD": 0, "EUR": 0}, key, today, traffic or {}, ok, servers, known)


def tr(name, users, node=None, out=None):
    node = float(users if node is None else node)
    out = node if out is None else float(out)
    return {name.lower(): {"name": name, "kind": "node", "users_gb": float(users), "node_gb": node, "out_gb": out, "in_gb": node - out}}


def iran(name, out, inn):
    """An Iran server's record as costs._iran_samples makes it: no customers' traffic, what it sent and received."""
    return {name.lower(): {"name": name, "kind": "iran", "users_gb": 0.0, "out_gb": float(out), "in_gb": float(inn), "node_gb": float(out + inn)}}


# ------------------------------------------------------------------ the months
def test_a_jalali_month_is_a_whole_run_of_days_and_the_next_one_starts_the_day_after():
    assert costs.key_parts("1405-08") == (1405, 8, "jalali") and costs.key_parts("2026-10") == (2026, 10, "gregorian")
    assert costs.month_bounds("1405-08") == (D(2026, 10, 23), D(2026, 11, 21))         # Aban 1405: 1 Aban is 23 October
    assert costs.month_bounds("2026-02") == (D(2026, 2, 1), D(2026, 2, 28))

    for key in ("1405-06", "1405-07", "1405-11", "1405-12"):
        assert costs.month_bounds(costs.shift_key(key, 1))[0] == costs.month_bounds(key)[1] + dt.timedelta(days=1)

    assert costs.key_of(D(2026, 10, 31), "jalali") == "1405-08" and costs.key_of(D(2026, 10, 31), "gregorian") == "2026-10"
    assert costs.shift_key("1405-12", 1) == "1406-01" and costs.shift_key("2026-01", -1) == "2025-12" and costs.shift_key("2026-10", 3) == "2027-01"
    assert costs.key_label("1405-08") == "Aban 1405" and costs.key_label("2026-10") == "October 2026"

    for bad in ("", "2026", "2026-13", "1200-01", "9999-01", "x"):
        with pytest.raises(ValueError):
            costs.key_parts(bad)


# ------------------------------------------------------------------ typed prices
def test_an_item_is_checked_and_a_jalali_date_is_kept_as_a_gregorian_one():
    it = costs.clean_item({"name": " TR1 ", "kind": "monthly", "amount": "12.5", "cur": "USD", "starts": "1405/08/01", "included": 5, "months": 4})
    assert it["name"] == "TR1" and it["amount"] == 12.5 and it["starts"] == "2026-10-23" and it["included"] == 0 and it["months"] == 1 and it["group"] == "other"
    assert costs.clean_item({"name": "t", "kind": "per_tb", "amount": 3, "included": "2"})["included"] == 2.0
    assert costs.clean_item({"name": "i", "kind": "once", "amount": 9, "starts": "2026-10-05", "months": 3})["months"] == 3
    assert costs.clean_item({"name": "v", "kind": "per_gb", "amount": 1500, "tax": "10"})["tax"] == 10.0 and it["tax"] == 0.0 and costs.clean_item({"name": "l", "kind": "monthly", "amount": 1, "cur": "TRY"})["cur"] == "TRY"

    for bad in ({"name": "a", "kind": "monthly", "amount": 1, "tax": 101}, {"name": "a", "kind": "monthly", "amount": 1, "tax": -1},{"name": "", "kind": "monthly", "amount": 1}, {"name": "a", "kind": "daily", "amount": 1}, {"name": "a", "kind": "monthly", "amount": -1}, {"name": "a", "kind": "monthly", "amount": "x"},
                {"name": "a", "kind": "monthly", "amount": float("inf")}, {"name": "a", "kind": "monthly", "amount": 1, "cur": "GBP"}, {"name": "a", "kind": "monthly", "amount": 1, "group": "zzz"},
                {"name": "a", "kind": "once", "amount": 1}, {"name": "a", "kind": "monthly", "amount": 1, "starts": "2026-10-10", "ends": "2026-10-01"}, {"name": "a", "kind": "monthly", "amount": 1, "starts": "yesterday"},
                {"name": "a", "kind": "once", "amount": 1, "starts": "2026-10-10", "months": 500}, {"name": "x" * 61, "kind": "monthly", "amount": 1}):
        with pytest.raises(ValueError):
            costs.clean_item(bad)


# ------------------------------------------------------------------ the arithmetic
def test_a_month_price_is_counted_by_the_days_that_have_passed_and_the_whole_month_is_the_price():
    r = run([item()], today=D(2026, 10, 16))
    assert (r["days"], r["elapsed"], r["open"]) == (31, 16, True) and r["so_far"] == 1600 and r["total"] == 3100
    r = run([item()], today=D(2026, 10, 31))
    assert (r["elapsed"], r["open"]) == (31, True) and r["so_far"] == r["total"] == 3100                # the last day is still going on: the month is not over until the next day
    r = run([item()], today=D(2026, 11, 1))
    assert (r["elapsed"], r["open"]) == (31, False) and r["so_far"] == r["total"] == 3100
    r = run([item()], today=D(2026, 9, 20))
    assert r["elapsed"] == 0 and r["so_far"] == 0 and r["total"] == 3100                    # a month that has not begun: nothing spent yet


def test_a_server_that_came_or_went_in_the_month_is_counted_for_its_days():
    assert run([item(starts="2026-10-11")])["total"] == 2100                                   # 21 of 31 days
    assert run([item(ends="2026-10-10")])["total"] == 1000                                     # 10 of 31 days
    r = run([item(starts="2026-11-05")])
    assert r["total"] == 0 and r["items"][0]["state"] == "idle"


def test_a_year_price_is_a_twelfth_each_month_and_one_payment_is_spread_over_its_months():
    assert run([item(kind="yearly", amount=1200)])["total"] == 100
    pay = item(kind="once", amount=1200, starts="2026-10-11", months=3)
    assert run([pay], today=D(2026, 10, 5))["so_far"] == 0 and run([pay], today=D(2026, 10, 5))["total"] == 400          # not paid yet on the 5th
    assert run([pay], today=D(2026, 10, 31))["so_far"] == 400
    assert run([pay], key="2026-11", today=D(2026, 11, 2))["so_far"] == 400 and run([pay], key="2026-12", today=D(2026, 12, 31))["total"] == 400
    assert run([pay], key="2027-01", today=D(2027, 1, 31))["total"] == 0 and run([pay], key="2026-09", today=D(2026, 9, 30))["total"] == 0
    jal = item(kind="once", amount=500, starts="1405/08/10")
    assert run([jal], key="1405-08", today=D(2026, 11, 21))["total"] == 500


def test_dollars_and_euros_are_turned_into_toman_and_a_missing_rate_is_said_not_counted_as_zero():
    r = run([item(amount=31, cur="USD"), item(amount=10, cur="EUR", id=2)], rates={"USD": 100000, "EUR": 0})
    assert r["total"] == 3100000 and r["usd_rate"] == 100000
    assert [x["state"] for x in r["items"]] == ["ok", "no_rate"] and any("no EUR rate" in w for w in r["warnings"])


def test_a_tax_is_added_on_top_of_the_price_whatever_its_kind_and_the_lira_has_its_own_rate():
    r = run([item(amount=31, cur="USD", tax=10), item(amount=100, cur="TRY", id=2)], rates={"USD": 100000, "TRY": 5420})
    assert r["items"][0]["so_far"] == 3410000 and r["items"][1]["so_far"] == 542000 and r["total"] == 3952000 and r["items"][0]["tax"] == 10
    gb = run([item(kind="per_gb", amount=1500, tax=10, node="TR2")], traffic=tr("TR2", 1000))
    assert gb["items"][0]["so_far"] == 1650000                                                  # 1,500 toman a GB + 10 % = 1,650 a GB
    assert run([item(kind="per_tb", amount=1500 * 1000, tax=10, node="TR2")], traffic=tr("TR2", 1000))["items"][0]["so_far"] == round(1500 * 1000 * 1.1)          # a TB is 1000 GB
    assert run([item(kind="yearly", amount=1200, tax=50)])["total"] == 150 and run([item(kind="once", amount=1000, tax=20, starts="2026-10-02")])["total"] == 1200
    assert run([item(amount=5, cur="TRY")], rates={"USD": 1})["items"][0]["state"] == "no_rate"


class _Resp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", costs.RATES_URL), response=httpx.Response(self.status))

    def json(self):
        return self.data


MARKET = {"updatedAt": 1793467681789, "stale": True, "prices": {"usd": {"priceToman": 270500, "changePct": -0.2065963255367815, "ts": 1793464195000}, "eur": {"priceToman": 302950, "changePct": 0.34779728386883074, "ts": 1793464186000},
                                                                  "try": {"priceToman": 5420, "changePct": -0.7326007326007326, "ts": 1793462958000}, "gbp": {"priceToman": 362880}}}


def test_todays_rates_are_read_from_the_site_and_saved_and_a_bad_answer_saves_nothing(monkeypatch):
    monkeypatch.setattr(costs.httpx, "get", lambda url, **kw: _Resp(MARKET) if url == costs.RATES_URL else pytest.fail(url))
    st = costs.fetch_rates()
    assert st["rates"] == {"USD": 270500.0, "EUR": 302950.0, "TRY": 5420.0} and st["rates_at"] == "2026-10-31 17:28 UTC"
    bad = {"prices": {"usd": {"priceToman": 0}, "eur": {"priceToman": 1}, "try": {"priceToman": 1}}}

    for answer in (_Resp(MARKET, 503), _Resp({"prices": {"usd": {"priceToman": 270500}}}), _Resp(bad), _Resp("nope"), _Resp({"prices": {"usd": {"priceToman": "x"}, "eur": {"priceToman": 1}, "try": {"priceToman": 1}}})):
        monkeypatch.setattr(costs.httpx, "get", lambda url, a=answer, **kw: a)

        with pytest.raises(RuntimeError):
            costs.fetch_rates()

    def down(url, **kw):
        raise httpx.ConnectTimeout("no route")

    monkeypatch.setattr(costs.httpx, "get", down)

    with pytest.raises(RuntimeError):
        costs.fetch_rates()

    assert costs.get_state()["rates"]["USD"] == 270500.0                                         # the last good rates stay


def test_the_rates_come_with_the_days_change_the_time_of_each_price_and_when_they_were_read(monkeypatch):
    monkeypatch.setattr(costs.httpx, "get", lambda url, **kw: _Resp(MARKET))
    st = costs.fetch_rates(1_800_000_000)
    at = lambda ts: dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")
    assert st["rates_info"] == {"USD": {"pct": -0.21, "at": at(1793464195)}, "EUR": {"pct": 0.35, "at": at(1793464186)}, "TRY": {"pct": -0.73, "at": at(1793462958)}}
    assert st["rates_stale"] is True and st["rates_error"] == "" and st["rates_checked"] == 1_800_000_000 and st["rates_auto"] is True
    assert costs.fetch_rates()["rates_checked"] > 1_700_000_000                                              # without a time: now


def test_the_job_reads_the_site_when_it_is_due_and_keeps_the_rates_when_the_site_fails(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        return _Resp(MARKET)

    monkeypatch.setattr(costs.httpx, "get", get)
    t0 = 1_800_000_000.0
    assert costs.run_rates(t0) is True and len(calls) == 1 and costs.get_state()["rates"]["USD"] == 270500.0
    assert costs.run_rates(t0 + 10) is False and len(calls) == 1                                              # read a moment ago
    assert costs.run_rates(t0 + costs.RATES_EVERY + 1) is True and len(calls) == 2
    costs.save_settings(rates_auto=False)
    assert costs.run_rates(t0 + 100_000) is False and len(calls) == 2 and costs.get_state()["rates_auto"] is False          # switched off: the site is not asked
    assert costs.save_settings(rates_auto=True)["rates_checked"] == 0                                         # switched on: due at once
    assert costs.run_rates(t0 + 100_001) is True and len(calls) == 3

    def down(url, **kw):
        raise httpx.ConnectTimeout("no route")

    monkeypatch.setattr(costs.httpx, "get", down)
    t1 = t0 + 200_000
    assert costs.run_rates(t1) is True
    st = costs.get_state()
    assert st["rates"]["USD"] == 270500.0 and st["rates_error"].startswith("the site did not give the rates") and st["rates_checked"] == int(t1)       # the rates stay, the failure is said
    assert costs.run_rates(t1 + 100) is False                                                                  # a failed read is tried again after RATES_RETRY, not at once
    assert costs.run_rates(t1 + costs.RATES_RETRY + 1) is True
    monkeypatch.setattr(costs.httpx, "get", get)
    assert costs.run_rates(t1 + 2 * costs.RATES_RETRY + 2) is True and costs.get_state()["rates_error"] == ""   # a good read clears it


def test_the_rates_by_themselves_are_a_setting_a_job_and_part_of_the_state(client):
    d = client.get("/api/costs").json()
    assert d["rates_auto"] is True and d["rates_info"] == {} and d["rates_checked"] == 0 and d["rates_error"] == "" and d["rates_stale"] is False
    assert client.put("/api/costs/settings", json={"rates_auto": False}).json()["rates_auto"] is False
    assert client.put("/api/costs/settings", json={"rates_auto": True}).json()["rates_auto"] is True
    main = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("cost-rates", 600, costs.run_rates)' in main and "    costs,\n" in main


def test_the_route_for_todays_rates_answers_with_the_rates_or_502(client, monkeypatch):
    monkeypatch.setattr(costs.httpx, "get", lambda url, **kw: _Resp(MARKET))
    r = client.post("/api/costs/rates/fetch").json()
    assert r["ok"] and r["rates"]["TRY"] == 5420.0 and client.get("/api/costs").json()["rates_at"].startswith("2026-10-31")
    monkeypatch.setattr(costs.httpx, "get", lambda url, **kw: _Resp(MARKET, 500))
    assert client.post("/api/costs/rates/fetch").status_code == 502


def test_a_price_per_tb_counts_the_traffic_of_its_node_after_the_free_tb_and_goes_on_to_the_end_of_the_month():
    t = tr("TR2", 2900, 3000)
    it = item(kind="per_tb", amount=2, cur="USD", node="tr2", included=1)
    r = run([it], today=D(2026, 10, 10), traffic=t, rates={"USD": 100000})
    row = r["items"][0]
    assert row["gb"] == 3000 and abs(row["so_far"] - 2 * 100000 * (3000 / 1000 - 1)) <= 1
    assert abs(row["month"] - 2 * 100000 * (3000 * 31 / 10 / 1000 - 1)) <= 1                   # 10 days gone: the month is the same traffic ×3.1
    assert abs(r["per_gb"] - row["so_far"] / 2900) < 0.01
    low = run([item(kind="per_tb", amount=2, cur="IRT", node="TR2", included=10)], today=D(2026, 10, 10), traffic=t)
    assert low["items"][0]["so_far"] == 0                                                      # inside the free TB: nothing to pay
    gb = run([item(kind="per_gb", amount=3, node="*", included=100)], today=D(2026, 10, 31), traffic={**t, **tr("DE1", 500)})
    assert gb["items"][0]["so_far"] == 3 * (3000 + 500 - 100)                                  # all nodes together, node GB


def test_a_price_counts_what_the_node_sends_or_both_ways_as_the_provider_does():
    t = tr("TR2", 900, 1000, out=920)
    assert run([item(kind="per_tb", amount=1000, node="TR2")], traffic=t)["items"][0]["so_far"] == 920                    # the default: what it SENDS (the download), the upload is free
    assert run([item(kind="per_tb", amount=1000, node="TR2", dirs="both")], traffic=t)["items"][0]["so_far"] == 1000      # a provider that bills both ways
    assert costs.clean_item({"name": "a", "kind": "per_tb", "amount": 1})["dirs"] == "out"

    with pytest.raises(ValueError):
        costs.clean_item({"name": "a", "kind": "per_tb", "amount": 1, "dirs": "up"})


def test_a_flat_price_with_traffic_in_its_plan_says_how_much_of_it_the_node_uses():
    plan = item(amount=3635400, node="TR3", quota=12)
    r = run([plan], traffic=tr("TR3", 11000, 12700, out=11676))
    assert r["items"][0]["quota"] == {"tb": 12.0, "gb_month": 11676.0, "pct": 97} and any("close to the 12 TB" in w for w in r["warnings"])
    over = run([plan], traffic=tr("TR3", 11000, 13000, out=12600))
    assert over["items"][0]["quota"]["pct"] == 105 and any("would pass the 12 TB" in w for w in over["warnings"])
    assert run([item(amount=1, node="TR3", quota=12, dirs="both")], traffic=tr("TR3", 11000, 12700, out=11676))["items"][0]["quota"]["pct"] == 106        # both ways: 12.7 TB
    mid = run([plan], today=D(2026, 10, 10), traffic=tr("TR3", 3000, 3100, out=3000))                                                         # 10 days gone: the pace carries on
    assert mid["items"][0]["quota"]["gb_month"] == 9300.0 and mid["items"][0]["quota"]["pct"] == 78 and not mid["warnings"]
    assert run([item(amount=1, node="TR3")], traffic=tr("TR3", 1))["items"][0]["quota"] is None                                              # no limit typed: nothing to compare
    assert run([plan], traffic=tr("TR3", 1), ok=False)["items"][0]["quota"] is None                                                           # the panel did not answer: no guess


def test_an_iran_server_is_priced_by_its_own_traffic_and_is_no_customers_traffic():
    rent = item(amount=1980000, tax=10, server_id=8, group="iran")
    traffic_item = item(kind="per_tb", amount=682000, tax=10, server_id=8, group="iran", id=2)
    t = {**tr("TR1", 100), **iran("iran-a", 8000, 8100)}
    r = run([rent, traffic_item], traffic=t, servers={8: "iran-a"}, known={"TR1"}, ok=False)               # the panel did not answer, the Iran server's own samples did
    rows = {x["id"]: x for x in r["items"]}
    assert rows[1]["state"] == "ok" and rows[2]["state"] == "ok" and rows[2]["gb"] == 8000 and rows[2]["node"] == "iran-a"
    assert r["so_far"] == round(1980000 * 1.1) + round(8 * 682000 * 1.1) and r["gb"] == 100                  # the customers' GB is the nodes' only
    assert [n["name"] for n in r["by_node"]] == ["TR1"] and r["by_server"][0]["name"] == "iran-a"
    assert r["by_server"][0]["priced"] and r["by_server"][0]["per_gb"] == round(r["so_far"] / 8000, 2) and r["per_gb"] is None
    assert any("estimate from the last days" in w for w in r["warnings"])
    both = run([item(kind="per_tb", amount=300000, tax=10, server_id=12, dirs="both")], traffic=iran("iran-c", 11751, 12626), servers={12: "iran-c"})
    assert both["so_far"] == round(24.377 * 300000 * 1.1)                                                      # download and upload are both billed


def test_an_iran_servers_traffic_is_read_from_the_panels_samples():
    from nikatel.models import Server, ServerSample

    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)
    now_ts = int(now.timestamp())

    with session() as db:
        for name, host, role, rx, tx, n in (("iran-a", "192.0.2.11", "iran", 50.0, 100.0, 720), ("iran-b", "192.0.2.12", "iran", 5.0, 5.0, 10), ("TR1", "192.0.2.13", "foreign", 9.0, 9.0, 720)):
            s = Server(name=name, host=host, role=role)
            db.add(s)
            db.flush()
            db.add_all([ServerSample(server_id=s.id, ts=now_ts - (n - i) * 120, cpu=0, ram=0, disk=0, load1=0, rx_mbps=rx, tx_mbps=tx, conn=0) for i in range(n + 1)])

    out, ok, errors, _names = costs.traffic_of(D(2026, 10, 1), D(2026, 10, 31), now)
    assert not ok and errors == ["no PasarGuard panel is connected"]                                           # no panel: said, and the Iran server is still there
    assert list(out) == ["iran-a"]                                                                           # iran-b has under an hour of samples, TR1 is no Iran server
    r = out["iran-a"]
    day = 100 * 125000 * 86400 / 1024 ** 3                                                                     # 100 Mbps for a day, in GB
    assert r["kind"] == "iran" and r["users_gb"] == 0 and abs(r["out_gb"] - day * 10) < 1 and abs(r["in_gb"] - day * 5) < 1 and abs(r["node_gb"] - day * 15) < 1 and r["sample_days"] == 1.0
    assert costs._iran_samples(D(2026, 10, 1), D(2026, 10, 10), now + dt.timedelta(days=30)) == {}           # the samples are older than the last days: no estimate


def test_a_price_by_traffic_without_a_node_or_without_the_traffic_is_left_out_and_said():
    r = run([item(kind="per_tb", amount=2, node="")], traffic=tr("TR2", 10))
    assert r["items"][0]["state"] == "needs_node" and r["total"] == 0 and any("no node is chosen" in w for w in r["warnings"])
    r = run([item(kind="per_tb", amount=2, node="TR2"), item(id=2)], traffic={}, ok=False)
    assert r["items"][0]["state"] == "no_traffic" and r["per_gb"] is None and r["per_gb_month"] is None and r["total"] == 3100 and any("could not be read" in w for w in r["warnings"])
    assert any("no traffic" in w for w in run([item()], traffic={})["warnings"])


def test_a_gb_costs_the_total_divided_by_what_the_customers_used_and_each_node_has_its_own_cost():
    t = {**tr("TR2", 100), **tr("DE1", 300)}
    r = run([item(amount=3100, server_id=5), item(amount=900, id=2, group="domain")], traffic=t, servers={5: "TR2"}, known={"TR2", "DE1"})
    assert r["so_far"] == 4000 and r["gb"] == 400 and r["per_gb"] == 10.0
    by = {n["name"]: n for n in r["by_node"]}
    assert by["TR2"]["priced"] and by["TR2"]["so_far"] == 3100 and by["TR2"]["per_gb"] == 31.0 and not by["DE1"]["priced"] and by["DE1"]["per_gb"] is None
    assert r["by_group"]["foreign"]["so_far"] == 3100 and r["by_group"]["domain"]["so_far"] == 900 and r["items"][0]["node"] == "TR2"
    open_ = run([item(amount=3100)], today=D(2026, 10, 10), traffic=tr("TR2", 100))
    assert open_["per_gb"] == round(1000 / 100, 2) and open_["gb_month"] == 310 and open_["per_gb_month"] == 10.0


# ------------------------------------------------------------------ settings and items through the service and the routes
def test_items_get_numbers_and_survive_a_change_and_the_rates_and_calendar_are_checked():
    a = costs.add_item({"name": "A", "kind": "monthly", "amount": 5})
    b = costs.add_item({"name": "B", "kind": "yearly", "amount": 12})
    assert (a["id"], b["id"]) == (1, 2)
    costs.update_item(1, {"name": "A2", "kind": "monthly", "amount": 6})
    costs.delete_item(2)
    assert [(i["id"], i["name"], i["amount"]) for i in costs.get_state()["items"]] == [(1, "A2", 6.0)]
    assert costs.add_item({"name": "C", "kind": "monthly", "amount": 1})["id"] == 3           # a number is never used twice
    st = costs.save_settings({"USD": "165000"}, "gregorian")
    assert st["rates"]["USD"] == 165000.0 and st["calendar"] == "gregorian" and st["rates"]["EUR"] == 0

    for call in (lambda: costs.update_item(99, {"name": "n", "kind": "monthly", "amount": 1}), lambda: costs.delete_item(99), lambda: costs.save_settings({"IRT": 1}),
                 lambda: costs.save_settings({"USD": -5}), lambda: costs.save_settings(None, "hijri")):
        with pytest.raises(ValueError):
            call()


def _stub_traffic(monkeypatch, nodes=("TR1",), gb=10):
    seen = []

    def fake(first, last, now=None):
        seen.append((first, last))
        return {n.lower(): {"name": n, "users_gb": float(gb), "node_gb": float(gb)} for n in nodes}, True, [], set(nodes)

    monkeypatch.setattr(costs, "traffic_of", fake)
    return seen


def test_a_finished_month_is_frozen_the_first_time_it_is_seen_and_a_new_price_does_not_rewrite_it(monkeypatch):
    _stub_traffic(monkeypatch)
    costs.save_settings(None, "gregorian")
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    now = dt.datetime(2026, 10, 15, 12, tzinfo=UTC)
    cur = costs.summary(now=now)                                                              # the current month: the one before it is put in the history by the way
    assert cur["month"] == "2026-10" and cur["current"] == "2026-10" and cur["open"] and not cur["frozen"]
    assert [h["month"] for h in costs.history()] == ["2026-09"] and costs.history()[0]["total"] == 31000 and costs.history()[0]["per_gb"] == 3100.0
    costs.update_item(1, {"name": "TR1", "kind": "monthly", "amount": 62000})
    old = costs.summary("2026-09", now=now)
    assert old["frozen"] and old["total"] == 31000 and old["current"] == "2026-10"
    new = costs.summary("2026-09", recompute=True, now=now)
    assert new["total"] == 62000 and costs.history()[0]["total"] == 62000


def test_a_month_is_not_frozen_on_its_last_day_and_one_saved_that_way_is_made_again(monkeypatch):
    _stub_traffic(monkeypatch)
    costs.save_settings(None, "gregorian")
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    last_day = dt.datetime(2026, 10, 31, 15, tzinfo=UTC)
    s = costs.summary(now=last_day)
    assert s["month"] == "2026-10" and s["open"] and not s["frozen"] and "2026-10" not in {h["month"] for h in costs.history()}          # (the evening's traffic is not in yet)
    s = costs.summary("2026-11", now=last_day)                                                    # a month that has not begun must not freeze the one that is still going
    assert s["open"] and "2026-10" not in {h["month"] for h in costs.history()}
    early = {**costs.summary("2026-09", now=last_day), "total": 1, "frozen_at": int(last_day.timestamp())}      # what 1.69.173 saved on a last day: frozen_at is ON the day
    costs._freeze("2026-10", early, last_day)
    assert costs.summary("2026-10", now=last_day)["total"] == 31000                                # still going: made again
    nov = dt.datetime(2026, 11, 1, 9, tzinfo=UTC)
    s = costs.summary("2026-10", now=nov)
    assert not s["open"] and s["frozen"] and s["total"] == 31000                                   # the day after: made again and saved for good (not the early copy)
    assert costs.summary("2026-10", now=nov + dt.timedelta(days=3))["frozen"]


def test_the_bar_says_today_in_both_calendars_and_the_days_of_the_month_in_the_other_one(monkeypatch):
    _stub_traffic(monkeypatch)
    costs.save_settings(None, "gregorian")
    s = costs.summary(now=dt.datetime(2026, 10, 31, 15, tzinfo=UTC))
    assert (s["today"], s["today_j"], s["today_g"], s["weekday"]) == ("2026-10-31", "9 Aban 1405", "31 Oct 2026", "Saturday")
    assert s["span_g"] == "1 – 31 Oct 2026" and s["span_j"] == "9 Mehr – 9 Aban 1405"
    j = costs.summary("1405-08", now=dt.datetime(2026, 10, 31, 15, tzinfo=UTC))
    assert j["label"] == "Aban 1405" and j["span_j"] == "1 – 30 Aban 1405" and j["span_g"] == "23 Oct – 21 Nov 2026" and j["today_j"] == "9 Aban 1405"
    assert costs.day_info(D(2026, 12, 25), D(2026, 12, 25), D(2027, 1, 22))["span_g"] == "25 Dec 2026 – 22 Jan 2027"
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    frozen = costs.summary("2026-09", now=dt.datetime(2026, 10, 31, 15, tzinfo=UTC))
    assert frozen["today_j"] == "9 Aban 1405" and frozen["span_g"] == "1 – 30 Sep 2026"


def test_a_month_without_traffic_or_without_prices_is_not_frozen(monkeypatch):
    _stub_traffic(monkeypatch, gb=0)
    costs.save_settings(None, "gregorian")
    now = dt.datetime(2026, 10, 15, 12, tzinfo=UTC)
    costs.summary("2026-09", now=now)
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    s = costs.summary("2026-09", now=now)
    assert not s["frozen"] and costs.history() == [] and s["per_gb"] is None


def test_the_routes_add_change_remove_and_add_up(client, monkeypatch):
    _stub_traffic(monkeypatch, nodes=("TR1", "DE1"), gb=50)
    d = client.get("/api/costs").json()
    assert d["ok"] and d["items"] == [] and d["calendar"] == "jalali" and {g["key"] for g in d["groups"]} >= {"foreign", "iran", "cdn", "domain"} and d["history"] == []
    r = client.post("/api/costs/items", json={"name": "TR1 rent", "group": "foreign", "kind": "monthly", "amount": 10, "cur": "USD", "node": "TR1"})
    assert r.status_code == 200 and r.json()["item"]["id"] == 1
    assert client.put("/api/costs/settings", json={"rates": {"USD": 160000}}).json()["rates"]["USD"] == 160000
    s = client.get("/api/costs/summary").json()
    assert s["ok"] and s["items"][0]["name"] == "TR1 rent" and s["usd_rate"] == 160000 and s["gb"] == 100 and s["by_node"][0]["name"] in ("TR1", "DE1") and s["so_far"] > 0
    assert client.put("/api/costs/items/1", json={"name": "TR1 rent", "kind": "monthly", "amount": 20, "cur": "USD"}).status_code == 200
    assert client.get("/api/costs").json()["items"][0]["amount"] == 20
    assert client.get("/api/costs/summary?month=2026-10").status_code == 200

    for resp in (client.post("/api/costs/items", json={"name": "x", "kind": "daily", "amount": 1}), client.put("/api/costs/items/9", json={"name": "x", "kind": "monthly", "amount": 1}),
                 client.delete("/api/costs/items/9"), client.put("/api/costs/settings", json={"rates": {"GBP": 1}}), client.put("/api/costs/settings", json={"calendar": "hijri"})):
        assert resp.status_code == 400

    assert client.get("/api/costs/summary?month=october").status_code == 422
    assert client.delete("/api/costs/items/1").json()["ok"] and client.get("/api/costs").json()["items"] == []


def test_the_costs_routes_need_a_signed_in_user():
    from fastapi.testclient import TestClient

    from nikatel.main import create_app

    with TestClient(create_app(run_jobs=False)) as c:
        for method, path in (("get", "/api/costs"), ("get", "/api/costs/summary"), ("post", "/api/costs/items"), ("put", "/api/costs/settings"), ("delete", "/api/costs/items/1")):
            assert getattr(c, method)(path).status_code in (401, 403, 422)


# ------------------------------------------------------------------ the traffic from the panels
def _panels(*rows):
    with session() as db:
        for name, nodes in rows:
            db.add(Panel(name=name, type="pasarguard", url="http://x", nodes=[{"name": n} for n in nodes]))


def test_the_traffic_of_a_month_is_asked_from_every_panel_to_today_and_the_nodes_are_merged(monkeypatch):
    _panels(("p1", ["TR1", "TR2"]), ("p2", ["TR1"]))
    asked = []

    def usage(c, preset, frm, to, now):
        asked.append((preset, frm, to))
        return {"by_node": [{"name": "TR1", "gb": 10.0, "up_gb": 4.0, "down_gb": 7.0}, {"name": "TR2", "gb": 5.0}]}

    monkeypatch.setattr(costs.panels, "cred_for", lambda pid: {"id": pid})
    monkeypatch.setattr(costs.pg_usage, "usage", usage)
    out, ok, errors, names = costs.traffic_of(D(2026, 10, 1), D(2026, 10, 31), dt.datetime(2026, 10, 15, 12, tzinfo=UTC))
    assert ok and errors == [] and names == {"TR1", "TR2"} and asked == [("custom", "2026-10-01", "2026-10-15")] * 2
    assert out["tr1"] == {"name": "TR1", "kind": "node", "users_gb": 20.0, "node_gb": 22.0, "out_gb": 14.0, "in_gb": 8.0}          # the node's own traffic is up + down: sent = down, received = up
    assert out["tr2"]["node_gb"] == 10.0 and out["tr2"]["out_gb"] == 10.0                                                    # without the split the users' GB stands in


def test_a_panel_that_does_not_answer_is_said_and_a_month_that_has_not_begun_asks_nothing(monkeypatch):
    _panels(("p1", ["TR1"]), ("p2", ["DE1"]))

    def usage(c, preset, frm, to, now):
        if c["id"] == 1:
            raise PanelError("the panel is down", 0)

        return {"by_node": [{"name": "DE1", "gb": 3.0}]}

    monkeypatch.setattr(costs.panels, "cred_for", lambda pid: {"id": pid})
    monkeypatch.setattr(costs.pg_usage, "usage", usage)
    out, ok, errors, _names = costs.traffic_of(D(2026, 10, 1), D(2026, 10, 31), dt.datetime(2026, 10, 15, tzinfo=UTC))
    assert not ok and "p1: the panel is down" in errors[0] and list(out) == ["de1"]
    monkeypatch.setattr(costs.pg_usage, "usage", lambda *a: pytest.fail("asked a panel for a month that has not begun"))
    assert costs.traffic_of(D(2026, 11, 1), D(2026, 11, 30), dt.datetime(2026, 10, 15, tzinfo=UTC))[1] is True


def test_without_a_panel_the_traffic_is_missing_and_it_is_said():
    out, ok, errors, _names = costs.traffic_of(D(2026, 10, 1), D(2026, 10, 31), dt.datetime(2026, 10, 15, tzinfo=UTC))
    assert out == {} and not ok and errors == ["no PasarGuard panel is connected"]


# ------------------------------------------------------------------ a month that began before the panel started counting
def test_a_month_that_began_before_the_panel_scales_the_gb_and_the_price_of_a_gb():
    t = tr("TR1", 100)
    full = run([item(amount=3100, node="TR1")], today=D(2026, 10, 10), traffic=t)
    half = costs.compute([item(amount=3100, node="TR1")], {"USD": 0, "EUR": 0}, "2026-10", D(2026, 10, 10), t, True, None, None, 0.5)
    assert full["gb_est"] == 100 and full["coverage"] == 1.0 and full["per_gb"] == 10.0 and not full["warnings"]
    assert half["gb"] == 100 and half["gb_est"] == 200 and half["coverage"] == 0.5 and half["gb_month"] == round(200 * 3.1, 2)
    assert half["per_gb"] == 5.0 and half["per_gb_month"] == round(3100 / (200 * 3.1), 2) and half["by_node"][0]["per_gb"] == 5.0                  # the cost of 10 days over the GB of 10 days
    assert any("first 5 days" in w for w in half["warnings"])
    assert costs.compute([item()], {}, "2026-10", D(2026, 10, 10), t, True, None, None, 0)["gb_est"] == 100                                       # a nonsense share is ignored


def test_the_first_day_with_traffic_is_read_from_the_usage_series(monkeypatch):
    _panels(("p1", ["TR1"]))
    monkeypatch.setattr(costs.panels, "cred_for", lambda pid: {"id": pid})
    series = [{"t": "2026-10-01", "bytes": 0}, {"t": "2026-10-02", "bytes": 0}, {"t": "2026-10-03", "bytes": 5}, {"t": "2026-10-04", "bytes": 9}]
    monkeypatch.setattr(costs.pg_usage, "usage", lambda c, preset, frm, to, now: {"granularity": "day", "series": series})
    assert costs.traffic_since(D(2026, 10, 1), D(2026, 10, 4)) == D(2026, 10, 3)
    assert costs.traffic_since(D(2026, 10, 1), D(2026, 10, 2)) is None                                         # one or two days: the series is by the hour
    monkeypatch.setattr(costs.pg_usage, "usage", lambda c, preset, frm, to, now: {"granularity": "hour", "series": series})
    assert costs.traffic_since(D(2026, 10, 1), D(2026, 10, 4)) is None
    monkeypatch.setattr(costs.pg_usage, "usage", lambda c, preset, frm, to, now: {"granularity": "day", "series": [{"t": "2026-10-01", "bytes": 0}]})
    assert costs.traffic_since(D(2026, 10, 1), D(2026, 10, 4)) is None                                         # nothing recorded at all

    def down(*a):
        raise PanelError("down", 0)

    monkeypatch.setattr(costs.pg_usage, "usage", down)
    assert costs.traffic_since(D(2026, 10, 1), D(2026, 10, 4)) is None


def test_the_summary_scales_the_traffic_when_the_panel_began_counting_late(monkeypatch):
    _stub_traffic(monkeypatch, gb=100)
    costs.save_settings(None, "gregorian")
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    monkeypatch.setattr(costs, "traffic_since", lambda first, upto, now=None: D(2026, 10, 6) if first <= D(2026, 10, 6) <= upto else None)
    s = costs.summary(now=dt.datetime(2026, 10, 10, 12, tzinfo=UTC))                                           # the panel counts from the 6th: 5 days of 10
    assert s["coverage"] == 0.5 and s["gb"] == 100 and s["gb_est"] == 200 and s["per_gb"] == round(s["so_far"] / 200, 2) and any("first 5 days" in w for w in s["warnings"])


# ------------------------------------------------------------------ the income of the shop bot
BOT_ANSWER = {"ok": True, "from": 1, "to": 2, "count": 730, "payers": 525, "total": 271091500, "buy": {"n": 349, "sum": 99740000}, "renew": {"n": 133, "sum": 38806500},
              "wallet": {"n": 152, "sum": 103567000}, "other": {"n": 96, "sum": 28978000}}


def _bot(monkeypatch, answer=None, fail=None):
    """A bot server in the inventory, chosen for the income; the SSH call is a stub that remembers what it was asked."""
    from nikatel.models import Server

    with session() as db:
        srv = Server(name="BOT", host="192.0.2.20", role="foreign")
        db.add(srv)
        db.flush()
        sid = srv.id

    costs.save_settings(None, None, sid)
    costs._INC_CACHE.clear()
    costs._INC_FAIL.clear()
    calls = []

    def fake(host, port, user, a, b):
        calls.append((host, port, user, a, b))

        if fail:
            raise RuntimeError(fail)

        return answer if answer is not None else json.dumps(BOT_ANSWER)

    monkeypatch.setattr(costs, "_income_ssh", fake)
    return calls


def test_the_bots_income_is_asked_for_whole_tehran_days_cut_at_now_and_kept_for_a_while(monkeypatch):
    calls = _bot(monkeypatch)
    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)
    r = costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)
    a, b = int(costs.pg_usage.tehran_day_start(D(2026, 10, 1)).timestamp()), int(now.timestamp())
    assert calls == [("192.0.2.20", 22, "root", a, b)] and r["total"] == 271091500 and r["count"] == 730 and r["by_kind"]["wallet"] == {"n": 152, "sum": 103567000}
    costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)
    assert len(calls) == 1                                                                                    # asked again at once: the kept answer
    t0 = costs._clock()
    monkeypatch.setattr(costs, "_clock", lambda: t0 + costs.INCOME_TTL + 1)
    costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)
    assert len(calls) == 2
    costs.bot_income(D(2026, 10, 1), D(2026, 10, 5), now)                                                     # a finished range ends at the end of its last Tehran day
    assert calls[-1][4] == int(costs.pg_usage.tehran_day_start(D(2026, 10, 6)).timestamp())
    assert costs.bot_income(D(2026, 11, 1), D(2026, 11, 3), now)["total"] == 0 and len(calls) == 3           # a month that has not begun: nothing to ask


def test_no_bot_server_chosen_means_no_income_and_a_missing_one_is_said():
    assert costs.bot_income(D(2026, 10, 1), D(2026, 10, 10)) is None
    costs.save_settings(None, None, 0)

    with pytest.raises(ValueError):
        costs.save_settings(None, None, 999)

    with pytest.raises(ValueError):
        costs.save_settings(None, None, -1)


@pytest.mark.parametrize("answer, fail, said", [(None, "the bot host refused: x", "refused"), ("not json", None, "not JSON"), (json.dumps({"ok": False, "error": "boom"}), None, "boom"),
                                                 (json.dumps({"ok": True, "total": 1}), None, "not what was asked"), ("", None, "not JSON")])
def test_a_refusal_or_a_wrong_answer_of_the_bot_host_is_said_and_the_page_goes_on(monkeypatch, answer, fail, said):
    _bot(monkeypatch, answer=answer, fail=fail)
    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)

    with pytest.raises(RuntimeError, match=said):
        costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)

    _stub_traffic(monkeypatch)
    costs.save_settings(None, "gregorian")
    s = costs.summary(now=now)
    assert "income" not in s and any("The shop bot's income could not be read" in w and said in w for w in s["warnings"])


def test_the_income_and_the_profit_are_in_the_summary(monkeypatch):
    _bot(monkeypatch)
    _stub_traffic(monkeypatch, gb=100)
    costs.save_settings(None, "gregorian")
    costs.add_item({"name": "TR1", "kind": "monthly", "amount": 31000})
    s = costs.summary(now=dt.datetime(2026, 10, 10, 12, tzinfo=UTC))                                          # 10 of 31 days
    inc = s["income"]
    assert inc["so_far"] == 271091500 and inc["month"] == round(271091500 * 31 / 10) and inc["bot"]["payers"] == 525
    assert inc["profit_so_far"] == 271091500 - s["so_far"] and inc["profit_month"] == inc["month"] - s["total"]
    assert inc["profit_per_gb"] == round(inc["profit_so_far"] / 100, 2)
    assert not any("could not be read" in w for w in s["warnings"])


def test_the_ssh_call_is_a_list_of_arguments_with_only_numbers_and_needs_the_panels_key(monkeypatch):
    import subprocess

    from nikatel.config import get_settings

    monkeypatch.setattr(costs.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="income_key"):
        costs._income_ssh("192.0.2.20", 22, "root", 1790109000, 1792701000)

    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "income_key").write_text("not a real key", encoding="utf-8")
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}\n', stderr="")

    monkeypatch.setattr(costs.subprocess, "run", run)
    assert costs._income_ssh("192.0.2.20", 2222, "root", 1790109000, 1792701000) == '{"ok": true}\n'
    argv, kw = seen[0]
    assert isinstance(argv, list) and argv[0] == "ssh" and argv[-1] == "income 1790109000 1792701000" and argv[-2] == "root@192.0.2.20" and argv[-3] == "--"
    assert "IdentitiesOnly=yes" in argv and "BatchMode=yes" in argv and str(d / "income_key") in argv and "2222" in argv and not kw.get("shell") and kw["timeout"] == 30
    monkeypatch.setattr(costs.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 255, stdout="", stderr="ssh: connect to host x port 22: Connection refused\n"))

    with pytest.raises(RuntimeError, match="refused"):
        costs._income_ssh("192.0.2.20", 22, "root", 1, 2)

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(costs.subprocess, "run", slow)

    with pytest.raises(RuntimeError, match="did not answer"):
        costs._income_ssh("192.0.2.20", 22, "root", 1, 2)


def test_a_refused_connection_is_tried_again_and_the_connection_is_kept_ten_minutes(monkeypatch):
    import subprocess

    from nikatel.config import get_settings

    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "income_key").write_text("not a real key", encoding="utf-8")
    refused = subprocess.CompletedProcess([], 255, "", "ssh: connect to host x port 22: Connection refused\n")
    good = subprocess.CompletedProcess([], 0, '{"ok": true}\n', "")
    seq, seen, slept = [refused] * (costs.INCOME_TRIES - 1) + [good], [], []

    def run(argv, **kw):
        seen.append(argv)
        return seq[len(seen) - 1]

    monkeypatch.setattr(costs.subprocess, "run", run)
    monkeypatch.setattr(costs.time, "sleep", lambda s: slept.append(s))
    assert costs._income_ssh("192.0.2.20", 22, "root", 1, 2) == '{"ok": true}\n' and len(seen) == costs.INCOME_TRIES and slept == [costs.INCOME_WAIT] * (costs.INCOME_TRIES - 1)
    assert "ControlMaster=auto" in seen[0] and "ControlPersist=600" in seen[0] and any(x.startswith("ControlPath=") and x.endswith("income-%C") for x in seen[0])
    seen.clear()
    slept.clear()
    seq[:] = [refused] * costs.INCOME_TRIES

    with pytest.raises(RuntimeError, match="refused"):
        costs._income_ssh("192.0.2.20", 22, "root", 1, 2)

    assert len(seen) == costs.INCOME_TRIES and len(slept) == costs.INCOME_TRIES - 1
    seen.clear()
    seq[:] = [subprocess.CompletedProcess([], 255, "", "Permission denied (publickey).\n")]

    with pytest.raises(RuntimeError, match="Permission denied"):
        costs._income_ssh("192.0.2.20", 22, "root", 1, 2)

    assert len(seen) == 1                                                                                     # only a refused connection is tried again, a wrong key is not


def test_when_the_bot_host_cannot_be_reached_the_last_good_read_is_shown_old_and_the_host_is_not_hammered(monkeypatch):
    _bot(monkeypatch)
    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)
    first = costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)
    clock = {"t": costs._clock() + costs.INCOME_TTL + 5}
    fails = []

    def refuse(*a):
        fails.append(a)
        raise RuntimeError("the bot host refused: Connection refused")

    monkeypatch.setattr(costs, "_clock", lambda: clock["t"])
    monkeypatch.setattr(costs, "_income_ssh", refuse)
    r = costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)
    assert r["stale"] is True and r["total"] == first["total"] and r["age"] >= costs.INCOME_TTL and "refused" in r["error"] and len(fails) == 1
    clock["t"] += 10
    assert costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)["stale"] and len(fails) == 1               # a moment later: not asked again
    clock["t"] += costs.INCOME_RETRY + 1
    assert costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)["stale"] and len(fails) == 2
    clock["t"] += costs.INCOME_STALE + 1                                                                      # a read this old is no longer shown

    with pytest.raises(RuntimeError, match="refused"):
        costs.bot_income(D(2026, 10, 1), D(2026, 10, 10), now)


def test_a_bot_host_that_just_failed_is_not_asked_again_for_another_month(monkeypatch):
    _bot(monkeypatch)
    fails = []

    def refuse(*a):
        fails.append(a)
        raise RuntimeError("the bot host refused: x")

    monkeypatch.setattr(costs, "_income_ssh", refuse)
    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)

    for first, upto in ((D(2026, 10, 1), D(2026, 10, 10)), (D(2026, 9, 1), D(2026, 9, 30)), (D(2026, 8, 1), D(2026, 8, 31))):          # a click on another month must not wait for the refusal again
        with pytest.raises(RuntimeError, match="refused"):
            costs.bot_income(first, upto, now)

    assert len(fails) == 1


def test_the_income_of_a_finished_range_is_kept_for_good(monkeypatch):
    calls = _bot(monkeypatch)
    now = dt.datetime(2026, 10, 10, 12, tzinfo=UTC)
    costs.bot_income(D(2026, 10, 1), D(2026, 10, 5), now)
    t0 = costs._clock()
    monkeypatch.setattr(costs, "_clock", lambda: t0 + costs.INCOME_STALE - 1)                                 # far past the short keeping time of a range still going
    costs.bot_income(D(2026, 10, 1), D(2026, 10, 5), now)
    assert len(calls) == 1                                                                                    # the days are over: nothing in them can change


def test_a_stale_read_of_the_income_is_said_in_the_summary(monkeypatch):
    _bot(monkeypatch)
    _stub_traffic(monkeypatch, gb=100)
    costs.save_settings(None, "gregorian")
    stale = {**costs._parse_income(json.dumps(BOT_ANSWER), 1, 2), "stale": True, "age": 1500, "error": "the bot host refused: x"}
    monkeypatch.setattr(costs, "bot_income", lambda first, upto, now=None: stale)
    s = costs.summary(now=dt.datetime(2026, 10, 10, 12, tzinfo=UTC))
    assert s["income"]["so_far"] == 271091500 and any("from 25 min ago" in w and "refused" in w for w in s["warnings"])


def test_the_bot_server_is_chosen_through_the_settings_route(client):
    from nikatel.models import Server

    assert client.get("/api/costs").json()["income"] == {"bot_server_id": 0}
    assert client.put("/api/costs/settings", json={"bot_server_id": 999}).status_code == 400

    with session() as db:
        srv = Server(name="BOT", host="192.0.2.20", role="foreign")
        db.add(srv)
        db.flush()
        sid = srv.id

    assert client.put("/api/costs/settings", json={"bot_server_id": sid}).json()["income"]["bot_server_id"] == sid
    assert client.put("/api/costs/settings", json={"bot_server_id": 0}).json()["income"]["bot_server_id"] == 0


# ------------------------------------------------------------------ the page is wired in
def test_the_page_is_in_the_menu_loaded_titled_and_asks_only_routes_that_exist(client):
    index, app, js = (WEB / "index.html").read_text(encoding="utf-8"), (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "costs.js").read_text(encoding="utf-8")
    main = (Path(__file__).resolve().parent.parent / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'data-page="costs"' in index and '/static/costs.js' in index and '/static/costs.css' in index and '"costs.js"' in main and '"costs.css"' in main
    assert index.index('data-page="pgpanel"') < index.index('data-page="costs"') < index.index('data-page="bot"')
    assert '  costs: ["Finance", ' in app and "PAGES.costs = async" in js and "<span>Finance</span>" in index and 'data-short="Finance"' in index          # the menu calls the whole section Finance (its internal name stays costs)
    for part in ('["overview", "Overview"]', '["income", "Income"]', '["expenses", "Expenses"]', '"Foreign servers"', '"Iran servers"', '"CDN and traffic"', '"Domains"'):
        assert part in js                                                                                       # Overview / Income / Expenses, and a tab for each section of the expenses
    spec = client.get("/api/openapi.json").json()["paths"]

    for method, path in (("GET", "/api/costs"), ("GET", "/api/costs/summary"), ("PUT", "/api/costs/settings"), ("POST", "/api/costs/rates/fetch"), ("POST", "/api/costs/items"), ("PUT", "/api/costs/items/{item_id}"), ("DELETE", "/api/costs/items/{item_id}")):
        assert method.lower() in spec.get(path, {}), (method, path)
        assert path.replace("/{item_id}", "") in js


def test_the_bar_shows_today_and_the_dollar_against_the_lira_and_each_server_has_a_card_without_the_tax_written():
    js = (WEB / "costs.js").read_text(encoding="utf-8")
    assert "USD/TRY" in js and "d.rates.USD / d.rates.TRY" in js and "s.today_j" in js and "s.today_g" in js and "s.span_g" in js and "s.span_j" in js
    assert "function csCard(" in js and "csCardRows(" in js and "Price per TB of traffic" in js and '"Traffic used"' in js and '"Traffic cost"' in js and '"Rent"' in js
    price = js[js.index("const csPrice"):js.index("const csGroupSum")]
    assert "tax" not in price.replace("csTaxed", "")                                                              # the price line is the price with its tax in it: the tax is not written
    assert js.count("% tax") == 1                                                                                 # (only the tooltip says what was typed)


def test_the_page_never_writes_a_price_without_asking_and_escapes_what_the_owner_typed():
    js = (WEB / "costs.js").read_text(encoding="utf-8")
    assert "await gconfirm(" in js and js.count("esc(") > 20
    assert "innerHTML = ${" not in js and "localStorage" not in js
