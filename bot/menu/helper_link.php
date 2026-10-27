<?php
/* The helper subscription link ("لینک کمکی اشتراک": the same token on the server's other domains, server_config.sub_links) shows in the Mini App ONLY (the owner, 2026-10-27, translated from Persian:
   "turn its display off in the bot - let it be active only in this Mini App - and keep the Mini App's and the bot's settings in step so no bug comes of it").
   One switch for both sides, BOT_STATES helperLink:  "mini" (also when unset: the Mini App shows it, the bot does not), "both" (as before this file), "off" (nobody).
   The bot's texts are written in several places (the new-subscription message, the service page, the two "your link changed / was updated" messages), so the cut is made where every message passes,
   in bot() of config.php: hlOutgoing() takes the helper-link block and the sentences that send the customer to it out of the text / caption of a message that is about to be sent - nothing else is touched,
   a message without the word is not even looked at. The Mini App reads the same switch (subAlt() in its api.php). The admin's own menu for the helper domains ("لینک اشتراک کمکی") is not a message to a customer and stays. */

/** "mini" | "both" | "off" (anything else, and nothing set, is "mini") */
function hlStage($botState = null): string {
    if (!is_array($botState)) $botState = $GLOBALS['botState'] ?? [];
    $m = is_array($botState) ? (string) ($botState['helperLink'] ?? 'mini') : 'mini';
    return ($m === 'both' || $m === 'off') ? $m : 'mini';
}

/** the text of a message without the helper link (a text that does not name it comes back as it was) */
function hlStrip(string $t): string {
    if (strpos($t, 'کمکی') === false) return $t;
    $o = $t;
    // the block: its header and every <code> line that follows (the new-subscription message has it after a blank line, the service page after one line break)
    $t = preg_replace('~\n{1,2}(?:🌐|🔁) لینک کمکی اشتراک:\n(?:<code>[^<]*</code>(?:\n\n(?=<code>))?)+~u', '', $t) ?? $t;
    // the sentences that send the customer to it (the first one is a line of the bot's source, so its line end may be \r\n)
    $t = preg_replace('~\r?\n⚠️ در صورتی که لینک اصلی اشتراک کار نکرد از لینک کمکی اشتراک استفاده کنید\.~u', '', $t) ?? $t;
    $t = str_replace(
        [" اگر لینک اصلی کار نکرد از لینک کمکی استفاده کنید.", "با لینک اشتراک اصلی یا کمکی برای شما"],
        [".", "با لینک اشتراک برای شما"],
        $t
    );
    return $t === '' ? $o : $t;
}

/** the request bot() is about to send: the helper link is taken out of its text and caption unless the stage is "both" */
function hlOutgoing(array $datas): array {
    try {
        if (hlStage() === 'both') return $datas;
        foreach (['text', 'caption'] as $k) {
            if (isset($datas[$k]) && is_string($datas[$k])) $datas[$k] = hlStrip($datas[$k]);
        }
    } catch (Throwable $e) {
    }
    return $datas;
}
