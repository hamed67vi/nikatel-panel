#!/usr/bin/env python3
# ruff: noqa      (a throw-away end-to-end harness: broad excepts and open() without a context are on purpose)
"""The "show configs" button of the shop bot, run end to end: the bot's OWN files (a copy of the real ones, before and after scripts/install-configs-button.sh) under `php -S`
on a PRIVATE MariaDB (tests/miniapp_security/sec_setup_db.sh, db nikabot_sec), a fake Telegram over TLS behind a CONNECT proxy that only forwards api.telegram.org to the fake, and a fake
Marzban-shaped panel. Telegram updates are posted to bot.php and what the bot sends is checked; the 'new subscription' message is made by the bot's own buildLinksAndSendQr (run on its own file).
Nothing leaves the box.

  usage: SEC_SCRATCH=/some/dir bash tests/miniapp_security/sec_setup_db.sh start && python3 bot_cfg.py <repo dir with scripts/install-configs-button.sh and bot/menu/configs_button.php> <copy of the bot's files> ; bash ... stop
  (ports 49534-49538)
"""
import json, os, re, select, shutil, socket, socketserver, ssl, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

B = os.environ.get("SEC_SCRATCH", "/tmp/cfg-sec-scratch")
MOD = os.path.abspath(sys.argv[1])
ORIG = os.path.abspath(sys.argv[2])
DB = "nikabot_sec"
SOCK = f"{B}/run/db.sock"
T = f"{B}/cfgbot"
PORT_BEFORE, PORT_AFTER, TGPORT, PROXYPORT = 49534, 49535, 49536, 49537
PANELPORT = 49538
TOKEN = "123456:TEST-TOKEN"
ADMIN, ADMIN2, NEWC, OLDC, AGENT = 900001, 900002, 900100, 900300, 900400
CARD, HOLDER, WALLET = "6037991234567890", "Test Holder", "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
NOTE_WORDS = ("⭐️ در صورتی که", "مشتری قدیمی یا معرفی شده", "شماره کارت", "به پشتیبانی پیام ارسال بفرمایید")      # the star line, as the owner wrote it (2026-10-27: "if you are an old or introduced customer, to get the card number message support")
NOTE_TAIL = "پیام ارسال بفرمایید"
RED = "اوتباند"
TG_LOG, TLOCK = [], threading.Lock()


# ------------------------------------------------------------------ db
def sql(q):
    r = subprocess.run(["mariadb", "--no-defaults", "--default-character-set=utf8mb4", "-S", SOCK, "-uroot", "-N", "-B", DB, "-e", q], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300] + " :: " + q[:120])
    return r.stdout.strip()


def esc(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


# ------------------------------------------------------------------ fake Telegram (TLS) + CONNECT proxy
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
            fields = {k.decode(): v.decode(errors="replace") for k, v in re.findall(rb'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S)}
        else:
            fields = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode(errors="replace")).items()}
        with TLOCK:
            TG_LOG.append({"method": method, "chat_id": str(fields.get("chat_id", "")), "text": fields.get("text") or fields.get("caption") or "", "markup": fields.get("reply_markup", ""), "parse": fields.get("parse_mode", "")})
        data = json.dumps({"ok": True, "result": {"message_id": 700 + len(TG_LOG)}}).encode()
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


def make_pki():
    d = f"{B}/pki"
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(f"{d}/tg.pem") or subprocess.run(f"openssl x509 -checkend 3600 -noout -in {d}/tg.pem", shell=True, capture_output=True).returncode != 0:
        subprocess.run(f"cd {d} && openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3 -subj /CN=card-test-ca 2>/dev/null && "
                       f"openssl req -newkey rsa:2048 -nodes -keyout tg.key -out tg.csr -subj /CN=api.telegram.org 2>/dev/null && printf 'subjectAltName=DNS:api.telegram.org' > ext.cnf && "
                       f"openssl x509 -req -in tg.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out tg.pem -days 3 -extfile ext.cnf 2>/dev/null", shell=True, check=True)


# ------------------------------------------------------------------ the two bots
def prepare():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(T)
    for name in ("before", "after"):
        shutil.copytree(ORIG, f"{T}/{name}")
        open(f"{T}/{name}/baseInfo.php", "w").write(f"""<?php
$botToken = '{TOKEN}'; $admin = {ADMIN}; $admins_list = [{ADMIN}, {ADMIN2}]; $botUsername = 'test_shop_bot'; $botUrl = 'https://bot.example.invalid/';
$dbUserName = 'mini'; $dbPassword = 'mini-test-pw'; $dbName = '{DB}'; $USDTPrice = 60000; $USDTPriceagent = 58000;
""")
        net = re.search(r"\$range\s*=\s*\[\s*['\"]([0-9.]+)/", open(f"{ORIG}/config.php", encoding="utf-8").read())      # (check() lets only Telegram's networks in: the first one of the bot's own list)
        open(f"{T}/{name}/prepend.php", "w").write(f"<?php $_SERVER['REMOTE_ADDR'] = '{net.group(1)}';\n")
    r = subprocess.run(["bash", f"{MOD}/scripts/install-configs-button.sh", f"{T}/after"], capture_output=True, text=True, env=dict(os.environ, BACKUP_DIR=f"{T}/bk"))
    print(r.stdout.strip())
    if r.returncode:
        print(r.stderr); sys.exit(2)


def reset(states=None, support_id="@test_support", keys=True):
    sql("SET FOREIGN_KEY_CHECKS=0; DELETE FROM users; DELETE FROM chats; DELETE FROM chats_info; DELETE FROM setting; DELETE FROM pays; DELETE FROM orders_list")
    st = {"botState": "on", "sellState": "on", "walletState": "on", "cartToCartState": "on", "supportID": support_id, "searchState": "on"}
    st.update(states or {})
    st = {k: v for k, v in st.items() if v is not None}
    pk = {"bankAccount": CARD, "holderName": HOLDER, "tronwallet": WALLET} if keys else {"bankAccount": CARD, "holderName": HOLDER}
    sql(f"INSERT INTO setting (type, value) VALUES ('BOT_STATES', {esc(json.dumps(st))}), ('PAYMENT_KEYS', {esc(json.dumps(pk))})")
    now = int(time.time())
    old_join = int(time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, 0)))
    old_buy = int(time.mktime((2026, 2, 1, 12, 0, 0, 0, 0, 0)))
    for uid, adm, agent, joined in ((ADMIN, 1, 0, now), (ADMIN2, 1, 0, now), (NEWC, 0, 0, now - 86400), (OLDC, 0, 0, old_join), (AGENT, 0, 1, now - 86400)):
        sql(f"INSERT INTO users (userid, name, username, refcode, wallet, date, step, isAdmin, is_agent) VALUES ('{uid}', 'T{uid}', 't{uid}', '0', 0, '{joined}', 'none', {adm}, {agent})")
    sql(f"INSERT INTO pays (hash_id, user_id, type, plan_id, server_id, price, request_date, state) VALUES ('oldbuy1', {OLDC}, 'BUY_SUB', 1, 1, 150000, {old_buy}, 'approved')")      # (the old customer: joined before 2026-03-01 and bought before it)
    with TLOCK:
        TG_LOG.clear()


def set_step(uid, step):
    sql(f"UPDATE users SET step = {esc(step)} WHERE userid = '{uid}'")


def step_of(uid):
    return sql(f"SELECT step FROM users WHERE userid = '{uid}'")


_mid = [1000]


def user(uid):
    return {"id": uid, "first_name": f"T{uid}", "username": f"t{uid}"}


def cb(uid, data):
    _mid[0] += 1
    return {"update_id": _mid[0], "callback_query": {"id": f"cb{_mid[0]}", "from": user(uid), "data": data,
            "message": {"message_id": 500, "chat": {"id": uid, "type": "private"}, "date": int(time.time()), "text": "menu", "reply_markup": {"inline_keyboard": []}}}}


def post(port, update):
    import urllib.request
    rq = urllib.request.Request(f"http://127.0.0.1:{port}/bot.php", data=json.dumps(update).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r:
            return r.status, r.read().decode(errors="replace")
    except Exception as e:
        return 0, repr(e)


def sent(chat=None, method=None, since=0):
    with TLOCK:
        return [m for m in TG_LOG[since:] if (chat is None or m["chat_id"] == str(chat)) and (method is None or m["method"] == method)]


def buttons(markup):
    try:
        return [b for row in json.loads(markup)["inline_keyboard"] for b in row]
    except Exception:
        return []


def rows(markup):
    try:
        return json.loads(markup)["inline_keyboard"]
    except Exception:
        return []


def last_msg(chat, since=0):
    ms = [m for m in sent(chat, "sendMessage", since)]
    return ms[-1] if ms else {"text": "", "markup": "", "parse": ""}


def topup(port, uid, amount=200000):
    """the customer taps a wallet top-up amount: returns (the keyboard rows, the hash of his new payment)"""
    post(port, cb(uid, f"increaseWallet_{amount}"))
    h = sql(f"SELECT hash_id FROM pays WHERE user_id = {uid} AND type = 'INCREASE_WALLET' ORDER BY id DESC LIMIT 1")
    return rows(last_msg(uid)["markup"]), h


# ------------------------------------------------------------------ the fake panel (Marzban's shape: token, one user with its configs, the subscription page)
PANEL = {"links": [], "up": True}


class Panel(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def _send(self, code, obj=None, raw=None):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n: self.rfile.read(n)
        if not PANEL["up"]: return self._send(503, {"detail": "down"})
        if self.path.startswith("/api/admin/token"): return self._send(200, {"access_token": "T", "token_type": "bearer"})
        self._send(404, {"detail": "no"})

    def do_GET(self):
        if not PANEL["up"]: return self._send(503, {"detail": "down"})
        m = re.match(r"/api/user/([^/?]+)", self.path)
        if m:
            name = urllib.parse.unquote(m.group(1))
            return self._send(200, {"username": name, "status": "active", "data_limit": 50 * 1073741824, "used_traffic": 1073741824,
                                    "subscription_url": f"http://127.0.0.1:{PANELPORT}/sub/TOK-{name}", "links": PANEL["links"]})
        if self.path.startswith("/sub/"): return self._send(200, raw=b"dm1lc3M6Ly94")
        self._send(404, {"detail": "not found"})      # (so the bot's probe /api/groups says: Marzban)


# ------------------------------------------------------------------ data
CUST, OTHER = 900100, 900300
STORED = ["vless://stored-1@old.example.com:443?type=ws&security=tls#old1", "vless://stored-2@old.example.com:443?type=ws&security=tls#old2"]
LIVE = ["vless://live-1@new.example.com:2083?type=ws&security=tls&path=%2Fa&sni=x#new1", "vless://live-2@new.example.com:2083?type=ws&security=tls#new2", "vmess://eyJ2IjoiMiJ9"]
TOKEN_A = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "A" * 60 + ".sig"      # (as long as a Marzban token: it cannot go into a callback)
TOKEN_B = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "B" * 60 + ".sig"
BTN_TEXT = "نمایش کانفیگ"
HASH_A = __import__("hashlib").md5(TOKEN_A.encode()).hexdigest()[:12]
HASH_B = __import__("hashlib").md5(TOKEN_B.encode()).hexdigest()[:12]


def reset_cfg(states=None, panel_up=True, live=None, stored=None):
    sql("SET FOREIGN_KEY_CHECKS=0; DELETE FROM users; DELETE FROM chats; DELETE FROM chats_info; DELETE FROM setting; DELETE FROM pays; DELETE FROM orders_list; "
        "DELETE FROM server_config; DELETE FROM server_plans; DELETE FROM server_info; DELETE FROM server_categories")
    st = {"botState": "on", "sellState": "on", "walletState": "on", "searchState": "on", "configLinkState": "off", "subLinkState": "on"}
    st.update(states or {})
    st = {k: v for k, v in st.items() if v is not None}
    sql(f"INSERT INTO setting (type, value) VALUES ('BOT_STATES', {esc(json.dumps(st))}), ('PAYMENT_KEYS', {esc(json.dumps({'bankAccount': '1', 'holderName': 'x'}))})")
    now = int(time.time())
    for uid, adm in ((ADMIN, 1), (CUST, 0), (OTHER, 0)):
        sql(f"INSERT INTO users (userid, name, username, refcode, wallet, date, step, isAdmin, is_agent) VALUES ('{uid}', 'T{uid}', 't{uid}', '0', 0, '{now - 86400}', 'none', {adm}, 0)")
    sql(f"INSERT INTO server_config (id, panel_url, ip, sni, header_type, request_header, response_header, security, tlsSettings, type, username, password) VALUES "
        f"(1, 'http://127.0.0.1:{PANELPORT}', '', '', 'none', '', '', 'none', '', 'marzban', 'u', 'p')")
    sql("INSERT INTO server_info (id, title, ucount, remark, flag, active, state) VALUES (1, 'Srv', '10', 'r', '', 1, 1)")
    sql("INSERT INTO server_categories (id, server_id, title, parent, step, active) VALUES (1, '1', 'Cat', 0, 4, 1)")
    sql("INSERT INTO server_plans (id, fileid, catid, server_id, inbound_id, acount, limitip, title, protocol, days, volume, type, price, descr, pic, active, step, date) VALUES "
        "(1, '1', 1, 1, 0, 5, 1, 'Plan', 'vless', 30, 50, 'unlimited', 100000, '', '', 1, 10, '0')")
    ids = []
    for uid, remark, tok in ((CUST, "user_a", TOKEN_A), (OTHER, "user_b", TOKEN_B)):
        sql(f"INSERT INTO orders_list (userid, token, transid, fileid, server_id, inbound_id, remark, uuid, protocol, expire_date, link, amount, status, date, notif, rahgozar, agent_bought) VALUES "
            f"('{uid}', {esc(tok)}, '', 1, 1, 0, '{remark}', '', 'vless', {now + 86400 * 20}, {esc(json.dumps(STORED if stored is None else stored))}, 100000, 1, '{now}', 0, 0, 0)")
        ids.append(int(sql(f"SELECT id FROM orders_list WHERE remark = '{remark}'")))
    PANEL["up"] = panel_up
    PANEL["links"] = LIVE if live is None else live
    for f in __import__("glob").glob("/tmp/cfgbtn_*"):
        os.remove(f)      # (the bot lets a customer tap once in three seconds)
    with TLOCK:
        TG_LOG.clear()
    return ids


def texts(uid, since=0):
    return [m["text"] for m in sent(uid, "sendMessage", since)]


def all_text(uid, since=0):
    return "\n".join(texts(uid, since))


def datas(markup):
    return [b.get("callback_data") for b in buttons(markup)]


# ------------------------------------------------------------------ scenarios
SC = []


def scn(i, title):
    def d(f): SC.append((i, title, f)); return f
    return d


@scn("CB1", "the customer taps the button of his service page (by order id): the configs come from the PANEL as it is now - not the stored (old) list; one message with a back button")
def cb1(port, after):
    oa, ob = reset_cfg(); problems = []
    post(port, cb(CUST, f"showCfgO{oa}"))
    t = all_text(CUST)
    if after:
        for l in LIVE:
            if f"<code>{l}</code>" not in t.replace("&amp;", "&"): problems.append(f"a live config is missing: {l[:40]!r} in {t[:300]!r}")
        if "old.example.com" in t: problems.append("the stored (old) list was used although the panel answered")
        ms = sent(CUST, "sendMessage")
        if len(ms) != 1: problems.append(f"{len(ms)} messages, 1 expected")
        elif datas(ms[0]["markup"]) != [f"orderDetails{oa}"]: problems.append(f"the back button: {datas(ms[0]['markup'])}")
        elif ms[0]["parse"].lower() != "html": problems.append(f"parse mode {ms[0]['parse']!r}")
        if "user_a" not in t: problems.append("the name of the service is not in the message")
    elif "new.example.com" in t:
        problems.append("(control) the old bot sent configs")
    return problems


@scn("CB2", "the button of the 'new subscription' message (by the hash of the token - a Marzban token is longer than a callback may be): the same configs")
def cb2(port, after):
    oa, ob = reset_cfg(); problems = []
    post(port, cb(CUST, f"showCfgT{HASH_A}"))
    t = all_text(CUST)
    if after:
        for l in LIVE:
            if f"<code>{l}</code>" not in t.replace("&amp;", "&"): problems.append(f"a live config is missing: {l[:40]!r}")
    elif "new.example.com" in t:
        problems.append("(control) the old bot sent configs")
    return problems


@scn("CB3", "somebody else's service (by id, by hash, by a made-up hash): nothing leaks, the customer is told it is not found")
def cb3(port, after):
    oa, ob = reset_cfg(); problems = []
    for data in (f"showCfgO{oa}", f"showCfgT{HASH_A}", "showCfgT" + "0" * 12, "showCfgO999999"):
        reset_cfg_keep_throttle()
        post(port, cb(OTHER, data))
        t = all_text(OTHER)
        for bad in ("example.com", "user_a", "vless://", "vmess://"):
            if bad in t: problems.append(f"{data}: {bad!r} leaked: {t[:200]!r}")
        if after and "پیدا نشد" not in t: problems.append(f"{data}: no 'not found' note: {t[:200]!r}")
    return problems


def reset_cfg_keep_throttle():
    for f in __import__("glob").glob("/tmp/cfgbtn_*"):
        os.remove(f)
    with TLOCK:
        TG_LOG.clear()


@scn("CB4", "the panel is down: the stored list is the fallback (the customer still gets configs)")
def cb4(port, after):
    oa, ob = reset_cfg(panel_up=False); problems = []
    post(port, cb(CUST, f"showCfgO{oa}"))
    t = all_text(CUST)
    if after:
        for l in STORED:
            if f"<code>{l}</code>" not in t.replace("&amp;", "&"): problems.append(f"a stored config is missing: {l[:40]!r} in {t[:300]!r}")
    return problems


@scn("CB5", "forty long configs: several messages, each below Telegram's 4096, every config exactly once and in order, the back button on the last message only")
def cb5(port, after):
    big = [f"vless://u{i:02d}@host{i:02d}.example.com:443?type=ws&security=tls&path=%2F{'p' * 380}&sni=s{i}#c{i:02d}" for i in range(40)]
    oa, ob = reset_cfg(live=big); problems = []
    post(port, cb(CUST, f"showCfgO{oa}"))
    ms = sent(CUST, "sendMessage")
    if not after: return problems
    if not 3 <= len(ms) <= 8: problems.append(f"{len(ms)} messages")
    seen = []
    for m in ms:
        if len(m["text"]) > 4096: problems.append(f"a message of {len(m['text'])} characters")
        seen += re.findall(r"<code>(vless://[^<]+)</code>", m["text"])
    if [s.replace("&amp;", "&") for s in seen] != big: problems.append(f"the configs sent are not the 40 in order: {len(seen)}")
    with_btn = [i for i, m in enumerate(ms) if datas(m["markup"])]
    if with_btn != [len(ms) - 1]: problems.append(f"the back button must be on the last message only: {with_btn}")
    return problems


@scn("CB6", "a config with & < > in it is sent escaped (HTML mode) - and shows up whole")
def cb6(port, after):
    odd = "vless://x@h.example.com:443?a=1&b=2<3>4#n&m"
    oa, ob = reset_cfg(live=[odd]); problems = []
    post(port, cb(CUST, f"showCfgO{oa}"))
    t = all_text(CUST)
    if after and "<code>vless://x@h.example.com:443?a=1&amp;b=2&lt;3&gt;4#n&amp;m</code>" not in t: problems.append(f"not escaped: {t[:300]!r}")
    return problems


@scn("CB7", "a second tap within three seconds is ignored (each tap asks the panel); after that it works again")
def cb7(port, after):
    oa, ob = reset_cfg(); problems = []
    post(port, cb(CUST, f"showCfgO{oa}")); n1 = len(sent(CUST, "sendMessage"))
    post(port, cb(CUST, f"showCfgO{oa}")); n2 = len(sent(CUST, "sendMessage"))
    if after:
        if n1 != 1 or n2 != 1: problems.append(f"messages after tap 1: {n1}, after tap 2: {n2} (1 and 1 expected)")
        time.sleep(3.3)
        post(port, cb(CUST, f"showCfgO{oa}"))
        if len(sent(CUST, "sendMessage")) != 2: problems.append("a tap after three seconds was ignored")
    return problems


@scn("CB8", "no config anywhere (the panel has none, the stored list is empty): a short note and the back button, no empty message")
def cb8(port, after):
    oa, ob = reset_cfg(live=[], stored=[]); problems = []
    post(port, cb(CUST, f"showCfgO{oa}"))
    ms = sent(CUST, "sendMessage")
    if after:
        if len(ms) != 1 or "کانفیگی برای نمایش پیدا نشد" not in ms[0]["text"] or datas(ms[0]["markup"]) != [f"orderDetails{oa}"]: problems.append(f"{[(m['text'][:80], datas(m['markup'])) for m in ms]}")
    return problems


@scn("SP1", "the service page ('my subscriptions' -> one service): the button is there while the configs are hidden; not when they are shown; not when BOT_STATES cfgButton = off; the other buttons stay")
def sp1(port, after):
    problems = []
    for states, want in (({}, True), ({"configLinkState": "on"}, False), ({"cfgButton": "off"}, False), ({"qrConfigState": "on", "qrSubState": "on"}, True)):
        oa, ob = reset_cfg(states=states)
        post(port, cb(CUST, f"orderDetails{oa}"))
        ed = [m for m in sent(CUST, "editMessageText")] or [m for m in sent(CUST, "sendMessage")]
        if not ed: problems.append(f"{states}: the page was not drawn"); continue
        d = datas(ed[-1]["markup"])
        if f"showCfgO{oa}" in d and not (after and want): problems.append(f"{states}: the button is there but should not be: {d}")
        if after and want and f"showCfgO{oa}" not in d: problems.append(f"{states}: the button is missing: {d}")
        if states.get("qrConfigState") and not ("showQrSub" + str(oa) in d and "showQrConfig" + str(oa) in d): problems.append(f"the QR buttons were lost: {d}")
        if f"deleteMyConfig{oa}" not in d: problems.append(f"{states}: the delete button is gone: {d}")
    return problems


# ------------------------------------------------------------------ the 'new subscription' message (the bot's own buildLinksAndSendQr, run on its own file)
DRIVER = r"""<?php
$dir = $argv[1]; $state = json_decode($argv[2], true); $links = json_decode($argv[3], true); $tok = $argv[4]; chdir($dir);
$botUsername = 'test_shop_bot'; $botState = $state;
require $dir . '/buy_helpers.php';
if (is_file($dir . '/configs_button.php')) require_once $dir . '/configs_button.php';
$response = (object) ['sub_link' => '/sub/' . $tok, 'vray_links' => $links, 'success' => true];
[$token, $vray, $uniq, $text, $keys, $qr] = buildLinksAndSendQr('marzban', ['panel_url' => 'https://panel.example.com', 'sub_links' => 'https://sub2.example.com'], $botState, 'https://bot.example.com/', 1, 'uuid', 'vless', 'rem', 443, 'ws', 0, 0, '', '', '',
    $response, 777, 10, 30, ['start_bot' => 'Start'], [0, 1], false, '', null, false);
echo json_encode(['token' => $token, 'text' => $text, 'keys' => json_decode($keys, true), 'qr' => $qr, 'vray' => json_decode($vray, true)], JSON_UNESCAPED_UNICODE);
"""


def delivery(after, state, links):
    name = "after" if after else "before"
    open(f"{T}/driver.php", "w").write(DRIVER)
    r = subprocess.run(["php", "-d", "display_errors=1", f"{T}/driver.php", f"{T}/{name}", json.dumps(state), json.dumps(links), TOKEN_A], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except Exception:
        raise RuntimeError("driver: " + (r.stdout + r.stderr)[:400])


@scn("DL1", "the 'new subscription' message while the configs are hidden: the subscription link (main + helper) and NO config in the text; the keyboard's first row is the button (callback <= 64 bytes); the QR is the subscription link")
def dl1(port, after):
    problems = []
    out = delivery(after, {"subLinkState": "on", "configLinkState": "off"}, LIVE)      # (three links: the old code drew a config's QR)
    rows_ = out["keys"]["inline_keyboard"]
    flat = [b for r in rows_ for b in r]
    sub = f"https://panel.example.com/sub/{TOKEN_A}"
    if after:
        if "vless://" in out["text"] or "vmess://" in out["text"]: problems.append(f"a config is in the text: {out['text'][:300]!r}")
        if sub not in out["text"] or f"https://sub2.example.com/sub/{TOKEN_A}" not in out["text"]: problems.append("the subscription links are missing")
        want = f"showCfgT{HASH_A}"
        if not rows_ or [b.get("callback_data") for b in rows_[0]] != [want]: problems.append(f"the first row must be the button: {rows_}")
        elif len(want.encode()) > 64: problems.append("callback too long")
        elif BTN_TEXT not in rows_[0][0]["text"]: problems.append(f"label {rows_[0][0]['text']!r}")
        if not any(b.get("url") for b in flat): problems.append("the start button is gone")
        if out["qr"] != sub: problems.append(f"the QR must be the subscription link: {out['qr']!r}")
        if out["token"] != TOKEN_A or out["vray"] != LIVE: problems.append("the token / stored configs returned for the order are not as before")
    else:
        if flat and any("showCfg" in str(b) for b in flat): problems.append("(control) button in the old bot")
        if out["qr"] != LIVE[1]: problems.append(f"(control) the old bot's QR with three links is the config: {out['qr']!r}")
    return problems


@scn("DL2", "the same message when the configs are shown (configLinkState on): as before - configs in the text, no button, the QR as before; and with cfgButton = off: no button")
def dl2(port, after):
    problems = []
    on = delivery(after, {"subLinkState": "on", "configLinkState": "on"}, LIVE)
    if "vless://live-1" not in on["text"]: problems.append("the configs must be in the text while they are shown")
    if any("showCfg" in str(b) for r in on["keys"]["inline_keyboard"] for b in r): problems.append("a button although the configs are in the text")
    if on["qr"] != LIVE[1]: problems.append(f"the QR changed: {on['qr']!r}")
    off = delivery(after, {"subLinkState": "on", "configLinkState": "off", "cfgButton": "off"}, LIVE)
    if any("showCfg" in str(b) for r in off["keys"]["inline_keyboard"] for b in r): problems.append("a button although cfgButton = off")
    return problems


@scn("DL3", "no subscription link at all (subLinkState off): the QR stays what it was (never empty), the button is there")
def dl3(port, after):
    problems = []
    out = delivery(after, {"subLinkState": "off", "configLinkState": "off"}, LIVE)
    if not out["qr"]: problems.append("empty QR")
    if after and not any("showCfgT" in str(b) for r in out["keys"]["inline_keyboard"] for b in r): problems.append("the button is missing")
    return problems


@scn("IN1", "the installed bot's files: five marked lines, php -l, the module is the repo's")
def in1(port, after):
    problems = []
    if not after: return problems
    n = sum(open(f"{T}/after/{f}", encoding="utf-8").read().count("[configs-button]") for f in ("bot.php", "config.php", "buy_helpers.php"))
    if n != 5: problems.append(f"{n} marked lines")
    for f in ("bot.php", "config.php", "buy_helpers.php", "configs_button.php"):
        if subprocess.run(["php", "-l", f"{T}/after/{f}"], capture_output=True).returncode: problems.append(f"php -l {f}")
    if open(f"{T}/after/configs_button.php", "rb").read() != open(f"{MOD}/bot/menu/configs_button.php", "rb").read(): problems.append("the module is not the repo's")
    return problems


def run_all(port, after, label):
    bad = 0
    for i, title, f in SC:
        t0 = time.time()
        try:
            p = f(port, after)
        except Exception as e:
            p = [f"harness error: {type(e).__name__}: {e}"]
        print(("HOLE " if p else "held ") + f"{label} {i:4} {title}" + ("" if not p else "\n        -> " + "; ".join(p)) + f"   ({time.time() - t0:.1f}s)", flush=True)
        bad += bool(p)
    return bad


def main():
    prepare(); make_pki()
    ts = ThreadingHTTPServer(("127.0.0.1", TGPORT), Tg)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{B}/pki/tg.pem", f"{B}/pki/tg.key")
    ts.socket = ctx.wrap_socket(ts.socket, server_side=True); threading.Thread(target=ts.serve_forever, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    px = socketserver.ThreadingTCPServer(("127.0.0.1", PROXYPORT), Proxy); px.daemon_threads = True; threading.Thread(target=px.serve_forever, daemon=True).start()
    pn = ThreadingHTTPServer(("127.0.0.1", PANELPORT), Panel); pn.daemon_threads = True; threading.Thread(target=pn.serve_forever, daemon=True).start()
    env = dict(os.environ, https_proxy=f"http://127.0.0.1:{PROXYPORT}", HTTPS_PROXY=f"http://127.0.0.1:{PROXYPORT}", PHP_CLI_SERVER_WORKERS="1")
    env.pop("http_proxy", None); env.pop("HTTP_PROXY", None); env["no_proxy"] = ""; env["NO_PROXY"] = ""
    procs = []
    for name, port in (("before", PORT_BEFORE), ("after", PORT_AFTER)):
        base = ["php", "-d", f"mysqli.default_socket={SOCK}", "-d", "display_errors=0", "-d", "log_errors=1", "-d", f"error_log={T}/{name}.err", "-d", f"curl.cainfo={B}/pki/ca.pem", "-d", f"openssl.cafile={B}/pki/ca.pem",
                "-d", f"auto_prepend_file={T}/{name}/prepend.php", "-S", f"127.0.0.1:{port}", "-t", f"{T}/{name}"]
        procs.append(subprocess.Popen(base, env=env, cwd=f"{T}/{name}", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1.5)
    try:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
        if only:
            SC[:] = [s for s in SC if s[0] in only]
        print("--- the bot as it is (the live files): the new-behaviour checks are expected to fail here")
        b = run_all(PORT_BEFORE, False, "before")
        print("--- the bot with configs_button installed")
        a = run_all(PORT_AFTER, True, "after ")
    finally:
        for p in procs: p.terminate()
        ts.shutdown(); px.shutdown(); pn.shutdown()
    errs = []
    for name in ("before", "after"):
        try:
            errs += [f"{name}: {l.strip()[:200]}" for l in open(f"{T}/{name}.err") if re.search(r"Fatal|Parse error|Uncaught", l)]
        except OSError:
            pass
    if "--show" in sys.argv:
        for k, v in SHOW.items():
            print(f"\n----- {k}\n{v}")
    print(f"\nPHP fatal errors: {len(errs)}" + "".join("\n    " + e for e in errs[:6]))
    print(f"\nafter: {a} HOLE ({len(SC)} scenarios); before: {b} (the old bot is only a control)")
    sys.exit(1 if a else 0)


main()
