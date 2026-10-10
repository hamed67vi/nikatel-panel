<?php
/* =====================================================================
   Mini App API of the shop bot.

   Lives in <bot dir>/app/ next to index.html. Every call carries Telegram's initData, verified with
   the bot token (HMAC-SHA256, "WebAppData"), so a request can only come from the real user inside
   Telegram. It uses the bot's database and the panels' HTTP APIs directly and mirrors what the bot
   writes (pays, orders_list, increase_order, cashback_log, chats) so the bot's admin side - approve
   / decline buttons, reports, the panel's Shop bot pages - keeps working on what the Mini App did.
   Nothing of the bot's webhook flow runs here.

   Actions (POST JSON {a, initData, ...} or ?a=):
     me · services · live · service · quote · plans · buy · renew · addvolume · adddays
     topup · receipt (multipart) · cancel_topup · history · gift · wheel · referral · apps
     tickets · ticket_messages · ticket_new · ticket_reply · report
   ===================================================================== */
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
error_reporting(E_ALL);
ini_set('display_errors', '0');
ini_set('log_errors', '1');
ini_set('error_log', __DIR__ . '/../settings/miniapp_error.log');

include_once __DIR__ . '/../baseInfo.php';     // $botToken, $admin, $admins_list, $botUsername, $botUrl, db credentials
require_once __DIR__ . '/../extras.php';       // exEmojiStrip, exOnHoldDays, exFaDigits

const GB = 1073741824;
const MIN_TOPUP = 10000;
const MAX_TOPUP = 50000000;
const RECEIPT_MAX_BYTES = 6 * 1024 * 1024;

function out(array $a): void { echo json_encode($a, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES); exit; }
function fail(string $m, int $code = 400): void { http_response_code($code); out(['ok' => false, 'error' => $m]); }

/* ---------------------------------------------------------------- database */
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
try {
    $connection = new mysqli('localhost', $dbUserName, $dbPassword, $dbName);
    $connection->set_charset('utf8mb4');
} catch (Throwable $e) {
    error_log('miniapp db: ' . $e->getMessage());
    fail('پایگاه داده در دسترس نیست.', 500);
}

function db(): mysqli { global $connection; return $connection; }

/** rows of a prepared query; every parameter is bound as a string (MariaDB converts) */
function rows(string $sql, array $p = []): array {
    $st = db()->prepare($sql);
    if ($p) { $p = array_values($p); $st->bind_param(str_repeat('s', count($p)), ...$p); }
    $st->execute();
    $r = $st->get_result();
    $out = $r ? $r->fetch_all(MYSQLI_ASSOC) : [];
    $st->close();
    return $out;
}
function row(string $sql, array $p = []): ?array { $r = rows($sql, $p); return $r[0] ?? null; }
/** runs a statement; returns the insert id when there is one, else the affected rows */
function run(string $sql, array $p = []): int {
    $st = db()->prepare($sql);
    if ($p) { $p = array_values($p); $st->bind_param(str_repeat('s', count($p)), ...$p); }
    $st->execute();
    $id = (int) $st->insert_id; $n = (int) $st->affected_rows;
    $st->close();
    return $id ?: $n;
}
function settingJson(string $type): array {
    $v = row("SELECT value FROM setting WHERE type = ?", [$type])['value'] ?? '';
    $j = json_decode((string) $v, true);
    return is_array($j) ? $j : [];
}
function settingValue(string $type): string { return (string) (row("SELECT value FROM setting WHERE type = ?", [$type])['value'] ?? ''); }
function on(string $key, string $default = 'off'): bool { global $botState; return ($botState[$key] ?? $default) === 'on'; }

/* ---------------------------------------------------------------- texts of the bot (values.php + the panel's overrides) */
function botTexts(): array {
    static $t = null;
    if ($t !== null) return $t;
    $mainValues = []; $buttonValues = [];
    try { include __DIR__ . '/../settings/values.php'; } catch (Throwable $e) { error_log('miniapp values: ' . $e->getMessage()); }
    foreach (['text' => 'mainValues', 'button' => 'buttonValues'] as $kind => $var) {
        $f = __DIR__ . "/../settings/{$kind}_overrides.json";
        if (is_file($f)) { $ov = json_decode((string) file_get_contents($f), true); if (is_array($ov)) $$var = array_merge($$var, $ov); }
    }
    return $t = [is_array($mainValues) ? $mainValues : [], is_array($buttonValues) ? $buttonValues : []];
}
function mainText(string $key, string $fallback = ''): string { [$m] = botTexts(); $v = $m[$key] ?? $fallback; return is_string($v) ? $v : $fallback; }
function buttonText(string $key, string $fallback): string { [, $b] = botTexts(); $v = $b[$key] ?? $fallback; return is_string($v) && trim($v) !== '' ? $v : $fallback; }
/** the bot's texts use a little HTML; only harmless tags reach the browser */
function safeHtml(string $s): string {
    $s = strip_tags($s, '<b><strong><i><em><code><u><s><br>');
    return nl2br($s, false);
}

/* ---------------------------------------------------------------- Telegram */
function tg(string $method, array $data, bool $multipart = false): ?array {
    global $botToken;
    $ch = curl_init("https://api.telegram.org/bot$botToken/$method");
    curl_setopt_array($ch, [CURLOPT_POST => true, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 20, CURLOPT_CONNECTTIMEOUT => 6, CURLOPT_IPRESOLVE => CURL_IPRESOLVE_V4,
        CURLOPT_POSTFIELDS => $multipart ? $data : http_build_query($data)]);
    $r = curl_exec($ch);
    curl_close($ch);
    $j = json_decode((string) $r, true);
    return is_array($j) ? $j : null;
}
function tgText(int|string $chat, string $html, ?array $markup = null): ?array {
    $d = ['chat_id' => $chat, 'text' => $html, 'parse_mode' => 'HTML', 'disable_web_page_preview' => true];
    if ($markup) $d['reply_markup'] = json_encode($markup, JSON_UNESCAPED_UNICODE);
    return tg('sendMessage', $d);
}
function h(string|int|null $s): string { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function userLink(int $uid, string $name): string { return "<a href=\"tg://user?id=$uid\">" . h($name !== '' ? $name : (string) $uid) . "</a>"; }
function fa(int|float $n): string { return number_format((float) $n); }

/* ---------------------------------------------------------------- Telegram WebApp initData */
function tgVerify(string $initData, string $token): ?array {
    if ($initData === '') return null;
    parse_str($initData, $d);
    if (empty($d['hash']) || !is_string($d['hash'])) return null;
    $hash = $d['hash'];
    unset($d['hash']);
    ksort($d);
    $check = implode("\n", array_map(fn($k) => $k . '=' . $d[$k], array_keys($d)));
    $secret = hash_hmac('sha256', $token, 'WebAppData', true);
    if (!hash_equals(hash_hmac('sha256', $check, $secret), $hash)) return null;
    if (!isset($d['auth_date']) || time() - (int) $d['auth_date'] > 86400) return null;
    $user = json_decode($d['user'] ?? '{}', true);
    if (!is_array($user) || !isset($user['id'])) return null;
    $user['start_param'] = $d['start_param'] ?? '';
    return $user;
}

/* ---------------------------------------------------------------- request */
$isMultipart = stripos($_SERVER['CONTENT_TYPE'] ?? '', 'multipart/form-data') !== false;
$body = $isMultipart ? $_POST : (json_decode((string) file_get_contents('php://input'), true) ?: []);
$action = (string) ($_GET['a'] ?? ($body['a'] ?? ''));
$user = tgVerify((string) ($body['initData'] ?? ''), (string) $botToken);
if (!$user) fail('این صفحه فقط از داخل تلگرام باز می‌شود.', 401);
$uid = (int) $user['id'];

$botState = settingJson('BOT_STATES');
$GLOBALS['botState'] = $botState;           // extras.php (exOnHoldDays) reads it
if (!on('botState', 'on') && $uid !== (int) $admin && !in_array($uid, array_map('intval', (array) ($admins_list ?? [])), true)) fail('ربات موقتاً خاموش است.', 503);

/* the user row (made if missing, like the bot does) */
$me = row("SELECT * FROM users WHERE userid = ?", [$uid]);
if (!$me) {
    $name = mb_substr(trim(($user['first_name'] ?? '') . ' ' . ($user['last_name'] ?? '')), 0, 60) ?: ' ';
    $un = (string) ($user['username'] ?? ' ');
    run("INSERT INTO users (userid, name, username, refcode, wallet, date, step) VALUES (?, ?, ?, 0, 0, ?, '')", [$uid, $name, $un, time()]);
    $me = row("SELECT * FROM users WHERE userid = ?", [$uid]) ?? ['userid' => $uid, 'name' => $name, 'username' => $un, 'wallet' => 0, 'is_agent' => 0, 'step' => '', 'discount_percent' => '', 'date' => time()];
}
if (($me['step'] ?? '') === 'banned') fail('دسترسی شما به ربات بسته است.', 403);
$isAgent = (int) ($me['is_agent'] ?? 0) === 1;
$admins = array_values(array_unique(array_filter(array_map('intval', array_merge([(int) $admin], (array) ($admins_list ?? []))))));

/* ---------------------------------------------------------------- helpers shared by the actions */
function randomId(int $n = 10): string { $a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'; $s = ''; for ($i = 0; $i < $n; $i++) $s .= $a[random_int(0, 61)]; return $s; }
function wallet(int $uid): int { return (int) (row("SELECT wallet FROM users WHERE userid = ?", [$uid])['wallet'] ?? 0); }
function serverOf(int $id): ?array {
    $s = row("SELECT i.id, i.title, i.flag, i.remark, i.active, i.ucount, c.type, c.panel_url, c.username, c.password FROM server_info i JOIN server_config c ON c.id = i.id WHERE i.id = ?", [$id]);
    if ($s) $s['label'] = trim(exEmojiStrip((string) $s['flag']) . ' ' . $s['title']);
    return $s;
}
/** the price this user pays for a plan: agents get their discount_percent (bot: calculateAgentPrice) */
function priceFor(array $me, array $plan): array {
    global $botState;
    $price = (int) $plan['price'];
    if ((int) ($me['is_agent'] ?? 0) !== 1) return [$price, 0, 0];
    $d = json_decode((string) ($me['discount_percent'] ?? ''), true); if (!is_array($d)) $d = [];
    $normal = (int) ($d['normal'] ?? 0);
    if (($botState['agencyPlanDiscount'] ?? '') === 'on') $pct = (int) ($d['plans'][(string) $plan['id']] ?? $d['plans'][(int) $plan['id']] ?? $normal);
    else $pct = (int) ($d['servers'][(string) $plan['server_id']] ?? $d['servers'][(int) $plan['server_id']] ?? $normal);
    if ($pct > 0) $price -= (int) floor($price * $pct / 100);
    return [$price, 1, $pct];
}
/** remark like the bot (buy_helpers.php buildRemark) */
function makeRemark(string $srvRemark, int $uid): string {
    global $botState;
    $srvRemark = preg_replace('/[^A-Za-z0-9_.-]/', '', $srvRemark) ?: 'u';
    if (($botState['remark'] ?? '') === 'digits') return $srvRemark . '-' . random_int(10000, 99999);
    return $srvRemark . '-' . $uid . '-' . random_int(1111, 99999);
}
function cashback(int $uid, int $amount): int {
    global $botState, $admin;
    $pct = (float) ($botState['cashbackPercent'] ?? 0);
    if ($pct <= 0 || $amount <= 0 || $uid === (int) $admin) return 0;
    $back = (int) floor($amount * $pct / 100);
    if ($back <= 0) return 0;
    db()->query("CREATE TABLE IF NOT EXISTS cashback_log (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, userid BIGINT NOT NULL, order_amount BIGINT NOT NULL DEFAULT 0, cashback BIGINT NOT NULL DEFAULT 0, at BIGINT NOT NULL, KEY u (userid)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    run("UPDATE users SET wallet = COALESCE(wallet, 0) + ? WHERE userid = ?", [$back, $uid]);
    run("INSERT INTO cashback_log (userid, order_amount, cashback, at) VALUES (?, ?, ?, ?)", [$uid, $amount, $back, time()]);
    tgText($uid, "🎁 <b>کش‌بک خرید</b>\n\n" . fa($back) . " تومان (" . rtrim(rtrim(number_format($pct, 1), '0'), '.') . "٪ مبلغ خرید) به کیف پول شما برگشت.");
    return $back;
}
function tellAdmins(string $html): void {
    global $admins, $botState;
    foreach ($admins as $a) tgText($a, $html);
    if (!empty($botState['rewardChannel'])) tgText($botState['rewardChannel'], $html);
}

/* ---------------------------------------------------------------- panel client (Marzban / PasarGuard / Marzneshin) */
function panelHttp(string $method, string $url, ?string $token = null, ?array $body = null, int $timeout = 12, array $headers = []): array {
    $hd = array_merge(['Accept: application/json'], $headers);
    if ($token) $hd[] = 'Authorization: Bearer ' . $token;
    if ($body !== null) $hd[] = 'Content-Type: application/json';
    $ch = curl_init($url);
    curl_setopt_array($ch, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => $timeout, CURLOPT_HTTPHEADER => $hd]);
    if ($body !== null) curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body, JSON_UNESCAPED_UNICODE));
    $raw = (string) curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$code, json_decode($raw, true), $raw];
}
function panelKind(array $srv): string {
    static $mem = [];
    $base = rtrim((string) $srv['panel_url'], '/');
    if (isset($mem[$base])) return $mem[$base];
    $type = strtolower((string) ($srv['type'] ?? ''));
    if (in_array($type, ['pasarguard', 'marzneshin'], true)) return $mem[$base] = $type;
    // a "marzban" entry may really be PasarGuard: the bot remembers what it found in settings/panel_kind.json
    $file = __DIR__ . '/../settings/panel_kind.json';
    $map = is_file($file) ? json_decode((string) file_get_contents($file), true) : [];
    if (!is_array($map)) $map = [];
    $key = md5($base);
    if (isset($map[$key])) return $mem[$base] = ($map[$key] === 'pasarguard' ? 'pasarguard' : 'marzban');
    $tok = panelToken($srv);
    if ($tok === null) return $mem[$base] = 'marzban';
    [$code] = panelHttp('GET', $base . '/api/groups', $tok, null, 6);
    $map[$key] = $code === 200 ? 'pasarguard' : 'marzban';
    @file_put_contents($file, json_encode($map));
    return $mem[$base] = $map[$key];
}
function panelToken(array $srv): ?string {
    static $cache = [];
    $base = rtrim((string) $srv['panel_url'], '/');
    if (isset($cache[$base])) return $cache[$base];
    $kind = strtolower((string) ($srv['type'] ?? ''));
    $url = $base . ($kind === 'marzneshin' ? '/api/admins/token' : '/api/admin/token');
    $ch = curl_init($url);
    curl_setopt_array($ch, [CURLOPT_POST => true, CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => 8,
        CURLOPT_POSTFIELDS => http_build_query(['username' => $srv['username'], 'password' => $srv['password']]), CURLOPT_HTTPHEADER => ['Content-Type: application/x-www-form-urlencoded', 'Accept: application/json']]);
    $j = json_decode((string) curl_exec($ch), true);
    curl_close($ch);
    return $cache[$base] = (is_array($j) && !empty($j['access_token']) ? (string) $j['access_token'] : null);
}
function panelUserPath(array $srv, string $remark): string { return (panelKind($srv) === 'marzneshin' ? '/api/users/' : '/api/user/') . rawurlencode($remark); }
function subUrl(array $srv, string $sub): string {
    if ($sub === '') return '';
    if (strpos($sub, 'http') !== 0) $sub = rtrim((string) $srv['panel_url'], '/') . '/' . ltrim($sub, '/');
    return $sub;
}
/** the config links behind a subscription (v2ray client name = plain list instead of the web page) */
function linksFromSub(string $sub): array {
    if ($sub === '') return [];
    [$code, , $raw] = panelHttp('GET', $sub, null, null, 8, ['User-Agent: v2rayN/6.45']);
    if ($code !== 200 || $raw === '') return [];
    $dec = base64_decode(trim($raw), true);
    $text = ($dec !== false && strpos($dec, '://') !== false) ? $dec : $raw;
    $links = [];
    foreach (preg_split('/\r\n|\r|\n/', $text) as $line) { $line = trim($line); if ($line !== '' && strpos($line, '://') !== false) $links[] = $line; }
    return $links;
}
function normalizeUser(array $srv, array $u): array {
    $exp = $u['expire'] ?? ($u['expire_date'] ?? null);
    if (is_string($exp)) $exp = strtotime($exp) ?: 0;
    $status = (string) ($u['status'] ?? (!empty($u['enabled']) ? 'active' : 'disabled'));
    if (isset($u['is_active']) && $u['is_active'] === false && $status === 'active') $status = 'disabled';
    $sub = subUrl($srv, (string) ($u['subscription_url'] ?? ''));
    return ['used' => (int) ($u['used_traffic'] ?? 0), 'limit' => (int) ($u['data_limit'] ?? 0), 'expire' => (int) $exp, 'status' => $status, 'sub' => $sub,
        'online' => $u['online_at'] ?? null, 'links' => isset($u['links']) && is_array($u['links']) ? array_values(array_filter($u['links'], 'is_string')) : [], 'hold' => (int) ($u['on_hold_expire_duration'] ?? 0)];
}
function panelGetUser(array $srv, string $remark): ?array {
    $tok = panelToken($srv);
    if ($tok === null) return null;
    [$code, $u] = panelHttp('GET', rtrim((string) $srv['panel_url'], '/') . panelUserPath($srv, $remark), $tok, null, 8);
    if ($code !== 200 || !is_array($u) || !isset($u['username'])) return null;
    return normalizeUser($srv, $u);
}
/** PasarGuard groups wanted by a plan: group_ids, or inbound tags mapped to group names; none = every group */
function pgGroupIds(array $srv, string $tok, array $planInfo): array {
    [, $g] = panelHttp('GET', rtrim((string) $srv['panel_url'], '/') . '/api/groups', $tok, null, 8);
    $all = [];
    foreach ((array) ($g['groups'] ?? []) as $grp) if (isset($grp['name'], $grp['id'])) $all[$grp['name']] = (int) $grp['id'];
    $want = [];
    foreach ((array) ($planInfo['group_ids'] ?? []) as $id) $want[] = (int) $id;
    foreach ((array) ($planInfo['inbounds'] ?? []) as $tags) foreach ((array) $tags as $tag) if (isset($all[$tag])) $want[] = $all[$tag];
    $want = array_values(array_unique(array_filter($want)));
    return $want ?: array_values($all);
}
function mnServiceIds(array $srv, string $tok, array $planInfo): array {
    [, $s] = panelHttp('GET', rtrim((string) $srv['panel_url'], '/') . '/api/services', $tok, null, 8);
    $all = [];
    foreach ((array) ($s['items'] ?? $s ?? []) as $it) if (is_array($it) && isset($it['id'])) $all[(string) ($it['name'] ?? $it['id'])] = (int) $it['id'];
    $want = [];
    foreach ((array) ($planInfo['service_ids'] ?? []) as $id) $want[] = (int) $id;
    foreach ((array) ($planInfo['inbounds'] ?? []) as $tags) foreach ((array) $tags as $tag) if (isset($all[$tag])) $want[] = $all[$tag];
    $want = array_values(array_unique(array_filter($want)));
    return $want ?: array_values($all);
}
/** creates the account of a plan on its panel; mirrors addMarzbanUser / mzPgAddUser / mnAddUser of the bot */
function panelCreateUser(array $srv, string $remark, float $gb, int $days, array $planInfo): array {
    $tok = panelToken($srv);
    if ($tok === null) return ['ok' => false, 'msg' => 'ورود به پنل ممکن نشد'];
    $base = rtrim((string) $srv['panel_url'], '/');
    $kind = panelKind($srv);
    $hold = function_exists('exOnHoldDays') ? (int) exOnHoldDays($days) : 0;
    $limit = (int) round($gb * GB);
    if ($kind === 'pasarguard') {
        $body = ['username' => $remark, 'proxy_settings' => new stdClass(), 'group_ids' => pgGroupIds($srv, $tok, $planInfo), 'expire' => $days > 0 ? gmdate('Y-m-d\TH:i:s\Z', time() + 86400 * $days) : null,
            'data_limit' => $limit, 'data_limit_reset_strategy' => 'no_reset', 'status' => 'active'];
        if ($hold > 0) { $body['status'] = 'on_hold'; $body['expire'] = null; $body['on_hold_expire_duration'] = $hold * 86400; }
        [$code, $r] = panelHttp('POST', $base . '/api/user', $tok, $body, 20);
    } elseif ($kind === 'marzneshin') {
        $body = ['username' => $remark, 'service_ids' => mnServiceIds($srv, $tok, $planInfo), 'data_limit' => $limit, 'data_limit_reset_strategy' => 'no_reset', 'note' => ''];
        if ($hold > 0) { $body['expire_strategy'] = 'start_on_first_use'; $body['usage_duration'] = $hold * 86400; }
        elseif ($days > 0) { $body['expire_strategy'] = 'fixed_date'; $body['expire_date'] = gmdate('Y-m-d\TH:i:s', time() + 86400 * $days); }
        else $body['expire_strategy'] = 'never';
        [$code, $r] = panelHttp('POST', $base . '/api/users', $tok, $body, 20);
    } else {
        $inb = $planInfo['inbounds'] ?? new stdClass();
        $body = ['username' => $remark, 'inbounds' => $inb ?: new stdClass(), 'proxies' => ['vmess' => new stdClass(), 'vless' => new stdClass(), 'trojan' => new stdClass(), 'shadowsocks' => new stdClass()],
            'expire' => $days > 0 ? time() + 86400 * $days : 0, 'data_limit' => $limit];
        if ($hold > 0) { $body['status'] = 'on_hold'; $body['expire'] = 0; $body['on_hold_expire_duration'] = $hold * 86400; }
        [$code, $r] = panelHttp('POST', $base . '/api/user', $tok, $body, 20);
    }
    if ($code >= 300 || !is_array($r) || !isset($r['username'])) {
        $detail = is_array($r) && isset($r['detail']) ? $r['detail'] : ('HTTP ' . $code);
        return ['ok' => false, 'msg' => is_scalar($detail) ? (string) $detail : json_encode($detail, JSON_UNESCAPED_UNICODE), 'code' => $code];
    }
    $u = normalizeUser($srv, $r);
    if (!$u['links']) { usleep(600000); $u['links'] = linksFromSub($u['sub']); }
    return ['ok' => true, 'sub' => $u['sub'], 'links' => $u['links'], 'remark' => $remark];
}
function panelModifyUser(array $srv, string $remark, array $fields): bool {
    $tok = panelToken($srv);
    if ($tok === null) return false;
    $kind = panelKind($srv);
    $body = [];
    if (array_key_exists('data_limit', $fields)) $body['data_limit'] = (int) $fields['data_limit'];
    if (array_key_exists('expire', $fields)) {
        $ts = (int) $fields['expire'];
        if ($kind === 'pasarguard') $body['expire'] = $ts > 0 ? gmdate('Y-m-d\TH:i:s\Z', $ts) : null;
        elseif ($kind === 'marzneshin') { $body['expire_strategy'] = $ts > 0 ? 'fixed_date' : 'never'; if ($ts > 0) $body['expire_date'] = gmdate('Y-m-d\TH:i:s', $ts); }
        else $body['expire'] = $ts;
    }
    if (isset($fields['status']) && $kind !== 'marzneshin') $body['status'] = $fields['status'];
    [$code, $r] = panelHttp('PUT', rtrim((string) $srv['panel_url'], '/') . panelUserPath($srv, $remark), $tok, $body, 15);
    return $code < 300 && is_array($r) && isset($r['username']);
}
function panelResetUser(array $srv, string $remark): bool {
    $tok = panelToken($srv);
    if ($tok === null) return false;
    [$code] = panelHttp('POST', rtrim((string) $srv['panel_url'], '/') . panelUserPath($srv, $remark) . '/reset', $tok, [], 15);
    return $code < 300;
}

/* ---------------------------------------------------------------- orders of this user */
function orderOf(int $id, int $uid): ?array {
    $o = row("SELECT o.*, s.title, s.flag, s.remark AS srv_remark, c.type, c.panel_url, c.username, c.password FROM orders_list o LEFT JOIN server_info s ON s.id = o.server_id LEFT JOIN server_config c ON c.id = o.server_id WHERE o.id = ? AND o.userid = ?", [$id, $uid]);
    if ($o) $o['label'] = trim(exEmojiStrip((string) ($o['flag'] ?? '')) . ' ' . ($o['title'] ?? ''));
    return $o;
}
function orderPlan(array $o): ?array { return (int) $o['fileid'] > 0 ? row("SELECT * FROM server_plans WHERE id = ?", [(int) $o['fileid']]) : null; }
function statusFa(string $s): string { return ['active' => 'فعال', 'on_hold' => 'منتظر اولین اتصال', 'disabled' => 'غیرفعال', 'expired' => 'منقضی', 'limited' => 'حجم تمام شده'][$s] ?? $s; }

/** takes money from the wallet inside a transaction and runs $work(); the pays row mirrors the bot's wallet payments */
function payWithWallet(int $uid, int $price, array $pay, callable $work): array {
    $c = db();
    $c->begin_transaction();
    try {
        $u = row("SELECT wallet FROM users WHERE userid = ? FOR UPDATE", [$uid]);
        $have = (int) ($u['wallet'] ?? 0);
        if ($have < $price) { $c->rollback(); return ['ok' => false, 'error' => 'موجودی کیف پول کافی نیست.', 'short' => $price - $have, 'wallet' => $have]; }
        $hash = randomId(10);
        run("INSERT INTO pays (hash_id, user_id, type, plan_id, server_id, volume, day, price, request_date, state, description, agent_bought, agent_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing_wallet', ?, ?, 0)",
            [$hash, $uid, $pay['type'], (int) ($pay['plan_id'] ?? 0), (int) ($pay['server_id'] ?? 0), (int) round((float) ($pay['volume'] ?? 0)), (int) ($pay['day'] ?? 0), $price, time(), (string) ($pay['description'] ?? ''), (int) ($pay['agent_bought'] ?? 0)]);
        run("UPDATE users SET wallet = wallet - ? WHERE userid = ?", [$price, $uid]);
        $res = $work($hash);
        if (!($res['ok'] ?? false)) { $c->rollback(); return $res; }
        run("UPDATE pays SET state = 'paid_with_wallet' WHERE hash_id = ?", [$hash]);
        $c->commit();
        $res['hash'] = $hash;
        $res['wallet'] = $have - $price;
        return $res;
    } catch (Throwable $e) {
        $c->rollback();
        error_log('miniapp pay: ' . $e->getMessage());
        return ['ok' => false, 'error' => 'پرداخت انجام نشد؛ مبلغی از کیف پول کم نشده است.'];
    }
}

/* ================================================================ actions */
try {
switch ($action) {

case 'me': {
    $cnt = (int) (row("SELECT COUNT(*) AS n FROM orders_list WHERE userid = ? AND status = 1", [$uid])['n'] ?? 0);
    $pending = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND type = 'INCREASE_WALLET' AND state = 'have_sent'", [$uid])['n'] ?? 0);
    $apps = (int) (row("SELECT COUNT(*) AS n FROM needed_sofwares WHERE status = 1")['n'] ?? 0);
    $amounts = array_values(array_filter(array_map('intval', explode(',', (string) ($botState['walletAmounts'] ?? '100000,200000,300000,500000,1000000'))), fn($v) => $v >= MIN_TOPUP));
    $support = trim((string) ($botState['supportID'] ?? ''));
    out(['ok' => true,
        'user' => ['id' => $uid, 'name' => trim((string) $me['name']), 'username' => trim((string) ($me['username'] ?? '')), 'wallet' => (int) $me['wallet'], 'agent' => $isAgent, 'services' => $cnt, 'pending_topups' => $pending, 'since' => (int) ($me['date'] ?? 0)],
        'features' => ['sell' => on('sellState', 'on'), 'wallet' => on('walletState', 'on'), 'card' => on('cartToCartState'), 'gift' => on('giftState'), 'wheel' => on('wheelState'), 'report' => on('reportState'),
            'renew' => on('renewAccountState', 'on'), 'addvolume' => on('increaseVolumeState', 'on'), 'adddays' => on('increaseTimeState', 'on'), 'apps' => $apps > 0, 'hold' => on('onHoldState'),
            'cashback' => (float) ($botState['cashbackPercent'] ?? 0), 'referral' => (int) settingValue('INVITE_BANNER_AMOUNT') > 0, 'sub' => on('subLinkState', 'on'), 'configs' => on('configLinkState', 'on')],
        'amounts' => $amounts, 'min_topup' => MIN_TOPUP, 'support' => ltrim($support, '@'), 'bot' => (string) ($botUsername ?? ''), 'start' => (string) ($user['start_param'] ?? '')]);
}

case 'services': {
    $res = rows("SELECT o.id, o.remark, o.expire_date, o.date, o.status, o.server_id, o.fileid, s.title, s.flag FROM orders_list o LEFT JOIN server_info s ON s.id = o.server_id WHERE o.userid = ? ORDER BY o.status DESC, o.id DESC LIMIT 60", [$uid]);
    $list = [];
    foreach ($res as $o) $list[] = ['id' => (int) $o['id'], 'remark' => $o['remark'], 'server' => trim(exEmojiStrip((string) ($o['flag'] ?? '')) . ' ' . ($o['title'] ?? '')), 'expire' => (int) $o['expire_date'], 'bought' => (int) $o['date'], 'active' => (int) $o['status'] === 1];
    out(['ok' => true, 'services' => $list]);
}

case 'live': {   // the live numbers of one service from its panel (the list loads them lazily)
    $o = orderOf((int) ($body['id'] ?? 0), $uid);
    if (!$o) fail('سرویس پیدا نشد.', 404);
    $live = !empty($o['panel_url']) ? panelGetUser($o, (string) $o['remark']) : null;
    if ($live) unset($live['links']);
    out(['ok' => true, 'id' => (int) $o['id'], 'live' => $live, 'status_fa' => $live ? statusFa($live['status']) : null]);
}

case 'service': {
    $o = orderOf((int) ($body['id'] ?? 0), $uid);
    if (!$o) fail('سرویس پیدا نشد.', 404);
    $live = !empty($o['panel_url']) ? panelGetUser($o, (string) $o['remark']) : null;
    $stored = json_decode((string) $o['link'], true);
    $links = $live && $live['links'] ? $live['links'] : (is_array($stored) ? array_values(array_filter($stored, 'is_string')) : []);
    if (!$links && $live && $live['sub'] !== '') $links = linksFromSub($live['sub']);
    $plan = orderPlan($o);
    out(['ok' => true, 'service' => ['id' => (int) $o['id'], 'remark' => $o['remark'], 'server' => $o['label'], 'server_id' => (int) $o['server_id'], 'expire' => (int) $o['expire_date'], 'bought' => (int) $o['date'], 'active' => (int) $o['status'] === 1,
        'plan' => $plan ? ['id' => (int) $plan['id'], 'title' => $plan['title'], 'volume' => (float) $plan['volume'], 'days' => (int) $plan['days']] : null,
        'live' => $live ? array_diff_key($live, ['links' => 1]) : null, 'status_fa' => $live ? statusFa($live['status']) : null, 'sub' => on('subLinkState', 'on') ? ($live['sub'] ?? '') : '', 'links' => on('configLinkState', 'on') ? $links : []]]);
}

case 'quote': {   // what renew / more volume / more days cost for one service
    $o = orderOf((int) ($body['id'] ?? 0), $uid);
    if (!$o) fail('سرویس پیدا نشد.', 404);
    $plan = orderPlan($o);
    $renew = null;
    if ($plan && on('renewAccountState', 'on') && (int) $plan['active'] === 1) { [$p] = priceFor($me, $plan); $renew = ['price' => $p, 'volume' => (float) $plan['volume'], 'days' => (int) $plan['days'], 'title' => $plan['title']]; }
    $gbPrice = (int) ($botState['gbPrice'] ?? 0); $dayPrice = (int) ($botState['dayPrice'] ?? 0);
    $volPresets = array_map(fn($r) => ['gb' => (float) $r['volume'], 'price' => (int) $r['price']], rows("SELECT price, volume FROM increase_plan ORDER BY volume"));
    $dayPresets = array_map(fn($r) => ['days' => (int) $r['volume'], 'price' => (int) $r['price']], rows("SELECT price, volume FROM increase_day ORDER BY volume"));
    out(['ok' => true, 'renew' => $renew, 'volume' => on('increaseVolumeState', 'on') && ($gbPrice > 0 || $volPresets) ? ['gb_price' => $gbPrice, 'presets' => $volPresets] : null,
        'days' => on('increaseTimeState', 'on') && ($dayPrice > 0 || $dayPresets) ? ['day_price' => $dayPrice, 'presets' => $dayPresets] : null, 'wallet' => wallet($uid)]);
}

case 'plans': {
    $res = rows("SELECT p.*, s.title AS server, s.flag, s.active AS sactive, s.ucount, c.title AS category FROM server_plans p LEFT JOIN server_info s ON s.id = p.server_id LEFT JOIN server_categories c ON c.id = p.catid WHERE p.active = 1 AND (s.active = 1 OR s.active IS NULL) ORDER BY p.catid, p.server_id, p.price");
    $plans = [];
    foreach ($res as $p) {
        [$price, , $pct] = priceFor($me, $p);
        $soldOut = ((int) $p['inbound_id'] === 0 && (int) ($p['ucount'] ?? 1) <= 0) || ((int) $p['inbound_id'] !== 0 && (int) ($p['acount'] ?? 1) <= 0);
        $plans[] = ['id' => (int) $p['id'], 'title' => $p['title'], 'descr' => trim((string) ($p['descr'] ?? '')), 'price' => $price, 'list_price' => (int) $p['price'], 'discount' => $pct, 'volume' => (float) $p['volume'], 'days' => (int) $p['days'],
            'users' => (int) ($p['limitip'] ?? 0), 'server' => trim(exEmojiStrip((string) ($p['flag'] ?? '')) . ' ' . ($p['server'] ?? '')), 'server_id' => (int) $p['server_id'], 'category' => trim((string) ($p['category'] ?? '')), 'sold_out' => $soldOut];
    }
    out(['ok' => true, 'plans' => $plans, 'sell' => on('sellState', 'on'), 'wallet' => wallet($uid), 'cashback' => (float) ($botState['cashbackPercent'] ?? 0)]);
}

case 'buy': {
    if (!on('sellState', 'on')) fail('فروش موقتاً غیرفعال است.');
    $plan = row("SELECT * FROM server_plans WHERE id = ? AND active = 1", [(int) ($body['plan_id'] ?? 0)]);
    if (!$plan) fail('این پلن در دسترس نیست.', 404);
    $srv = serverOf((int) $plan['server_id']);
    if (!$srv || (int) $srv['active'] !== 1 || empty($srv['panel_url'])) fail('سرور این پلن فعال نیست.');
    if (!in_array(strtolower((string) $srv['type']), ['marzban', 'pasarguard', 'marzneshin'], true)) fail('خرید این پلن فقط از داخل ربات ممکن است.');
    if ((int) $plan['inbound_id'] === 0 && (int) $srv['ucount'] <= 0) fail(mainText('out_of_server_capacity', 'ظرفیت این سرور پر شده است.'));
    if ((int) $plan['inbound_id'] !== 0 && (int) $plan['acount'] <= 0) fail(mainText('out_of_connection_capacity', 'ظرفیت این پلن پر شده است.'));
    [$price, $agentBought] = priceFor($me, $plan);
    $planInfo = json_decode((string) ($plan['custom_sni'] ?? ''), true); if (!is_array($planInfo)) $planInfo = [];
    $days = (int) $plan['days']; $gb = (float) $plan['volume'];

    $r = payWithWallet($uid, $price, ['type' => 'BUY_SUB', 'plan_id' => (int) $plan['id'], 'server_id' => (int) $srv['id'], 'volume' => $gb, 'day' => $days, 'description' => (string) $plan['title'], 'agent_bought' => $agentBought], function (string $hash) use ($uid, $plan, $srv, $planInfo, $gb, $days, $price, $agentBought) {
        $remark = makeRemark((string) $srv['remark'], $uid);
        $made = panelCreateUser($srv, $remark, $gb, $days, $planInfo);
        if (!$made['ok'] && stripos((string) $made['msg'], 'exist') !== false) { $remark .= random_int(1111, 99999); $made = panelCreateUser($srv, $remark, $gb, $days, $planInfo); }
        if (!$made['ok']) { error_log("miniapp buy: panel refused ($hash): " . $made['msg']); return ['ok' => false, 'error' => 'سرور پاسخ نداد؛ مبلغی از کیف پول کم نشد. چند دقیقه دیگر دوباره تلاش کنید.']; }
        $token = strpos($made['sub'], '/sub/') !== false ? explode('/sub/', $made['sub'], 2)[1] : randomId(16);
        $expire = $days > 0 ? time() + 86400 * $days : 0;
        $oid = run("INSERT INTO orders_list (userid, token, transid, fileid, server_id, inbound_id, remark, uuid, protocol, expire_date, link, amount, status, date, notif, rahgozar, agent_bought) VALUES (?, ?, '', ?, ?, ?, ?, '', ?, ?, ?, ?, 1, ?, 0, ?, ?)",
            [$uid, $token, (int) $plan['id'], (int) $srv['id'], (int) $plan['inbound_id'], $remark, (string) ($plan['protocol'] ?: 'vless'), $expire, json_encode($made['links'], JSON_UNESCAPED_SLASHES), $price, (string) time(), (int) ($plan['rahgozar'] ?? 0), $agentBought]);
        if ((int) $plan['inbound_id'] === 0) run("UPDATE server_info SET ucount = ucount - 1 WHERE id = ?", [(int) $srv['id']]);
        else run("UPDATE server_plans SET acount = acount - 1 WHERE id = ?", [(int) $plan['id']]);
        return ['ok' => true, 'order_id' => $oid, 'remark' => $remark, 'sub' => $made['sub'], 'links' => $made['links'], 'expire' => $expire];
    });
    if (!($r['ok'] ?? false)) out(array_merge(['ok' => false], $r));

    $back = cashback($uid, $price);
    $sub = on('subLinkState', 'on') ? $r['sub'] : '';
    tgText($uid, "✅ <b>خرید شما انجام شد</b>\n\n📦 " . h($plan['title']) . "\n🌍 " . h($srv['label']) . "\n🔖 <code>" . h($r['remark']) . "</code>\n⏳ " . ($days > 0 ? $days . " روز" : "نامحدود") . " · 🔋 " . rtrim(rtrim(number_format($gb, 2), '0'), '.') . " گیگ\n💰 " . fa($price) . " تومان از کیف پول" . ($sub !== '' ? "\n\n🔗 لینک اشتراک:\n<code>" . h($sub) . "</code>" : '') . "\n\nجزئیات و کانفیگ‌ها در مینی‌اپ 👇");
    tellAdmins("🛍 <b>خرید جدید (مینی‌اپ)</b>\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>" . ($isAgent ? " · همکار" : '') . "\n📦 " . h($plan['title']) . " · " . h($srv['label']) . "\n🔖 <code>" . h($r['remark']) . "</code>\n💰 " . fa($price) . " تومان (کیف پول)" . ($back ? "\n🎁 کش‌بک: " . fa($back) . " تومان" : '') . "\n💳 موجودی بعد از خرید: " . fa($r['wallet'] + $back) . " تومان");
    out(['ok' => true, 'order_id' => $r['order_id'], 'remark' => $r['remark'], 'wallet' => $r['wallet'] + $back, 'cashback' => $back, 'sub' => $sub, 'links' => on('configLinkState', 'on') ? $r['links'] : [], 'expire' => $r['expire']]);
}

case 'renew': {
    if (!on('renewAccountState', 'on')) fail('تمدید از مینی‌اپ غیرفعال است.');
    $o = orderOf((int) ($body['id'] ?? 0), $uid);
    if (!$o || empty($o['panel_url'])) fail('سرویس پیدا نشد.', 404);
    $plan = orderPlan($o);
    if (!$plan || (int) $plan['active'] !== 1) fail('پلن این سرویس دیگر فعال نیست؛ یک سرویس جدید بخرید.');
    [$price] = priceFor($me, $plan);
    $days = (int) $plan['days']; $gb = (float) $plan['volume'];
    $r = payWithWallet($uid, $price, ['type' => 'BUY_SUB', 'plan_id' => (int) $plan['id'], 'server_id' => (int) $o['server_id'], 'volume' => $gb, 'day' => $days, 'description' => 'تمدید ' . $o['remark'], 'agent_bought' => $isAgent ? 1 : 0], function (string $hash) use ($o, $gb, $days) {
        $live = panelGetUser($o, (string) $o['remark']);
        if (!$live) return ['ok' => false, 'error' => 'سرور پاسخ نداد؛ مبلغی از کیف پول کم نشد.'];
        $from = max(time(), $live['expire'] > 0 ? $live['expire'] : 0);
        $expire = $days > 0 ? $from + 86400 * $days : 0;
        if (!panelModifyUser($o, (string) $o['remark'], ['data_limit' => (int) round($gb * GB), 'expire' => $expire, 'status' => 'active'])) return ['ok' => false, 'error' => 'پنل تغییر را نپذیرفت؛ مبلغی از کیف پول کم نشد.'];
        panelResetUser($o, (string) $o['remark']);
        run("UPDATE orders_list SET expire_date = ?, status = 1, notif = 0 WHERE id = ?", [$expire, (int) $o['id']]);
        return ['ok' => true, 'expire' => $expire];
    });
    if (!($r['ok'] ?? false)) out(array_merge(['ok' => false], $r));
    $back = cashback($uid, $price);
    tgText($uid, "🔄 <b>سرویس تمدید شد</b>\n\n🔖 <code>" . h($o['remark']) . "</code>\n⏳ تا " . ($r['expire'] ? date('Y-m-d', $r['expire']) : 'نامحدود') . " · 🔋 " . rtrim(rtrim(number_format($gb, 2), '0'), '.') . " گیگ\n💰 " . fa($price) . " تومان از کیف پول");
    tellAdmins("🔄 <b>تمدید (مینی‌اپ)</b>\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>\n🔖 <code>" . h($o['remark']) . "</code> · " . h($o['label']) . "\n📦 " . h($plan['title']) . "\n💰 " . fa($price) . " تومان (کیف پول)");
    out(['ok' => true, 'expire' => $r['expire'], 'wallet' => $r['wallet'] + $back, 'cashback' => $back]);
}

case 'addvolume':
case 'adddays': {
    $isVol = $action === 'addvolume';
    if (!on($isVol ? 'increaseVolumeState' : 'increaseTimeState', 'on')) fail('این امکان غیرفعال است.');
    $o = orderOf((int) ($body['id'] ?? 0), $uid);
    if (!$o || empty($o['panel_url'])) fail('سرویس پیدا نشد.', 404);
    $n = (int) ($body['amount'] ?? 0);
    if ($n < 1 || $n > ($isVol ? 2000 : 1000)) fail('مقدار نامعتبر است.');
    $preset = row($isVol ? "SELECT price FROM increase_plan WHERE volume = ? LIMIT 1" : "SELECT price FROM increase_day WHERE volume = ? LIMIT 1", [$n]);
    $unit = (int) ($botState[$isVol ? 'gbPrice' : 'dayPrice'] ?? 0);
    $price = $preset ? (int) $preset['price'] : $unit * $n;
    if ($price <= 0) fail('قیمت این مورد تعریف نشده است.');
    if ($isAgent) { [$p] = priceFor($me, ['id' => 0, 'server_id' => (int) $o['server_id'], 'price' => $price]); $price = $p; }
    $r = payWithWallet($uid, $price, ['type' => $isVol ? 'INCREASE_VOLUME' : 'INCREASE_TIME', 'plan_id' => (int) $o['fileid'], 'server_id' => (int) $o['server_id'], 'volume' => $isVol ? $n : 0, 'day' => $isVol ? 0 : $n, 'description' => $o['remark'], 'agent_bought' => $isAgent ? 1 : 0], function (string $hash) use ($o, $n, $isVol, $uid, $price) {
        $live = panelGetUser($o, (string) $o['remark']);
        if (!$live) return ['ok' => false, 'error' => 'سرور پاسخ نداد؛ مبلغی از کیف پول کم نشد.'];
        $fields = ['status' => 'active'];
        if ($isVol) $fields['data_limit'] = $live['limit'] > 0 ? $live['limit'] + $n * GB : 0;
        else $fields['expire'] = ($live['expire'] > 0 ? max(time(), $live['expire']) : time()) + 86400 * $n;
        if (!panelModifyUser($o, (string) $o['remark'], $fields)) return ['ok' => false, 'error' => 'پنل تغییر را نپذیرفت؛ مبلغی از کیف پول کم نشد.'];
        if (!$isVol) run("UPDATE orders_list SET expire_date = ?, notif = 0 WHERE id = ?", [$fields['expire'], (int) $o['id']]);
        run("INSERT INTO increase_order (userid, server_id, inbound_id, remark, volume, day, amount, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [$uid, (int) $o['server_id'], (int) $o['inbound_id'], $o['remark'], $isVol ? $n : 0, $isVol ? 0 : $n, $price, (string) time()]);
        return ['ok' => true, 'expire' => $fields['expire'] ?? (int) $o['expire_date'], 'limit' => $fields['data_limit'] ?? $live['limit']];
    });
    if (!($r['ok'] ?? false)) out(array_merge(['ok' => false], $r));
    $what = $isVol ? "$n گیگ حجم" : "$n روز";
    tgText($uid, "➕ <b>$what به سرویس شما اضافه شد</b>\n\n🔖 <code>" . h($o['remark']) . "</code>\n💰 " . fa($price) . " تومان از کیف پول");
    tellAdmins("➕ <b>افزایش $what (مینی‌اپ)</b>\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>\n🔖 <code>" . h($o['remark']) . "</code> · " . h($o['label']) . "\n💰 " . fa($price) . " تومان (کیف پول)");
    out(['ok' => true, 'expire' => $r['expire'], 'limit' => $r['limit'], 'wallet' => $r['wallet']]);
}

case 'topup': {   // card to card: a pending INCREASE_WALLET like the bot's startWalletPayment, plus the card details
    if (!on('walletState', 'on')) fail('کیف پول غیرفعال است.');
    if (!on('cartToCartState')) fail('پرداخت کارت‌به‌کارت فعال نیست؛ از داخل ربات شارژ کنید.');
    $amount = (int) ($body['amount'] ?? 0);
    if ($amount < MIN_TOPUP || $amount > MAX_TOPUP) fail('مبلغ باید بین ' . fa(MIN_TOPUP) . ' و ' . fa(MAX_TOPUP) . ' تومان باشد.');
    $waiting = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND type = 'INCREASE_WALLET' AND state = 'have_sent'", [$uid])['n'] ?? 0);
    if ($waiting >= 3) fail('سه رسید شما هنوز در انتظار بررسی است؛ لطفاً تا بررسی آن‌ها صبر کنید.');
    run("DELETE FROM pays WHERE user_id = ? AND type = 'INCREASE_WALLET' AND state = 'pending'", [$uid]);
    $hash = randomId(9);
    run("INSERT INTO pays (hash_id, user_id, type, plan_id, volume, day, price, request_date, state, agent_bought, agent_count) VALUES (?, ?, 'INCREASE_WALLET', 0, 0, 0, ?, ?, 'pending', 0, 0)", [$hash, $uid, $amount, time()]);

    // the card details (the bot's buildCartToCartInfoMessage: new users without history get the gift-card / USDT text)
    $keys = settingJson('PAYMENT_KEYS');
    do { $last3 = random_int(100, 999); } while ($last3 % 10 === 0);
    $exact = $amount + $last3;
    $approved = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND state = 'approved'", [$uid])['n'] ?? 0);
    $cutoff = strtotime('2026-03-01 00:00:00') ?: 1772323200;
    $approvedBefore = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND state = 'approved' AND request_date < ?", [$uid, $cutoff])['n'] ?? 0);
    $joined = (int) ($me['date'] ?? 0);
    $trusted = $isAgent || ($joined > 0 && $joined < $cutoff && ($approvedBefore >= 1 || $approved >= 2)) || ($joined > 0 && $joined <= time() - 10 * 86400 && $approved >= 3);
    $type = $amount >= 12000000 ? 'usdt' : ($trusted && !empty($keys['bankAccount']) ? 'detail' : 'gift');
    $usdRate = (float) ($type === 'usdt' || $type === 'gift' ? ($isAgent && !empty($USDTPriceagent) ? $USDTPriceagent : ($USDTPrice ?? 0)) : 0);
    $usdt = $usdRate > 0 ? round($exact / $usdRate, 2) : 0;
    $tpl = mainText($type === 'detail' ? 'brw_cart_to_cart_detail' : 'brw_cart_to_cart_usdt');
    $text = strtr($tpl, ['PRICE-TOMAN-ROUND' => fa($amount), 'ACCOUNT-NUMBER' => (string) ($keys['bankAccount'] ?? '---'), 'HOLDER-NAME' => (string) ($keys['holderName'] ?? '---'), 'PRICE-TOMAN' => fa($exact), 'PRICE-RIAL' => fa($exact * 10), 'PRICE-USDT' => number_format($usdt, 2, '.', ''), 'USDT-WALLET' => (string) ($keys['tronwallet'] ?? '---')]);
    out(['ok' => true, 'hash' => $hash, 'amount' => $amount, 'exact' => $exact, 'kind' => $type, 'card' => $type === 'detail' ? (string) ($keys['bankAccount'] ?? '') : '', 'holder' => $type === 'detail' ? (string) ($keys['holderName'] ?? '') : '',
        'usdt_wallet' => $type !== 'detail' ? (string) ($keys['tronwallet'] ?? '') : '', 'usdt' => $usdt, 'text' => safeHtml($text), 'expires_in' => 3600]);
}

case 'cancel_topup': {
    run("DELETE FROM pays WHERE user_id = ? AND hash_id = ? AND type = 'INCREASE_WALLET' AND state = 'pending'", [$uid, (string) ($body['hash'] ?? '')]);
    out(['ok' => true]);
}

case 'receipt': {   // the receipt picture goes to the admins exactly like from the bot: approve / decline buttons work there
    $hash = (string) ($body['hash'] ?? '');
    $pay = row("SELECT * FROM pays WHERE hash_id = ? AND user_id = ? AND type = 'INCREASE_WALLET'", [$hash, $uid]);
    if (!$pay) fail('درخواست شارژ پیدا نشد؛ دوباره مبلغ را انتخاب کنید.', 404);
    if ($pay['state'] === 'have_sent') fail('رسید این درخواست قبلاً ارسال شده و در انتظار بررسی است.');
    if ($pay['state'] !== 'pending') fail('این درخواست دیگر فعال نیست.');
    $f = $_FILES['photo'] ?? null;
    if (!$f || ($f['error'] ?? 1) !== UPLOAD_ERR_OK) fail('تصویر رسید دریافت نشد.');
    if ((int) $f['size'] > RECEIPT_MAX_BYTES) fail('حجم تصویر بیشتر از ۶ مگابایت است.');
    $mime = (string) (mime_content_type($f['tmp_name']) ?: '');
    if (!in_array($mime, ['image/jpeg', 'image/png', 'image/webp'], true)) fail('فقط تصویر (JPG / PNG) پذیرفته می‌شود.');
    $ext = ['image/jpeg' => 'jpg', 'image/png' => 'png', 'image/webp' => 'webp'][$mime];

    [$mainValues, $buttonValues] = botTexts();
    $caption = str_replace(['PRICE', 'USERNAME', 'NAME', 'USER-ID'], [fa((int) $pay['price']), (string) $me['username'], (string) $me['name'], (string) $uid], (string) ($mainValues['increase_wallet_request_message'] ?? "💳 درخواست افزایش موجودی\n\n💰 مبلغ: PRICE تومان\n👤 NAME (@USERNAME)\n🆔 USER-ID"));
    $caption .= "\n\n📲 ارسال از مینی‌اپ";
    $approvedCount = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND state = 'approved'", [$uid])['n'] ?? 0);
    $declinedCount = (int) (row("SELECT COUNT(*) AS n FROM pays WHERE user_id = ? AND state = 'declined'", [$uid])['n'] ?? 0);
    $approvedSum = (int) (row("SELECT COALESCE(SUM(price), 0) AS s FROM pays WHERE user_id = ? AND state = 'approved'", [$uid])['s'] ?? 0);
    $kb = json_encode(['inline_keyboard' => [
        [['text' => buttonText('decline', '❌ عدم تایید'), 'callback_data' => "declineAction_$hash"], ['text' => buttonText('approve', '✅ تایید'), 'callback_data' => "approvePayment$hash"]],
        [['text' => $approvedCount . ' عدد', 'callback_data' => "noop_okc_$hash"], ['text' => 'پرداخت موفق', 'callback_data' => "noop_ok_$hash"]],
        [['text' => $declinedCount . ' عدد', 'callback_data' => "noop_badc_$hash"], ['text' => 'پرداخت رد شده', 'callback_data' => "noop_bad_$hash"]],
        [['text' => fa($approvedSum) . ' تومان', 'callback_data' => "noop_sumv_$hash"], ['text' => 'مجموع', 'callback_data' => "noop_sum_$hash"]],
        [['text' => "🆔 $hash", 'callback_data' => "noop_$hash"]]]], JSON_UNESCAPED_UNICODE);

    $first = tg('sendPhoto', ['chat_id' => $admin, 'photo' => new CURLFile($f['tmp_name'], $mime, "receipt.$ext"), 'caption' => $caption, 'parse_mode' => 'HTML', 'reply_markup' => $kb], true);
    $msgId = $first['result']['message_id'] ?? null;
    if (!$msgId) { error_log('miniapp receipt: sendPhoto failed: ' . json_encode($first)); fail('ارسال رسید به پشتیبانی انجام نشد؛ دوباره تلاش کنید.', 502); }
    $fileId = '';
    foreach ((array) ($first['result']['photo'] ?? []) as $ph) $fileId = (string) ($ph['file_id'] ?? $fileId);
    $others = [];
    foreach ($admins as $a) {
        if ($a === (int) $admin || $fileId === '') continue;
        $r = tg('sendPhoto', ['chat_id' => $a, 'photo' => $fileId, 'caption' => $caption, 'parse_mode' => 'HTML', 'reply_markup' => $kb]);
        if (isset($r['result']['message_id'])) $others[] = ['chat_id' => $a, 'message_id' => $r['result']['message_id']];
    }
    run("UPDATE pays SET state = 'have_sent', message_id = ?, chat_id = ?, admins_messages = ? WHERE hash_id = ?", [$msgId, (string) $admin, $others ? json_encode($others, JSON_UNESCAPED_UNICODE) : null, $hash]);
    tgText($uid, (string) ($mainValues['order_submitted'] ?? '✅ رسید شما ثبت شد و پس از بررسی، کیف پول شارژ می‌شود.'));
    out(['ok' => true, 'state' => 'have_sent']);
}

case 'history': {
    $pays = rows("SELECT hash_id, type, price, state, request_date, description FROM pays WHERE user_id = ? ORDER BY id DESC LIMIT 40", [$uid]);
    $list = [];
    $typeFa = ['INCREASE_WALLET' => 'شارژ کیف پول', 'BUY_SUB' => 'خرید سرویس', 'INCREASE_VOLUME' => 'افزایش حجم', 'INCREASE_TIME' => 'افزایش زمان', 'RENEW_SUB' => 'تمدید'];
    $stateFa = ['pending' => 'منتظر رسید', 'have_sent' => 'در انتظار تأیید', 'approved' => 'تأیید شد', 'auto_cart_approved' => 'تأیید شد', 'paid' => 'پرداخت شد', 'paid_with_wallet' => 'از کیف پول', 'declined' => 'رد شد', 'low_wallet' => 'موجودی کم', 'expired_have_sent' => 'منقضی', 'expired_pending' => 'منقضی', 'processing_wallet' => 'در حال پردازش'];
    foreach ($pays as $p) $list[] = ['hash' => $p['hash_id'], 'type' => $typeFa[$p['type']] ?? $p['type'], 'kind' => $p['type'], 'price' => (int) $p['price'], 'state' => $p['state'], 'state_fa' => $stateFa[$p['state']] ?? $p['state'], 'at' => (int) $p['request_date'], 'note' => (string) ($p['description'] ?? '')];
    $cb = [];
    try { $cb = rows("SELECT order_amount, cashback, at FROM cashback_log WHERE userid = ? ORDER BY id DESC LIMIT 20", [$uid]); } catch (Throwable $e) {}
    out(['ok' => true, 'pays' => $list, 'cashback' => array_map(fn($r) => ['order' => (int) $r['order_amount'], 'back' => (int) $r['cashback'], 'at' => (int) $r['at']], $cb), 'wallet' => wallet($uid)]);
}

case 'gift': {
    if (!on('giftState')) fail('کد هدیه فعال نیست.');
    $code = trim((string) ($body['code'] ?? ''));
    if ($code === '' || mb_strlen($code) > 64) fail('کد را وارد کن.');
    $g = row("SELECT * FROM gift_codes WHERE code = ?", [$code]);
    if (!$g) fail('این کد هدیه وجود ندارد.');
    if ((int) $g['expires'] > 0 && (int) $g['expires'] < time()) fail('مهلت این کد تمام شده است.');
    if ((int) $g['used'] >= (int) $g['max_uses']) fail('این کد قبلاً استفاده شده است.');
    $by = json_decode((string) $g['used_by'], true) ?: [];
    if (in_array((string) $uid, array_map('strval', $by), true)) fail('شما قبلاً این کد را استفاده کرده‌اید.');
    $by[] = (string) $uid;
    run("UPDATE gift_codes SET used = used + 1, used_by = ? WHERE id = ?", [json_encode($by), (int) $g['id']]);
    run("UPDATE users SET wallet = COALESCE(wallet, 0) + ? WHERE userid = ?", [(int) $g['amount'], $uid]);
    out(['ok' => true, 'amount' => (int) $g['amount'], 'wallet' => wallet($uid)]);
}

case 'wheel': {
    if (!on('wheelState')) fail('گردونه فعال نیست.');
    db()->query("CREATE TABLE IF NOT EXISTS wheel_log (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, userid BIGINT NOT NULL, prize BIGINT NOT NULL DEFAULT 0, at BIGINT NOT NULL, KEY u (userid)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    $hours = max(1, (int) ($botState['wheelHours'] ?? 24));
    $last = (int) (row("SELECT at FROM wheel_log WHERE userid = ? ORDER BY id DESC LIMIT 1", [$uid])['at'] ?? 0);
    $left = $last + $hours * 3600 - time();
    $prizes = array_values(array_filter(array_map('intval', explode(',', (string) ($botState['wheelPrizes'] ?? '0,0,0,1000,2000,5000'))), fn($v) => $v >= 0)) ?: [0];
    if (!empty($body['check'])) out(['ok' => true, 'left' => max(0, $left), 'hours' => $hours, 'prizes' => $prizes]);
    if ($left > 0) fail('گردونه بعدی ' . intdiv($left, 3600) . ' ساعت و ' . intdiv($left % 3600, 60) . ' دقیقه دیگر.');
    $i = random_int(0, count($prizes) - 1); $prize = $prizes[$i];
    run("INSERT INTO wheel_log (userid, prize, at) VALUES (?, ?, ?)", [$uid, $prize, time()]);
    if ($prize > 0) run("UPDATE users SET wallet = COALESCE(wallet, 0) + ? WHERE userid = ?", [$prize, $uid]);
    out(['ok' => true, 'prize' => $prize, 'index' => $i, 'prizes' => $prizes, 'wallet' => wallet($uid), 'hours' => $hours]);
}

case 'referral': {
    $n = (int) (row("SELECT COUNT(*) AS n FROM users WHERE refered_by = ?", [$uid])['n'] ?? 0);
    $buyers = (int) (row("SELECT COUNT(DISTINCT o.userid) AS n FROM orders_list o JOIN users u ON u.userid = o.userid WHERE u.refered_by = ?", [$uid])['n'] ?? 0);
    out(['ok' => true, 'link' => 'https://t.me/' . ($botUsername ?? '') . '?start=' . $uid, 'invited' => $n, 'buyers' => $buyers, 'reward' => (int) settingValue('INVITE_BANNER_AMOUNT'), 'text' => safeHtml(settingValue('INVITE_BANNER_TEXT'))]);
}

case 'apps': {
    $apps = rows("SELECT title, link FROM needed_sofwares WHERE status = 1 ORDER BY id");
    out(['ok' => true, 'apps' => array_map(fn($a) => ['title' => exEmojiStrip((string) $a['title']), 'link' => $a['link']], $apps)]);
}

case 'tickets': {
    $res = rows("SELECT id, title, category, state, create_date FROM chats WHERE user_id = ? ORDER BY id DESC LIMIT 30", [$uid]);
    $t = [];
    foreach ($res as $r) {
        $last = row("SELECT text, msg_type, sent_date FROM chats_info WHERE chat_id = ? ORDER BY id DESC LIMIT 1", [(int) $r['id']]);
        $lt = $last ? (string) $last['text'] : '';
        $j = json_decode($lt, true); if (is_array($j)) $lt = (string) ($j['caption'] ?? '📷 تصویر');
        $t[] = ['id' => (int) $r['id'], 'title' => $r['title'], 'category' => $r['category'], 'state' => (int) $r['state'], 'created' => (int) $r['create_date'], 'last' => $last ? ['text' => mb_substr($lt, 0, 160), 'from' => $last['msg_type'], 'at' => (int) $last['sent_date']] : null];
    }
    $cats = array_values(array_filter(array_map(fn($c) => is_string($c) ? $c : (string) ($c['title'] ?? ''), settingJson('TICKETS_CATEGORY'))));
    out(['ok' => true, 'tickets' => $t, 'categories' => $cats]);
}

case 'ticket_messages': {
    $id = (int) ($body['id'] ?? 0);
    if (!row("SELECT id FROM chats WHERE id = ? AND user_id = ?", [$id, $uid])) fail('تیکت پیدا نشد.', 404);
    $m = [];
    foreach (rows("SELECT text, msg_type, sent_date FROM chats_info WHERE chat_id = ? ORDER BY id ASC LIMIT 200", [$id]) as $r) {
        $j = json_decode((string) $r['text'], true);
        $m[] = ['text' => is_array($j) ? (string) ($j['caption'] ?? '📷 تصویر') : $r['text'], 'from' => $r['msg_type'], 'at' => (int) $r['sent_date']];
    }
    out(['ok' => true, 'messages' => $m]);
}

case 'ticket_new': {
    $title = mb_substr(trim((string) ($body['title'] ?? '')), 0, 80);
    $text = mb_substr(trim((string) ($body['text'] ?? '')), 0, 2000);
    $cat = mb_substr(trim((string) ($body['category'] ?? '')), 0, 60) ?: 'مینی‌اپ';
    if ($title === '' || $text === '') fail('عنوان و متن را بنویس.');
    $open = (int) (row("SELECT COUNT(*) AS n FROM chats WHERE user_id = ? AND state <> 2", [$uid])['n'] ?? 0);
    if ($open >= 5) fail('پنج تیکت باز داری؛ تا پاسخ آن‌ها صبر کن.');
    $t = time();
    $cid = run("INSERT INTO chats (user_id, create_date, title, category, state, rate) VALUES (?, ?, ?, ?, 0, 0)", [$uid, $t, $title, $cat]);
    run("INSERT INTO chats_info (chat_id, sent_date, msg_type, text) VALUES (?, ?, 'USER', ?)", [$cid, $t, $text]);
    $msg = "🎫 <b>تیکت جدید (مینی‌اپ)</b>\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>\n📂 " . h($cat) . "\n📝 <b>" . h($title) . "</b>\n\n" . h($text);
    foreach ($admins as $a) tgText($a, $msg, ['inline_keyboard' => [[['text' => 'پاسخ', 'callback_data' => "reply_$cid"]]]]);
    out(['ok' => true, 'id' => $cid]);
}

case 'ticket_reply': {
    $id = (int) ($body['id'] ?? 0); $text = mb_substr(trim((string) ($body['text'] ?? '')), 0, 2000);
    $c = row("SELECT id, title, state FROM chats WHERE id = ? AND user_id = ?", [$id, $uid]);
    if (!$c) fail('تیکت پیدا نشد.', 404);
    if ((int) $c['state'] === 2) fail('این تیکت بسته شده است؛ تیکت جدید بزن.');
    if ($text === '') fail('متن خالی است.');
    run("INSERT INTO chats_info (chat_id, sent_date, msg_type, text) VALUES (?, ?, 'USER', ?)", [$id, time(), $text]);
    run("UPDATE chats SET state = 0 WHERE id = ?", [$id]);
    $msg = "💬 <b>پیام جدید در تیکت</b> [" . h($c['title']) . "]\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>\n\n" . h($text);
    foreach ($admins as $a) tgText($a, $msg, ['inline_keyboard' => [[['text' => 'پاسخ', 'callback_data' => "reply_$id"]]]]);
    out(['ok' => true]);
}

case 'report': {
    if (!on('reportState')) fail('گزارش اختلال فعال نیست.');
    $id = (int) ($body['id'] ?? 0); $text = mb_substr(trim((string) ($body['text'] ?? '')), 0, 500);
    $o = orderOf($id, $uid);
    if (!$o) fail('سرویس پیدا نشد.', 404);
    db()->query("CREATE TABLE IF NOT EXISTS outage_reports (id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, userid BIGINT NOT NULL, order_id INT NOT NULL, remark VARCHAR(190) NULL, text TEXT NULL, at BIGINT NOT NULL, state VARCHAR(20) NOT NULL DEFAULT 'open') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    $recent = (int) (row("SELECT COUNT(*) AS n FROM outage_reports WHERE userid = ? AND at > ?", [$uid, time() - 3600])['n'] ?? 0);
    if ($recent >= 3) fail('در یک ساعت گذشته سه گزارش فرستاده‌ای؛ پشتیبانی در حال بررسی است.');
    run("INSERT INTO outage_reports (userid, order_id, remark, text, at) VALUES (?, ?, ?, ?, ?)", [$uid, $id, $o['remark'], $text, time()]);
    tellAdmins("🛠 <b>گزارش اختلال سرویس (مینی‌اپ)</b>\n\n👤 " . userLink($uid, (string) $me['name']) . " · <code>$uid</code>\n🔖 <code>" . h($o['remark']) . "</code> · " . h($o['label']) . "\n\n" . h($text !== '' ? $text : '(بدون توضیح)'));
    out(['ok' => true]);
}

default:
    fail('unknown action', 404);
}
} catch (Throwable $e) {
    error_log("miniapp $action: " . get_class($e) . ': ' . $e->getMessage() . ' @ ' . $e->getFile() . ':' . $e->getLine());
    fail('خطای داخلی؛ چند لحظه بعد دوباره تلاش کنید.', 500);
}
