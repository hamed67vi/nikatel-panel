"""Reading a problem: panel, a service on your server, or outside - with the sentences the panel really writes."""
import pytest

from nikatel.services import diagnosis as dg


@pytest.mark.parametrize("text,origin,kind", [
    ("ssh 203.0.113.5 exit 255: ssh: connect to host 203.0.113.5 port 22: Connection timed out", "outside", "unreachable"),
    ("Server fr1 does not answer: ssh connection lost (Connection timed out)", "outside", "unreachable"),
    ("Config Fastly is not working: curl: (28) SSL connection timeout", "outside", "probe_path"),
    ("srv-a1: the two servers cannot reach each other (loss 100% / 100%): the route between them is cut or filtered", "outside", "route"),
    ("plain ping passes but GRE packets do not (inner link 100% loss): GRE (protocol 47) is filtered on this route", "outside", "route"),
    ("3.84 % of the TCP segments are sent again while the server downloads: the route from this server loses packets.", "outside", "route"),
    ("Mass disruption right now (several servers offline or many tunnels down): this tunnel is not deleted or rebuilt.", "outside", "storm"),
    ("ArvanCloud answered 502: bad gateway", "outside", "remote_service"),
    ("tunnel services are not running: backhaul-iran-tcpmux-9443.service", "server", "service"),
    ("the tunnel's control port 9443 cannot be reached from the other side: it is blocked", "server", "service"),
    ("Stopped: another tunnel test is still running", "panel", "busy"),
    ("Not repaired: this tunnel was already repaired 2 times in the last hour. A human should look at it.", "panel", "limit"),
    ("The panel is read-only: nothing is changed.", "panel", "read_only"),
    ("sqlite3.OperationalError: database is locked", "panel", "database"),
    ("internal error (request id a1b2c3)", "panel", "bug"),
    ("something nobody has seen before", "unknown", "unknown"),
])
def test_classify_the_sentences_the_panel_writes(text, origin, kind):
    c = dg.classify(text)
    assert (c["origin"], c["kind"]) == (origin, kind), c
    assert c["hint"]


def test_the_panel_own_rules_win_over_the_words_they_contain():
    # "timed out" inside the panel's own wait is the panel's, but a connection timeout is outside
    assert dg.classify("timed out")["origin"] == "panel"
    assert dg.classify("ssh connection lost (Connection timed out)")["origin"] == "outside"


def test_a_repair_story_reads_in_four_parts():
    story = ["Starting…", "srv-b2: the link flaps: 4 of the last 10 checks failed - the route is unstable, a restart does not cure that.", "History: “restart” failed 3 times in a row on this tunnel - it is skipped.",
             "Rebuild: Tunnel rebuilt but still lossy", "The loss is not on this pair's route but on one server's own network.", "srv-b2: still not up. A human should look at it."]
    s = dg.summarize(story, False, "autopilot", "srv-b2")
    assert s["problem"].startswith("the link flaps") and "Where:" not in s["problem"] and s["result"].endswith("A human should look at it.")
    long = dg.summarize(["Starting…", "t: the two servers cannot reach each other: cut. Where: the loss is only on the route between this pair."], False, "autopilot", "t")
    assert long["problem"] == "the two servers cannot reach each other: cut."
    assert s["tried"] == ["Rebuild: Tunnel rebuilt but still lossy"]
    assert s["origin"] == "outside" and s["kind"] == "route" and s["hint"] and s["ok"] is False
    run = dg.summarize(["Starting…", "Looking at both servers…"], None, "fix", "t")
    assert run["done"] is False and run["problem"] == "" and run["origin"] == "unknown"
    solved = dg.summarize(["Starting…", "Found: units are not running (state down)", "restarted backhaul-iran.service because x → ok", "Checked again: the tunnel works."], True, "fix", "t")
    assert solved["problem"].startswith("units are not running") and solved["origin"] == "server" and solved["tried"][0].startswith("restarted")


def test_a_refused_request_is_a_plain_answer_and_a_5xx_is_a_bug():
    assert dg.is_plain_answer("GET /api/servers -> 401 in 11ms") and dg.is_plain_answer("POST /api/x -> 422 in 3ms")
    assert not dg.is_plain_answer("GET /api/servers -> 500 in 11ms")
    assert dg.classify("GET /api/servers -> 500 in 11ms")["kind"] == "bug"
    g = dg.group_findings([{"level": "WARNING", "domain": "http", "text": "GET /api/servers -> 401 in 11ms", "ts": "x"}])
    assert g["total"] == 0 and g["groups"] == []


def test_shape_groups_the_same_sentence_with_other_numbers_and_addresses():
    a = dg.shape("ssh 203.0.113.5 exit 255: connect to host 203.0.113.5 port 22: Connection timed out")
    b = dg.shape("ssh 198.51.100.9 exit 255: connect to host 198.51.100.9 port 2222: Connection timed out")
    assert a == b


def test_group_findings_counts_by_origin_and_puts_the_panel_first():
    lines = [{"level": "WARNING", "domain": "ssh", "text": f"ssh 203.0.113.{i} exit 255: Connection timed out", "ts": f"2026-10-12T10:0{i}:00Z"} for i in range(5)]
    lines += [{"level": "ERROR", "domain": "jobs", "text": "job alerts failed: database is locked", "ts": "2026-10-12T10:09:00Z"}, {"level": "INFO", "domain": "http", "text": "fine", "ts": "x"}]
    g = dg.group_findings(lines)
    assert g["by_origin"]["outside"] == 5 and g["by_origin"]["panel"] == 1 and g["total"] == 6
    assert g["groups"][0]["origin"] == "panel" and g["groups"][0]["level"] == "ERROR" and g["groups"][1]["count"] == 5
