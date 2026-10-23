<?php
/**
 * A short message to the agent when the admin raises his discount (installed beside bot.php by scripts/install-agent-discount-notice.sh).
 *
 * The owner (2026-10-24, translated from Persian): "a short message must go to the user when we add a discount for him".
 * The admin's menu of an agent (the bot's own "editAgentDiscount" flow: general discount, a server's discount, a plan's discount) saves the new value and shows the agent's discount screen; this file adds
 * one thing after that: when the NEW discount is higher than what the agent had, the agent gets one short message. A lowered or unchanged discount sends nothing.
 * For a server / plan discount the "old" value is the special value he had, or the general discount when there was none (that is what he paid before).
 * BOT_STATES agentDiscountMsg = "off" switches the message off (any other value / not set = on). Nothing here can stop the admin's flow: every failure is swallowed.
 */

/** the discount an agent gets in this place: the general one, or the special one of a server / a plan (which falls back to the general one); $json is the discount_percent JSON text or the decoded array */
function agDiscountValue($json, string $kind, string $key): float
{
    $d = is_array($json) ? $json : json_decode((string) $json, true);
    if (!is_array($d)) return 0.0;
    $v = $kind === 'Server' ? ($d['servers'][$key] ?? null) : ($kind === 'Plan' ? ($d['plans'][$key] ?? null) : null);
    return (float) ($v ?? $d['normal'] ?? 0);
}

/** "20" instead of "20.0" */
function agDiscountNumber(float $n): string
{
    return rtrim(rtrim(number_format($n, 2, '.', ''), '0'), '.');
}

/** the text of the message; $label is the title of the server / plan for a special discount, '' for the general one */
function agDiscountText(string $kind, string $label, float $new): string
{
    $n = agDiscountNumber($new);
    if ($kind === 'Server' && $label !== '') return "🎁 تخفیف همکاری شما برای سرور <b>" . htmlspecialchars($label) . "</b> به <b>$n درصد</b> افزایش پیدا کرد و از همین الان اعمال می‌شود.";
    if ($kind === 'Plan' && $label !== '') return "🎁 تخفیف همکاری شما برای پلن <b>" . htmlspecialchars($label) . "</b> به <b>$n درصد</b> افزایش پیدا کرد و از همین الان اعمال می‌شود.";
    return "🎁 تخفیف همکاری شما به <b>$n درصد</b> افزایش پیدا کرد و از همین الان روی خریدهای شما اعمال می‌شود.";
}

/** the title of a server / plan (for the message); '' when it is not found */
function agDiscountLabel($connection, string $kind, string $key): string
{
    if (($kind !== 'Server' && $kind !== 'Plan') || !ctype_digit($key)) return '';
    $id = (int) $key;
    $st = $connection->prepare($kind === 'Server' ? "SELECT `title` FROM `server_info` WHERE `id` = ?" : "SELECT `title` FROM `server_plans` WHERE `id` = ?");
    $st->bind_param("i", $id);
    $st->execute();
    $r = $st->get_result()->fetch_assoc();
    $st->close();
    return is_array($r) ? trim((string) ($r['title'] ?? '')) : '';
}

/**
 * called by bot.php right after the admin saved a discount of an agent: $kind = Normal | Server | Plan, $agentId, $key (server / plan id, or whatever follows in the callback for Normal),
 * $oldJson = the agent's discount_percent as it was, $newArr = the decoded new discount. true = a message was sent.
 */
function agDiscountNotice($kind, $agentId, $key, $oldJson, $newArr): bool
{
    global $botState, $connection;
    try {
        if ((string) (((array) $botState)['agentDiscountMsg'] ?? 'on') === 'off') return false;
        $kind = (string) $kind;
        if (!in_array($kind, ['Normal', 'Server', 'Plan'], true) || !ctype_digit((string) $agentId)) return false;
        $key = (string) $key;
        $old = agDiscountValue($oldJson, $kind, $key);
        $new = agDiscountValue($newArr, $kind, $key);
        if (!($new > $old)) return false;
        sendMessage(agDiscountText($kind, agDiscountLabel($connection, $kind, $key), $new), null, "HTML", (int) $agentId);
    } catch (Throwable $e) {
        return false;
    }
    return true;
}
