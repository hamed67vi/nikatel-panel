#!/usr/bin/env python3
# ruff: noqa      (a throw-away end-to-end harness: broad excepts and open() without a context are on purpose)
"""The support section of the shop bot, run end to end: the bot's OWN bot.php / config.php (a copy of the real files, before and after scripts/install-support-section.sh) under `php -S`
on a PRIVATE MariaDB (sec_setup_db.sh, db nikabot_sec) and a fake Telegram over TLS behind a CONNECT proxy that only forwards api.telegram.org to the fake. Telegram updates are posted to bot.php
and what the bot sends (customer, admins) and what it stores is checked. Nothing leaves the box.

  usage: SEC_SCRATCH=/some/dir bash sec_setup_db.sh start && python3 bot_support.py <dir with scripts/install-support-section.sh and bot/menu/support_section.php> <copy of the bot's files> ; bash sec_setup_db.sh stop
  (ports 49520-49523)
"""
import json, os, re, select, shutil, socket, socketserver, ssl, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

B = os.environ.get("SEC_SCRATCH", "/tmp/miniapp-sec-scratch")
MOD = os.path.abspath(sys.argv[1])
ORIG = os.path.abspath(sys.argv[2])
DB = "nikabot_sec"
SOCK = f"{B}/run/db.sock"
T = f"{B}/supbot"
PORT_BEFORE, PORT_AFTER, TGPORT, PROXYPORT = 49520, 49521, 49522, 49523
TOKEN = "123456:TEST-TOKEN"
ADMIN, ADMIN2, U, OTHER = 900001, 900002, 900100, 900200
TG_LOG, TLOCK = [], threading.Lock()


# ------------------------------------------------------------------ db
def sql(q):
    r = subprocess.run(["mariadb", "--no-defaults", "-S", SOCK, "-uroot", "-N", "-B", DB, "-e", q], capture_output=True, text=True)
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
            TG_LOG.append({"method": method, "chat_id": str(fields.get("chat_id", "")), "text": fields.get("text") or fields.get("caption") or "", "markup": fields.get("reply_markup", ""), "mid": fields.get("message_id", ""), "token_ok": bool(m) and m.group(1) == TOKEN})
        out = {"ok": True, "result": {"message_id": 700 + len(TG_LOG)}}
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


def make_pki():
    d = f"{B}/pki"
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(f"{d}/tg.pem"):
        subprocess.run(f"cd {d} && openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3 -subj /CN=sec-test-ca 2>/dev/null && "
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
    for script in ("install-support-section.sh", "install-agent-discount-notice.sh"):      # (both mods of the bot are put in: they must live together)
        r = subprocess.run(["bash", f"{MOD}/scripts/{script}", f"{T}/after"], capture_output=True, text=True, env=dict(os.environ, BACKUP_DIR=f"{T}/bk"))
        if r.returncode:
            print(r.stdout, r.stderr); sys.exit(2)


def reset(states=None, support_id="@test_support"):
    sql("SET FOREIGN_KEY_CHECKS=0; DELETE FROM users; DELETE FROM chats; DELETE FROM chats_info; DELETE FROM setting; DELETE FROM pays")
    st = {"botState": "on", "sellState": "on", "walletState": "on", "cartToCartState": "on", "supportID": support_id, "searchState": "on", "supportSection": "on"}      # (the section is published here; ST1 tries the stages)
    st.update(states or {})
    st = {k: v for k, v in st.items() if v is not None}      # (a state given as None is left out: "not set")
    sql(f"INSERT INTO setting (type, value) VALUES ('BOT_STATES', {esc(json.dumps(st))}), ('PAYMENT_KEYS', {esc('{}')})")
    for uid, adm in ((ADMIN, 1), (ADMIN2, 1), (U, 0), (OTHER, 0)):
        sql(f"INSERT INTO users (userid, name, username, refcode, wallet, date, step, isAdmin, is_agent) VALUES ('{uid}', 'T{uid}', 't{uid}', '0', 0, '{int(time.time())}', 'none', {adm}, 0)")
    with TLOCK:
        TG_LOG.clear()


def mk_ticket(uid, state=0, msgs=(("USER", "first"),)):
    now = int(time.time())
    sql(f"INSERT INTO chats (user_id, create_date, title, category, state, rate) VALUES ({uid}, {now}, 'title', 'cat', {state}, 0)")
    cid = int(sql("SELECT MAX(id) FROM chats"))
    for i, (typ, text) in enumerate(msgs):
        sql(f"INSERT INTO chats_info (chat_id, sent_date, msg_type, text) VALUES ({cid}, {now + i}, '{typ}', {esc(text)})")
    return cid


def set_step(uid, step):
    sql(f"UPDATE users SET step = {esc(step)} WHERE userid = '{uid}'")


def step_of(uid):
    return sql(f"SELECT step FROM users WHERE userid = '{uid}'")


def infos(cid):
    return [tuple(r.split("\t", 1)) for r in sql(f"SELECT msg_type, text FROM chats_info WHERE chat_id = {cid} ORDER BY id").splitlines() if r]


def botstate():
    return json.loads(sql("SELECT value FROM setting WHERE type = 'BOT_STATES'"))


_mid = [1000]


def user(uid):
    return {"id": uid, "first_name": f"T{uid}", "username": f"t{uid}"}


def msg(uid, text=None, photo=False, caption=None):
    _mid[0] += 1
    m = {"message_id": _mid[0], "from": user(uid), "chat": {"id": uid, "type": "private"}, "date": int(time.time())}
    if text is not None: m["text"] = text
    if photo:
        m["photo"] = [{"file_id": "PHOTO1", "file_unique_id": "u1", "width": 10, "height": 10}]
        if caption: m["caption"] = caption
    return {"update_id": _mid[0], "message": m}


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


def last_markup(chat, since=0):
    ms = [m for m in sent(chat, since=since) if m["markup"]]
    return buttons(ms[-1]["markup"]) if ms else []


def datas(bs): return [b.get("callback_data") for b in bs if "callback_data" in b]
def urls(bs): return [b.get("url") for b in bs if "url" in b]


# ------------------------------------------------------------------ scenarios
SC = []
OLD_MENU = []      # (the customer's main menu as the old bot draws it, filled by the run of the old files)


def scn(i, title):
    def d(f): SC.append((i, title, f)); return f
    return d


NEW = "پیام جدید"                                   # "new message" (the admins' notice starts with it)
REG = "پیام شما با موفقیت ثبت شد"      # "your message was registered"


def admin_notices(since=0):
    return [m for c in (ADMIN, ADMIN2) for m in sent(c, since=since) if m["method"] in ("sendMessage", "sendPhoto") and NEW in m["text"]]


@scn("MM1", "the main menu has a support button (after), not before")
def mm1(port, after):
    reset(); problems = []
    c, t = post(port, cb(U, "mainMenu"))
    d = datas(last_markup(U))
    if ("supportSection" in d) != after:
        problems.append(f"supportSection in the main menu = {'supportSection' in d} (callbacks: {d[:12]}) HTTP {c} {t[:80]}")
    return problems


@scn("SC1", "the support screen: tickets + the operator's chat (supportID), the old rows unchanged")
def sc1(port, after):
    reset(); problems = []
    c, t = post(port, cb(U, "supportSection"))
    bs = last_markup(U)
    d = datas(bs)
    if not {"usersNewTicket", "usersOpenTickets", "userAllTickets", "mainMenu"} <= set(d):
        problems.append(f"the ticket buttons or the way back are missing: {d} HTTP {c} {t[:80]}")
    u = urls(bs)
    if after and u != ["https://t.me/test_support"]:
        problems.append(f"the operator's chat button: {u}, want ['https://t.me/test_support']")
    if not after and u:
        problems.append(f"the old screen has a url button: {u}")
    return problems


@scn("SC2", "supportChat = off: only the ticket is left; a supportID that is not a link: no chat button, the tickets still work")
def sc2(port, after):
    problems = []
    reset(states={"supportChat": "off"})
    post(port, cb(U, "supportSection")); bs = last_markup(U)
    if urls(bs) or "usersNewTicket" not in datas(bs):
        problems.append(f"switched off: urls {urls(bs)}, callbacks {datas(bs)}")
    for bad in ("", "x y z", "@ab", "https://t.me/", "javascript:alert(1)"):
        reset(support_id=bad)
        post(port, cb(U, "supportSection")); bs = last_markup(U)
        if urls(bs) or "usersNewTicket" not in datas(bs):
            problems.append(f"supportID {bad!r}: urls {urls(bs)}, callbacks {datas(bs)}")
    reset(support_id="https://t.me/some_operator")
    post(port, cb(U, "supportSection"))
    if after and urls(last_markup(U)) != ["https://t.me/some_operator"]:
        problems.append(f"a t.me link as supportID: {urls(last_markup(U))}")
    return problems


@scn("AD1", "the admin's ticket menu has the switch; a tap flips BOT_STATES supportChat and the label; a customer cannot flip it")
def ad1(port, after):
    reset(); problems = []
    post(port, cb(ADMIN, "ticketsList")); d = datas(last_markup(ADMIN))
    if not {"openTickets", "newTickets", "allTickets", "ticketsCategory", "managePanel"} <= set(d):
        problems.append(f"the old ticket menu buttons are missing: {d}")
    if after:
        if "toggleSupportChat" not in d or "toggleSupportStage" not in d:
            problems.append(f"no switch in the ticket menu: {d}")
        label = [b["text"] for b in last_markup(ADMIN) if b.get("callback_data") == "toggleSupportChat"]
        if not label or "✅" not in label[0]:
            problems.append(f"the switch should say on (check mark): {label}")
        post(port, cb(ADMIN, "toggleSupportChat"))
        if botstate().get("supportChat") != "off":
            problems.append(f"the tap did not switch it off: {botstate().get('supportChat')}")
        label = [b["text"] for b in last_markup(ADMIN) if b.get("callback_data") == "toggleSupportChat"]
        if not label or "❌" not in label[0]:
            problems.append(f"after the tap the switch should say off (cross): {label}")
        n = len(TG_LOG)
        post(port, cb(U, "supportSection"))
        if urls(last_markup(U)):
            problems.append("the customer still has the chat button while it is switched off")
        post(port, cb(ADMIN, "toggleSupportChat"))
        if botstate().get("supportChat") != "on":
            problems.append(f"the second tap did not switch it on: {botstate().get('supportChat')}")
        post(port, cb(U, "toggleSupportChat"))
        if botstate().get("supportChat") != "on":
            problems.append("a customer's tap changed the switch")
        if sql("SELECT COUNT(*) FROM setting WHERE type='BOT_STATES'") != "1":
            problems.append("BOT_STATES rows != 1")
        if botstate().get("supportID") != "@test_support" or botstate().get("sellState") != "on":
            problems.append("the other settings of BOT_STATES changed")
    else:
        if "toggleSupportChat" in d:
            problems.append("the old menu has the switch")
    return problems


@scn("ST1", "the stage (BOT_STATES supportSection): unset / admin = the section exists for the admins only and a customer gets exactly the old bot; on = everybody; off = nobody; the admin's button cycles admin -> on -> off -> admin")
def st1(port, after):
    problems = []
    for mode, admin_sees, user_sees in ((None, True, False), ("admin", True, False), ("on", True, True), ("off", False, False)):
        reset(states={"supportSection": mode})
        post(port, cb(ADMIN, "mainMenu")); a = "supportSection" in datas(last_markup(ADMIN))
        post(port, cb(U, "mainMenu")); u = "supportSection" in datas(last_markup(U))
        rows = [r for r in (json.loads(([m for m in sent(U) if m["markup"]] or [{"markup": "{}"}])[-1]["markup"]).get("inline_keyboard") or []) if r]      # (the customer's main menu without the empty rows the bot builds anyway)
        if not rows:
            problems.append(f"{mode}: could not read the customer's main menu")
        if not after:
            OLD_MENU.append(rows)
        elif OLD_MENU and not user_sees and rows != OLD_MENU[0]:
            problems.append(f"{mode}: a customer the stage does not cover has a different main menu than the old bot's: {rows} vs {OLD_MENU[0]}")
        if after and (a, u) != (admin_sees, user_sees):
            problems.append(f"{mode}: the main menu has the support button for the admin={a} / the customer={u}, want {admin_sees} / {user_sees}")
        post(port, cb(U, "supportSection")); chat = bool(urls(last_markup(U)))      # (a stale button of a customer: the new screen only where the stage covers him)
        if after and chat != user_sees:
            problems.append(f"{mode}: a customer's tap on supportSection gave the new screen = {chat}, want {user_sees}")
        post(port, cb(ADMIN2, "supportSection")); chat = bool(urls(last_markup(ADMIN2)))
        if after and chat != admin_sees:
            problems.append(f"{mode}: an admin's tap on supportSection gave the new screen = {chat}, want {admin_sees}")
        for uid, covered in ((U, user_sees), (ADMIN2, admin_sees)):      # (an admin writing as a customer is the way to test it)
            cid = mk_ticket(uid); set_step(uid, f"sendMsg_{cid}"); n = len(TG_LOG)
            post(port, msg(uid, "again"))
            told = [m for m in sent(ADMIN, since=n) if m["method"] in ("sendMessage", "sendPhoto") and NEW in m["text"]]
            if after and bool(told) == covered:
                problems.append(f"{mode}: a follow-up of {'the customer' if uid == U else 'an admin'} was {'announced' if told else 'quiet'}, want {'quiet' if covered else 'announced as before'}")
            if len(infos(cid)) != 2:
                problems.append(f"{mode}: the follow-up was not saved ({infos(cid)})")
    if after:
        reset(states={"supportSection": None}); seq = []
        for _ in range(4):
            post(port, cb(ADMIN, "toggleSupportStage")); seq.append(botstate().get("supportSection"))
        if seq != ["on", "off", "admin", "on"]:
            problems.append(f"the admin's button gave {seq}, want ['on', 'off', 'admin', 'on']")
        label = [b["text"] for b in last_markup(ADMIN) if b.get("callback_data") == "toggleSupportStage"]
        if not label or "🌐" not in label[0]:
            problems.append(f"the button should say 'everybody' after the 4th tap: {label}")
        reset(states={"supportSection": "admin"}); post(port, cb(U, "toggleSupportStage"))
        if botstate().get("supportSection") != "admin":
            problems.append("a customer's tap changed the stage")
    return problems


@scn("TK1", "a follow-up (the last message of the ticket is the customer's own): saved, answered 'registered', NOT sent to the admins; before: announced")
def tk1(port, after):
    reset(); problems = []
    cid = mk_ticket(U, state=0)
    set_step(U, f"sendMsg_{cid}")
    n = len(TG_LOG)
    c, t = post(port, msg(U, "hello again"))
    got = infos(cid)
    if got[-1:] != [("USER", "hello again")] and not (len(got) == 2 and got[1][0] == "USER" and "hello again" in got[1][1]):
        problems.append(f"the follow-up was not saved: {got} HTTP {c} {t[:80]}")
    notices = admin_notices(n)
    if after and notices:
        problems.append(f"the admins were told again: {len(notices)} message(s)")
    if not after and len(notices) != 2:
        problems.append(f"(the old bot) the admins should get 2 messages, got {len(notices)}")
    mine = [m for m in sent(U, since=n) if REG in m["text"]]
    if not mine:
        problems.append("the customer got no 'registered' answer")
    if step_of(U) != "none":
        problems.append(f"the step is {step_of(U)!r}, want 'none'")
    if after and sql(f"SELECT state FROM chats WHERE id={cid}") != "0":
        problems.append("the ticket state changed")
    return problems


@scn("TK2", "after an answer of the support the next customer message is announced again, once; the one after that is quiet")
def tk2(port, after):
    reset(); problems = []
    cid = mk_ticket(U, state=1, msgs=(("USER", "first"), ("ADMIN", "answer")))
    set_step(U, f"sendMsg_{cid}")
    n = len(TG_LOG)
    post(port, msg(U, "thanks, one more thing"))
    if len(admin_notices(n)) != 2:
        problems.append(f"a message after an answer must reach both admins once: {len(admin_notices(n))}")
    if len(infos(cid)) != 3:
        problems.append(f"messages stored: {infos(cid)}")
    set_step(U, f"sendMsg_{cid}")
    n = len(TG_LOG)
    post(port, msg(U, "and another"))
    if after and admin_notices(n):
        problems.append("the second message in a row was announced")
    if len(infos(cid)) != 4:
        problems.append(f"messages stored after the second: {infos(cid)}")
    return problems


@scn("TK3", "a photo as a follow-up is saved the way the bot saves it ({file_id, caption}) and is quiet too")
def tk3(port, after):
    reset(); problems = []
    cid = mk_ticket(U)
    set_step(U, f"sendMsg_{cid}")
    n = len(TG_LOG)
    post(port, msg(U, photo=True, caption="see this"))
    got = infos(cid)
    ok = len(got) == 2 and got[1][0] == "USER" and "PHOTO1" in got[1][1] and "see this" in got[1][1]
    if not ok:
        problems.append(f"the photo was not saved as expected: {got}")
    if after and admin_notices(n):
        problems.append("a photo follow-up was announced")
    return problems


@scn("TK4", "somebody else's ticket, a closed ticket, the cancel button and a ticket that does not exist: the old handler decides (announced as before)")
def tk4(port, after):
    problems = []
    reset()
    cid = mk_ticket(OTHER)
    set_step(U, f"sendMsg_{cid}"); n = len(TG_LOG)
    post(port, msg(U, "into someone else's ticket"))
    if len(admin_notices(n)) != 2:
        problems.append(f"another customer's ticket: the old handler should announce it to both admins (got {len(admin_notices(n))})")
    reset()
    cid = mk_ticket(U, state=2)
    set_step(U, f"sendMsg_{cid}"); n = len(TG_LOG)
    post(port, msg(U, "into a closed ticket"))
    if len(admin_notices(n)) != 2:
        problems.append(f"a closed ticket: announced to {len(admin_notices(n))} admins (the old behaviour is 2)")
    reset()
    cid = mk_ticket(U)
    set_step(U, f"sendMsg_{cid}"); n = len(TG_LOG)
    CANCEL = "❌ لغو عملیات"      # (the bot's cancel button as values.php has it: it must not be taken for a message)
    post(port, msg(U, CANCEL))
    if infos(cid) != [("USER", "first")] or admin_notices(n):
        problems.append(f"the cancel button was taken for a message: {infos(cid)}, {len(admin_notices(n))} notice(s)")
    if step_of(U) == f"sendMsg_{cid}":
        problems.append("the cancel button did not leave the step")
    return problems


@scn("TK5", "the first message of a new ticket still reaches the admins (the ticket flow itself is untouched)")
def tk5(port, after):
    reset(); problems = []
    set_step(U, "sendTicket_cat"); sql(f"UPDATE users SET temp='my title' WHERE userid='{U}'")
    n = len(TG_LOG)
    post(port, msg(U, "my first message"))
    if sql(f"SELECT COUNT(*) FROM chats WHERE user_id={U}") != "1" or len(admin_notices(n)) + len([m for c in (ADMIN, ADMIN2) for m in sent(c, since=n) if "my first message" in m["text"]]) == 0:
        problems.append("a new ticket was not created or not announced")
    return problems


@scn("AG1", "the admin raises an agent's discount (general / a server's / a plan's): the agent gets ONE short message; the same or a lower value, a refused value, a switched-off message: nothing; what is saved is the same in the old bot")
def ag1(port, after):
    problems = []
    agent = OTHER

    def setup(states=None):
        reset(states=states)
        sql("DELETE FROM server_info; DELETE FROM server_plans")
        sql("INSERT INTO server_info (id, title, ucount, remark, flag, active, state) VALUES (1, 'Test DE', '100', 'tst', 'DE', 1, 1)")
        sql("INSERT INTO server_plans (id, fileid, catid, server_id, inbound_id, acount, limitip, title, protocol, days, volume, type, price, descr, pic, active, step, date, custom_sni) VALUES (1, '', 1, 1, 0, 10, 1, 'plan', 'vless', 30, 50, 'x', 130000, 'd', '', 1, 0, '', NULL)")
        ten = esc(json.dumps({"normal": "10"}))
        sql(f"UPDATE users SET is_agent = 1, discount_percent = {ten} WHERE userid = '{agent}'")

    def edit(kind, key, value):
        set_step(ADMIN, f"editAgentDiscount{kind}{agent}_{key}")
        n = len(TG_LOG)
        post(port, msg(ADMIN, value))
        return [m for m in sent(agent, since=n) if m["method"] == "sendMessage"]

    def stored():
        return json.loads(sql(f"SELECT discount_percent FROM users WHERE userid = '{agent}'"))
    setup()
    got = edit("Normal", "0", "20")
    if stored().get("normal") != "20":
        problems.append(f"the general discount was not saved: {stored()}")
    if after and (len(got) != 1 or "20" not in got[0]["text"] or len(got[0]["text"]) > 200):
        problems.append(f"raising 10 -> 20: the agent should get one short message with 20, got {[m['text'] for m in got]}")
    if not after and got:
        problems.append("the old bot told the agent")
    for v in ("15", "15", "9"):
        if edit("Normal", "0", v):
            problems.append(f"a lower / equal value ({v}) was announced")
    edit("Normal", "0", "15")
    if edit("Normal", "0", "abc") or stored().get("normal") != "15":
        problems.append(f"a text that is not a number: sent or saved ({stored()})")
    got = edit("Server", "1", "30")
    if stored().get("servers", {}).get("1") != "30" or (after and (len(got) != 1 or "Test DE" not in got[0]["text"] or "30" not in got[0]["text"])):
        problems.append(f"a server discount 30 over the general 15: saved {stored().get('servers')}, messages {[m['text'] for m in got]}")
    if edit("Server", "1", "12"):
        problems.append("a lowered server discount was announced")
    got = edit("Plan", "1", "40")
    if stored().get("plans", {}).get("1") != "40" or (after and (len(got) != 1 or "plan" not in got[0]["text"] or "40" not in got[0]["text"])):
        problems.append(f"a plan discount 40: saved {stored().get('plans')}, messages {[m['text'] for m in got]}")
    setup(states={"agentDiscountMsg": "off"})
    got = edit("Normal", "0", "25")
    if got or stored().get("normal") != "25":
        problems.append(f"agentDiscountMsg = off: sent {len(got)} / saved {stored()}")
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
    if "--nohelper" in sys.argv:      # the helper file is gone after the installer ran (deleted by hand): every new line must fall back to the old code
        os.remove(f"{T}/after/support_section.php")
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
        if "--nohelper" in sys.argv:
            SC[:] = [s for s in SC if s[0] in ("SC1", "AD1", "TK1", "TK2", "TK3", "TK5")]
            print("--- installed, but support_section.php is missing: the bot must behave exactly like the old one")
            a = run_all(PORT_AFTER, False, "nohelper"); b = 0
        else:
            print("--- the bot as it is (the old files): the new-behaviour checks are expected to fail here")
            b = run_all(PORT_BEFORE, False, "before")
            print("--- the bot with the support section installed")
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
    print(f"\nPHP fatal errors: {len(errs)}" + "".join("\n    " + e for e in errs[:6]))
    print(f"\nafter: {a} HOLE ({len(SC)} scenarios); before: {b} (the old bot is only a control)")
    sys.exit(1 if a else 0)


main()
