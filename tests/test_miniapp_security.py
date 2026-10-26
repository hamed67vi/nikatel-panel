"""The Mini App of the shop bot, audited for security and for its payments (the owner, 2026-10-23: "test the Telegram Mini App from zero to 100 and check it has no security problem. If you
see any inconsistency, especially in its payment system, fix it").

The attack harness (tests/miniapp_security/sec.py: api.php under php -S on a private MariaDB with the bot's schema, a fake Marzban, a fake Telegram over TLS) found these and ran again
clean after the fixes; it needs MariaDB, php and openssl, so the full run is opt-in (NIKATEL_MINIAPP_SEC=1) and the tests below keep every fix from being undone without them:
the PHP functions are run on their own, the page is drawn with a hostile support name (node), and the order of the steps inside the payment code is checked in the source."""
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "bot" / "miniapp"
HARNESS = Path(__file__).with_name("miniapp_security")
NODE, PHP = shutil.which("node"), shutil.which("php")
DRIVER = Path(__file__).with_name("miniapp_driver.js")
SRC = (MINI / "api.php").read_text(encoding="utf-8")
JS = (MINI / "app.js").read_text(encoding="utf-8")


def _case(name: str) -> str:
    """the body of one `case 'name': {` of api.php (addvolume and adddays share one body)"""
    start = SRC.index(f"case '{name}':")
    if name == "addvolume":
        return SRC[start:SRC.index("case 'topup':", start)]
    nxt = SRC.find("\ncase '", start + 10)
    return SRC[start:nxt if nxt > 0 else len(SRC)]


def _fn(name: str) -> str:
    start = SRC.index(f"function {name}(")
    end = SRC.find("\n}\n", start)
    one_line = SRC.find("\n", start)
    return SRC[start:one_line] if SRC[start:one_line].rstrip().endswith("}") else SRC[start:end + 3]


def _php(tmp_path, body: str, *args: str) -> str:
    script = tmp_path / "t.php"
    script.write_text("<?php\n" + body, encoding="utf-8")
    return subprocess.run([PHP, str(script), *args], capture_output=True, text=True, check=True).stdout


def _init(uid: int, token: str, auth_date: int | None = None) -> str:
    user = json.dumps({"id": uid, "first_name": "A"}, separators=(",", ":"))
    d = {"auth_date": str(auth_date or int(time.time())), "query_id": "q", "user": user}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(secret, "\n".join(f"{k}={d[k]}" for k in sorted(d)).encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(d)


# ---------------------------------------------------------------------------------------------- authentication
@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_an_empty_bot_token_verifies_nothing(tmp_path):
    """HMAC("WebAppData", token) is the signing key: with an empty token (a broken baseInfo.php) the key is public and anyone could sign an initData for any user id - the harness did it (AU2)"""
    out = _php(tmp_path, _fn("tgVerify") + textwrap.dedent(r"""
        $r = [];
        foreach ([[$argv[1], ''], [$argv[1], 'x:y'], [$argv[2], ''], [$argv[2], 'real:token']] as [$i, $t]) { $e = false; $u = tgVerify($i, $t, $e); $r[] = $u['id'] ?? null; }
        echo json_encode($r);
        """), _init(7, ""), _init(7, "real:token"))
    assert json.loads(out) == [None, None, None, 7]


def test_the_token_is_checked_before_any_other_work():
    assert "if ($initData === '' || $token === '') return null;" in SRC
    assert "hash_equals(hash_hmac('sha256', $check, $secret), $hash)" in SRC and "86400" in SRC      # (constant-time compare, one day of validity: unchanged)


# ---------------------------------------------------------------------------------------------- price and keys
@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_an_agent_percentage_never_makes_the_price_negative_or_higher_than_the_list_price(tmp_path):
    """discount_percent {"normal":150} (or a per-server 250) made the price negative: the purchase paid the customer (PY6)"""
    out = _php(tmp_path, _fn("priceFor") + textwrap.dedent(r"""
        $botState = ['agencyPlanDiscount' => 'off'];
        $plan = ['id' => 1, 'server_id' => 1, 'price' => 130000];
        $r = [];
        foreach (['{"normal":150}', '{"normal":100}', '{"normal":-50}', '{"normal":"100%"}', '{"normal":30,"servers":{"1":250}}', 'not json', '{"normal":99999999999}', '{"normal":10}', ''] as $d) {
            $r[] = priceFor(['is_agent' => 1, 'discount_percent' => $d], $plan)[0];
        }
        $r[] = priceFor(['is_agent' => 0], ['id' => 1, 'server_id' => 1, 'price' => -5])[0];
        echo json_encode($r);
        """))
    assert json.loads(out) == [0, 0, 130000, 0, 0, 130000, 0, 117000, 130000, 0]


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_the_key_of_a_payment_is_a_short_plain_string_or_nothing(tmp_path):
    out = _php(tmp_path, _fn("ridOf") + textwrap.dedent(r"""
        echo json_encode([ridOf(['rid' => 'abcdefgh12']), ridOf(['rid' => 'short']), ridOf(['rid' => 'has space in it']), ridOf(['rid' => str_repeat('x', 41)]), ridOf(['rid' => 12345678]), ridOf(['rid' => ['abcdefghij']]), ridOf([])]);
        """))
    assert json.loads(out) == ["mini:abcdefgh12", "", "", "", "", "", ""]


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_what_puts_a_panel_account_back_is_its_limit_its_expiry_and_a_settable_status(tmp_path):
    out = _php(tmp_path, _fn("panelBack") + textwrap.dedent(r"""
        echo json_encode([panelBack(['limit' => 5, 'expire' => 9, 'status' => 'active']), panelBack(['limit' => 0, 'expire' => 0, 'status' => 'expired']), panelBack(['limit' => 1, 'expire' => 2, 'status' => 'on_hold'])]);
        """))
    assert json.loads(out) == [{"data_limit": 5, "expire": 9, "status": "active"}, {"data_limit": 0, "expire": 0}, {"data_limit": 1, "expire": 2, "status": "on_hold"}]


# ---------------------------------------------------------------------------------------------- the order of the steps inside the payments
def test_a_purchase_takes_its_slot_in_one_statement_before_the_panel_is_called():
    """6 customers buying the last slot: the old "check, call the panel, then ucount - 1" let them all through and InnoDB deadlocked five AFTER their accounts existed (PY9)"""
    buy = _case("buy")
    assert buy.index("takeSlot(") < buy.index("payWithWallet(") < buy.index("giveSlotBack(")
    assert "ucount - 1" not in buy and "acount - 1" not in buy
    take = _fn("takeSlot")
    assert "CAST(ucount AS SIGNED) > 0" in take and "acount > 0" in take


def test_an_account_made_for_an_order_that_could_not_be_saved_is_taken_back_and_a_panel_change_is_put_back():
    """PY8 / PY21: the order rows failed after the panel made the account (or changed it) - an orphan account, a free extension"""
    pay, buy = _fn("payWithWallet"), _case("buy")
    assert "is_callable($res['undo'] ?? null)" in pay and pay.index("$c->rollback()") < pay.index("($res['undo'])()")
    assert "panelDeleteUser($srv, $remark)" in buy and "catch (Throwable $e) { $undo(); throw $e; }" in buy
    assert ">= 500)) panelDeleteUser($srv, $remark)" in buy      # (a time-out or a 5xx may have made the account before the answer was lost)
    for name in ("renew", "addvolume"):
        c = _case(name)
        assert "panelBack($live)" in c
        assert c.index("INSERT INTO increase_order") < c.index("panelModifyUser(")      # the rows first (they can fail), the panel last


def test_a_renewal_is_written_like_the_bots_renewal_and_extra_volume_and_days_like_the_bots_extras():
    """bot.php: RENEW_ACCOUNT with the order id, volume/day 0 and one increase_order row (config.php counts "renewals today" from that table); INCREASE_VOLUME_<order>_<id> / INCREASE_DAY_<order>_<id> (PY18)"""
    renew, extra, hist = _case("renew"), _case("addvolume"), _case("history")
    assert "'type' => 'RENEW_ACCOUNT'" in renew and "'order_id' => (int) $o['id']" in renew and "INSERT INTO increase_order (userid, server_id, inbound_id, remark, amount, date)" in renew
    assert "'INCREASE_VOLUME_' : 'INCREASE_DAY_'" in extra and "'order_id' => (int) $o['id']" in extra and "'id' => (int) $o['fileid']" in extra      # (the plan percentage is looked up by the plan of the order, like the bot)
    assert "'RENEW_ACCOUNT' => 'تمدید'" in hist and "$kindOf" in hist
    assert 'p.kind === "BUY_SUB" || p.kind === "RENEW_ACCOUNT"' in JS


def test_extra_volume_is_not_sold_for_a_service_that_has_no_limit():
    """setting data_limit 0 keeps the account unlimited: the customer paid for nothing (PY17)"""
    c = _case("addvolume")
    assert "if ($isVol && $live['limit'] <= 0) return ['ok' => false" in c and "$live['limit'] + $n * GB" in c


def test_a_plan_that_costs_nothing_is_the_bots_one_per_customer_test_account():
    """bot.php getTestAccount / freeTrial: price 0 plans, users.freetrial = 'used', admins and agents on a 100 % discount excepted; the Mini App sold it again and again (PY20)"""
    buy = _case("buy")
    assert "$isTrial = (int) $plan['price'] <= 0;" in buy and "($me['freetrial'] ?? '') === 'used'" in buy
    assert "SELECT freetrial FROM users WHERE userid = ? FOR UPDATE" in buy and "UPDATE users SET freetrial = 'used'" in buy
    assert "$normalDiscount === '100'" in buy


def test_one_payment_key_is_one_payment():
    """a lost answer (a slow panel, a dropped connection) followed by a tap on "try again" paid twice (PY22); the key is read under the row lock, so two requests at once cannot both pass"""
    pay = _fn("payWithWallet")
    assert pay.index("FOR UPDATE") < pay.index("payid = ?") < pay.index("INSERT INTO pays") and "payid) VALUES" in pay and "'duplicate' => true" in pay
    for name in ("buy", "renew", "addvolume"):
        assert "ridOf($body)" in _case(name)
    assert "buyAgain(" in _case("buy") and "!empty($r['duplicate'])" in _case("renew") and "!empty($r['duplicate'])" in _case("addvolume")
    assert 'api("buy", {plan_id: id, rid: ridFor("buy:" + id), code: dc ? dc.code : ""})' in JS and 'api("renew", {id, rid: ridFor("renew:" + id)})' in JS and "rid: ridFor(rk)" in JS
    assert "net: true" in JS and "if (!r.net) delete RID" in JS


def test_one_receipt_at_a_time_for_one_top_up_and_one_top_up_request_at_a_time_for_one_customer():
    """five requests at once each sent their own photo with their own Approve button to every admin (PY10); eight top-up requests at once left two pending rows (PY12)"""
    rec, top = _case("receipt"), _case("topup")
    assert rec.index("GET_LOCK(") < rec.index("tg('sendPhoto'") and "'miniapp_receipt_' . md5($hash)" in rec
    assert top.index("begin_transaction()") < top.index("FOR UPDATE") < top.index("DELETE FROM pays") < top.index("INSERT INTO pays") < top.index("db()->commit()")


def test_gift_codes_have_a_brake_and_only_pay_a_positive_amount():
    gift = _case("gift")
    assert "throttled($uid, 'gift', 10, 900)" in gift and gift.index("throttled(") < gift.index("begin_transaction")
    assert "(int) $g['amount'] <= 0" in gift and "throttleHit($uid, 'gift')" in gift
    assert "throttled($uid, 'ticket', 20, 600)" in _case("ticket_reply")
    assert "try { db()->rollback(); } catch (Throwable $e2) {}" in SRC      # (an exception in the middle of a transaction leaves nothing half done)


def test_a_new_customer_row_is_like_the_bots_and_a_second_first_request_is_not_an_error():
    at = SRC.index("INSERT IGNORE INTO users")
    assert "INSERT IGNORE INTO users (userid, name, username, refcode, wallet, date) VALUES" in SRC and "step" not in SRC[at:at + 140]


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_a_stock_bot_without_extras_php_still_starts_the_mini_app(tmp_path):
    """the main bot has no extras.php (the extras layer lives on the test bot): api.php required it and died with a fatal on the first request"""
    head = SRC[SRC.index("if (is_file(__DIR__ . '/../extras.php'))"):SRC.index("const GB = ")]
    assert "require_once __DIR__ . '/../extras.php';" in head and "if (!function_exists('exEmojiStrip'))" in head and "if (!function_exists('exOnHoldDays'))" in head
    out = _php(tmp_path, textwrap.dedent(r"""
        $botState = ['onHoldState' => 'on', 'onHoldDays' => 0];
        """) + head.replace("__DIR__ . '/../extras.php'", "'/nonexistent/extras.php'") + textwrap.dedent(r"""
        echo json_encode([exEmojiStrip('x {emoji:12345678:🙂} y'), exEmojiStrip('plain'), exOnHoldDays(30), (function () { global $botState; $botState['onHoldState'] = 'off'; return exOnHoldDays(30); })(), (function () { global $botState; $botState['onHoldState'] = 'on'; $botState['onHoldDays'] = 7; return exOnHoldDays(30); })()], JSON_UNESCAPED_UNICODE);
        """))
    assert json.loads(out) == ["x 🙂 y", "plain", 30, 0, 7]


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_premium_emoji_tokens_in_the_bots_texts_never_reach_the_page(tmp_path):
    """the bot's text_overrides.json / button_overrides.json can hold {emoji:ID:😀} (premium emoji, installed on the main bot 2026-10-22); the Mini App reads the same files and shows the plain character, never the token"""
    (tmp_path / "app").mkdir()
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "values.php").write_text("<?php\n$mainValues = ['a' => 'x {emoji:12345678:😀} y', 'n' => 5, 'l' => ['k' => '{emoji:12345678:🙂}']];\n$buttonValues = ['b' => '{emoji:99999999} z'];\n", encoding="utf-8")
    (tmp_path / "settings" / "text_overrides.json").write_text(json.dumps({"c": "{emoji:11111111:⭐} top"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "settings" / "button_overrides.json").write_text(json.dumps({"d": "go {emoji:22222222:👉}"}, ensure_ascii=False), encoding="utf-8")
    head = SRC[SRC.index("if (!function_exists('exEmojiStrip'))"):SRC.index("const GB = ")]
    script = tmp_path / "app" / "t.php"      # (botTexts reads ../settings/ next to the script, as in the bot's folder)
    script.write_text("<?php\n" + head + _fn("botTexts") + _fn("mainText") + _fn("buttonText") + "\n[$m, $b] = botTexts();\necho json_encode([$m, $b, mainText('a'), buttonText('b', 'fallback'), buttonText('zz', 'fallback')], JSON_UNESCAPED_UNICODE);\n", encoding="utf-8")
    got = json.loads(subprocess.run([PHP, str(script)], capture_output=True, text=True, check=True).stdout)
    assert got[0] == {"a": "x 😀 y", "n": 5, "l": {"k": "{emoji:12345678:🙂}"}, "c": "⭐ top"}      # (the strings of the first level, the overrides too; a number and a nested array are left as they are)
    assert got[1] == {"b": "⭐ z", "d": "go 👉"}                                                # (a token without a fallback character becomes ⭐, as in the bot)
    assert got[2:] == ["x 😀 y", "⭐ z", "fallback"]
    assert "[pe-support]" in SRC


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_buy_page_first_asks_for_the_kind_of_service_then_shows_its_plans():
    """the owner (2026-10-23): "for buying a service put a category so that the customer first chooses the kind of service": the servers of the bot are the kinds; step one has a card per kind
    (plans and the cheapest price), a card opens the plans of that kind with the bot's time categories as tabs and a way back; with one kind only the page is as it was"""
    res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "buygroups"], capture_output=True, text=True, timeout=90, check=True).stdout)
    assert "crash" not in res and res["errors"] == [], res
    g = res["values"]["buygroups"]
    assert g["cards1"] == 3 and g["plans1"] == 0 and g["seg1"] and g["title1"] and g["from3"]      # (no plan on the first screen, no time tabs, the cheapest price of a kind)
    assert g["plans2"] == 1 and g["only1"] and g["backBtn"] and g["tabs2"] == 2                    # (only the plans of that kind; its time categories are tabs)
    assert g["plans3"] == 2 and g["again"]                                                         # (another kind; the way back is the first screen)


def test_the_first_step_of_the_buy_page_is_staged_the_admins_first():
    """BOT_STATES miniBuyGroups: 'admin' (the default) = only the bot's admins, 'on' = everybody, 'off' = nobody (the harness scenario BG1 runs the three)"""
    fn = _fn("buyGroups")
    assert "$botState['miniBuyGroups'] ?? 'admin'" in fn and "$m === 'on' || ($m === 'admin' && in_array((int) $uid, (array) $admins, true))" in fn
    assert "'buy_groups' => buyGroups()" in _case("me")
    assert "!!S.feat.buy_groups && srvs.length > 1" in JS


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_a_server_the_bot_merged_into_another_is_one_kind_of_service_on_the_buy_page():
    """the owner (2026-10-23): "the unlimited subscription must not be separate any more, the bot's categories were updated, make it like the bot's": the bot's settings/server_merge.json {"2": 1}
    shows server 2 INSIDE server 1; the page has one card for the pair, its time categories hold the plans of both (by price), every plan keeps its own server for the purchase"""
    res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "buymerge"], capture_output=True, text=True, timeout=90, check=True).stdout)
    assert "crash" not in res and res["errors"] == [], res
    g = res["values"]["buymerge"]
    assert g["cards1"] == 2 and g["noS2"] and g["s1n"] and g["s3"] and g["order"]      # (S1 with the plans of both servers, S3; the merged server has no card of its own; the bot's order, by id)
    assert g["tabs2"] == 3 and g["c1plans"] == 2 and g["c1both"] and g["byPrice"]      # (the time categories of both servers are tabs; one category lists the plans of both, cheapest first)
    assert g["cards2"] == 1 and g["head2"] and g["unlimOnly"]                          # (one heading, the parent's; a category of the merged server only is reachable too)


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_a_merged_pair_that_is_the_only_kind_has_no_heading_of_the_merged_server():
    """the main bot today: its other servers do not sell, so the merged pair is the ONLY kind (no first step); its plans must still show the parent's heading, the time tabs of both servers, cheapest first"""
    res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "buymerge1"], capture_output=True, text=True, timeout=90, check=True).stdout)
    assert "crash" not in res and res["errors"] == [], res
    g = res["values"]["buymerge1"]
    assert g["noStepOne"] and g["tabs"] == 3 and g["plans"] == 2 and g["heads"] == 1 and g["head"] and g["byPrice"] and g["unlim"], g

def test_the_merge_file_is_read_the_way_the_bot_reads_it(tmp_path):
    """bot/server_merge.php smMap(): {"child": parent}; a chain (the parent is itself a child), a self-map, zero or a negative id are ignored; no file or a broken one = no merge"""
    assert "'/../settings/server_merge.json'" in _fn("mergeMap")
    (tmp_path / "app").mkdir(); (tmp_path / "settings").mkdir()
    script = tmp_path / "app" / "t.php"
    script.write_text("<?php\n" + _fn("mergeMap") + "\necho json_encode(mergeMap());\n", encoding="utf-8")
    for content, want in ((None, []), ("not json{", []), ('{"2": 1, "3": "1", "4": 4, "5": 0, "-1": 1, "6": 3}', {"2": 1, "3": 1}), ('{"2": 1, "1": 7}', {"1": 7})):
        f = tmp_path / "settings" / "server_merge.json"
        f.unlink(missing_ok=True)
        if content is not None:
            f.write_text(content, encoding="utf-8")
        assert json.loads(subprocess.run([PHP, str(script)], capture_output=True, text=True, check=True).stdout) == want, content
    plans = _case("plans")
    assert "$merge = mergeMap()" in plans and "'group_id' => $gid" in plans and "if ($gid !== $sid && !isset($parents[$gid])) continue;" in plans      # (a merged server whose parent does not sell cannot be reached in the bot: its plans are not listed)
    assert "AND state = 1 AND id IN (" in plans and "'server_id' => $sid" in plans                                                                       # (the plan keeps its own server for the purchase)


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_history_shows_the_last_three_rows_and_the_rest_behind_a_button():
    """the owner (2026-10-23): "show the last 3 in the history and put an option to show the rest": three rows, then one button that opens (and closes) the older rows and the cashbacks"""
    res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "history"], capture_output=True, text=True, timeout=90, check=True).stdout)
    assert "crash" not in res and res["errors"] == [], res
    h = res["values"]["history"]
    assert h["shown"] == 3 and h["hidden"] == 3 and h["hiddenAtFirst"] and h["button"] and h["cashbackBehind"]      # (5 rows + a cashback: 3 at once, 2 rows + the cashback behind the button)
    assert h["opened"] and h["closed"]                                                                               # (the button opens and closes them)
    assert h["fewRows"] == 3 and not h["fewButton"]                                                                   # (three rows or fewer: no button)

def test_the_mini_app_lists_and_sells_only_what_the_bot_sells():
    """the Mini App listed 58 plans where the bot shows 17: a LEFT JOIN let the plans of deleted servers through (s.active IS NULL) and server_info.state (0 = the bot does not sell from it) was never read"""
    plans, buy = _case("plans"), _case("buy")
    assert "FROM server_plans p JOIN server_info s ON s.id = p.server_id" in plans and "LEFT JOIN server_info" not in plans
    assert "p.active = 1 AND s.active = 1 AND s.state = 1" in plans and "s.active IS NULL" not in plans
    assert "(int) $srv['state'] !== 1" in buy and "i.state" in _fn("serverOf")


def test_the_backup_of_a_deploy_is_not_inside_the_web_root():
    """the old copies in app/.prev-<time>/ were served to anybody (app.js, index.html; api.php answered 500 there)"""
    sh = (ROOT / "scripts" / "deploy-miniapp.sh").read_text(encoding="utf-8")
    assert r'BACKUP="$(dirname "$DIR")/.miniapp-prev-$(basename "$DIR")/$STAMP"' in sh
    assert r"mkdir -p '$DIR/app' '$BACKUP'" in sh and r"'$BACKUP/'" in sh and r"mkdir -p '$DIR/app/.prev'" not in sh      # (the copy goes beside the bot folder, never into app/)
    assert r"for old in '$DIR'/app/.prev '$DIR'/app/.prev-*; do" in sh      # (a folder an earlier deploy left in the web root is moved out)
    assert "It keeps the previous files in `app/.prev/`" not in (MINI / "README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------- the page
@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_a_quote_in_the_support_id_or_the_bot_name_stays_inside_the_string_of_the_handler():
    """onclick="openLink('https://t.me/${esc(S.support)}')": esc() makes &#39; which the browser turns back into ' BEFORE the handler runs, so x');alert(1);(' ran alert (the copy buttons had the same bug in 1.5.0)"""
    out = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "hostile"], capture_output=True, text=True, timeout=90, check=True).stdout
    res = json.loads(out)
    assert "crash" not in res, res
    assert res["errors"] == []
    assert res["values"]["unknownCalls"] == []
    mine = [h for h in res["values"]["openLinks"] if "t.me/x" in h]      # (the support card on three pages, the wallet card, and two more in the "more" list)
    assert len(mine) >= 6 and all(h.startswith('openLink("https://t.me/x') and h.endswith('")') for h in mine), mine


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_page_keeps_the_key_of_a_payment_while_the_answer_is_lost_and_drops_it_once_it_has_one():
    """a key per sheet: two purchases = two keys; the answer lost (the fetch threw) = the SAME key on the next tap, so api.php can show the first result instead of charging again; any answer (done or refused) drops it"""
    out = subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), "payflow"], capture_output=True, text=True, timeout=90, check=True).stdout
    res = json.loads(out)
    assert "crash" not in res and res["errors"] == [], res
    r = res["values"]["rids"]
    assert len(r) == 6 and all(re.fullmatch(r"[A-Za-z0-9_-]{8,40}", x) for x in r)
    assert r[0] != r[1] and r[2] == r[3] == r[4] and r[5] not in (r[0], r[1], r[4])
    rr = res["values"]["renewRids"]
    assert len(rr) == 2 and rr[0] == rr[1] and res["values"]["left"] == []


@pytest.mark.skipif(PHP is None, reason="php is not installed here")
def test_the_helper_subscription_links_are_the_servers_other_domains_with_the_same_token(tmp_path):
    """the bot's buildSubBackupSection: every domain of server_config.sub_links + /sub/<token>; the owner (2026-10-23): "if a subscription has helper links, show them there" (the Mini App showed only the main link)"""
    out = _php(tmp_path, "function on($k, $d = 'off') { global $botState; return ($botState[$k] ?? $d) === 'on'; }\n" + _fn("subAlt") + textwrap.dedent(r"""
        $botState = ['subLinkState' => 'on'];
        $raw = 'https://a.example.com, https://b.example.com/ ,javascript:alert(1),ftp://x.example,,https://a.example.com,http://c.example:8443/path';
        $r = [subAlt($raw, 'https://panel.example/sub/TOK1?x=1', 'saved'), subAlt($raw, '', 'saved'), subAlt($raw, 'https://panel.example/user/none', ''), subAlt('', 'https://p/sub/T', 'saved'), subAlt(null, '', 'saved')];
        $botState['subLinkState'] = 'off';
        $r[] = subAlt($raw, 'https://p/sub/T', 'saved');
        echo json_encode($r);
        """))
    got = json.loads(out)
    assert got[0] == ["https://a.example.com/sub/TOK1", "https://b.example.com/sub/TOK1", "http://c.example:8443/path/sub/TOK1"]      # the token of the live link (without its query)
    assert got[1] == ["https://a.example.com/sub/saved", "https://b.example.com/sub/saved", "http://c.example:8443/path/sub/saved"]      # no live link: the token saved with the order
    assert got[2:] == [[], [], [], []]
    for name in ("service", "buy"):
        assert "'sub_alt' =>" in _case(name)
    assert "c.sub_links" in _fn("orderOf") and "c.sub_links" in _fn("serverOf") and "'sub_alt' => subAlt(" in _fn("buyAgain")


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_page_shows_the_helper_links_and_the_apps_by_system_with_the_recommended_ones_first():
    """the owner (2026-10-23): "show the recommended apps; NPV and Karing must be among them for iPhone and Android": a title with (پیشنهادی) is a badge, the apps are grouped by system"""
    res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js")], capture_output=True, text=True, timeout=90, check=True).stdout)
    assert "crash" not in res and res["errors"] == [], res
    v = res["values"]
    assert v["altBoxes"] >= 2 and v["altCopy"] >= 3                       # the service page and the page after a purchase, a copy handler per link (the quotes in one stay inside its string)
    assert v["apps"]["groups"] == ["📱 آیفون", "🤖 اندروید", "🪟 ویندوز", "💻 مک", "📚 آموزش"]
    assert v["apps"]["pills"] == 2 and v["apps"]["recFirst"] and v["apps"]["noMarker"]


@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_new_ticket_button_is_gone_from_the_support_page_when_it_is_switched_off():
    """the owner (2026-10-26, translated): "for now switch this option off and do not show it there" (the "+ new ticket" button of the Mini App's support page): BOT_STATES miniTicketNew = off"""
    def run(mode):
        res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), mode], capture_output=True, text=True, timeout=90, check=True).stdout)
        assert "crash" not in res and res["errors"] == [], res
        return res["values"]["ticket"]
    on, off = run("ticket"), run("noticket")
    assert on["newButton"] and on["list"]                              # (the default: the button is there)
    assert not off["newButton"] and off["list"]                        # (switched off: no button, the list of the tickets stays)
    api = (MINI / "api.php").read_text(encoding="utf-8")
    assert "'ticket_new' => on('miniTicketNew', 'on')" in api
    case = api[api.index("case 'ticket_new'"):api.index("case 'ticket_reply'")]
    assert "if (!on('miniTicketNew', 'on')) fail(" in case and case.index("miniTicketNew") < case.index("INSERT INTO chats")      # (refused on the server too, before anything is written)
    assert "miniTicketNew" not in api[api.index("case 'ticket_reply'"):api.index("case 'report'")]                                    # (a reply in an open ticket is not affected)

@pytest.mark.skipif(NODE is None, reason="node is not installed here")
def test_the_buy_sheet_has_a_field_for_a_discount_code_and_the_server_does_the_sums():
    """the owner (2026-10-26, translated): "add a section for the discount code" (with a screenshot of the buy sheet): a field + "apply" under the plan, the price shown with the code, the code sent with the purchase;
    the page never says what the price is - the server asks the bot's own table `discounts` again when the customer pays"""
    def run(mode):
        res = json.loads(subprocess.run([NODE, str(DRIVER), str(MINI / "app.js"), mode], capture_output=True, text=True, timeout=90, check=True).stdout)
        assert "crash" not in res and res["errors"] == [], res
        return res["values"]
    d = run("discount")["discount"]
    assert d["field"] and d["plain"]                                   # (the field and the apply button; the price without a code)
    assert d["checkBody"] and d["applied"] and d["newPrice"]           # (the code - trimmed, with a quote in it - is sent, shown escaped, the new price and the old one crossed out)
    assert d["buyBody"] and d["after"]                                 # (the purchase carries the code and a key; after it the sheet starts clean)
    assert run("nodiscount")["nodiscount"]["field"] is False           # (switched off: no field)
    api = (MINI / "api.php").read_text(encoding="utf-8")
    assert "'discount_code' => on('miniDiscountCode', 'on')" in api
    buy = api[api.index("case 'buy'"):api.index("case 'renew'")]
    assert buy.index("discountApply(") < buy.index("takeSlot(") < buy.index("discountConsume(") < buy.index("panelCreateUser(")      # (checked before a slot is taken, used up inside the transaction BEFORE the panel is called)
    fn = api[api.index("function discountApply("):api.index("function discountConsume(")]
    for word in ("'user'", "'agents'", "'non_agents'", "'buyers'", "'non_buyers'", "'has_active_order'", "'no_active_order'", "'plan'", "'server'", "'category'", "min_price", "max_discount", "can_use", "used_by", "'percent'"):
        assert word in fn, word                                        # (every rule of the bot's applyDiscountToPay)
    assert "FOR UPDATE" in api[api.index("function discountRow("):api.index("function discountApply(")] and "expire_count = IF(expire_count = -1, -1, expire_count - 1)" in api

def test_only_web_links_are_opened_and_the_files_have_a_new_version_for_the_cache():
    assert r'if (!/^https?:\/\//i.test(u)) return;' in JS
    html = (MINI / "index.html").read_text(encoding="utf-8")
    assert "app.css?v=3.0.12" in html and "app.js?v=3.0.12" in html


# ---------------------------------------------------------------------------------------------- the whole attack harness (opt in)
@pytest.mark.skipif(not os.environ.get("NIKATEL_MINIAPP_SEC") or not shutil.which("mariadbd") or PHP is None or not shutil.which("openssl"),
                    reason="the attack harness needs NIKATEL_MINIAPP_SEC=1, mariadbd, php and openssl (it starts a private MariaDB and php -S on ports 49510-49514)")
def test_the_attack_harness_finds_no_hole(tmp_path):
    env = dict(os.environ, SEC_SCRATCH=str(tmp_path / "sec"), SEC_RESULTS=str(tmp_path))
    subprocess.run(["bash", str(HARNESS / "sec_setup_db.sh"), "start"], env=env, check=True, capture_output=True, timeout=300)
    try:
        r = subprocess.run([sys.executable, str(HARNESS / "sec.py"), str(MINI), "--label", "pytest"], check=False, env=env, capture_output=True, text=True, timeout=1500)
    finally:
        subprocess.run(["bash", str(HARNESS / "sec_setup_db.sh"), "stop"], check=False, env=env, capture_output=True, timeout=120)
    assert "0 HOLE, 0 error" in r.stdout, r.stdout[-3000:]
