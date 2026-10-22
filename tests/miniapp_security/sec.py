#!/usr/bin/env python3
# ruff: noqa      (a throw-away attack harness: broad excepts, unused unpacking and open() without a context are on purpose)
"""Mini App SECURITY / PAYMENT harness (scratch only, nothing leaves the box, nothing of the live panel is touched).

  usage: SEC_SCRATCH=/some/dir bash sec_setup_db.sh start && python3 sec.py <dir with api.php> [--only AU1,PY2] [--label before|after]; bash sec_setup_db.sh stop

What it starts, all private, on ports 49510-49514:
  * api.php under `php -S` with 8 workers (a real parallel race), on a PRIVATE MariaDB (sec_setup_db.sh, db nikabot_sec, the bot's schema)
  * a fake Marzban panel (create / get / modify / reset / delete, knobs to fail, to be slow, to answer with a huge token)
  * a fake Telegram over TLS (private CA) behind a CONNECT proxy that only forwards api.telegram.org to the fake: every message the admins and the customer
    would get is recorded, so the receipt / notification rows can be checked
Every scenario prints  HOLE <id> [severity] ...  (the attack worked / the inconsistency is there)  or  held <id>.
"""
import hashlib, hmac, html.parser, json, os, random, re, select, socket, socketserver, ssl, struct, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.environ.get("SEC_SCRATCH", "/tmp/miniapp-sec-scratch")           # everything the run makes lives here (private db, certificates, two copies of the bot tree)
API_DIR = os.path.abspath(sys.argv[1])
ONLY = set((sys.argv[sys.argv.index("--only") + 1]).split(",")) if "--only" in sys.argv else None
LABEL = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else ""
SOCK = f"{B}/run/db.sock"
DB = "nikabot_sec"
SCHEMA = os.path.join(HERE, "nikabot_schema.sql")                      # the bot's tables (structure only), 23 of them
PORT, PORT_EMPTY, PPORT, TGPORT, PROXYPORT = 49510, 49511, 49512, 49513, 49514
TOKEN = "123456:SEC-TEST-TOKEN-NOT-REAL"
ADMIN, ADMIN2 = 999000111, 999000222
U, V, W = 700000001, 700000002, 700000003
GB = 1073741824
PNG = None


def png():
    global PNG
    if PNG is None:
        def ch(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        PNG = b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + ch(b"IEND", b"")
    return PNG


# ------------------------------------------------------------------ db
def sql(q, db=DB):
    r = subprocess.run(["mariadb", "--no-defaults", "--default-character-set=utf8mb4", "-S", SOCK, "-uroot", db, "-N", "-B", "-e", q], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return [l.split("\t") for l in r.stdout.splitlines()]


def q1(query, default=None):
    r = sql(query)
    return r[0][0] if r and r[0] and r[0][0] != "NULL" else default


def esc(s):
    return "NULL" if s is None else "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


def wallet(uid=U):
    r = sql(f"SELECT wallet FROM users WHERE userid='{uid}'")
    return int(r[0][0]) if r else None


# ------------------------------------------------------------------ initData + calls
def init_data(uid=U, auth_date=None, first="Ali", token=TOKEN, extra=None, username="ali_t"):
    user = json.dumps({"id": uid, "first_name": first, "username": username}, separators=(",", ":"), ensure_ascii=False)
    d = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAHtest", "user": user}
    if extra:
        d.update(extra)
    chk = "\n".join(f"{k}={d[k]}" for k in sorted(d))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(secret, chk.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(d)


def call(a, body=None, uid=U, init=True, raw=None, method="POST", query=None, ctype="application/json", multipart=None, port=None, first="Ali", headers=None):
    url = f"http://127.0.0.1:{port or PORT}/api.php?" + (query if query is not None else f"a={a}")
    if raw is not None:
        data = raw if isinstance(raw, bytes) else raw.encode()
    elif multipart is not None:
        bnd = "----secb"
        parts = []
        for k, v in multipart.items():
            if isinstance(v, tuple):
                parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"; filename="{v[0]}"\r\nContent-Type: {v[1]}\r\n\r\n'.encode() + v[2] + b"\r\n")
            else:
                parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        data = b"".join(parts) + f"--{bnd}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={bnd}"
    else:
        b = dict(body or {})
        if init is True:
            b["initData"] = init_data(uid, first=first)
        elif init:
            b["initData"] = init
        data = json.dumps(b, ensure_ascii=False).encode()
    h = {"Content-Type": ctype}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data if method == "POST" else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, txt, hd = r.status, r.read().decode(errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        code, txt, hd = e.code, e.read().decode(errors="replace"), dict(e.headers)
    except Exception as e:
        return 0, None, f"transport: {e}"
    try:
        return code, json.loads(txt), txt
    except Exception:
        return code, None, txt


def ok(j):
    return bool(j) and j.get("ok") is True


def par(n, fn):
    res = [None] * n

    def run(i):
        try:
            res[i] = fn(i)
        except Exception as e:
            res[i] = (0, None, f"exc {e}")
    ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    return res


# ------------------------------------------------------------------ fake Marzban
PU = {}
PANEL = {}
PLOCK = threading.Lock()


def panel_reset():
    PU.clear()
    PANEL.update(fail_create=None, fail_modify=None, delay_create=0.0, sub_pad=0, creates=0, modifies=0, deletes=0, down=False, hang_after_create=0)


class Panel(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def _send(self, code, obj, raw=None):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        p, body = self.path, self._body()
        if PANEL["down"]:
            return self._send(503, {"detail": "down"})
        if p == "/api/admin/token":
            return self._send(200, {"access_token": "TOK", "token_type": "bearer"})
        if p == "/api/user":
            if PANEL["delay_create"]:
                time.sleep(PANEL["delay_create"])
            if PANEL["fail_create"]:
                return self._send(PANEL["fail_create"], {"detail": "boom"})
            j = json.loads(body or b"{}")
            with PLOCK:
                if j["username"] in PU:
                    return self._send(409, {"detail": "User already exists"})
                PANEL["creates"] += 1
                tok = "tok" + str(PANEL["creates"]) + "A" * PANEL["sub_pad"]
                PU[j["username"]] = {"username": j["username"], "status": "active", "expire": j.get("expire", 0), "data_limit": j.get("data_limit", 0), "used_traffic": 3 * GB,
                                     "subscription_url": f"/sub/{tok}", "links": ["vless://aaaa@h.invalid:443?security=tls#" + j["username"]], "online_at": None}
                rec = dict(PU[j["username"]])
            if PANEL["hang_after_create"]:
                time.sleep(PANEL["hang_after_create"])          # the account exists on the panel but the answer comes after the client gave up
            try:
                return self._send(200, rec)
            except Exception:
                return None
        if p.startswith("/api/user/") and p.endswith("/reset"):
            u = urllib.parse.unquote(p[len("/api/user/"):-len("/reset")])
            if u in PU:
                PU[u]["used_traffic"] = 0
                return self._send(200, PU[u])
        return self._send(404, {"detail": "Not Found"})

    def do_GET(self):
        p = self.path
        if p.startswith("/api/user/"):
            u = urllib.parse.unquote(p[len("/api/user/"):])
            return self._send(200, PU[u]) if u in PU else self._send(404, {"detail": "User not found"})
        if p.startswith("/sub/"):
            import base64
            return self._send(200, None, base64.b64encode(b"vless://aaaa@h.invalid:443?security=tls#x\n").decode().encode())
        return self._send(404, {"detail": "Not Found"})

    def do_PUT(self):
        p, body = self.path, self._body()
        if p.startswith("/api/user/"):
            if PANEL["fail_modify"]:
                return self._send(PANEL["fail_modify"], {"detail": "boom"})
            u = urllib.parse.unquote(p[len("/api/user/"):])
            if u in PU:
                PANEL["modifies"] += 1
                PU[u].update({k: v for k, v in json.loads(body or b"{}").items() if k in ("data_limit", "expire", "status")})
                return self._send(200, PU[u])
        return self._send(404, {"detail": "Not Found"})

    def do_DELETE(self):
        p = self.path
        if p.startswith("/api/user/"):
            u = urllib.parse.unquote(p[len("/api/user/"):])
            if u in PU:
                PANEL["deletes"] += 1
                del PU[u]
                return self._send(200, {})
        return self._send(404, {"detail": "Not Found"})


# ------------------------------------------------------------------ fake Telegram (TLS) + CONNECT proxy
TG_LOG, TG = [], {"delay": {}, "fail": set(), "mid": 1000}
TLOCK = threading.Lock()


def parse_multipart(ct, body):
    m = re.search(r'boundary=([^;]+)', ct)
    fields, files = {}, {}
    if not m:
        return fields, files
    bnd = ("--" + m.group(1).strip('"')).encode()
    for part in body.split(bnd)[1:]:
        if part.startswith(b"--"):
            break
        head, _, data = part.lstrip(b"\r\n").partition(b"\r\n\r\n")
        data = data.rsplit(b"\r\n", 1)[0]
        nm = re.search(rb'name="([^"]*)"', head)
        fn = re.search(rb'filename="([^"]*)"', head)
        if nm and fn:
            files[nm.group(1).decode()] = (fn.group(1).decode(errors="replace"), data)
        elif nm:
            fields[nm.group(1).decode()] = data.decode(errors="replace")
    return fields, files


class Tg(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        ct = self.headers.get("Content-Type", "")
        m = re.match(r"/bot([^/]+)/(\w+)", self.path)
        method = m.group(2) if m else "?"
        if ct.startswith("multipart/"):
            fields, files = parse_multipart(ct, body)
        else:
            fields, files = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode(errors="replace")).items()}, {}
        with TLOCK:
            TG["mid"] += 1
            mid = TG["mid"]
            TG_LOG.append({"method": method, "chat_id": str(fields.get("chat_id", "")), "text": fields.get("text") or fields.get("caption") or "", "markup": fields.get("reply_markup", ""), "has_file": bool(files) or fields.get("photo", "") != "", "mid": mid, "token_ok": bool(m) and m.group(1) == TOKEN})
        if TG["delay"].get(method):
            time.sleep(TG["delay"][method])
        if method in TG["fail"]:
            out = {"ok": False, "description": "fail"}
        elif method == "sendPhoto":
            out = {"ok": True, "result": {"message_id": mid, "photo": [{"file_id": f"F{mid}"}]}}
        else:
            out = {"ok": True, "result": {"message_id": mid}}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(data)


class Proxy(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode(errors="replace")
        while True:
            h = self.rfile.readline()
            if h in (b"\r\n", b"\n", b""):
                break
        if not line.startswith("CONNECT api.telegram.org:443"):
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        up = socket.create_connection(("127.0.0.1", TGPORT))
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n"); self.wfile.flush()
        a, b = self.connection, up
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 60)
                if not r:
                    break
                for s in r:
                    d = s.recv(65536)
                    if not d:
                        return
                    (b if s is a else a).sendall(d)
        finally:
            up.close()


def tg_msgs(method=None, chat=None):
    with TLOCK:
        return [m for m in TG_LOG if (method is None or m["method"] == method) and (chat is None or m["chat_id"] == str(chat))]


def make_pki():
    d = f"{B}/pki"
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(f"{d}/tg.pem"):
        subprocess.run(f"cd {d} && openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3 -subj /CN=sec-test-ca 2>/dev/null && "
                       f"openssl req -newkey rsa:2048 -nodes -keyout tg.key -out tg.csr -subj /CN=api.telegram.org 2>/dev/null && printf 'subjectAltName=DNS:api.telegram.org' > ext.cnf && "
                       f"openssl x509 -req -in tg.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out tg.pem -days 3 -extfile ext.cnf 2>/dev/null", shell=True, check=True)
    return d


# ------------------------------------------------------------------ the bot around api.php
def prepare():
    for tree, token in (("bot", TOKEN), ("bot_empty", "")):
        for d in (f"{B}/{tree}", f"{B}/{tree}/app", f"{B}/{tree}/settings"):
            os.makedirs(d, exist_ok=True)
        subprocess.run(f"cp {API_DIR}/api.php {B}/{tree}/app/api.php; rm -f {B}/{tree}/settings/*.json {B}/{tree}/settings/miniapp_error.log", shell=True, check=True)
        open(f"{B}/{tree}/baseInfo.php", "w").write(f"""<?php
$botToken = '{token}'; $admin = {ADMIN}; $admins_list = [{ADMIN}, {ADMIN2}]; $botUsername = 'test_shop_bot'; $botUrl = 'https://bot.example.invalid/';
$dbUserName = 'mini'; $dbPassword = 'mini-test-pw'; $dbName = '{DB}'; $USDTPrice = 60000; $USDTPriceagent = 58000;
""")
        open(f"{B}/{tree}/extras.php", "w").write("""<?php
function exEmojiStrip($s) { return trim(preg_replace('/[\\x{1F000}-\\x{1FFFF}\\x{2600}-\\x{27BF}]/u', '', (string) $s)); }
function exOnHoldDays($d) { return 0; }
function exFaDigits($s) { return $s; }
""")
        open(f"{B}/{tree}/settings/values.php", "w").write("""<?php
$mainValues = ['brw_cart_to_cart_detail' => "PRICE-TOMAN to ACCOUNT-NUMBER (HOLDER-NAME)", 'brw_cart_to_cart_usdt' => "USDT PRICE-USDT to USDT-WALLET", 'out_of_server_capacity' => 'full', 'order_submitted' => 'ok'];
$buttonValues = [];
""")


DATA_TABLES = ["users", "orders_list", "pays", "setting", "server_info", "server_config", "server_plans", "server_categories", "increase_plan", "increase_day", "needed_sofwares", "chats", "chats_info", "discounts", "increase_order"]
LAZY_TABLES = ["gift_codes", "wheel_log", "cashback_log", "outage_reports", "miniapp_throttle"]
BASE_STATES = {"botState": "on", "sellState": "on", "walletState": "on", "cartToCartState": "on", "giftState": "on", "wheelState": "on", "reportState": "on", "renewAccountState": "on", "increaseVolumeState": "on",
               "increaseTimeState": "on", "onHoldState": "off", "subLinkState": "on", "configLinkState": "on", "cashbackPercent": "0", "gbPrice": "2500", "dayPrice": "1500", "walletAmounts": "100000,200000,500000",
               "supportID": "@test_support", "wheelHours": "24", "wheelPrizes": "0,1000,2000", "remark": "digits"}


def reset(states=None, panel_delay=0.0, tg_delay=0.0, ucount="100", acount=10):
    for t in LAZY_TABLES:
        sql(f"DROP TABLE IF EXISTS {t}")
    sql("SET FOREIGN_KEY_CHECKS=0; " + "; ".join(f"DELETE FROM {t}" for t in DATA_TABLES))
    panel_reset()
    PANEL["delay_create"] = panel_delay
    with TLOCK:
        TG_LOG.clear(); TG["fail"].clear(); TG["delay"].clear()
        if tg_delay:
            TG["delay"]["sendPhoto"] = tg_delay
    st = dict(BASE_STATES); st.update(states or {})
    sql("INSERT INTO setting (type, value) VALUES ('BOT_STATES', %s), ('PAYMENT_KEYS', %s), ('INVITE_BANNER_AMOUNT', '5000'), ('INVITE_BANNER_TEXT', %s), ('TICKETS_CATEGORY', %s)" % (
        esc(json.dumps(st)), esc(json.dumps({"bankAccount": "6037997512345678", "holderName": "Test Holder", "tronwallet": "TXyZabcdefghijklmnopqrstuvwxyz1234567890AB"})),
        esc(json.dumps({"type": "text", "text": "hi LINK <b>x</b>"})), esc(json.dumps(["tech", "money"]))))
    sql(f"INSERT INTO server_info (id, title, ucount, remark, flag, active, state) VALUES (1, 'Test DE', '{ucount}', 'tst', 'DE', 1, 1)")
    sql(f"INSERT INTO server_config (id, panel_url, ip, sni, header_type, request_header, response_header, security, tlsSettings, type, username, password) VALUES (1, 'http://127.0.0.1:{PPORT}', '', '', 'none', '', '', 'none', '', 'marzban', 'a', 'b')")
    sql("INSERT INTO server_categories (id, server_id, title, step) VALUES (1, '1', 'cat', 1)")
    sql(f"INSERT INTO server_plans (id, fileid, catid, server_id, inbound_id, acount, limitip, title, protocol, days, volume, type, price, descr, pic, active, step, date, custom_sni) VALUES (1, '', 1, 1, 0, {acount}, 1, 'plan', 'vless', 30, 50, 'x', 130000, 'd', '', 1, 0, '', NULL)")
    sql("INSERT INTO increase_plan (volume, price) VALUES (5, 12000)"); sql("INSERT INTO increase_day (volume, price) VALUES (7, 9000)")
    sql("INSERT INTO needed_sofwares (title, link, status) VALUES ('app', 'https://example.invalid/app', 1)")
    for tree in ("bot", "bot_empty"):
        LOGSEEN.extend(l for l in errlog(tree).splitlines() if l)           # (what the PHP error log said before it is cleared for the next scenario)
        subprocess.run(f"rm -f {B}/{tree}/settings/*.json {B}/{tree}/settings/miniapp_error.log", shell=True)


def mk_user(uid, wal=0, agent=0, disc=None, step="none"):
    sql(f"INSERT INTO users (userid, name, username, refcode, wallet, date, step, is_agent, discount_percent) VALUES ('{uid}', 'T{uid}', 't{uid}', '0', {wal}, '{int(time.time())}', {esc(step)}, {agent}, {esc(disc)})")


def mk_service(uid, remark=None, expire=None, limit=50 * GB, plan=1):
    remark = remark or f"svc{uid}"
    expire = expire if expire is not None else int(time.time()) + 20 * 86400
    sql(f"INSERT INTO orders_list (userid, token, transid, fileid, server_id, inbound_id, remark, uuid, protocol, expire_date, link, amount, status, date, notif, rahgozar, agent_bought) VALUES ('{uid}', 'tok', '', {plan}, 1, 0, '{remark}', '', 'vless', {expire}, '[]', 130000, 1, '{int(time.time())}', 0, 0, 0)")
    PU[remark] = {"username": remark, "status": "active", "expire": expire, "data_limit": limit, "used_traffic": 3 * GB, "subscription_url": f"/sub/seed-{remark}", "links": ["vless://a@h.invalid:443#" + remark], "online_at": None}
    return int(q1(f"SELECT id FROM orders_list WHERE remark='{remark}'"))


def errlog(tree="bot"):
    f = f"{B}/{tree}/settings/miniapp_error.log"
    return open(f, errors="replace").read() if os.path.exists(f) else ""


# ------------------------------------------------------------------ scenarios
SC = []
RESULTS = []
LOGSEEN = []


def scn(sid, sev, title):
    def d(f):
        SC.append((sid, sev, title, f))
        return f
    return d


# ===== authentication
@scn("AU1", "HIGH", "forged / tampered initData is refused (random hash, swapped user id, unsigned extra field, hash in upper case, array tricks)")
def au1():
    reset()
    good = init_data(U)
    d = dict(urllib.parse.parse_qsl(good))
    u2 = dict(d); u2["user"] = d["user"].replace(str(U), str(V))
    cases = {
        "random hash": urllib.parse.urlencode(dict(d, hash="0" * 64)),
        "swapped user id": urllib.parse.urlencode(u2),
        "extra unsigned field": good + "&extra=1",
        "upper-case hash": urllib.parse.urlencode(dict(d, hash=d["hash"].upper())),
        "hash twice (bad last)": good + "&hash=" + "1" * 64,
        "hash array": good + "&hash[]=x",
        "user as array": urllib.parse.urlencode({k: v for k, v in d.items() if k != "user"}) + "&user[id]=" + str(V),
        "empty": "", "only hash": "hash=abc",
        "signed with another bot token": init_data(U, token="9:OTHER"),
        "newline smuggling": urllib.parse.urlencode({"auth_date": d["auth_date"], "query_id": "q\nuser=" + d["user"], "hash": d["hash"]}),
    }
    bad = [k for k, v in cases.items() if call("me", init=v)[0] == 200]
    return bool(bad), f"accepted: {bad}" if bad else f"{len(cases)} forged variants all 401"


@scn("AU2", "HIGH", "a bot whose token is EMPTY (broken config) must not accept initData signed with the publicly known empty-token key")
def au2():
    reset()
    c, j, t = call("me", init=init_data(U, token=""), port=PORT_EMPTY)
    return c == 200, f"empty-token server answered {c}: {t[:80]}"


@scn("AU3", "LOW", "replay window: a day-old signature is refused, a fresh one can be reused (by design, 24 h)")
def au3():
    reset()
    old = call("me", init=init_data(U, auth_date=int(time.time()) - 86401))
    edge = call("me", init=init_data(U, auth_date=int(time.time()) - 86300))
    fut = call("me", init=init_data(U, auth_date=int(time.time()) + 10 * 86400))
    same = init_data(U)
    rep = [call("me", init=same)[0] for _ in range(3)]
    detail = f"86401s old->{old[0]}, 86300s->{edge[0]}, +10 days in the future->{fut[0]}, same signature x3->{rep}"
    return old[0] == 200, detail


@scn("AU4", "MED", "banned user and a bot switched off are answered 403/503 for everybody but admins")
def au4():
    reset()
    mk_user(U, step="banned"); mk_user(V)
    c1 = call("me", uid=U)[0]
    sql("UPDATE setting SET value=REPLACE(value, '\"botState\": \"on\"', '\"botState\": \"off\"') WHERE type='BOT_STATES'")
    c2 = call("me", uid=V)[0]
    c3 = call("me", uid=ADMIN)[0]
    return not (c1 == 403 and c2 == 503 and c3 == 200), f"banned->{c1}, bot off (user)->{c2}, bot off (admin)->{c3}"


@scn("AU5", "HIGH", "IDOR: another user's service / ticket / receipt / pending top-up cannot be read, paid for or changed, whatever ids and user ids the body carries")
def au5():
    reset(states={"cashbackPercent": "0"})
    mk_user(U, wal=5_000_000); mk_user(V, wal=5_000_000)
    oid = mk_service(U, "svcA")
    cid = int(q1("SELECT 1") and 0)
    c, j, t = call("ticket_new", {"title": "t", "text": "secret text", "category": "tech"}, uid=U); cid = j["id"]
    c, j, t = call("topup", {"amount": 100000}, uid=U); hA = j["hash"]
    sql(f"INSERT INTO pays (hash_id, user_id, type, plan_id, volume, day, price, request_date, state, agent_bought, agent_count) VALUES ('HAVESENT1', {U}, 'INCREASE_WALLET', 0, 0, 0, 50000, {int(time.time())}, 'have_sent', 0, 0)")
    w0, pu0, extra = wallet(V), json.dumps(PU, sort_keys=True), {"userid": U, "user_id": U, "uid": U, "userId": U}
    tries = []
    for a, body in [("live", {"id": oid}), ("service", {"id": oid}), ("quote", {"id": oid}), ("renew", {"id": oid}), ("addvolume", {"id": oid, "amount": 5}), ("adddays", {"id": oid, "amount": 7}),
                    ("report", {"id": oid, "text": "x"}), ("ticket_messages", {"id": cid}), ("ticket_reply", {"id": cid, "text": "hijack"}), ("cancel_topup", {"hash": hA})]:
        c, j, t = call(a, dict(body, **extra), uid=V)
        if ok(j) and a != "cancel_topup":
            tries.append(a)
    c, j, t = call("receipt", multipart={"hash": hA, "initData": init_data(V), "a": "receipt", "photo": ("r.png", "image/png", png())}, uid=V)
    if ok(j):
        tries.append("receipt")
    still = int(q1(f"SELECT COUNT(*) FROM pays WHERE hash_id='{hA}' AND state='pending'", "0"))
    leaks = [a for a in ("services", "history", "tickets") if str(U) in call(a, uid=V)[2] or "svcA" in call(a, uid=V)[2] or "hijack" in call(a, uid=V)[2]]
    bad = bool(tries) or wallet(V) != w0 or json.dumps(PU, sort_keys=True) != pu0 or still != 1 or bool(leaks)
    return bad, f"succeeded: {tries}, V wallet {w0}->{wallet(V)}, A's pending top-up rows {still}, lists leaking A: {leaks}"


@scn("AU6", "MED", "no secret (bot token, db password, panel login or access token) appears in any answer of a full customer session")
def au6():
    reset(states={"cashbackPercent": "5"})
    mk_user(U, wal=2_000_000)
    outs = []
    c, j, t = call("buy", {"plan_id": 1}); outs.append(t); oid = j.get("order_id")
    for a, b in [("me", {}), ("services", {}), ("live", {"id": oid}), ("service", {"id": oid}), ("quote", {"id": oid}), ("plans", {}), ("history", {}), ("referral", {}), ("apps", {}), ("tickets", {}),
                 ("topup", {"amount": 100000}), ("wheel", {"check": 1}), ("renew", {"id": oid}), ("nope", {})]:
        outs.append(call(a, b)[2])
    blob = "\n".join(outs)
    found = [s for s in (TOKEN, "mini-test-pw", "SEC-TEST-TOKEN", '"password"', "access_token", "dbPassword", "/root/", "/var/www", "Stack trace", ".php on line", "mysqli_sql_exception") if s in blob]
    return bool(found), f"found in answers: {found}"


# ===== payment
@scn("PY1", "HIGH", "double spend: 8 parallel purchases with money for one -> exactly one service, wallet never negative, one panel account")
def py1():
    reset(panel_delay=0.2)
    mk_user(U, wal=135_000)
    r = par(8, lambda i: call("buy", {"plan_id": 1}))
    wins = sum(1 for c, j, t in r if ok(j))
    w, orders, accounts = wallet(), int(q1(f"SELECT COUNT(*) FROM orders_list WHERE userid='{U}'")), len(PU)
    paid = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={U} AND state='paid_with_wallet'"))
    stuck = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={U} AND state NOT IN ('paid_with_wallet')", "0"))
    bad = wins != 1 or w != 5_000 or orders != 1 or accounts != 1 or paid != 1 or stuck != 0
    return bad, f"wins={wins} wallet={w} (want 5000) orders={orders} panel accounts={accounts} paid rows={paid} other pays rows={stuck}"


@scn("PY2", "HIGH", "money for exactly 3: 12 parallel purchases / renewals / volume / days -> only what the wallet pays for goes through, nothing below zero")
def py2():
    out = []
    reset(panel_delay=0.1)
    mk_user(U, wal=390_000)
    r = par(12, lambda i: call("buy", {"plan_id": 1}))
    w1, n1 = wallet(), sum(1 for c, j, t in r if ok(j))
    out.append(("buy x12", n1 == 3 and w1 == 0, f"wins={n1} wallet={w1}"))
    reset(panel_delay=0)
    mk_user(U, wal=260_000); oid = mk_service(U)
    r = par(8, lambda i: call("renew", {"id": oid}))
    w2, n2 = wallet(), sum(1 for c, j, t in r if ok(j))
    out.append(("renew x8", n2 == 2 and w2 == 0, f"wins={n2} wallet={w2}"))
    reset()
    mk_user(U, wal=24_000); oid = mk_service(U)
    lim0 = PU[f"svc{U}"]["data_limit"]
    r = par(8, lambda i: call("addvolume", {"id": oid, "amount": 5}))
    w3, n3 = wallet(), sum(1 for c, j, t in r if ok(j))
    got = (PU[f"svc{U}"]["data_limit"] - lim0) // GB
    out.append(("addvolume x8", n3 == 2 and w3 == 0 and got == 10, f"wins={n3} wallet={w3} GB added={got}"))
    reset()
    mk_user(U, wal=18_000); oid = mk_service(U)
    r = par(8, lambda i: call("adddays", {"id": oid, "amount": 7}))
    w4, n4 = wallet(), sum(1 for c, j, t in r if ok(j))
    out.append(("adddays x8", n4 == 2 and w4 == 0, f"wins={n4} wallet={w4}"))
    bad = [f"{n}: {d}" for n, good, d in out if not good]
    return bool(bad), "; ".join(bad) if bad else "; ".join(f"{n} ok" for n, _, _ in out)


@scn("PY3", "HIGH", "extra volume / days: odd amounts (negative, 0, fraction, string, array, huge, bool) never charge for something other than what the panel got")
def py3():
    vals = [-1, 0, 1, 2, 2.5, "3", "3.9", "1e1", "abc", "0x10", " 7 ", [5], [], {"a": 1}, True, False, None, "\u0665", 10 ** 12, 2 ** 63, "9" * 30, 1e30, 2001, 1001, 1000, 2000]
    problems = []
    for kind, act in (("volume", "addvolume"), ("days", "adddays")):
        for v in vals:
            reset()
            mk_user(U, wal=50_000_000); oid = mk_service(U)
            rem = f"svc{U}"
            lim0, exp0, w0 = PU[rem]["data_limit"], PU[rem]["expire"], wallet()
            c, j, t = call(act, {"id": oid, "amount": v})
            w1 = wallet(); charged = w0 - w1
            gb = (PU[rem]["data_limit"] - lim0) / GB
            days = (PU[rem]["expire"] - exp0) / 86400
            n = gb if kind == "volume" else round(days)
            if ok(j):
                unit = 2500 if kind == "volume" else 1500
                preset = {5: 12000} if kind == "volume" else {7: 9000}
                want = preset.get(int(n), unit * int(n))
                if not (n >= 1 and charged == want and charged > 0 and float(n).is_integer()):
                    problems.append(f"{act} amount={v!r}: charged {charged}, panel got {n} (want {want})")
            elif charged != 0 or gb != 0 or days != 0:
                problems.append(f"{act} amount={v!r}: refused but charged {charged}, panel +{gb} GB +{days} d")
    return bool(problems), "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else "") if problems else f"{len(vals) * 2} odd amounts: price always = DB price of what was delivered"


@scn("PY4", "HIGH", "top-up amount: negative / zero / fraction / string / array / huge / bool are refused or kept exact; the stored price is what was asked, within limits")
def py4():
    vals = [-100000, 0, 9999, 10000, 100000.7, "150000", "1e5", "abc", [100000], {"a": 1}, True, None, 10 ** 12, 2 ** 63, 50_000_001, 50_000_000, "\u0662\u0660\u0660\u0660\u0660\u0660", 1e30, " 120000 "]
    problems = []
    for v in vals:
        reset()
        mk_user(U)
        c, j, t = call("topup", {"amount": v})
        rows = sql(f"SELECT price FROM pays WHERE user_id={U} AND type='INCREASE_WALLET'")
        price = int(rows[0][0]) if rows else None
        if ok(j):
            if not (10000 <= price <= 50_000_000 and price == j["amount"] and j["exact"] - j["amount"] in range(100, 1000)):
                problems.append(f"amount={v!r}: stored {price}, answered {j.get('amount')}/{j.get('exact')}")
        elif rows:
            problems.append(f"amount={v!r}: refused but a pays row was written ({price})")
    return bool(problems), "; ".join(problems[:6]) if problems else f"{len(vals)} odd top-up amounts handled"


@scn("PY5", "HIGH", "price tampering: price / discount / agent fields in the request body change nothing; the charge is the plan price in the DB")
def py5():
    reset()
    mk_user(U, wal=1_000_000)
    extra = {"price": 1, "amount": 1, "total": 1, "discount": 99, "discount_percent": 99, "is_agent": 1, "agent": 1, "wallet": 10 ** 9, "user_id": V, "server_id": 999, "days": 1, "volume": 1, "plan": {"price": 1}, "state": "paid"}
    w0 = wallet(); c, j, t = call("buy", dict(extra, plan_id=1))
    oid = j.get("order_id")
    w1 = wallet(); w1c = w0 - w1
    c, j, t = call("renew", dict(extra, id=oid)); w2c = w1 - wallet()
    pays = sql(f"SELECT price, volume, day, server_id FROM pays WHERE user_id={U} AND state='paid_with_wallet' ORDER BY id")
    bad = w1c != 130000 or w2c != 130000
    return bad, f"buy charged {w1c}, renew charged {w2c} (want 130000 each); pays rows {pays}"


@scn("PY6", "MED", "agent discount: an impossible percentage (above 100, negative, text) never pays the customer or charges above the list price")
def py6():
    problems = []
    for disc in ('{"normal":150}', '{"normal":100}', '{"normal":-50}', '{"normal":"100%"}', '{"normal":30,"servers":{"1":250}}', 'not json', '{"normal":1e3}', '{"normal":99999999999}'):
        reset()
        mk_user(U, wal=1_000_000, agent=1, disc=disc)
        w0 = wallet(); c, j, t = call("buy", {"plan_id": 1})
        charged = w0 - wallet()
        if ok(j) and not (0 <= charged <= 130000):
            problems.append(f"discount_percent={disc}: charged {charged}")
        c, j2, t = call("plans")
        shown = j2["plans"][0]["price"] if ok(j2) else None
        if shown is not None and not (0 <= shown <= 130000):
            problems.append(f"discount_percent={disc}: plan list shows price {shown}")
    return bool(problems), "; ".join(problems[:5]) if problems else "agent price always within 0..list price"


@scn("PY7", "HIGH", "panel failure after the wallet was locked: down / 500 / no username -> wallet intact, no pays row, no order, capacity untouched")
def py7():
    problems = []
    for name, setup in (("create 500", lambda: PANEL.update(fail_create=500)), ("panel down", lambda: PANEL.update(down=True))):
        reset(); mk_user(U, wal=500_000); setup()
        c, j, t = call("buy", {"plan_id": 1})
        rows = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={U}")); orders = int(q1("SELECT COUNT(*) FROM orders_list")); uc = q1("SELECT ucount FROM server_info WHERE id=1")
        if ok(j) or wallet() != 500_000 or rows or orders or uc != "100":
            problems.append(f"buy/{name}: ok={ok(j)} wallet={wallet()} pays={rows} orders={orders} ucount={uc}")
    for name, key in (("renew", "fail_modify"), ("addvolume", "fail_modify")):
        reset(); mk_user(U, wal=500_000); oid = mk_service(U); PANEL[key] = 500
        c, j, t = call(name, {"id": oid, "amount": 5} if name == "addvolume" else {"id": oid})
        rows = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={U}"))
        if ok(j) or wallet() != 500_000 or rows:
            problems.append(f"{name}/panel 500: ok={ok(j)} wallet={wallet()} pays={rows}")
    return bool(problems), "; ".join(problems) if problems else "every refused panel call left wallet, pays, orders and capacity as they were"


@scn("PY8", "MED", "the account was made on the panel but the order could not be saved: nothing charged AND no orphan account left on the panel; renew / extra: no free extension")
def py8():
    problems = []
    reset(); mk_user(U, wal=500_000); PANEL["sub_pad"] = 1500      # token column is varchar(1000): the INSERT fails after the panel made the account
    c, j, t = call("buy", {"plan_id": 1})
    if wallet() != 500_000:
        problems.append(f"buy: wallet {wallet()} after a failed order insert")
    if PU:
        problems.append(f"buy: {len(PU)} orphan account(s) left on the panel with no order (customer got nothing, the account exists)")
    for act, body in (("renew", {}), ("adddays", {"amount": 7})):
        reset(); mk_user(U, wal=500_000)
        far = 2_147_000_000                                      # 2038: the next expire_date does not fit the int(11) column, the DB step fails after the panel was changed
        oid = mk_service(U, expire=far)
        e0 = PU[f"svc{U}"]["expire"]
        c, j, t = call(act, dict(body, id=oid))
        if wallet() == 500_000 and PU[f"svc{U}"]["expire"] != e0:
            problems.append(f"{act}: wallet untouched but the panel account was extended anyway (free extension)")
    return bool(problems), "; ".join(problems) if problems else "failed order step leaves panel, wallet and rows consistent"


@scn("PY9", "MED", "server capacity: 6 customers buying the last slot at once -> one wins, the counter never goes below zero")
def py9():
    reset(panel_delay=0.25, ucount="1")
    for i in range(6):
        mk_user(U + i, wal=200_000)
    r = par(6, lambda i: call("buy", {"plan_id": 1}, uid=U + i))
    wins, uc = sum(1 for c, j, t in r if ok(j)), q1("SELECT ucount FROM server_info WHERE id=1")
    fails = sorted(set((j or {}).get("error", t)[:70] for c, j, t in r if not ok(j)))
    return wins != 1 or uc != "0" or len(PU) != 1, f"wins={wins} (want 1) ucount={uc} (want 0), panel accounts={len(PU)} (want 1; the others are orphans), refusals: {fails}"


@scn("PY10", "HIGH", "card-to-card receipt: 5 parallel uploads for the same top-up -> ONE approve message set for the admins and one state change")
def py10():
    reset(tg_delay=0.3)
    mk_user(U)
    c, j, t = call("topup", {"amount": 100000}); h = j["hash"]
    r = par(5, lambda i: call("receipt", multipart={"hash": h, "initData": init_data(U), "a": "receipt", "photo": ("r.png", "image/png", png())}))
    wins = sum(1 for c, j, t in r if ok(j))
    photos_main = len(tg_msgs("sendPhoto", ADMIN)); photos_2 = len(tg_msgs("sendPhoto", ADMIN2))
    st = sql(f"SELECT state, message_id FROM pays WHERE hash_id='{h}'")
    bad = wins != 1 or photos_main != 1 or photos_2 != 1
    return bad, f"accepted={wins} (want 1), photos to admin1={photos_main}, to admin2={photos_2} (want 1 each), final row={st}"


@scn("PY11", "MED", "card-to-card receipt: Telegram refusing the photo leaves the top-up retryable (state still pending), not stuck")
def py11():
    reset(); mk_user(U)
    c, j, t = call("topup", {"amount": 100000}); h = j["hash"]
    TG["fail"].add("sendPhoto")
    c, j, t = call("receipt", multipart={"hash": h, "initData": init_data(U), "a": "receipt", "photo": ("r.png", "image/png", png())})
    st = q1(f"SELECT state FROM pays WHERE hash_id='{h}'")
    TG["fail"].clear()
    c2, j2, t2 = call("receipt", multipart={"hash": h, "initData": init_data(U), "a": "receipt", "photo": ("r.png", "image/png", png())})
    return not (not ok(j) and st == "pending" and ok(j2)), f"first={c} state after={st}, retry ok={ok(j2)}"


@scn("PY12", "MED", "top-up requests in parallel: at most one pending row, and the 3-receipts-waiting limit holds")
def py12():
    reset(); mk_user(U)
    r = par(8, lambda i: call("topup", {"amount": 100000 + i * 1000}))
    pend = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={U} AND type='INCREASE_WALLET' AND state='pending'"))
    reset(); mk_user(U)
    for i in range(3):
        sql(f"INSERT INTO pays (hash_id, user_id, type, plan_id, volume, day, price, request_date, state, agent_bought, agent_count) VALUES ('HS{i}', {U}, 'INCREASE_WALLET', 0, 0, 0, 50000, {int(time.time())}, 'have_sent', 0, 0)")
    c, j, t = call("topup", {"amount": 100000})
    return pend > 1 or ok(j), f"pending rows after 8 parallel requests={pend} (want 1), 4th top-up with 3 waiting answered ok={ok(j)}"


def disk_files():
    """files a request could have left: the upload temp dir (PHP removes its uploads at the end of a request) and the bot trees (the error log and the json caches excepted)"""
    out = subprocess.run(f"find {B}/uptmp {B}/bot {B}/bot_empty -type f 2>/dev/null | grep -v -E 'miniapp_error.log|panel_kind.json|baseInfo.php|extras.php|values.php|api.php' | sort | md5sum", shell=True, capture_output=True, text=True).stdout.split()[0]
    return out


@scn("PY13", "MED", "receipt upload: only a real picture, nothing is stored on disk, a name / polyglot / svg / huge file does not matter")
def py13():
    reset(); mk_user(U)
    files0 = disk_files()
    cases = {
        "php polyglot png": ("../../x.php", "image/png", png() + b"<?php system($_GET[0]); ?>"), "svg with script": ("a.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
        "html as jpg": ("a.jpg", "image/jpeg", b"<html><script>alert(1)</script></html>"), "gif": ("a.gif", "image/gif", b"GIF89a" + b"0" * 30), "7 MB png": ("big.png", "image/png", png() + b"0" * (7 * 1024 * 1024)),
        "empty file": ("e.png", "image/png", b""), "text named png": ("a.png", "image/png", b"hello"),
    }
    accepted, notes = [], []
    for name, (fn, ct, data) in cases.items():
        reset(); mk_user(U)
        c, j, t = call("topup", {"amount": 100000}); h = j["hash"]
        c, j, t = call("receipt", multipart={"hash": h, "initData": init_data(U), "a": "receipt", "photo": (fn, ct, data)})
        if ok(j):
            accepted.append(name)
    files1 = disk_files()
    real = {"php polyglot png"}                                     # a genuine PNG with trailing bytes is accepted on purpose: it is only forwarded to Telegram
    bad = [a for a in accepted if a not in real]
    return bool(bad) or files1 != files0, f"accepted besides the real-picture cases: {bad}; new files on disk {files0}->{files1}"


@scn("PY14", "HIGH", "gift code: 6 parallel taps once; 8 users on a 2-use code -> 2 credits; case / space / repeat tricks; negative or absurd amounts; guessing is slowed down")
def py14():
    problems = []
    reset(); mk_user(U)
    sql("CREATE TABLE gift_codes (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, code VARCHAR(64) NOT NULL, amount BIGINT NOT NULL DEFAULT 0, max_uses INT NOT NULL DEFAULT 1, used INT NOT NULL DEFAULT 0, used_by TEXT NULL, expires BIGINT NOT NULL DEFAULT 0, created BIGINT NOT NULL DEFAULT 0, note VARCHAR(255) NULL, UNIQUE KEY code (code)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    sql("INSERT INTO gift_codes (code, amount, max_uses, used, used_by, expires, created) VALUES ('ONCE', 50000, 1, 0, '[]', 0, 1), ('TWICE', 7000, 2, 0, '[]', 0, 1), ('NEG', -90000, 5, 0, '[]', 0, 1), ('BIG', 4000000000, 5, 0, '[]', 0, 1), ('OLD', 1000, 5, 0, '[]', 1, 1)")
    r = par(6, lambda i: call("gift", {"code": "ONCE"}))
    wins = sum(1 for c, j, t in r if ok(j))
    if wins != 1 or wallet() != 50000:
        problems.append(f"6 taps: wins={wins} wallet={wallet()}")
    for variant in ("once", " ONCE ", "ONCE\t", "Once"):
        c, j, t = call("gift", {"code": variant})
        if ok(j):
            problems.append(f"variant {variant!r} paid again")
    for i in range(8):
        mk_user(U + 10 + i)
    r = par(8, lambda i: call("gift", {"code": "TWICE"}, uid=U + 10 + i))
    wins = sum(1 for c, j, t in r if ok(j)); total = sum(wallet(U + 10 + i) or 0 for i in range(8))
    if wins != 2 or total != 14000:
        problems.append(f"2-use code, 8 users: wins={wins} credited={total}")
    wn = wallet(U + 11); c, j, t = call("gift", {"code": "NEG"}, uid=U + 11)
    if (wallet(U + 11) or 0) < (wn or 0):
        problems.append(f"negative gift code (amount -90000) DEBITED the wallet: {wn}->{wallet(U + 11)}")
    c, j, t = call("gift", {"code": "BIG"}, uid=U + 12)
    if c >= 500 and not j:
        problems.append("a huge gift amount ended in a raw 500 instead of a JSON answer")
    c, j, t = call("gift", {"code": "OLD"}, uid=U + 13)
    if ok(j):
        problems.append("expired code accepted")
    t0 = time.time(); statuses = [call("gift", {"code": f"GUESS{i}"}, uid=U + 14)[0] for i in range(60)]
    if all(s == 400 for s in statuses):
        problems.append(f"no brake on guessing: 60 wrong codes in {time.time() - t0:.1f}s, every one answered normally (a code can be brute-forced)")
    return bool(problems), "; ".join(problems) if problems else "gift codes held"


@scn("PY15", "HIGH", "lucky wheel: 8 parallel spins pay once; another user is unaffected; prizes above wallet limits do not crash")
def py15():
    reset(states={"wheelPrizes": "5000,5000,5000"}); mk_user(U); mk_user(V)
    r = par(8, lambda i: call("wheel", uid=U))
    wins = sum(1 for c, j, t in r if ok(j)); logs = int(q1(f"SELECT COUNT(*) FROM wheel_log WHERE userid={U}"))
    c, j, t = call("wheel", uid=V)
    return wins != 1 or logs != 1 or wallet(U) != 5000 or not ok(j), f"wins={wins} log rows={logs} wallet={wallet(U)} other user ok={ok(j)}"


@scn("PY16", "HIGH", "ledger: a storm of mixed parallel requests keeps wallet = start - paid + cashback + wheel + gift, never negative, no payment left half done")
def py16():
    reset(states={"cashbackPercent": "5", "wheelPrizes": "1000,2000"}, panel_delay=0.05)
    users = [U + i for i in range(4)]
    for u in users:
        mk_user(u, wal=300_000)
    sql("CREATE TABLE gift_codes (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, code VARCHAR(64) NOT NULL, amount BIGINT NOT NULL DEFAULT 0, max_uses INT NOT NULL DEFAULT 1, used INT NOT NULL DEFAULT 0, used_by TEXT NULL, expires BIGINT NOT NULL DEFAULT 0, created BIGINT NOT NULL DEFAULT 0, note VARCHAR(255) NULL, UNIQUE KEY code (code)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    sql("INSERT INTO gift_codes (code, amount, max_uses, used, used_by, expires, created) VALUES ('G', 3000, 100, 0, '[]', 0, 1)")
    oids = {u: mk_service(u) for u in users}
    rnd = random.Random(7)
    ops = []
    for _ in range(80):
        u = rnd.choice(users)
        ops.append((u, rnd.choice(["buy", "renew", "addvolume", "adddays", "wheel", "gift", "topup"])))

    def one(i):
        u, a = ops[i]
        body = {"buy": {"plan_id": 1}, "renew": {"id": oids[u]}, "addvolume": {"id": oids[u], "amount": 5}, "adddays": {"id": oids[u], "amount": 7}, "wheel": {}, "gift": {"code": "G"}, "topup": {"amount": 100000}}[a]
        return call(a, body, uid=u)
    par(len(ops), one)
    problems = []
    for u in users:
        w = wallet(u)
        paid = int(q1(f"SELECT COALESCE(SUM(price),0) FROM pays WHERE user_id={u} AND state='paid_with_wallet'"))
        cb = int(q1(f"SELECT COALESCE(SUM(cashback),0) FROM cashback_log WHERE userid={u}", "0"))
        wh = int(q1(f"SELECT COALESCE(SUM(prize),0) FROM wheel_log WHERE userid={u}", "0"))
        gf = 3000 if q1(f"SELECT COUNT(*) FROM gift_codes WHERE used_by LIKE '%\"{u}\"%'", "0") != "0" else 0
        stuck = int(q1(f"SELECT COUNT(*) FROM pays WHERE user_id={u} AND state IN ('processing_wallet')"))
        if w < 0 or w != 300_000 - paid + cb + wh + gf or stuck:
            problems.append(f"user {u}: wallet {w}, ledger says {300_000 - paid + cb + wh + gf}, stuck rows {stuck}")
    acc = int(q1("SELECT COUNT(*) FROM orders_list WHERE remark NOT LIKE 'svc%'"))
    if len(PU) - len(users) != acc:
        problems.append(f"panel accounts bought {len(PU) - len(users)} but orders saved {acc}")
    return bool(problems), "; ".join(problems) if problems else f"{len(ops)} parallel mixed requests: ledger exact, no negative wallet"


@scn("PY17", "MED", "extra volume on an UNLIMITED service is not sold (the panel keeps it unlimited: the customer would pay for nothing)")
def py17():
    reset(); mk_user(U, wal=100_000); oid = mk_service(U, limit=0)
    w0 = wallet(); c, j, t = call("addvolume", {"id": oid, "amount": 5})
    return ok(j) and wallet() < w0, f"addvolume on limit=0: ok={ok(j)} charged={w0 - wallet()} panel limit now {PU[f'svc{U}']['data_limit']}"


@scn("PY18", "MED", "bot parity: what the Mini App writes for a renewal is what the bot writes (type RENEW_ACCOUNT, order_id, increase_order row) so reports and history agree")
def py18():
    reset(); mk_user(U, wal=500_000); oid = mk_service(U)
    c, j, t = call("renew", {"id": oid})
    p = sql(f"SELECT type, order_id, plan_id, state FROM pays WHERE user_id={U}")
    io = int(q1(f"SELECT COUNT(*) FROM increase_order WHERE userid='{U}'"))
    c, h, t = call("history")
    typ = [x["type"] for x in h["pays"]] if ok(h) else []
    bad = not (p and p[0][0] == "RENEW_ACCOUNT" and p[0][1] == str(oid) and io == 1)
    return bad, f"pays row (type, order_id, plan_id, state)={p}, increase_order rows={io} (the bot writes one per renewal), history shows {typ}"


@scn("PY19", "LOW", "new user row is made like the bot's (step 'none'), and 8 first requests in parallel do not crash")
def py19():
    reset()
    r = par(8, lambda i: call("me", uid=U + 50))
    codes = sorted(set(c for c, j, t in r))
    step = q1(f"SELECT step FROM users WHERE userid='{U + 50}'")
    return codes != [200] or step != "none", f"status codes of 8 parallel first requests={codes}, users.step={step!r} (the bot's default is 'none')"


@scn("PY20", "HIGH", "a plan that costs nothing is the bot's one-per-customer test account: not sold again and again (users.freetrial), admins excepted")
def py20():
    reset(); mk_user(U, wal=0); mk_user(V, wal=0)
    sql("UPDATE server_plans SET price = 0 WHERE id = 1")
    r = par(5, lambda i: call("buy", {"plan_id": 1}, uid=U))
    wins = sum(1 for c, j, t in r if ok(j))
    c, j, t = call("buy", {"plan_id": 1}, uid=U)
    later = ok(j)
    ft = q1(f"SELECT freetrial FROM users WHERE userid='{U}'")
    c, j2, t = call("buy", {"plan_id": 1}, uid=V)
    other = ok(j2)
    return wins != 1 or later or ft != "used" or not other or len(PU) != 2, f"5 parallel free buys -> {wins} accounts (want 1), a later one ok={later}, freetrial={ft!r}, another customer can still take theirs={other}, panel accounts={len(PU)} (want 2)"

@scn("PY21", "MED", "the panel made the account but its answer came too late (time-out): no orphan account is left behind and nothing is charged")
def py21():
    reset(); mk_user(U, wal=500_000); PANEL["hang_after_create"] = 21
    c, j, t = call("buy", {"plan_id": 1})
    time.sleep(1.0)
    return bool(PU) or wallet() != 500_000 or ok(j), f"buy answered ok={ok(j)}, wallet={wallet()} (want 500000), accounts left on the panel={len(PU)} (want 0)"


@scn("PY22", "HIGH", "a lost answer: the same payment key sent again (parallel, later, or after a refused try) charges once and shows the first result; a new key is a new payment; bad keys are ignored")
def py22():
    problems, rid = [], "rid-test-0001-abcd"
    reset(panel_delay=0.1); mk_user(U, wal=1_000_000)
    r = par(6, lambda i: call("buy", {"plan_id": 1, "rid": rid}))
    wins = [j for c, j, t in r if ok(j)]
    orders = int(q1(f"SELECT COUNT(*) FROM orders_list WHERE userid='{U}'"))
    if len(wins) != 6 or wallet() != 870_000 or len(PU) != 1 or orders != 1:
        problems.append(f"buy x6 same key: ok={len(wins)} wallet={wallet()} (want 870000) accounts={len(PU)} orders={orders}")
    c, j2, t = call("buy", {"plan_id": 1, "rid": rid})
    if not (ok(j2) and wins and j2.get("order_id") == wins[0].get("order_id") and j2.get("remark") == wins[0].get("remark") and j2.get("sub") == wins[0].get("sub")):
        problems.append(f"a later retry did not show the first result: {t[:120]}")
    reset(); mk_user(U, wal=500_000); PANEL["fail_create"] = 500
    call("buy", {"plan_id": 1, "rid": rid}); PANEL["fail_create"] = None
    c, j, t = call("buy", {"plan_id": 1, "rid": rid})
    if not ok(j) or wallet() != 370_000:
        problems.append(f"retry after a refused try: ok={ok(j)} wallet={wallet()} (want 370000)")
    c, j, t = call("buy", {"plan_id": 1, "rid": rid + "2"})
    if not ok(j) or wallet() != 240_000:
        problems.append(f"a new key must be a new purchase: ok={ok(j)} wallet={wallet()} (want 240000)")
    reset(); mk_user(U, wal=500_000); oid = mk_service(U)
    for _ in range(2):
        call("renew", {"id": oid, "rid": rid})
    if wallet() != 370_000:
        problems.append(f"renew twice with one key: wallet={wallet()} (want 370000)")
    lim0 = PU[f"svc{U}"]["data_limit"]
    for _ in range(3):
        call("addvolume", {"id": oid, "amount": 5, "rid": rid + "v"})
    if wallet() != 358_000 or PU[f"svc{U}"]["data_limit"] - lim0 != 5 * GB:
        problems.append(f"addvolume x3 with one key: wallet={wallet()} (want 358000), GB added={(PU[f'svc{U}']['data_limit'] - lim0) / GB}")
    e0 = PU[f"svc{U}"]["expire"]
    for _ in range(3):
        call("adddays", {"id": oid, "amount": 7, "rid": rid + "d"})
    if wallet() != 349_000 or PU[f"svc{U}"]["expire"] - e0 != 7 * 86400:
        problems.append(f"adddays x3 with one key: wallet={wallet()} (want 349000), days added={(PU[f'svc{U}']['expire'] - e0) / 86400}")
    reset(); mk_user(U, wal=1_000_000)
    for bad in ("short", "has space in it!!", "x" * 50, 12345, ["abcdefghij"], None):
        call("buy", {"plan_id": 1, "rid": bad})
    if wallet() != 1_000_000 - 6 * 130_000:
        problems.append(f"6 buys with unusable keys must be 6 purchases: wallet={wallet()}")
    return bool(problems), "; ".join(problems) if problems else "one key = one payment (buy, renew, volume, days); a refused try can be retried; other keys pay again"

# ===== injection and web basics
SQLI = ["'", "\"", "' OR '1'='1", "1' OR '1'='1' -- ", "1; DROP TABLE users; -- ", "' UNION SELECT 1,2,3 -- ", "\\", "%27", "1 AND SLEEP(2)", "') OR 1=1 -- ", "0x27", "\x00", "a' or 1=1#", "1) UNION SELECT password FROM server_config -- ", "O'Brien", "%' OR '%'='", "{\"a\":\"'\"}"]


@scn("IN1", "HIGH", "SQL injection: odd strings in every parameter of every action: JSON answers, no SQL error, tables intact, no extra rows")
def in1():
    reset(states={"giftState": "on"}); mk_user(U, wal=1_000_000); oid = mk_service(U)
    sql("CREATE TABLE IF NOT EXISTS gift_codes (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, code VARCHAR(64) NOT NULL, amount BIGINT NOT NULL DEFAULT 0, max_uses INT NOT NULL DEFAULT 1, used INT NOT NULL DEFAULT 0, used_by TEXT NULL, expires BIGINT NOT NULL DEFAULT 0, created BIGINT NOT NULL DEFAULT 0, note VARCHAR(255) NULL, UNIQUE KEY code (code))")
    tables0 = int(q1("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='nikabot_sec'"))
    users0 = int(q1("SELECT COUNT(*) FROM users")); w0 = wallet()
    problems = []
    c, j, t = call("topup", {"amount": 100000}); hh = j["hash"]
    endpoints = [("gift", ["code"]), ("cancel_topup", ["hash"]), ("ticket_new", ["title", "text", "category"]), ("report", ["text"]), ("live", ["id"]), ("service", ["id"]), ("quote", ["id"]), ("plans", ["x"]),
                 ("buy", ["plan_id"]), ("addvolume", ["amount"]), ("topup", ["amount"]), ("ticket_messages", ["id"]), ("ticket_reply", ["id", "text"]), ("wheel", ["check"]), ("history", ["x"])]
    for a, params in endpoints:
        for p in params:
            for s in SQLI:
                body = {"id": oid, "text": "x", "plan_id": 1, "amount": 5, "hash": hh, "code": "X", "title": "t", "category": "tech", "check": 1}
                body[p] = s
                c, j, t = call(a, body)
                if j is None or c >= 500:
                    problems.append(f"{a}.{p}={s!r}: HTTP {c} not JSON: {t[:60]}")
                    break
    mp = [call("receipt", multipart={"hash": s, "initData": init_data(U), "a": "receipt", "photo": ("r.png", "image/png", png())}) for s in SQLI]
    problems += [f"receipt.hash: HTTP {c} not JSON" for c, j, t in mp if j is None]
    log = errlog()
    if re.search(r"SQL syntax|mysqli_sql_exception|Unknown column|Truncated", log):
        problems.append("SQL error in the Mini App error log: " + re.findall(r".*(?:SQL syntax|mysqli_sql_exception|Unknown column).*", log)[0][:120])
    if int(q1("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='nikabot_sec'")) < tables0 or int(q1("SELECT COUNT(*) FROM users")) != users0:
        problems.append("tables or users changed")
    name = "x' OR '1'='1\\"
    c, j, t = call("me", uid=U + 77, first=name)
    stored = q1(f"SELECT name FROM users WHERE userid='{U + 77}'")
    if stored is None or stored.replace("\\\\", "\\") != name.strip():
        problems.append(f"second-order: name stored as {stored!r}")
    return bool(problems), "; ".join(problems[:5]) if problems else f"{sum(len(p) for _, p in endpoints) * len(SQLI) + len(SQLI) + 1} injection attempts: all prepared, JSON in, JSON out"


class Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.found = []

    def handle_starttag(self, tag, attrs): self.found.append((tag, attrs))
    handle_startendtag = handle_starttag


@scn("IN2", "MED", "XSS in the HTML the API sends to the page (safeHtml): only <b><strong><i><em><code><u><s><br> with no attribute ever comes out")
def in2():
    src = open(f"{API_DIR}/api.php", encoding="utf-8").read()
    start = src.index("function safeHtml("); fn = src[start:src.index("\n}\n", start) + 3]
    corpus = ['a <b onmouseover="x()">bold</b>', '<b title=">" <img src=x onerror=alert(1)>', '<b <img src=x onerror=alert(1)>', '<i/onclick=alert(1)>x', '<s\nonmouseover=alert(1)>x', "<code/**/onerror=1>x", '<br/ x=1>', '<u onmouseover=1 <u>x',
              '<svg><script>alert(1)</script>', '<b title="a>b" onmouseover="alert(1)">x</b>', "<b title='a>b' onmouseover='alert(1)'>x</b>", '<<b>img src=x onerror=alert(1)>', '<b><img src=x onerror=alert(1)//</b>', '<em style="x">y',
              '<a href="javascript:alert(1)">l</a>', '<script>alert(1)</script>', '<b>"><img src=x onerror=alert(1)>', "<b a=\"'>\" <img src=x onerror=alert(1)>", '<strong\tonmouseover=alert(1)>x', '<b\x00 onmouseover=1>x', '<B ONMOUSEOVER=1>x',
              '&lt;img src=x onerror=alert(1)&gt;', '<b x="<img src=x onerror=alert(1)>">y']
    open(f"{B}/safehtml_t.php", "w").write("<?php\n" + fn + "\n$c = json_decode(file_get_contents($argv[1]), true); echo json_encode(array_map('safeHtml', $c), JSON_UNESCAPED_UNICODE);\n")
    open(f"{B}/safehtml_in.json", "w").write(json.dumps(corpus))
    out = json.loads(subprocess.run(["php", f"{B}/safehtml_t.php", f"{B}/safehtml_in.json"], capture_output=True, text=True, check=True).stdout)
    allowed, bad = {"b", "strong", "i", "em", "code", "u", "s", "br"}, []
    for src_s, res in zip(corpus, out):
        tp = Tags(); tp.feed(res)
        for tag, attrs in tp.found:
            if tag not in allowed or attrs:
                bad.append(f"{src_s!r} -> {res!r}"); break
    return bool(bad), "; ".join(bad[:3]) if bad else f"{len(corpus)} payloads: only bare allowed tags remain"


@scn("IN3", "LOW", "headers and errors: JSON content type, nosniff, no-store, no CORS opening, no PHP notice / path in any error")
def in3():
    reset(); mk_user(U)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api.php?a=me", data=b"{}", headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    try:
        r = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        r = e
    h = {k.lower(): v for k, v in r.headers.items()}
    probs = []
    if not h.get("content-type", "").startswith("application/json"):
        probs.append("content-type " + h.get("content-type", "?"))
    if h.get("x-content-type-options") != "nosniff":
        probs.append("no nosniff")
    if "no-store" not in h.get("cache-control", ""):
        probs.append("not no-store")
    if "access-control-allow-origin" in h:
        probs.append("CORS header " + h["access-control-allow-origin"])
    junk = [("a[]=x", {}), ("a=" + "x" * 5000, {}), ("a=../../etc/passwd", {})]
    for qs, b in junk:
        c, j, t = call("", b, query=qs, init=True)
        if j is None or any(s in t for s in ("/root/", "/var/www", "Warning", "Notice", "Fatal", "Stack trace", "on line")):
            probs.append(f"query {qs[:20]!r}: {c} {t[:80]!r}")
    return bool(probs), "; ".join(probs) if probs else "headers fine; no CORS; errors are JSON sentences"


@scn("IN4", "LOW", "an oversized or odd body: 15 MB JSON, 1 MB text field, deep JSON -> JSON answer (not a fatal)")
def in4():
    reset(); mk_user(U)
    probs = []
    for name, raw in (("15MB json", '{"a":"me","pad":"' + "x" * 15_000_000 + '"}'), ("deep json", "[" * 5000 + "]" * 5000), ("1MB text", json.dumps({"a": "ticket_new", "title": "t", "text": "y" * 1_000_000, "initData": init_data(U)}))):
        c, j, t = call("me", raw=raw, init=None)
        if j is None and c >= 500:
            probs.append(f"{name}: HTTP {c} no JSON")
    return bool(probs), "; ".join(probs) if probs else "no fatal on oversized bodies"


@scn("IN5", "MED", "notifications: purchase / renew / top-up reach the customer and every admin once, without Telegram HTML breaking (a name like A<B&C)")
def in5():
    reset(states={"cashbackPercent": "5"}); mk_user(U, wal=500_000)
    c, j, t = call("buy", {"plan_id": 1}, first="A<b>&C")
    msgs = tg_msgs("sendMessage")
    to_user = [m for m in msgs if m["chat_id"] == str(U)]
    to_a1, to_a2 = [m for m in msgs if m["chat_id"] == str(ADMIN)], [m for m in msgs if m["chat_id"] == str(ADMIN2)]
    bad_html = [m["text"][:60] for m in msgs if "A<b>&C" in m["text"]]
    return not (len(to_user) >= 1 and len(to_a1) == 1 and len(to_a2) == 1 and not bad_html and all(m["token_ok"] for m in msgs)), f"customer msgs={len(to_user)}, admin1={len(to_a1)}, admin2={len(to_a2)}, raw '<' in text={bad_html}"


# ------------------------------------------------------------------ main
def main():
    prepare(); make_pki(); os.makedirs(f"{B}/uptmp", exist_ok=True)
    panel_reset()
    ps = ThreadingHTTPServer(("127.0.0.1", PPORT), Panel); threading.Thread(target=ps.serve_forever, daemon=True).start()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{B}/pki/tg.pem", f"{B}/pki/tg.key")
    ts = ThreadingHTTPServer(("127.0.0.1", TGPORT), Tg); ts.socket = ctx.wrap_socket(ts.socket, server_side=True); threading.Thread(target=ts.serve_forever, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    px = socketserver.ThreadingTCPServer(("127.0.0.1", PROXYPORT), Proxy); px.daemon_threads = True; threading.Thread(target=px.serve_forever, daemon=True).start()
    env = dict(os.environ, https_proxy=f"http://127.0.0.1:{PROXYPORT}", HTTPS_PROXY=f"http://127.0.0.1:{PROXYPORT}", PHP_CLI_SERVER_WORKERS=os.environ.get("SEC_WORKERS", "4"))      # (the build server has ~3.8 GB and no swap: 4 workers by default)
    env.pop("http_proxy", None); env.pop("HTTP_PROXY", None); env["no_proxy"] = ""; env["NO_PROXY"] = ""
    base = ["php", "-d", f"mysqli.default_socket={SOCK}", "-d", "display_errors=0", "-d", f"curl.cainfo={B}/pki/ca.pem", "-d", f"openssl.cafile={B}/pki/ca.pem", "-d", f"upload_tmp_dir={B}/uptmp", "-d", "upload_max_filesize=20M", "-d", "post_max_size=24M"]
    php1 = subprocess.Popen(base + ["-S", f"127.0.0.1:{PORT}", "-t", f"{B}/bot/app"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env2 = dict(env, PHP_CLI_SERVER_WORKERS="1")
    php2 = subprocess.Popen(base + ["-S", f"127.0.0.1:{PORT_EMPTY}", "-t", f"{B}/bot_empty/app"], env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        for sid, sev, title, f in SC:
            if ONLY and sid not in ONLY:
                continue
            t0 = time.time()
            try:
                vuln, detail = f()
            except Exception as e:
                vuln, detail = None, f"harness error: {type(e).__name__}: {e}"
            tag = "ERROR" if vuln is None else ("HOLE " if vuln else "held ")
            line = f"{tag} {sid:5} [{sev:4}] {title}\n        -> {detail}   ({time.time() - t0:.1f}s)"
            print(line, flush=True)
            RESULTS.append((sid, sev, title, vuln, detail))
    finally:
        php1.terminate(); php2.terminate(); ps.shutdown(); ts.shutdown(); px.shutdown()
    LOGSEEN.extend(l for l in errlog("bot").splitlines() if l)
    noisy = sorted({re.sub(r"^\[[^\]]*\] ", "", l)[:160] for l in LOGSEEN if re.search(r"Warning|Notice|Deprecated|Fatal|Undefined|Uncaught|Parse error", l)})
    print(f"\nPHP error log: {len(LOGSEEN)} lines over the run, {len(noisy)} kinds of warning / notice / fatal" + ("".join("\n    " + n for n in noisy[:8]) if noisy else ""))
    holes = [r for r in RESULTS if r[3]]
    print(f"\n{len(RESULTS) - len(holes)} held, {len(holes)} HOLE, {sum(1 for r in RESULTS if r[3] is None)} error   [{LABEL}]")
    if LABEL:
        rdir = os.environ.get("SEC_RESULTS", HERE)
        os.makedirs(rdir, exist_ok=True)
        with open(os.path.join(rdir, f"results-{LABEL}.txt"), "w") as fh:
            for sid, sev, title, vuln, detail in RESULTS:
                fh.write(f"{'ERROR' if vuln is None else ('HOLE' if vuln else 'held')}\t{sid}\t{sev}\t{title}\t{detail}\n")


main()
