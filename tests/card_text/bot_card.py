#!/usr/bin/env python3
# ruff: noqa      (a throw-away end-to-end harness: broad excepts and open() without a context are on purpose)
"""The card-to-card screen and the currency button of the shop bot, run end to end: the bot's OWN files (a copy of the real ones, before and after scripts/install-card-text.sh) under `php -S`
on a PRIVATE MariaDB (tests/miniapp_security/sec_setup_db.sh, db nikabot_sec) and a fake Telegram over TLS behind a CONNECT proxy that only forwards api.telegram.org to the fake. Telegram updates are
posted to bot.php and what the bot sends and what it stores is checked. Nothing leaves the box.

  usage: SEC_SCRATCH=/some/dir bash tests/miniapp_security/sec_setup_db.sh start && python3 bot_card.py <repo dir with scripts/install-card-text.sh and bot/menu/card_text.php> <copy of the bot's files> ; bash ... stop
  (ports 49530-49533)
"""
import json, os, re, select, shutil, socket, socketserver, ssl, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

B = os.environ.get("SEC_SCRATCH", "/tmp/card-sec-scratch")
MOD = os.path.abspath(sys.argv[1])
ORIG = os.path.abspath(sys.argv[2])
DB = "nikabot_sec"
SOCK = f"{B}/run/db.sock"
T = f"{B}/cardbot"
PORT_BEFORE, PORT_AFTER, TGPORT, PROXYPORT = 49530, 49531, 49532, 49533
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
    r = subprocess.run(["bash", f"{MOD}/scripts/install-card-text.sh", f"{T}/after"], capture_output=True, text=True, env=dict(os.environ, BACKUP_DIR=f"{T}/bk"))
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


# ------------------------------------------------------------------ scenarios
SC = []
SHOW = {}      # (what the customers see, printed with --show)


def scn(i, title):
    def d(f): SC.append((i, title, f)); return f
    return d


def has_note(t): return all(w in t for w in NOTE_WORDS)


@scn("WK1", "wallet top-up keyboard: card button, the currency button right under it (after), cancel last")
def wk1(port, after):
    reset(); problems = []
    rs, h = topup(port, NEWC)
    flat = [b for r in rs for b in r]
    datas = [b.get("callback_data") for b in flat]
    if f"payIncreaseWalletWithCartToCart{h}" not in datas:
        problems.append(f"no card button: {datas}")
        return problems
    i = datas.index(f"payIncreaseWalletWithCartToCart{h}")
    cur = [b for b in flat if (b.get("callback_data") or "").startswith("cuz_")]
    if after:
        if len(cur) != 1 or cur[0]["callback_data"] != f"cuz_payIncreaseWalletWithCartToCart{h}" or datas.index(cur[0]["callback_data"]) != i + 1:
            problems.append(f"the currency button must be ONE button right under the card button: {datas}")
        elif "پرداخت ارزی" not in cur[0]["text"]:
            problems.append(f"label: {cur[0]['text']!r}")
        if len(rs[i + 1]) != 1:
            problems.append(f"the currency button must have a row of its own: {rs[i + 1]}")
        if datas[-1] != "mainMenu":
            problems.append(f"cancel is not last: {datas}")
    elif cur:
        problems.append("the old bot has a currency button")
    return problems


@scn("CN1", "a NEW customer taps card-to-card: the star line + the support id; no card number, no wallet, no red outband line")
def cn1(port, after):
    reset(); problems = []
    rs, h = topup(port, NEWC)
    post(port, cb(NEWC, f"payIncreaseWalletWithCartToCart{h}"))
    m = last_msg(NEWC); t = m["text"]
    if after:
        SHOW["a NEW customer taps card-to-card"] = t
        SHOW["the top-up keyboard (labels)"] = " | ".join(b["text"] for r in rs for b in r)
        if not has_note(t): problems.append(f"the star line is missing: {t[:160]!r}")
        if "@test_support" not in t: problems.append(f"the support id is missing: {t[:200]!r}")
        for bad in (CARD, HOLDER, WALLET, RED, "PRICE", "ACCOUNT"):
            if bad in t: problems.append(f"{bad!r} must not be shown to a new customer: {t[:200]!r}")
        if t.count("\n") > 4: problems.append(f"only the star line + the support line: {t!r}")
        if m["parse"].lower() != "html": problems.append(f"parse mode {m['parse']!r}")
        d = [b.get("callback_data") for b in buttons(m["markup"])]
        if d != [f"sendIncreaseWalletReceipt{h}", f"cancelIncreaseWalletReceipt{h}"]: problems.append(f"the receipt / cancel buttons: {d}")
        if step_of(NEWC) != f"payIncreaseWalletWithCartToCart{h}": problems.append(f"step {step_of(NEWC)!r}")
    return problems


@scn("CO1", "an OLD customer taps card-to-card: the card message (amount, card number, holder) as always - no 'message support' line, no support id line")
def co1(port, after):
    reset(); problems = []
    rs, h = topup(port, OLDC)
    post(port, cb(OLDC, f"payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(OLDC)["text"]
    if after:
        SHOW["an OLD customer taps card-to-card"] = t
        if CARD not in t or HOLDER not in t: problems.append(f"the card number / holder must be shown: {t[:300]!r}")
        if has_note(t) or NOTE_TAIL in t or "معرفی شده" in t: problems.append(f"an old customer needs no 'message support' line: {t[:120]!r}")
        if "@test_support" in t or "🎧" in t: problems.append("an old customer needs no support id line")
        if RED in t: problems.append("red outband line")
    else:
        if CARD not in t: problems.append(f"(control) the old bot should show the card to an old customer: {t[:120]!r}")
    return problems


@scn("CA1", "an agent is an old customer (the bot's own rule): the card message")
def ca1(port, after):
    reset(); problems = []
    rs, h = topup(port, AGENT)
    post(port, cb(AGENT, f"payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(AGENT)["text"]
    if after and (has_note(t) or CARD not in t): problems.append(f"agent: {t[:200]!r}")
    return problems


@scn("CC1", "a NEW customer taps the currency button: the tether message (USDT amount, wallet, network, receipt), no star line, no card, no red line; same step and receipt buttons as the card flow")
def cc1(port, after):
    reset(); problems = []
    rs, h = topup(port, NEWC)
    c, tx = post(port, cb(NEWC, f"cuz_payIncreaseWalletWithCartToCart{h}"))
    m = last_msg(NEWC); t = m["text"]
    if after:
        SHOW["a customer taps the currency payment"] = t
        if WALLET not in t: problems.append(f"the wallet is missing: {t[:200]!r}")
        mt = re.search(r"<code>([0-9.]+) USDT</code>", t)
        if not mt: problems.append(f"no USDT amount: {t[:200]!r}")
        else:
            usdt = float(mt.group(1))
            if not 3.0 < usdt < 4.2: problems.append(f"the USDT amount for 200,000 toman at 60,000 should be about 3.3-3.4 (a few hundred toman more): {usdt}")
        if "BEP20" not in t or "هش تراکنش" not in t: problems.append("network / hash line missing")
        for bad in (CARD, HOLDER, RED, "مشتری قدیمی", NOTE_TAIL, "PRICE", "USDT-WALLET"):
            if bad in t: problems.append(f"{bad!r} must not be in the tether message")
        d = [b.get("callback_data") for b in buttons(m["markup"])]
        if d != [f"sendIncreaseWalletReceipt{h}", f"cancelIncreaseWalletReceipt{h}"]: problems.append(f"the receipt / cancel buttons: {d}")
        if step_of(NEWC) != f"payIncreaseWalletWithCartToCart{h}": problems.append(f"step {step_of(NEWC)!r} (must be the card flow's own)")
        # the receipt button of this very message goes on as always
        n0 = len(TG_LOG)
        post(port, cb(NEWC, f"sendIncreaseWalletReceipt{h}"))
        if step_of(NEWC) != f"waitIncreaseWalletReceipt{h}": problems.append(f"after 'send receipt' the step is {step_of(NEWC)!r}")
    return problems


@scn("CC2", "an OLD customer taps the currency button: the same tether message (no card number)")
def cc2(port, after):
    reset(); problems = []
    rs, h = topup(port, OLDC)
    post(port, cb(OLDC, f"cuz_payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(OLDC)["text"]
    if after and (WALLET not in t or CARD in t or has_note(t)): problems.append(f"old customer, currency: {t[:300]!r}")
    return problems


@scn("CC3", "a card tap after a currency tap (same customer) shows the card screen again: nothing sticks between requests")
def cc3(port, after):
    reset(); problems = []
    rs, h = topup(port, OLDC)
    post(port, cb(OLDC, f"cuz_payIncreaseWalletWithCartToCart{h}"))
    post(port, cb(OLDC, f"payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(OLDC)["text"]
    if after and (CARD not in t or has_note(t) or "PRICE" in t or "برای شارژ یا خرید" in t): problems.append(f"{t[:200]!r}")      # (the old card message holds the wallet line too: what must NOT come back is the currency text)
    return problems


@scn("RN1", "renew: the prePayCheck list gets the currency button; a tap on it opens the tether message and the renew card flow's step")
def rn1(port, after):
    reset(); problems = []
    now = int(time.time())
    sql(f"INSERT INTO orders_list (id, userid, token, transid, fileid, server_id, inbound_id, remark, uuid, protocol, expire_date, link, amount, status, date) VALUES (77, '{NEWC}', 't', 'x', 5, 1, 0, 'r', 'u', 'vless', {now + 86400}, 'l', 100000, 1, '{now}')")
    sql(f"INSERT INTO pays (hash_id, user_id, type, plan_id, server_id, price, request_date, state, order_id) VALUES ('hRen1', {NEWC}, 'RENEW_ACCOUNT', 5, 1, 150000, {now}, 'pending', 77)")
    post(port, cb(NEWC, "prePayCheck_Renew_CartToCart_hRen1"))
    t = last_msg(NEWC)["text"]
    if after and (not has_note(t) or "@test_support" not in t): problems.append(f"renew, card: {t[:200]!r}")
    if step_of(NEWC) != "payRenewWithCartToCarthRen1": problems.append(f"renew card step {step_of(NEWC)!r}")
    post(port, cb(NEWC, "cuz_prePayCheck_Renew_CartToCart_hRen1"))
    t = last_msg(NEWC)["text"]
    if after and (WALLET not in t or has_note(t)): problems.append(f"renew, currency: {t[:200]!r}")
    if after and step_of(NEWC) != "payRenewWithCartToCarthRen1": problems.append(f"renew currency step {step_of(NEWC)!r}")
    return problems


@scn("SW1", "ctcText = off: the old card texts come back (the control's text), the currency button stays; ctcCurrency = off: no button, a stale tap shows the card screen")
def sw1(port, after):
    problems = []
    reset(states={"ctcText": "off"})
    rs, h = topup(port, NEWC)
    post(port, cb(NEWC, f"payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(NEWC)["text"]
    if after and (has_note(t) is False and "USDT" not in t): problems.append(f"ctcText off: neither the old texts nor ours? {t[:160]!r}")
    if after and "@test_support" in t: problems.append(f"ctcText off must not show the support line: {t[:160]!r}")
    reset(states={"ctcCurrency": "off"})
    rs, h = topup(port, NEWC)
    if any((b.get("callback_data") or "").startswith("cuz_") for r in rs for b in r): problems.append("ctcCurrency off: the button is still there")
    post(port, cb(NEWC, f"cuz_payIncreaseWalletWithCartToCart{h}"))
    t = last_msg(NEWC)["text"]
    if after and (not has_note(t) or WALLET in t or "برای شارژ یا خرید" in t): problems.append(f"ctcCurrency off, stale tap: {t[:200]!r}")
    return problems


@scn("SW2", "no tether wallet in the payment keys: no currency button")
def sw2(port, after):
    reset(keys=False); problems = []
    rs, h = topup(port, NEWC)
    if any((b.get("callback_data") or "").startswith("cuz_") for r in rs for b in r): problems.append("a button without a wallet")
    return problems


@scn("SM1", "the support id as the bot has it: @name, a bare name, a t.me link; none = the star line alone")
def sm1(port, after):
    problems = []
    if not after:
        return problems
    for sid, want in (("@test_support", "@test_support"), ("test_support", "@test_support"), ("https://t.me/test_support", "@test_support"), ("", None), ("<b>x&y", None)):
        reset(support_id=sid)
        rs, h = topup(port, NEWC)
        post(port, cb(NEWC, f"payIncreaseWalletWithCartToCart{h}"))
        t = last_msg(NEWC)["text"]
        if not has_note(t): problems.append(f"{sid!r}: the star line is missing")
        if want and want not in t: problems.append(f"{sid!r}: want {want} in {t[-80:]!r}")
        if want is None and ("🎧" in t and sid == ""): problems.append(f"{sid!r}: a support line without an id")
        if sid.startswith("<b>") and "<b>x" in t: problems.append("a support id with markup must be escaped")
    return problems


@scn("MN1", "the other buttons of the top-up screen and the other payment screens are untouched (wallet button in the buy keyboard, receipt flow, main menu)")
def mn1(port, after):
    reset(); problems = []
    post(port, cb(NEWC, "mainMenu"))
    t = ([m for m in sent(NEWC) if m["markup"]] or [{"markup": ""}])[-1]
    if not t["markup"]: problems.append("the main menu has no keyboard")
    if any((b.get("callback_data") or "").startswith("cuz_") for b in buttons(t["markup"])): problems.append("a currency button in the main menu")
    n0 = len(TG_LOG)
    post(port, cb(NEWC, "increaseMyWallet"))
    m = ([m for m in sent(NEWC, since=n0) if m["markup"]] or [{"markup": ""}])[-1]
    d = [b.get("callback_data") for b in buttons(m["markup"])]
    if "increaseWallet_200000" not in d or any((x or "").startswith("cuz_") for x in d): problems.append(f"the amount list: {d}")
    return problems


@scn("RD1", "the red outband line is gone from settings/values.php (after); the control still has it")
def rd1(port, after):
    name = "after" if after else "before"
    txt = open(f"{T}/{name}/settings/values.php", encoding="utf-8").read()
    n = txt.count(RED)
    if after and n: return [f"{n} line(s) with the red text are still in values.php"]
    if not after and not n: return ["(control) the old values.php should have the red line"]
    return []


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
        print("--- the bot with card_text installed")
        a = run_all(PORT_AFTER, True, "after ")
    finally:
        for p in procs: p.terminate()
        ts.shutdown(); px.shutdown()
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
