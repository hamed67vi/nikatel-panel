<?php
/* The "show configs" button of the shop bot (the owner, 2026-10-27, translated from Persian: "when the subscription is sent to the user in the bot, send ONLY the subscription link - but add a 'show configs' button to it").
   The bot's own switch BOT_STATES configLinkState = "off" already keeps the configs out of the texts; this file adds what was missing - a way for the customer to ask for them:
     cfgBtnKeys()    the keyboard of the "your new subscription" message (buildLinksAndSendQr) gets one more row: the button
     cfgBtnQr()      ... and its QR code is the subscription link, never a config (the old code drew a config's QR when the service had three links or fewer)
     cfgBtnFlat()    the buttons of the service page ("my subscriptions" -> one service) get the button
     cfgBtnHandle()  the tap: the customer's own service only; the configs are asked from the panel live (a host moved in the panel, the stored list is old), the stored list is the fallback; sent in as many messages as they need
   Nothing here draws a button when configLinkState is not "off" (the configs are in the text then). BOT_STATES cfgButton = "off" switches the button off. Every hook asks function_exists() first and every failure is swallowed. */

const CFGBTN_TEXT = '📋 نمایش کانفیگ‌ها';

function cfgBtnState($botState = null): array {
    if (!is_array($botState)) $botState = $GLOBALS['botState'] ?? [];
    return is_array($botState) ? $botState : [];
}

/** the configs are hidden from the texts (the owner's switch) and the button is not switched off */
function cfgBtnWanted($botState = null): bool {
    $s = cfgBtnState($botState);
    return ($s['configLinkState'] ?? '') === 'off' && ($s['cfgButton'] ?? '') !== 'off';
}

/** a list of config strings out of a json text / an array / an object: whatever is not a non-empty string is dropped */
function cfgBtnLinks($v): array {
    if (is_string($v)) $v = json_decode($v, true);
    if (is_object($v)) $v = (array) $v;
    if (!is_array($v)) return [];
    $out = [];
    foreach ($v as $l) if (is_string($l) && trim($l) !== '') $out[] = trim($l);
    return $out;
}

/** the hash that stands for a token inside a callback (64 bytes at most; a Marzban token is longer than that) */
function cfgBtnHash(string $token): string {
    return substr(md5($token), 0, 12);
}

/** the inline keyboard (json) of the new-subscription message with the button as its first row */
function cfgBtnKeys($keys, $token, $botState = null) {
    try {
        $token = (string) $token;
        if ($token === '' || !cfgBtnWanted($botState)) return $keys;
        $kb = json_decode((string) $keys, true);
        if (!is_array($kb) || !isset($kb['inline_keyboard']) || !is_array($kb['inline_keyboard'])) return $keys;
        array_unshift($kb['inline_keyboard'], [['text' => CFGBTN_TEXT, 'callback_data' => 'showCfgT' . cfgBtnHash($token)]]);
        return json_encode($kb, JSON_UNESCAPED_UNICODE);
    } catch (Throwable $e) {
        return $keys;
    }
}

/** what the QR code of the new-subscription message holds: the subscription link while the configs are hidden */
function cfgBtnQr($qrLink, $mainSubLink, $botState = null) {
    $sub = (string) $mainSubLink;
    return ($sub !== '' && cfgBtnWanted($botState)) ? $sub : $qrLink;
}

/** the button list of one service page */
function cfgBtnFlat($flat, $orderId, $botState = null) {
    try {
        if (!is_array($flat) || (int) $orderId <= 0 || !cfgBtnWanted($botState)) return $flat;
        $flat[] = ['text' => CFGBTN_TEXT, 'callback_data' => 'showCfgO' . (int) $orderId];
        return $flat;
    } catch (Throwable $e) {
        return $flat;
    }
}

/** the bot's update is a tap on the button: true = handled (the caller exits) */
function cfgBtnHandle(): bool {
    global $data, $from_id;
    if (!is_string($data ?? null) || !preg_match('/^showCfg(?:T([0-9a-f]{12})|O([0-9]{1,12}))$/', $data, $m)) return false;
    $uid = (int) ($from_id ?? 0);
    try {
        if (function_exists('bot') && !empty($GLOBALS['callbackId'])) bot('answercallbackquery', ['callback_query_id' => $GLOBALS['callbackId']]);      // (the spinner stops now; the answer comes as messages)
        if ($uid > 0) cfgBtnShow($uid, (string) ($m[1] ?? ''), (int) ($m[2] ?? 0));
    } catch (Throwable $e) {
        error_log('configs-button: ' . $e->getMessage());
        try { sendMessage('خطایی پیش آمد؛ کمی بعد دوباره امتحان کنید.', null, 'HTML', $uid); } catch (Throwable $e2) { }
    }
    return true;
}

/** the customer's own service by the hash of its token or by its id; null = not his / not there */
function cfgBtnOrder(int $uid, string $hash, int $oid): ?array {
    global $connection;
    if ($hash !== '') {
        $stmt = $connection->prepare("SELECT * FROM `orders_list` WHERE `userid`=? AND `status`=1 AND `token` IS NOT NULL AND LEFT(MD5(`token`),12)=? ORDER BY `id` DESC LIMIT 1");
        $stmt->bind_param('is', $uid, $hash);
    } else {
        $stmt = $connection->prepare("SELECT * FROM `orders_list` WHERE `userid`=? AND `id`=? LIMIT 1");
        $stmt->bind_param('ii', $uid, $oid);
    }
    $stmt->execute();
    $row = $stmt->get_result()->fetch_assoc();
    $stmt->close();
    return $row ?: null;
}

function cfgBtnShow(int $uid, string $hash, int $oid): void {
    global $buttonValues;
    $f = sys_get_temp_dir() . '/cfgbtn_' . $uid;      // (one tap every three seconds: each one asks the panel)
    $last = (int) @file_get_contents($f);
    if ($last > time() - 3 && $last <= time()) return;
    @file_put_contents($f, (string) time());

    $order = cfgBtnOrder($uid, $hash, $oid);
    if (!$order) { sendMessage('سرویس پیدا نشد.', null, 'HTML', $uid); return; }

    $links = [];
    $server = function_exists('getServerConfigById') ? getServerConfigById((int) $order['server_id']) : null;
    if (is_array($server) && ($server['type'] ?? '') === 'marzban' && function_exists('getMarzbanUserInfo')) {
        $info = @getMarzbanUserInfo((int) $order['server_id'], (string) $order['remark']);
        $links = cfgBtnLinks(is_object($info) ? ($info->links ?? null) : null);
    }
    if (!$links) $links = cfgBtnLinks($order['link'] ?? '');
    $links = array_slice($links, 0, 60);
    $back = json_encode(['inline_keyboard' => [[['text' => $buttonValues['back_button'] ?? '🔙 بازگشت', 'callback_data' => 'orderDetails' . (int) $order['id']]]]], JSON_UNESCAPED_UNICODE);
    if (!$links) { sendMessage("کانفیگی برای نمایش پیدا نشد؛ از لینک اشتراک استفاده کنید.", $back, 'HTML', $uid); return; }

    $head = "📋 <b>کانفیگ‌های سرویس</b> <code>" . htmlspecialchars((string) $order['remark'], ENT_NOQUOTES, 'UTF-8') . "</code>\n\n";
    $foot = "💡 بهتر است سرویس را با <b>لینک اشتراک</b> وارد کنید تا همه‌ی کانفیگ‌ها خودکار به‌روز بمانند.";
    $parts = [];
    $buf = $head;
    foreach ($links as $l) {
        $piece = "<code>" . htmlspecialchars($l, ENT_NOQUOTES, 'UTF-8') . "</code>\n\n";
        if (strlen($buf) + strlen($piece) > 3500 && $buf !== $head) { $parts[] = $buf; $buf = ''; }
        $buf .= $piece;
    }
    $parts[] = $buf;
    $last = count($parts) - 1;
    foreach ($parts as $i => $p) {
        sendMessage($i === $last ? $p . $foot : $p, $i === $last ? $back : null, 'HTML', $uid);
    }
}
