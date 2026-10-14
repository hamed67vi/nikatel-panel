"""The Core tab of a PasarGuard panel: parsing, secrets, checks, warnings, the line diff (and its JavaScript twin), the cores, dry run, save, backups,
rollback, restart, and the API. A fake panel stands in for PasarGuard; no test reaches a real one."""
import copy
import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Panel
from nikatel.services import panels, pg_cores
from nikatel.services.panels import PanelError

ROOT = Path(__file__).resolve().parents[1]
C = {"id": 7, "type": "pasarguard"}
SECRETS = ("PRIVATE-KEY-AAAA", "OBFS-PASSWORD-CCCC", "CLIENT-UUID-DDDD", "INLINE-CERT-KEY-EEEE")


def core_config() -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "VLESS WS 80", "port": 80, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}, "streamSettings": {"network": "ws", "security": "none", "wsSettings": {"path": "/ws"}}},
            {"tag": "VLESS REALITY 443", "port": 443, "protocol": "vless", "settings": {"clients": [{"id": "CLIENT-UUID-DDDD", "email": "fixed"}], "decryption": "none"},
             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"privateKey": "PRIVATE-KEY-AAAA", "publicKey": "public-key-bbbb", "shortIds": ["ab"], "dest": "example.test:443", "serverNames": ["example.test"]}}},
            {"tag": "TROJAN TLS 2087", "port": 2087, "protocol": "trojan", "settings": {"clients": []},
             "streamSettings": {"network": "ws", "security": "tls", "tlsSettings": {"certificates": [{"certificateFile": "/certs/a.pem", "keyFile": "/certs/a.key"}, {"certificate": ["line one"], "key": ["INLINE-CERT-KEY-EEEE"]}]}}},
            {"tag": "SS TCP", "port": 1080, "protocol": "shadowsocks", "settings": {"clients": [], "network": "tcp"}, "streamSettings": {"network": "tcp", "security": "none"}},
            {"tag": "HY2 2083", "port": 2083, "protocol": "hysteria", "settings": {"clients": [], "version": 2},
             "streamSettings": {"network": "hysteria", "security": "tls", "finalmask": {"udp": [{"type": "salamander", "settings": {"password": "OBFS-PASSWORD-CCCC"}}]}}},
        ],
        "outbounds": [{"tag": "DIRECT", "protocol": "freedom"}, {"tag": "BLOCK", "protocol": "blackhole"},
                      {"tag": "prov-a", "protocol": "socks", "settings": {"servers": [{"address": "203.0.113.9", "port": 1080}]}}],
        "routing": {"rules": [{"type": "field", "ip": ["geoip:private"], "outboundTag": "BLOCK"}, {"type": "field", "inboundTag": ["prov-a"], "outboundTag": "prov-a"}]},
    }


HOSTS = [{"id": 1, "remark": "h1", "inbound_tag": "VLESS WS 80", "port": None, "is_disabled": False},
         {"id": 2, "remark": "h2", "inbound_tag": "VLESS WS 80", "port": 8080, "is_disabled": True},
         {"id": 3, "remark": "h3", "inbound_tag": "TROJAN TLS 2087", "port": None, "is_disabled": False}]
GROUPS = [{"id": 1, "name": "Main", "inbound_tags": ["VLESS WS 80", "VLESS REALITY 443", "TROJAN TLS 2087"], "total_users": 629, "is_disabled": False}]


class Fake:
    """A PasarGuard 5.x panel: one core, hosts, groups, nodes. It keeps what a PUT gives it, records every call and can be told to fail."""

    def __init__(self):
        self.cores = {1: {"id": 1, "name": "Default Core Config", "type": "xray", "config": core_config(), "exclude_inbound_tags": [], "fallbacks_inbound_tags": [], "created_at": "2026-01-01T00:00:00Z"}}
        self.hosts = copy.deepcopy(HOSTS)
        self.groups = copy.deepcopy(GROUPS)
        self.nodes = [{"id": 1, "name": "node-a", "status": "connected", "xray_version": "26.3.27", "address": "203.0.113.21"},
                      {"id": 2, "name": "node-b", "status": "connected", "xray_version": "26.3.27", "address": "203.0.113.22"}]
        self.log: list[tuple[str, str]] = []
        self.puts: list[dict] = []
        self.restarts: list[int] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.forget_once = False

    @property
    def config(self) -> dict:
        return self.cores[1]["config"]

    def __call__(self, c, method, path, **kw):
        u = urlsplit(path)
        self.log.append((method, path))

        if (method, u.path) in self.fail:
            raise self.fail[(method, u.path)]

        if u.path == "/api/cores" and method == "GET":
            return {"count": len(self.cores), "cores": copy.deepcopy(list(self.cores.values()))}

        m = re.fullmatch(r"/api/core/(\d+)(/restart)?", u.path)

        if m:
            cid = int(m.group(1))

            if cid not in self.cores:
                raise PanelError("HTTP 404: Core config not found", 404)

            if m.group(2) and method == "POST":
                self.restarts.append(cid)
                return None

            if method == "GET":
                return copy.deepcopy(self.cores[cid])

            if method == "PUT":
                return self._put(cid, u.query, kw["body"])

        if u.path == "/api/hosts":
            return copy.deepcopy(self.hosts)

        if u.path == "/api/groups":
            return {"groups": copy.deepcopy(self.groups), "total": len(self.groups)}

        if u.path == "/api/nodes":
            return {"nodes": copy.deepcopy(self.nodes), "total": len(self.nodes)}

        raise AssertionError(f"unexpected call {method} {path}")

    def _put(self, cid: int, query: str, body: dict):
        self.puts.append({"core": cid, "query": query, "body": copy.deepcopy(body)})
        core = self.cores[cid]

        if core["type"] == "xray" and not body["config"].get("inbounds"):
            raise PanelError("HTTP 400: config doesn't have inbounds", 400)

        cfg = copy.deepcopy(body["config"])

        if self.forget_once:                                            # a panel that answers 200 and keeps something else
            self.forget_once = False
            cfg["inbounds"] = cfg["inbounds"][1:]

        core.update(config=cfg, name=body.get("name", core["name"]), type=body.get("type", core["type"]),
                    exclude_inbound_tags=body.get("exclude_inbound_tags", []), fallbacks_inbound_tags=body.get("fallbacks_inbound_tags", []))
        return copy.deepcopy(core)


@pytest.fixture(autouse=True)
def clean_backups():
    """The data folder lives for the whole test run: every test starts without the backups of the tests before it."""
    d = get_settings().data_dir / "pg-backups"

    if d.exists():
        for f in d.glob("cores-*"):
            f.unlink()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(panels, "call", f)
    return f


@pytest.fixture
def rw(monkeypatch):
    """The panel in read-write mode (the tests start read-only, like a new install)."""
    monkeypatch.setattr(get_settings(), "read_only", False)


# ------------------------------------------------------------------ reading an edited text
def test_parse_text_is_strict_and_says_where():
    assert pg_cores.parse_text('{"a": [1, 2]}') == {"a": [1, 2]}

    with pytest.raises(ValueError, match=r"not valid JSON: .*line 3, column 1"):
        pg_cores.parse_text('{\n  "a": 1,\n}')

    with pytest.raises(ValueError, match="appears twice"):
        pg_cores.parse_text('{"tag": "a", "tag": "b"}')

    with pytest.raises(ValueError, match="NaN is not valid JSON"):
        pg_cores.parse_text('{"a": NaN}')

    with pytest.raises(ValueError, match="not valid JSON"):
        pg_cores.parse_text("[" * 5000)


# ------------------------------------------------------------------ secrets
def test_secrets_become_placeholders_and_come_back():
    cfg = core_config()
    masked, n = pg_cores.mask_secrets(cfg)
    raw = json.dumps(masked)
    assert n == 4 and not any(s in raw for s in SECRETS)
    assert "public-key-bbbb" in raw and "/certs/a.key" in raw and "example.test" in raw             # a public key, a key FILE name and the rest stay readable
    assert any(s in json.dumps(cfg) for s in SECRETS)                                               # the original is not touched
    assert pg_cores.mask_secrets(cfg)[0] == masked                                                  # the same secret always has the same placeholder
    assert pg_cores.unmask_secrets(masked, cfg) == cfg

    typed = copy.deepcopy(masked)
    typed["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"] = "NEW-KEY-TYPED"
    assert pg_cores.unmask_secrets(typed, cfg)["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"] == "NEW-KEY-TYPED"      # a secret that is typed in stays

    clone = copy.deepcopy(masked)
    clone["inbounds"].append(copy.deepcopy(clone["inbounds"][1]))
    clone["inbounds"][-1]["tag"] = "copy"
    assert pg_cores.unmask_secrets(clone, cfg)["inbounds"][-1]["streamSettings"]["realitySettings"]["privateKey"] == "PRIVATE-KEY-AAAA"  # a copied block keeps its secret

    bad = copy.deepcopy(masked)
    bad["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"] = "<secret:0123456789>"
    with pytest.raises(ValueError, match=r"the placeholder <secret:0123456789> at inbounds\[1\]\.streamSettings\.realitySettings\.privateKey is not a secret of the config on the panel"):
        pg_cores.unmask_secrets(bad, cfg)


def test_what_counts_as_a_secret():
    cfg = {"settings": {"clients": [{"id": "u-1", "flow": "x"}], "password": "p"}, "id": "top-level-id-is-not-a-secret", "streamSettings": {"hysteriaSettings": {"auth": "a"}, "tlsSettings": {"certificates": [{"keyFile": "/k", "key": ["zz-key-line"]}]}},
           "users": [{"id": "u-2", "pass": "q", "user": "name"}], "key": "plain", "auth": "noauth", "password": ""}
    masked, n = pg_cores.mask_secrets(cfg)
    assert n == 6
    assert masked["settings"]["clients"][0]["flow"] == "x" and masked["id"] == "top-level-id-is-not-a-secret" and masked["key"] == "plain" and masked["auth"] == "noauth" and masked["password"] == ""
    assert masked["users"][0]["user"] == "name" and "u-2" not in json.dumps(masked) and "zz-key-line" not in json.dumps(masked)


# ------------------------------------------------------------------ the structure
@pytest.mark.parametrize("listen,public", [(None, True), ("", True), ("0.0.0.0", True), ("::", True), ("8.8.8.8", True), ("10.1.2.3", True), ("node.example.test", True),
                                          ("127.0.0.1", False), ("127.0.0.9", False), ("::1", False), ("[::1]", False), ("localhost", False), ("/dev/shm/x.sock", False), ("@abstract", False)])
def test_which_listen_addresses_are_public(listen, public):
    assert pg_cores.is_public_listen(listen) is public


def test_rows_of_inbounds_and_outbounds():
    rows = {r["tag"]: r for r in pg_cores.inbound_rows(core_config())}
    assert rows["VLESS WS 80"] == {"tag": "VLESS WS 80", "port": 80, "listen": "", "protocol": "vless", "network": "ws", "security": "none", "public": True}
    assert rows["VLESS REALITY 443"]["security"] == "reality" and rows["SS TCP"]["network"] == "tcp"
    ob = pg_cores.outbound_rows(core_config())
    assert [o["tag"] for o in ob] == ["DIRECT", "BLOCK", "prov-a"] and ob[2]["target"] == "203.0.113.9:1080" and ob[0]["target"] == ""
    assert pg_cores.rule_count(core_config()) == 2 and pg_cores.rule_count({}) == 0 and pg_cores.rule_count(None) == 0
    assert pg_cores.inbound_rows({"inbounds": ["junk", {"protocol": "vless"}]})[0]["tag"] == "" and pg_cores.inbound_rows([1]) == []        # a strange shape gives rows or nothing, never a crash


def good() -> dict:
    return {"inbounds": [{"tag": "a", "port": 1}], "outbounds": [{"tag": "o", "protocol": "freedom"}]}


def test_check_config_names_every_structure_problem():
    assert pg_cores.check_config(core_config()) == []
    assert pg_cores.check_config([]) == ["the config must be a JSON object"] and pg_cores.check_config(None) == ["the config must be a JSON object"]
    assert pg_cores.check_config({}) == ["“inbounds” must be an array", "“outbounds” must be an array"]
    assert pg_cores.check_config({"inbounds": [], "outbounds": []}) == ["the config has no inbounds: the panel refuses it", "the config has no outbounds: the panel refuses it"]
    assert pg_cores.check_config({**good(), "inbounds": [{"tag": "a"}, {"tag": "b"}, {"tag": "a"}]}) == ["the tag “a” is used by inbound #1 and inbound #3: every tag must be unique"]
    assert pg_cores.check_config({**good(), "inbounds": [{"port": 1}, {"tag": "  "}, {"tag": 5}, "junk"]}) == ["inbound #1 has no tag", "inbound #2 has no tag", "inbound #3 has no tag", "inbound #4 is not an object"]
    assert "comma or <=>" in pg_cores.check_config({**good(), "inbounds": [{"tag": "a,b"}]})[0] and "comma or <=>" in pg_cores.check_config({**good(), "inbounds": [{"tag": "a<=>b"}]})[0]
    assert pg_cores.check_config({**good(), "outbounds": [{"protocol": "freedom"}, "junk"]}) == ["outbound #1 has no tag: the panel refuses it", "outbound #2 is not an object"]
    assert pg_cores.check_config({"x": 1}, "wireguard") == [] and pg_cores.check_config({}, "wireguard") == ["the config must not be empty"]      # only an Xray core has inbounds and outbounds


# ------------------------------------------------------------------ the warnings
def without(cfg: dict, tag: str) -> dict:
    new = copy.deepcopy(cfg)
    new["inbounds"] = [i for i in new["inbounds"] if i["tag"] != tag]
    return new


def inbound(cfg: dict, tag: str) -> dict:
    return next(i for i in cfg["inbounds"] if i["tag"] == tag)


def codes(w: list[dict]) -> list[str]:
    return [x["code"] for x in w]


def test_a_removed_inbound_that_hosts_and_groups_use_is_a_danger():
    old = core_config()
    w = pg_cores.warnings(old, without(old, "VLESS WS 80"), HOSTS, GROUPS)
    assert codes(w) == ["tag_removed"] and w[0]["level"] == "danger" and w[0]["tag"] == "VLESS WS 80"
    assert "2 hosts" in w[0]["text"] and "“Main” (629 users)" in w[0]["text"]
    assert pg_cores.warnings(old, without(old, "SS TCP"), HOSTS, GROUPS) == []                         # nothing uses it: only the diff shows it
    assert pg_cores.warnings(old, without(old, "HY2 2083"), HOSTS, GROUPS) == []
    unknown = pg_cores.warnings(old, without(old, "SS TCP"), None, GROUPS)
    assert codes(unknown) == ["tag_removed"] and unknown[0]["level"] == "warn" and "could not be read" in unknown[0]["text"]


def test_a_renamed_inbound_is_recognised():
    old = core_config()
    new = copy.deepcopy(old)
    inbound(new, "TROJAN TLS 2087")["tag"] = "TROJAN TLS 2088"
    w = pg_cores.warnings(old, new, HOSTS, GROUPS)
    assert codes(w) == ["tag_removed"] and "looks renamed to “TROJAN TLS 2088”" in w[0]["text"] and "1 host and the group “Main”" in w[0]["text"]


def test_a_changed_port_names_the_hosts_that_follow_it():
    old = core_config()
    new = copy.deepcopy(old)
    inbound(new, "VLESS WS 80")["port"] = 8080
    w = pg_cores.warnings(old, new, HOSTS, GROUPS)
    assert codes(w) == ["port_changed"] and w[0]["level"] == "warn"
    assert "port 80 → 8080" in w[0]["text"] and "2 hosts use this inbound, 1 of them follows its port, so their links change" in w[0]["text"]
    inbound(new, "VLESS WS 80")["port"] = "80"                                                         # 80 written as text is the same port
    assert pg_cores.warnings(old, new, HOSTS, GROUPS) == []
    inbound(new, "SS TCP")["port"] = 1081                                                               # no host uses it: still a changed port
    assert "port 1080 → 1081." in pg_cores.warnings(old, new, HOSTS, GROUPS)[0]["text"]


def test_an_inbound_without_tls_that_newly_accepts_outside_connections_is_flagged():
    old = core_config()
    new = copy.deepcopy(old)
    new["inbounds"].append({"tag": "VLESS TCP 1616", "port": 1616, "protocol": "vless", "settings": {"clients": []}, "streamSettings": {"network": "tcp", "security": "none"}})
    w = pg_cores.warnings(old, new, HOSTS, GROUPS)
    assert codes(w) == ["plain_exposed"] and w[0]["tag"] == "VLESS TCP 1616" and "no TLS or REALITY" in w[0]["text"] and "port 1616" in w[0]["text"]

    new = copy.deepcopy(old)
    inbound(new, "TROJAN TLS 2087")["streamSettings"]["security"] = "none"                              # TLS taken away from an existing inbound
    assert codes(pg_cores.warnings(old, new, HOSTS, GROUPS)) == ["plain_exposed"]

    new = copy.deepcopy(old)
    inbound(new, "VLESS WS 80")["listen"] = "127.0.0.1"                                                 # an exposed one moved to the machine itself: better, no warning
    assert pg_cores.warnings(old, new, HOSTS, GROUPS) == []
    back = pg_cores.warnings(new, old, HOSTS, GROUPS)                                                   # and the other way round it is flagged again
    assert codes(back) == ["plain_exposed"]

    for quiet in ({"tag": "SS 2", "port": 1090, "protocol": "shadowsocks", "streamSettings": {"network": "tcp", "security": "none"}},          # has its own encryption
                  {"tag": "local", "port": 1091, "protocol": "vless", "listen": "127.0.0.1", "streamSettings": {"network": "tcp", "security": "none"}},   # not reachable from outside
                  {"tag": "tls", "port": 1092, "protocol": "vless", "streamSettings": {"network": "tcp", "security": "tls"}}):
        new = copy.deepcopy(old)
        new["inbounds"].append(quiet)
        assert pg_cores.warnings(old, new, HOSTS, GROUPS) == [], quiet["tag"]

    assert pg_cores.warnings(old, copy.deepcopy(old), HOSTS, GROUPS) == []                              # what the old config already had is not repeated


def test_routing_warnings():
    old = core_config()
    new = copy.deepcopy(old)
    new["outbounds"] = [o for o in new["outbounds"] if o["tag"] != "BLOCK"]
    w = pg_cores.warnings(old, new, HOSTS, GROUPS)
    assert codes(w) == ["rule_target_missing"] and w[0]["level"] == "danger" and "“BLOCK”" in w[0]["text"] and "first outbound" in w[0]["text"]

    new = copy.deepcopy(old)
    new["routing"]["rules"].append({"type": "field", "balancerTag": "ghost"})
    assert codes(pg_cores.warnings(old, new, HOSTS, GROUPS)) == ["rule_balancer_missing"]

    new = copy.deepcopy(old)
    new["outbounds"].insert(0, {"tag": "prov-b", "protocol": "socks"})
    w = pg_cores.warnings(old, new, HOSTS, GROUPS)
    assert codes(w) == ["default_outbound"] and "from “DIRECT” to “prov-b”" in w[0]["text"]

    new = copy.deepcopy(old)
    new["outbounds"].append({"tag": "DIRECT", "protocol": "freedom"})
    assert codes(pg_cores.warnings(old, new, HOSTS, GROUPS)) == ["outbound_twice"]

    broken = copy.deepcopy(old)
    broken["routing"]["rules"].append({"type": "field", "outboundTag": "gone"})                         # a dangling rule the old config already had: not repeated
    assert pg_cores.warnings(broken, copy.deepcopy(broken), HOSTS, GROUPS) == []


def test_hosts_and_groups_in_other_shapes_do_not_break_the_warnings():
    use = pg_cores.usage_by_tag([{"inbound_tag": None}, {"tag": "x", "port": 0}, {"inbound_tag": "x", "port": 443, "is_disabled": True}], [{"inbound_tags": "not a list"}, {"inbound_tags": ["x", 5, ""], "name": None, "id": 9}])
    assert use["x"] == {"hosts": 2, "hosts_on": 1, "own_port": 1, "groups": [{"name": "9", "users": None}]}
    old = core_config()
    w = pg_cores.warnings(old, without(old, "VLESS WS 80"), [{"inbound_tag": "VLESS WS 80"}], [{"name": "G", "inbound_tags": ["VLESS WS 80"]}])
    assert codes(w) == ["tag_removed"] and "1 host and the group “G” still use it" in w[0]["text"]          # no user count when the panel gave none


# ------------------------------------------------------------------ the line diff
def lcs_len(a: list[str], b: list[str]) -> int:
    row = [0] * (len(b) + 1)

    for x in a:
        prev = 0

        for j, y in enumerate(b, 1):
            cur = row[j]
            row[j] = prev + 1 if x == y else max(row[j], row[j - 1])
            prev = cur

    return row[-1]


def test_line_diff_shows_only_what_changed():
    old = "\n".join(f"line {i}" for i in range(30))
    new = old.replace("line 10", "line ten").replace("line 25\n", "")
    d = pg_cores.line_diff(old, new)
    assert (d["same"], d["added"], d["removed"], d["coarse"]) == (False, 1, 2, False)
    assert d["rows"] == [["~", 8], ["=", "line 8"], ["=", "line 9"], ["-", "line 10"], ["+", "line ten"], ["=", "line 11"], ["=", "line 12"], ["~", 10], ["=", "line 23"], ["=", "line 24"], ["-", "line 25"], ["=", "line 26"], ["=", "line 27"], ["~", 2]]
    assert pg_cores.line_diff(old, old) == {"same": True, "added": 0, "removed": 0, "coarse": False, "truncated": False, "rows": [["~", 30]]}
    assert pg_cores.line_diff("", "a\nb")["added"] == 2 and pg_cores.line_diff("a\nb", "")["removed"] == 2 and pg_cores.line_diff("", "")["same"] is True


def test_line_diff_is_correct_and_shortest_on_random_texts():
    rng = random.Random(11)

    for _ in range(300):
        a = [rng.choice("abcdef") for _ in range(rng.randint(0, 40))]
        b = list(a)

        for _ in range(rng.randint(0, 8)):
            op = rng.choice("ird")
            i = rng.randint(0, len(b))

            if op == "i":
                b.insert(i, rng.choice("abcdefg"))
            elif b and op == "r":
                b[min(i, len(b) - 1)] = rng.choice("abcdefg")
            elif b:
                del b[min(i, len(b) - 1)]

        ops, coarse = pg_cores.diff_ops(a, b)
        assert not coarse
        assert [t for o, t in ops if o in "=-"] == a and [t for o, t in ops if o in "=+"] == b            # the script turns a into b
        assert sum(1 for o, _t in ops if o == "=") == lcs_len(a, b)                                       # and it is the shortest one


def test_line_diff_gives_up_politely_on_a_rewrite():
    a, b = [f"old {i}" for i in range(40)], [f"new {i}" for i in range(40)]
    ops, coarse = pg_cores.diff_ops(["top"] + a + ["end"], ["top"] + b + ["end"], max_d=10)
    assert coarse and ops[0] == ("=", "top") and ops[-1] == ("=", "end") and [t for o, t in ops if o == "-"] == a and [t for o, t in ops if o == "+"] == b


# ------------------------------------------------------------------ the JavaScript twin
DRIVER = r"""
const vm = require('vm'), fs = require('fs');
const sandbox = {window: {}, pgTabs: {}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\n;this.__api = {diff: pgCoresDiffText, check: pgCoresCheck};', sandbox, {filename: 'pg_cores.js'});
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {diff: input.diffs.map(([a, b]) => sandbox.__api.diff(a, b)), check: input.checks.map(([cfg, t]) => sandbox.__api.check(cfg, t))};
process.stdout.write(JSON.stringify(out));
"""


def run_js(tmp_path, diffs, checks):
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is not installed here")

    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    r = subprocess.run([node, str(driver), str(ROOT / "nikatel" / "web" / "pg_cores.js")], input=json.dumps({"diffs": diffs, "checks": checks}), capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


def test_the_javascript_diff_and_checks_give_the_same_answers(tmp_path):
    rng = random.Random(5)
    diffs = [("", ""), ("a", "a"), ("", "x\ny"), ("x\ny", ""), ("a\nb\nc", "a\nB\nc"), ("1\n2\n3\n4\n5\n6\n7\n8\n9", "1\n2\n3\n4\n5\n6\n7\n8\n9\n10")]

    for _ in range(60):
        a = [rng.choice("abcde") for _ in range(rng.randint(0, 30))]
        b = [x for x in a if rng.random() > 0.2] + [rng.choice("abcdef") for _ in range(rng.randint(0, 4))]

        if rng.random() < 0.2:
            rng.shuffle(b)

        diffs.append(("\n".join(a), "\n".join(b)))

    diffs.append(("\n".join(f"a{i}" for i in range(1600)), "\n".join(f"b{i}" for i in range(1600))))      # more different lines than the diff follows: both give up the same way
    cfg = core_config()
    checks = [(None, "xray"), ([], "xray"), ("text", "xray"), ({}, "xray"), (cfg, "xray"), ({"inbounds": [], "outbounds": []}, "xray"), ({"inbounds": {}, "outbounds": 1}, "xray"),
              ({**good(), "inbounds": [{"tag": "a"}, {"tag": "b"}, {"tag": "a"}]}, "xray"), ({**good(), "inbounds": [{"port": 1}, {"tag": "  "}, {"tag": 5}, "junk", None]}, "xray"),
              ({**good(), "inbounds": [{"tag": "a,b"}]}, "xray"), ({**good(), "inbounds": [{"tag": "a<=>b"}]}, "xray"), ({**good(), "outbounds": [{"protocol": "freedom"}, "junk", {"tag": ""}]}, "xray"),
              ({"x": 1}, "wireguard"), ({}, "wireguard"), ([], "wireguard")]
    got = run_js(tmp_path, diffs, checks)

    for (a, b), js in zip(diffs, got["diff"]):
        assert js == json.loads(json.dumps(pg_cores.line_diff(a, b))), (a[:40], b[:40])

    for (value, kind), js in zip(checks, got["check"]):
        assert js == pg_cores.check_config(value, kind), value


# ------------------------------------------------------------------ the cores of a panel
def test_the_list_counts_what_each_core_has(fake):
    rows = pg_cores.list_cores(C)
    assert len(rows) == 1
    r = rows[0]
    assert (r["id"], r["name"], r["type"], r["inbounds"], r["outbounds"], r["rules"], r["backups"]) == (1, "Default Core Config", "xray", 5, 3, 2, 0)
    assert r["created_at"] == "2026-01-01T00:00:00Z" and r["updated_at"] is None and "config" not in r


def test_the_list_copes_with_other_shapes(monkeypatch):
    raw = [{"id": 3, "name": None, "config": None, "exclude_inbound_tags": "a, b,,", "fallbacks_inbound_tags": None, "updated_at": "2026-02-02T00:00:00Z"}, {"name": "no id"}, "junk", None, {"id": "x"}]
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: raw)
    rows = pg_cores.list_cores(C)
    assert len(rows) == 1 and rows[0]["id"] == 3 and rows[0]["type"] == "xray" and rows[0]["inbounds"] == 0 and rows[0]["exclude_inbound_tags"] == ["a", "b"] and rows[0]["updated_at"] == "2026-02-02T00:00:00Z"
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: {"cores": "nope"})
    assert pg_cores.list_cores(C) == []


def test_the_view_hides_every_secret_and_shows_who_uses_each_inbound(fake):
    v = pg_cores.core_view(C, 1)
    assert not any(s in json.dumps(v) for s in SECRETS)
    assert v["masked"] == 4 and v["text"] == json.dumps(v["config"], indent=2, ensure_ascii=False) and v["rules"] == 2 and "restarts Xray" in v["applies"]
    rows = {r["tag"]: r for r in v["inbounds"]}
    assert rows["VLESS WS 80"]["hosts"] == 2 and rows["VLESS WS 80"]["groups"] == ["Main"] and rows["SS TCP"]["hosts"] == 0 and rows["SS TCP"]["groups"] == []
    assert v["usage_known"] == {"hosts": True, "groups": True} and v["backups"] == [] and v["core"]["name"] == "Default Core Config"
    assert [o["tag"] for o in v["outbounds"]] == ["DIRECT", "BLOCK", "prov-a"]


def test_the_view_still_works_when_hosts_and_groups_cannot_be_read(fake):
    fake.fail[("GET", "/api/hosts")] = PanelError("HTTP 500", 500)
    v = pg_cores.core_view(C, 1)
    assert v["usage_known"] == {"hosts": False, "groups": True} and {r["hosts"] for r in v["inbounds"]} == {None}


def test_a_core_the_panel_does_not_have(fake):
    with pytest.raises(PanelError) as e:
        pg_cores.core_view(C, 99)

    assert e.value.code == 404


# ------------------------------------------------------------------ dry run
def backup_files() -> list[Path]:
    d = get_settings().data_dir / "pg-backups"
    return sorted(d.glob("cores-*.json")) if d.exists() else []


def test_a_dry_run_changes_nothing_and_tells_everything(fake):
    cfg = pg_cores.mask_secrets(fake.config)[0]
    cfg["inbounds"] = [i for i in cfg["inbounds"] if i["tag"] != "VLESS WS 80"]
    r = pg_cores.dry_run(C, 1, text=json.dumps(cfg, indent=2))
    assert r["valid"] is True and r["errors"] == [] and r["changed"] is True and r["needs_confirm"] is True and codes(r["warnings"]) == ["tag_removed"]
    assert r["diff"]["removed"] > 5 and r["diff"]["added"] == 0 and any(row[0] == "-" and '"VLESS WS 80"' in row[1] for row in r["diff"]["rows"])
    assert r["inbounds"] == 4 and r["outbounds"] == 3 and r["rev"] == pg_cores.core_view(C, 1)["rev"]
    assert not any(s in json.dumps(r) for s in SECRETS)
    assert fake.puts == [] and all(m == "GET" for m, _p in fake.log) and backup_files() == []            # nothing was written: not to the panel, not to the disk
    same = pg_cores.dry_run(C, 1, text=json.dumps(pg_cores.mask_secrets(fake.config)[0]))
    assert same["valid"] and same["changed"] is False and same["warnings"] == [] and same["diff"]["same"] is True


def test_a_dry_run_reports_bad_input_instead_of_failing(fake):
    r = pg_cores.dry_run(C, 1, text='{"inbounds": [')
    assert r["valid"] is False and r["errors"][0].startswith("not valid JSON") and r["diff"] is None and r["changed"] is None
    r = pg_cores.dry_run(C, 1, text='{"inbounds": [{"tag": "a"}, {"tag": "a"}], "outbounds": []}')
    assert r["valid"] is False and len(r["errors"]) == 2 and r["diff"]["added"] > 0
    assert pg_cores.dry_run(C, 1, text="[1]")["errors"] == ["the config must be a JSON object"]
    assert "send the config" in pg_cores.dry_run(C, 1)["errors"][0]
    stale = json.dumps({"inbounds": [{"tag": "x", "settings": {"password": "<secret:0123456789>"}}], "outbounds": [{"tag": "o"}]})
    assert "not a secret of the config on the panel" in pg_cores.dry_run(C, 1, text=stale)["errors"][0]
    obj = pg_cores.dry_run(C, 1, config=pg_cores.mask_secrets(fake.config)[0])                           # an API client may send an object instead of text
    assert obj["valid"] and obj["changed"] is False
    assert fake.puts == []


# ------------------------------------------------------------------ save
def new_text(fake, edit) -> str:
    """The text of the config on the fake panel, secrets as placeholders, after edit(dict) changed it."""
    cfg = pg_cores.mask_secrets(fake.config)[0]
    edit(cfg)
    return json.dumps(cfg, indent=2)


def test_a_save_backs_the_old_config_up_first_and_keeps_the_secrets(fake, rw):
    old = copy.deepcopy(fake.config)
    text = new_text(fake, lambda c: c["log"].update(loglevel="debug"))
    out = pg_cores.save(C, 1, text=text)
    assert out["saved"] is True and out["warnings"] == [] and out["restarted"] is False and out["inbounds"] == 5 and out["outbounds"] == 3
    assert [m for m, _p in fake.log if m != "GET"] == ["PUT"] and fake.restarts == []                    # stored, not applied
    put = fake.puts[0]
    assert put["query"] == "restart_nodes=false&modify_users=false" and put["body"]["name"] == "Default Core Config" and put["body"]["type"] == "xray"
    assert put["body"]["config"]["log"]["loglevel"] == "debug" and put["body"]["exclude_inbound_tags"] == [] and put["body"]["fallbacks_inbound_tags"] == []
    expect = copy.deepcopy(old)
    expect["log"]["loglevel"] = "debug"
    assert put["body"]["config"] == expect                                                              # every secret went back in; nothing but the edit changed
    files = backup_files()
    assert [f.name for f in files] == [out["backup"]] and (files[0].stat().st_mode & 0o777) == 0o600 and (files[0].parent.stat().st_mode & 0o777) == 0o700      # the file and its folder are private
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["config"] == old and saved["panel"] == 7 and saved["core"] == 1 and saved["name"] == "Default Core Config"       # the file holds the config as it was
    assert out["rev"] == pg_cores.core_view(C, 1)["rev"]
    texts = [e.text for e in _events()]
    assert any("changed: 5 inbounds, 3 outbounds" in t and out["backup"] in t for t in texts) and not any(s in t for t in texts for s in SECRETS)


def _events() -> list[Event]:
    with session() as db:
        rows = list(db.query(Event).all())
        db.expunge_all()
        return rows


def test_a_save_keeps_the_names_and_tag_lists_of_the_core(fake, rw):
    fake.cores[1]["exclude_inbound_tags"] = ["SS TCP"]
    fake.cores[1]["fallbacks_inbound_tags"] = ["HY2 2083"]
    pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="info")))
    assert fake.puts[0]["body"]["exclude_inbound_tags"] == ["SS TCP"] and fake.puts[0]["body"]["fallbacks_inbound_tags"] == ["HY2 2083"]
    assert fake.cores[1]["exclude_inbound_tags"] == ["SS TCP"]


def test_a_save_is_refused_in_read_only_mode(fake):
    with pytest.raises(ValueError, match="read-only"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="x")))

    with pytest.raises(ValueError, match="read-only"):
        pg_cores.restore(C, 1, "cores-7-1-1800000000000.json")

    with pytest.raises(ValueError, match="read-only"):
        pg_cores.restart(C, 1, confirm=True)

    assert fake.puts == [] and fake.restarts == [] and backup_files() == []


def test_a_save_with_warnings_needs_confirm(fake, rw):
    text = new_text(fake, lambda c: c["inbounds"].pop(0))                                               # VLESS WS 80 goes: two hosts and a group use it
    with pytest.raises(pg_cores.NeedsConfirm) as e:
        pg_cores.save(C, 1, text=text)

    assert "1 warning" in str(e.value) and "VLESS WS 80" in str(e.value) and "confirm: true" in str(e.value) and e.value.warnings[0]["code"] == "tag_removed"
    assert fake.puts == [] and backup_files() == []
    out = pg_cores.save(C, 1, text=text, confirm=True)
    assert out["saved"] and codes(out["warnings"]) == ["tag_removed"] and len(fake.puts) == 1 and len(fake.config["inbounds"]) == 4


def test_a_save_that_is_invalid_or_empty_is_refused_before_anything_is_written(fake, rw):
    for bad, why in (('{"inbounds": [', "not valid JSON"), ('{"inbounds": [{"tag": "a"}, {"tag": "a"}], "outbounds": [{"tag": "o"}]}', "must be unique"), ('{"inbounds": [], "outbounds": []}', "no inbounds")):
        with pytest.raises(ValueError, match=why):
            pg_cores.save(C, 1, text=bad, confirm=True)

    with pytest.raises(ValueError, match="nothing to save"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: None))

    with pytest.raises(ValueError, match="send the config"):
        pg_cores.save(C, 1)

    assert fake.puts == [] and backup_files() == []


def test_a_save_made_from_an_older_read_is_refused(fake, rw):
    rev = pg_cores.core_view(C, 1)["rev"]
    fake.config["log"]["loglevel"] = "error"                                                            # somebody changed the core in the panel's own dashboard
    with pytest.raises(pg_cores.Conflict, match="reload it"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), base_rev=rev)

    assert fake.puts == []
    assert pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), base_rev=pg_cores.core_view(C, 1)["rev"])["saved"]


def test_a_config_the_panel_refuses_leaves_no_backup(fake, rw, monkeypatch):
    def refuse(c, method, path, **kw):
        if method == "PUT":
            raise PanelError("HTTP 400: Xray rejected the config", 400)

        return fake(c, method, path, **kw)

    monkeypatch.setattr(panels, "call", refuse)
    with pytest.raises(PanelError, match="the panel refused the config .*Xray rejected") as e:
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))

    assert e.value.code == 400 and backup_files() == [] and fake.puts == []


def test_what_the_panel_says_about_a_failure_never_carries_a_secret(fake, rw, monkeypatch):
    quote = "HTTP 422: input {'privateKey': 'PRIVATE-KEY-AAAA', 'password': 'OBFS-PASSWORD-CCCC'} is not valid"

    def refuse(c, method, path, **kw):
        if method == "PUT":
            raise PanelError(quote, 422)

        return fake(c, method, path, **kw)

    monkeypatch.setattr(panels, "call", refuse)
    with pytest.raises(PanelError) as e:
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))

    assert "<hidden>" in str(e.value) and not any(s in str(e.value) for s in SECRETS)

    def down(c, method, path, **kw):
        if method == "PUT":
            raise PanelError("HTTP 500: " + quote, 500)

        return fake(c, method, path, **kw)

    monkeypatch.setattr(panels, "call", down)
    with pytest.raises(RuntimeError, match="could not be put back") as e2:
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))

    assert "<hidden>" in str(e2.value) and not any(s in str(e2.value) for s in SECRETS)
    assert pg_cores._scrub("a b c", core_config()) == "a b c" and pg_cores._scrub("x PRIVATE-KEY-AAAA y", core_config()) == "x <hidden> y"


def test_what_a_failed_restart_says_never_carries_a_secret(fake, rw):
    fake.fail[("POST", "/api/core/1/restart")] = PanelError("HTTP 500: node said PRIVATE-KEY-AAAA", 500)
    out = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), restart_after=True, confirm=True)
    assert out["restarted"] is False and "<hidden>" in out["restart_error"] and "PRIVATE-KEY-AAAA" not in out["restart_error"]


def test_when_the_panel_does_not_keep_the_config_the_old_one_is_put_back(fake, rw):
    fake.forget_once = True
    old = copy.deepcopy(fake.config)
    with pytest.raises(RuntimeError, match="did not keep the new config.*old config was put back"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))

    assert len(fake.puts) == 2 and fake.config == old                                                    # the second PUT is the rollback
    assert len(backup_files()) == 1 and not any("changed:" in e.text for e in _events())


def test_when_the_panel_cannot_be_reached_the_error_names_the_backup(fake, rw):
    fake.fail[("PUT", "/api/core/1")] = PanelError("no answer from the panel (ConnectError)")
    with pytest.raises(RuntimeError, match=r"could not be put back.*restore the backup cores-7-1-\d+\.json"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))

    assert len(fake.puts) == 0 and len(backup_files()) == 1                                              # the panel was down for both PUTs; the backup is the way back


def test_only_the_last_twenty_backups_are_kept(fake, rw):
    for i in range(23):
        pg_cores.save(C, 1, text=new_text(fake, lambda c, i=i: c["log"].update(loglevel=f"level{i}")))

    names = [b["file"] for b in pg_cores.backups(7, 1)]
    assert len(names) == 20 and len(backup_files()) == 20 and names == sorted(names, reverse=True) and len(set(names)) == 20
    assert json.loads((get_settings().data_dir / "pg-backups" / names[0]).read_text(encoding="utf-8"))["config"]["log"]["loglevel"] == "level21"      # the newest is the config before the last save
    assert pg_cores.backups(7, 2) == [] and pg_cores.backups(8, 1) == []                                  # other cores and panels have their own


def test_two_backups_in_the_same_millisecond_never_overwrite_each_other(fake):
    cur = pg_cores._get_core(C, 1)
    names = {pg_cores.backup_write(7, cur, "a") for _ in range(5)}
    assert len(names) == 5 and len(backup_files()) == 5


def test_the_backup_list_skips_damaged_files(fake):
    pg_cores.backup_write(7, pg_cores._get_core(C, 1), "good")
    (get_settings().data_dir / "pg-backups" / "cores-7-1-1700000000000.json").write_text("{not json", encoding="utf-8")
    assert [b["note"] for b in pg_cores.backups(7, 1)] == ["good"]
    with pytest.raises(ValueError, match="unknown backup"):
        pg_cores.backup_view(C, 1, "cores-7-1-1700000000000.json")


# ------------------------------------------------------------------ restore and view
def test_restore_puts_a_backup_back_after_backing_the_current_config_up(fake, rw):
    original = copy.deepcopy(fake.config)
    first = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))
    changed = copy.deepcopy(fake.config)
    out = pg_cores.restore(C, 1, first["backup"])
    assert out["saved"] and fake.config == original and out["backup"] != first["backup"]
    newest = json.loads((get_settings().data_dir / "pg-backups" / out["backup"]).read_text(encoding="utf-8"))
    assert newest["config"] == changed and newest["note"] == f"before restoring {first['backup']}"       # the rollback is itself undoable
    assert len(fake.puts) == 2 and [m for m, _p in fake.log if m != "GET"] == ["PUT", "PUT"]
    with pytest.raises(ValueError, match="nothing to restore"):
        pg_cores.restore(C, 1, first["backup"])


def test_restore_has_the_same_confirm_rule_as_a_save(fake, rw):
    first = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["inbounds"].append({"tag": "NEW 9000", "port": 9000, "protocol": "vless", "listen": "127.0.0.1", "settings": {"clients": []}, "streamSettings": {"network": "tcp", "security": "none"}})))
    fake.hosts.append({"id": 9, "remark": "h9", "inbound_tag": "NEW 9000", "port": None, "is_disabled": False})      # a host now points at the inbound the backup does not have
    v = pg_cores.backup_view(C, 1, first["backup"])
    assert codes(v["warnings"]) == ["tag_removed"] and v["errors"] == [] and v["same"] is False and v["diff"]["removed"] > 5
    puts = len(fake.puts)
    with pytest.raises(pg_cores.NeedsConfirm):
        pg_cores.restore(C, 1, first["backup"])

    assert len(fake.puts) == puts
    assert pg_cores.restore(C, 1, first["backup"], confirm=True)["saved"] and len(fake.puts) == puts + 1


def test_a_view_of_a_backup_hides_the_secrets_and_changes_nothing(fake, rw):
    first = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")))
    puts, files = len(fake.puts), backup_files()
    v = pg_cores.backup_view(C, 1, first["backup"])
    assert not any(s in json.dumps(v) for s in SECRETS) and v["text"].count("<secret:") == 4 and v["backup"]["file"] == first["backup"] and v["backup"]["inbounds"] == 5
    assert v["diff"]["added"] == 1 and v["diff"]["removed"] == 1                                         # loglevel: warning -> debug, seen from the panel's config to the backup
    assert len(fake.puts) == puts and backup_files() == files


@pytest.mark.parametrize("name", ["../x", "cores-7-1-1800000000000.json/../../x", "cores-8-1-1800000000000.json", "cores-7-2-1800000000000.json", "cores-7-1-1.json", "cores-7-1-1800000000000.json", "x" * 300, ""])
def test_a_backup_name_must_be_one_of_this_panels_and_this_cores_files(fake, rw, name):
    with pytest.raises(ValueError, match="unknown backup"):
        pg_cores.backup_view(C, 1, name)

    with pytest.raises(ValueError, match="unknown backup"):
        pg_cores.restore(C, 1, name, confirm=True)


# ------------------------------------------------------------------ restart
def test_restart_needs_confirm_and_reports_the_nodes(fake, rw):
    with pytest.raises(ValueError, match="confirm: true"):
        pg_cores.restart(C, 1)

    assert fake.restarts == []
    fake.nodes.append({"id": 3, "name": "node-c", "status": "error", "message": "xray did not start", "xray_version": "26.3.27", "address": "203.0.113.23"})
    fake.nodes.append({"id": 4, "name": "node-d", "status": "disabled", "xray_version": "", "address": "203.0.113.24"})
    out = pg_cores.restart(C, 1, confirm=True)
    assert fake.restarts == [1] and out["restarted"] is True
    assert out["nodes"] == {"known": True, "total": 3, "connected": 2, "connecting": 0, "problems": [{"name": "node-c", "status": "error", "message": "xray did not start"}]}
    assert any("Xray restarted" in e.text for e in _events())
    with pytest.raises(PanelError):
        pg_cores.restart(C, 99, confirm=True)


def test_save_and_restart_restarts_only_after_the_config_is_stored(fake, rw):
    with pytest.raises(ValueError, match="confirm: true"):
        pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), restart_after=True)

    assert fake.puts == [] and fake.restarts == []
    out = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), restart_after=True, confirm=True)
    assert out["restarted"] is True and out["restart_error"] == "" and out["nodes"]["connected"] == 2 and fake.restarts == [1]
    writes = [(m, p) for m, p in fake.log if m != "GET"]
    assert [m for m, _p in writes] == ["PUT", "POST"] and writes[1][1] == "/api/core/1/restart"


def test_a_failed_restart_does_not_undo_the_save(fake, rw):
    fake.fail[("POST", "/api/core/1/restart")] = PanelError("HTTP 500: node unreachable", 500)
    out = pg_cores.save(C, 1, text=new_text(fake, lambda c: c["log"].update(loglevel="debug")), restart_after=True, confirm=True)
    assert out["saved"] is True and out["restarted"] is False and "node unreachable" in out["restart_error"] and fake.config["log"]["loglevel"] == "debug"


def test_a_non_xray_core_is_edited_as_plain_json(fake, rw):
    fake.cores[2] = {"id": 2, "name": "WG", "type": "wireguard", "config": {"interface_name": "wg0", "private_key": "WG-PRIVATE-KEY", "listen_port": 51820}, "exclude_inbound_tags": [], "fallbacks_inbound_tags": [], "created_at": "2026-03-03T00:00:00Z"}
    v = pg_cores.core_view(C, 2)
    assert v["inbounds"] == [] and "WG-PRIVATE-KEY" not in json.dumps(v) and v["config"]["interface_name"] == "wg0" and v["masked"] == 1
    text = json.dumps({**v["config"], "listen_port": 51821})
    assert pg_cores.dry_run(C, 2, text=text)["valid"] is True
    out = pg_cores.save(C, 2, text=text)
    assert out["saved"] and fake.cores[2]["config"] == {"interface_name": "wg0", "private_key": "WG-PRIVATE-KEY", "listen_port": 51821}
    assert pg_cores.dry_run(C, 2, text="{}")["errors"] == ["the config must not be empty"]


# ------------------------------------------------------------------ the API
@pytest.fixture
def panel_row():
    with session() as db:
        p = Panel(name="alpha", type="pasarguard", url="https://panel-a.example.test", username="admin", secrets={"password": "x"}, status="up")
        db.add(p)
        db.flush()
        return p.id


def base(pid: int, core: int = 1) -> str:
    return f"/api/panels/{pid}/pg/cores/{core}"


def test_the_api_lists_and_opens_cores(client, fake, panel_row):
    j = client.get(f"/api/panels/{panel_row}/pg/cores").json()
    assert j["ok"] is True and len(j["cores"]) == 1 and j["cores"][0]["inbounds"] == 5
    r = client.get(base(panel_row))
    assert r.status_code == 200 and not any(s in r.text for s in SECRETS)
    j = r.json()
    assert j["ok"] is True and j["rev"] and j["masked"] == 4 and j["inbounds"][0]["tag"] == "VLESS WS 80"
    assert client.get(base(panel_row, 99)).status_code == 404 and client.get(base(panel_row, 99)).json()["code"] == "not_found"        # the panel's 404 is a 404
    assert client.get(f"/api/panels/{panel_row + 50}/pg/cores").status_code == 404


def test_the_api_refuses_a_marzban_panel_and_reports_a_panel_that_is_down(client, fake, panel_row, monkeypatch):
    with session() as db:
        db.add(Panel(name="beta", type="marzban", url="https://panel-b.example.test", username="admin", secrets={"password": "x"}, status="up"))
        db.flush()
        marz = db.query(Panel).filter(Panel.name == "beta").one().id

    r = client.get(f"/api/panels/{marz}/pg/cores")
    assert r.status_code == 400 and "PasarGuard" in r.json()["error"]

    def down(c, m, p, **kw):
        raise PanelError("no answer from the panel (ConnectError)")

    monkeypatch.setattr(panels, "call", down)
    r = client.get(f"/api/panels/{panel_row}/pg/cores")
    assert r.status_code == 502 and r.json()["code"] == "upstream_failed" and "no answer" in r.json()["error"]
    assert client.post(base(panel_row) + "/dry-run", json={"text": "{}"}).status_code == 502


def test_the_api_dry_run_changes_nothing(client, fake, panel_row):
    text = new_text(fake, lambda c: c["inbounds"].pop(0))
    r = client.post(base(panel_row) + "/dry-run", json={"text": text})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["valid"] is True and j["needs_confirm"] is True and codes(j["warnings"]) == ["tag_removed"] and j["diff"]["removed"] > 5
    assert fake.puts == [] and backup_files() == []
    bad = client.post(base(panel_row) + "/dry-run", json={"text": "{nope"}).json()
    assert bad["ok"] is True and bad["valid"] is False and bad["errors"][0].startswith("not valid JSON")
    assert client.post(base(panel_row) + "/dry-run", json={}).status_code == 422                          # neither text nor config
    assert client.post(base(panel_row) + "/dry-run", json={"text": "{}", "config": {}}).status_code == 422
    assert client.post(base(panel_row) + "/dry-run", json={"text": "x" * (pg_cores.MAX_TEXT + 1)}).status_code == 422
    assert client.post(base(panel_row, 99) + "/dry-run", json={"text": "{}"}).status_code == 404


def test_the_api_saves_with_confirm_and_keeps_every_rule(client, fake, panel_row, monkeypatch):
    text = new_text(fake, lambda c: c["inbounds"].pop(0))
    r = client.put(base(panel_row), json={"text": text})
    assert r.status_code == 400 and "read-only" in r.json()["error"]                                     # a new install is read-only
    monkeypatch.setattr(get_settings(), "read_only", False)
    r = client.put(base(panel_row), json={"text": text})
    assert r.status_code == 409 and r.json()["code"] == "conflict" and "confirm: true" in r.json()["error"] and fake.puts == []
    rev = client.get(base(panel_row)).json()["rev"]
    r = client.put(base(panel_row), json={"text": text, "confirm": True, "base_rev": "0" * 16})
    assert r.status_code == 409 and "reload it" in r.json()["error"] and fake.puts == []
    r = client.put(base(panel_row), json={"text": text, "confirm": True, "base_rev": rev, "note": "drop the plain WS inbound"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["saved"] is True and j["backup"].startswith(f"cores-{panel_row}-1-") and codes(j["warnings"]) == ["tag_removed"] and len(fake.puts) == 1
    saved = json.loads((get_settings().data_dir / "pg-backups" / j["backup"]).read_text(encoding="utf-8"))
    assert saved["note"] == "drop the plain WS inbound"
    assert client.put(base(panel_row), json={"text": "{}"}).status_code == 400                           # invalid
    assert client.put(base(panel_row), json={"text": new_text(fake, lambda c: None)}).json()["error"].startswith("nothing to save")
    assert client.put(base(panel_row), json={"text": "{}", "restart": True}).json()["error"].find("confirm: true") >= 0


def test_the_api_round_trip_keeps_the_secrets_of_the_panel(client, fake, panel_row, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    before = copy.deepcopy(fake.config)
    j = client.get(base(panel_row)).json()
    assert client.put(base(panel_row), json={"text": j["text"]}).json()["error"].startswith("nothing to save")      # the text as shown is the config as it is
    edit = json.loads(j["text"])
    edit["log"]["loglevel"] = "debug"
    r = client.put(base(panel_row), json={"config": edit, "base_rev": j["rev"]})
    assert r.status_code == 200
    after = copy.deepcopy(before)
    after["log"]["loglevel"] = "debug"
    assert fake.config == after


def test_the_api_backups_and_restore(client, fake, panel_row, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    original = copy.deepcopy(fake.config)
    assert client.get(base(panel_row) + "/backups").json() == {"ok": True, "backups": []}
    saved = client.put(base(panel_row), json={"text": new_text(fake, lambda c: c["log"].update(loglevel="debug"))}).json()
    lst = client.get(base(panel_row) + "/backups").json()["backups"]
    assert [b["file"] for b in lst] == [saved["backup"]] and lst[0]["inbounds"] == 5
    v = client.get(base(panel_row) + f"/backups/{saved['backup']}")
    assert v.status_code == 200 and not any(s in v.text for s in SECRETS) and v.json()["diff"]["added"] == 1
    assert client.get(base(panel_row) + "/backups/cores-1-1-1800000000000.json").status_code == 400
    r = client.post(base(panel_row) + f"/backups/{saved['backup']}/restore", json={"restart": True})
    assert r.status_code == 400 and "confirm: true" in r.json()["error"]
    r = client.post(base(panel_row) + f"/backups/{saved['backup']}/restore")                              # no body at all is fine
    assert r.status_code == 200 and r.json()["saved"] is True and fake.config == original
    assert len(client.get(base(panel_row) + "/backups").json()["backups"]) == 2
    assert client.post(base(panel_row) + "/backups/nope.json/restore", json={"confirm": True}).status_code == 400


def test_the_api_restart(client, fake, panel_row, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    r = client.post(base(panel_row) + "/restart", json={})
    assert r.status_code == 400 and "confirm: true" in r.json()["error"] and fake.restarts == []
    r = client.post(base(panel_row) + "/restart", json={"confirm": True})
    assert r.status_code == 200 and r.json()["restarted"] is True and r.json()["nodes"]["connected"] == 2 and fake.restarts == [1]
    assert client.post(base(panel_row, 99) + "/restart", json={"confirm": True}).status_code == 404


def test_every_route_of_the_tab_has_a_short_description(client):
    spec = client.app.openapi()
    mine = {(m, p): op for p, ops in spec["paths"].items() if "/pg/cores" in p for m, op in ops.items()}
    assert len(mine) == 8
    assert all(0 < len((op.get("description") or "").split("\n")[0]) <= 300 for op in mine.values())
