#!/usr/bin/env python3
# ruff: noqa      (a throw-away end-to-end harness: broad excepts and open() without a context are on purpose)
"""The Mini App's wallet top-up (card to card + the currency payment), run end to end: the Mini App's OWN api.php (the live one = 'before', the new one = 'after') under `php -S` in a copy of the bot's
tree on a PRIVATE MariaDB (tests/miniapp_security/sec_setup_db.sh, db nikabot_sec). Nothing leaves the box.

  usage: SEC_SCRATCH=/some/dir bash tests/miniapp_security/sec_setup_db.sh start && python3 miniapp_card.py <dir with api.php app.js index.html (the new Mini App)> <copy of the bot's files> ; bash ... stop
  (ports 49540-49541)
"""
import hashlib, hmac, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

B = os.environ.get("SEC_SCRATCH", "/tmp/card-sec-scratch")
NEW_APP = os.path.abspath(sys.argv[1])
ORIG = os.path.abspath(sys.argv[2])
DB = "nikabot_sec"
SOCK = f"{B}/run/db.sock"
T = f"{B}/cardmini"
PORT_BEFORE, PORT_AFTER = 49540, 49541
TOKEN = "123456:TEST-TOKEN"
ADMIN = 900001
NEWC, OLDC, AGENT = 900100, 900300, 900400
CARD, HOLDER, WALLET = "6037991234567890", "Test Holder", "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
NOTE_WORDS = ("⭐️ در صورتی که", "مشتری قدیمی یا معرفی شده", "شماره کارت", "به پشتیبانی پیام ارسال بفرمایید")


def sql(q):
    r = subprocess.run(["mariadb", "--no-defaults", "--default-character-set=utf8mb4", "-S", SOCK, "-uroot", "-N", "-B", DB, "-e", q], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300] + " :: " + q[:120])
    return r.stdout.strip()


def esc(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def init_data(uid):
    user = json.dumps({"id": uid, "first_name": "Ali", "username": f"u{uid}"}, separators=(",", ":"), ensure_ascii=False)
    d = {"auth_date": str(int(time.time())), "query_id": "AAHtest", "user": user}
    chk = "\n".join(f"{k}={d[k]}" for k in sorted(d))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(secret, chk.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(d)


def call(port, a, body, uid):
    b = dict(body or {}); b["initData"] = init_data(uid)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api.php?a={a}", data=json.dumps(b, ensure_ascii=False).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"transport {e}"}
    try:
        return json.loads(txt)
    except Exception:
        return {"ok": False, "error": "not json: " + txt[:100]}


def prepare():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(T)
    for name in ("before", "after"):
        shutil.copytree(ORIG, f"{T}/{name}")
        open(f"{T}/{name}/baseInfo.php", "w").write(f"""<?php
$botToken = '{TOKEN}'; $admin = {ADMIN}; $admins_list = [{ADMIN}]; $botUsername = 'test_shop_bot'; $botUrl = 'https://bot.example.invalid/';
$dbUserName = 'mini'; $dbPassword = 'mini-test-pw'; $dbName = '{DB}'; $USDTPrice = 260000; $USDTPriceagent = 260000;
""")
    for f in ("api.php", "app.js", "index.html"):
        shutil.copy(f"{NEW_APP}/{f}", f"{T}/after/app/{f}")
    # the bot-side texts of the live bot (values.php) are used as they are; the card_text of the bot is NOT installed here (the Mini App has its own copy of the words)


def reset(states=None, support_id="@test_support", keys=True):
    sql("SET FOREIGN_KEY_CHECKS=0; DELETE FROM users; DELETE FROM setting; DELETE FROM pays")
    st = {"botState": "on", "sellState": "on", "walletState": "on", "cartToCartState": "on", "supportID": support_id}
    st.update(states or {})
    pk = {"bankAccount": CARD, "holderName": HOLDER, "tronwallet": WALLET} if keys else {"bankAccount": CARD, "holderName": HOLDER}
    sql(f"INSERT INTO setting (type, value) VALUES ('BOT_STATES', {esc(json.dumps(st))}), ('PAYMENT_KEYS', {esc(json.dumps(pk))})")
    now = int(time.time())
    old_join = int(time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, 0)))
    old_buy = int(time.mktime((2026, 2, 1, 12, 0, 0, 0, 0, 0)))
    for uid, agent, joined in ((NEWC, 0, now - 86400), (OLDC, 0, old_join), (AGENT, 1, now - 86400)):
        sql(f"INSERT INTO users (userid, name, username, refcode, wallet, date, step, isAdmin, is_agent) VALUES ('{uid}', 'T{uid}', 'u{uid}', '0', 0, '{joined}', 'none', 0, {agent})")
    sql(f"INSERT INTO pays (hash_id, user_id, type, plan_id, server_id, price, request_date, state) VALUES ('oldbuy1', {OLDC}, 'BUY_SUB', 1, 1, 150000, {old_buy}, 'approved')")


def has_note(t): return all(w in t for w in NOTE_WORDS)


SC = []
SHOW = {}


def scn(i, title):
    def d(f): SC.append((i, title, f)); return f
    return d


@scn("MA1", "a NEW customer, card: the star line + the support id (and a support button), no amount rows, no card, no wallet")
def ma1(port, after):
    reset(); problems = []
    r = call(port, "topup", {"amount": 200000}, NEWC)
    if not r.get("ok"): return [f"topup refused: {r}"]
    if after:
        SHOW["Mini App, a NEW customer, card-to-card"] = r["text"]
        t = r["text"]
        if r.get("v") != 2 or r.get("kind") != "gift": problems.append(f"v/kind: {r.get('v')} {r.get('kind')}")
        if not has_note(t) or "@test_support" not in t: problems.append(f"text: {t[:200]!r}")
        if r.get("support") != "test_support": problems.append(f"support: {r.get('support')!r}")
        for bad in (CARD, HOLDER, WALLET, "اوتباند"):
            if bad in t: problems.append(f"{bad!r} in the text")
        if r.get("card") or r.get("holder") or r.get("usdt_wallet") or r.get("usdt"): problems.append(f"card / wallet fields must be empty: {({k: r.get(k) for k in ('card', 'holder', 'usdt_wallet', 'usdt')})}")
        if sql(f"SELECT COUNT(*) FROM pays WHERE user_id = {NEWC} AND type = 'INCREASE_WALLET' AND state = 'pending' AND hash_id = {esc(r['hash'])}") != "1": problems.append("the pending top-up row is missing")
    return problems


@scn("MA2", "an OLD customer, card: the card message (card number, holder, the tether equivalent is not 0.00), no star line")
def ma2(port, after):
    reset(); problems = []
    r = call(port, "topup", {"amount": 200000}, OLDC)
    if not r.get("ok"): return [f"topup refused: {r}"]
    t = r["text"]
    if after:
        SHOW["Mini App, an OLD customer, card-to-card"] = t
        if r.get("kind") != "detail" or r.get("card") != CARD or r.get("holder") != HOLDER: problems.append(f"fields: {r.get('kind')} {r.get('card')} {r.get('holder')}")
        if CARD not in t or has_note(t) or "معرفی شده" in t: problems.append(f"text: {t[:200]!r}")
        if "0.00 USDT" in t or "USDT" not in t: problems.append("the tether equivalent line is missing / 0.00")
        if r.get("usdt_wallet"): problems.append("the card page must not carry the currency wallet field")
    return problems


@scn("MA3", "an agent is trusted (the bot's rule): the card message")
def ma3(port, after):
    reset()
    r = call(port, "topup", {"amount": 200000}, AGENT)
    return [] if not after or (r.get("ok") and r.get("kind") == "detail" and CARD in r["text"]) else [f"agent: {r}"]


@scn("MC1", "currency payment (new customer): the tether amount, the wallet, the network - no card, no star line")
def mc1(port, after):
    reset(); problems = []
    r = call(port, "topup", {"amount": 200000, "method": "currency"}, NEWC)
    if not after:
        return []      # (the old api ignores 'method': nothing to check)
    if not r.get("ok"): return [f"currency refused: {r}"]
    t = r["text"]
    SHOW["Mini App, the currency payment"] = t
    if r.get("kind") != "currency" or r.get("usdt_wallet") != WALLET: problems.append(f"fields: {r.get('kind')} {r.get('usdt_wallet')}")
    if not (0.7 < float(r.get("usdt") or 0) < 0.9): problems.append(f"usdt for 200,000 toman at 260,000: {r.get('usdt')}")
    if WALLET not in t or "BEP20" not in t or str(r["usdt"]) not in t: problems.append(f"text: {t[:240]!r}")
    for bad in (CARD, HOLDER, "اوتباند", "مشتری قدیمی", "PRICE", "USDT-WALLET", "ارسال رسید را بزنید"):
        if bad in t: problems.append(f"{bad!r} in the currency text")
    if r.get("card") or r.get("support"): problems.append("card / support fields must be empty")
    if sql(f"SELECT COUNT(*) FROM pays WHERE user_id = {NEWC} AND type = 'INCREASE_WALLET' AND state = 'pending' AND price = 200000 AND hash_id = {esc(r['hash'])}") != "1": problems.append("the pending top-up row is missing")
    return problems


@scn("MC2", "currency payment for an OLD customer: the same tether page (no card number)")
def mc2(port, after):
    reset()
    r = call(port, "topup", {"amount": 200000, "method": "currency"}, OLDC)
    if not after: return []
    return [] if r.get("ok") and r.get("kind") == "currency" and CARD not in r["text"] and WALLET in r["text"] else [f"{r}"]


@scn("MC3", "the currency button's switches: no wallet in the keys / ctcCurrency = off -> 'me' says currency false and topup(currency) is refused with no pending row")
def mc3(port, after):
    if not after: return []
    problems = []
    for label, kw in (("no wallet", {"keys": False}), ("ctcCurrency off", {"states": {"ctcCurrency": "off"}}), ("card payments off", {"states": {"cartToCartState": "off"}})):
        reset(**kw)
        me = call(port, "me", {}, NEWC)
        if (me.get("features") or {}).get("currency") is not False: problems.append(f"{label}: me.features.currency = {(me.get('features') or {}).get('currency')}")
        r = call(port, "topup", {"amount": 200000, "method": "currency"}, NEWC)
        if r.get("ok"): problems.append(f"{label}: topup(currency) was accepted")
        if sql(f"SELECT COUNT(*) FROM pays WHERE user_id = {NEWC} AND type = 'INCREASE_WALLET'") != "0": problems.append(f"{label}: a pending row was made")
    reset()
    me = call(port, "me", {}, NEWC)
    if (me.get("features") or {}).get("currency") is not True: problems.append(f"normal: me.features.currency = {(me.get('features') or {}).get('currency')}")
    return problems


@scn("MS1", "the support id forms and 'none'")
def ms1(port, after):
    if not after: return []
    problems = []
    for sid, want in (("@test_support", "test_support"), ("test_support", "test_support"), ("https://t.me/test_support", "test_support"), ("", "")):
        reset(support_id=sid)
        r = call(port, "topup", {"amount": 200000}, NEWC)
        if r.get("support") != want: problems.append(f"{sid!r}: support={r.get('support')!r}")
        if not has_note(r.get("text", "")): problems.append(f"{sid!r}: the star line is missing")
        if want == "" and "🎧" in r.get("text", ""): problems.append("a support line without an id")
    return problems      # (the Mini App tops up at most 10,000,000 toman, so the bot's 12,000,000 rule can not be reached from here)


@scn("MS2", "the cancel / history calls still work on a currency top-up")
def ms2(port, after):
    if not after: return []
    reset()
    r = call(port, "topup", {"amount": 150000, "method": "currency"}, NEWC)
    c = call(port, "cancel_topup", {"hash": r.get("hash", "")}, NEWC)
    n = sql(f"SELECT COUNT(*) FROM pays WHERE user_id = {NEWC} AND type = 'INCREASE_WALLET' AND state = 'pending'")
    return [] if c.get("ok") and n == "0" else [f"cancel: {c} rows left {n}"]


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
    prepare()
    procs = []
    for name, port in (("before", PORT_BEFORE), ("after", PORT_AFTER)):
        base = ["php", "-d", f"mysqli.default_socket={SOCK}", "-d", "display_errors=0", "-d", "log_errors=1", "-d", f"error_log={T}/{name}.err", "-S", f"127.0.0.1:{port}", "-t", f"{T}/{name}/app"]
        procs.append(subprocess.Popen(base, cwd=f"{T}/{name}/app", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, PHP_CLI_SERVER_WORKERS="2")))
    time.sleep(1.5)
    try:
        print("--- the Mini App as it is (the live one): only a control")
        b = run_all(PORT_BEFORE, False, "before")
        print("--- the new Mini App")
        a = run_all(PORT_AFTER, True, "after ")
    finally:
        for p in procs: p.terminate()
    if "--show" in sys.argv:
        for k, v in SHOW.items(): print(f"\n----- {k}\n{v}")
    errs = []
    for name in ("before", "after"):
        for logf in (f"{T}/{name}.err", f"{T}/{name}/settings/miniapp_error.log"):
            try:
                errs += [f"{name}: {l.strip()[:200]}" for l in open(logf) if re.search(r"Fatal|Parse error|Uncaught|Warning|Notice|Deprecated", l)]
            except OSError:
                pass
    print(f"\nPHP errors/warnings: {len(errs)}" + "".join("\n    " + e for e in errs[:8]))
    print(f"\nafter: {a} HOLE ({len(SC)} scenarios); before: {b} (control)")
    sys.exit(1 if a else 0)


main()
