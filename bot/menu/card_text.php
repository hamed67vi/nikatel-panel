<?php
/**
 * The card-to-card screen and the "currency payment" button of the shop bot (installed beside config.php by scripts/install-card-text.sh).
 *
 * The owner (2026-10-27, translated): "I want to fix my payment methods. In the card-to-card part show only this text [the star line]. If the customer is an old one, show my card number
 * below it; if not an old one, put the support id so they can message" and "add the currency payment under card-to-card" (= pay in tether to his wallet) and "delete this text of mine
 * entirely" (the red line "before paying tether for outband, agree the exact tether amount with support").
 *
 * (Later the same day, translated: "the text is: star, to get the card number message support / support: @id. If the customer is an old one, show the card number" = the star line is short and only for the others.)
 * What it does, with the bot's own rule for "old customer" (resolveCartToCartMessageType() in buy_helpers.php says 'detail' = the card may be shown; nothing of that rule is changed):
 *   - the card-to-card screen of an old customer is the card message (amount, card number, holder) as it always was; any other customer gets the owner's star line + the support id;
 *   - under every card-to-card button a second button "currency payment" is put (one place for all of them: bot() calls ctcOutgoing() for every request, so the six buy / renew / add-days /
 *     add-volume / wallet-top-up lists need no change): the same flow, with the tether message (amount in USDT, the owner's wallet, the network, "send the receipt");
 *   - the button's callback is "cuz_" + the card button's callback; ctcStrip() (first line of bot.php after check()) takes the prefix off again and remembers it, so every old handler runs unchanged.
 * BOT_STATES: ctcText = off  -> the old card-to-card texts come back;  ctcCurrency = off -> no currency button (an old button that is tapped shows the card screen).  Unset = on.
 * Texts (all optional, in the owner's texts file): ctc_card_note, ctc_currency_text, ctc_currency_button; the defaults are below.
 */

const CTC_NOTE_DEFAULT = "⭐️ در صورتی که <b>مشتری قدیمی یا معرفی شده</b> هستید برای دریافت <b>شماره کارت</b> به پشتیبانی پیام ارسال بفرمایید";
const CTC_CURRENCY_DEFAULT = "❇️ برای شارژ یا خرید <b>مطابق توضیحات زیر</b> اقدام کنید.\n\n💎 مقدار <code>PRICE-USDT USDT</code> را به آدرس کیف پول زیر واریز کنید.\n\n<code>USDT-WALLET</code>\n\n✅ واریز تتر باید روی <b>شبکه BEP20 (BSC)</b> باشد. رسید تتر باید دارای <b>هش تراکنش</b> باشد.\n\n📤 <b>ارسال رسید</b> را بزنید و بعد تصویر را بفرستید.";
const CTC_BUTTON_DEFAULT = "💎 پرداخت ارزی";
const CTC_PREFIX = 'cuz_';

/** a switch of BOT_STATES: on unless it says "off" */
function ctcOn(string $key): bool
{
    $st = $GLOBALS['botState'] ?? null;
    return !is_array($st) || (($st[$key] ?? 'on') !== 'off');
}

/** a text of the owner's (the bot's $mainValues / $buttonValues) or the default of this file */
function ctcWord(string $key, string $default, ?array $values = null): string
{
    $values ??= $GLOBALS['mainValues'] ?? [];
    $v = (is_array($values) && isset($values[$key]) && is_string($values[$key])) ? trim($values[$key]) : '';
    return $v !== '' ? $values[$key] : $default;
}

/** the support id of the bot as a line under the star line: "@name" (a bare name or a t.me link of a user is written as @name); nothing when no support id is set */
function ctcSupportLine(): string
{
    $st = $GLOBALS['botState'] ?? [];
    $id = trim((string) (is_array($st) ? ($st['supportID'] ?? '') : ''));
    if ($id === '') return '';
    if (preg_match('~^https?://t\.me/([A-Za-z0-9_]{3,})/?$~i', $id, $m) || preg_match('/^@?([A-Za-z0-9_]{3,})$/', $id, $m)) $id = '@' . $m[1];
    return '🎧 پشتیبانی: ' . htmlspecialchars($id, ENT_NOQUOTES, 'UTF-8');
}

/** the tether message: the bot's own function fills amount / wallet in (all three of its templates are swapped for ours for this one call) */
function ctcCurrencyMessage(array $paymentKeys, array $mainValues, int $priceToman, int $userId): string
{
    $tpl = ctcWord('ctc_currency_text', CTC_CURRENCY_DEFAULT, $mainValues);
    foreach (['brw_cart_to_cart_detail', 'brw_cart_to_cart_usdt', 'brw_cart_to_cart_gift'] as $k) $mainValues[$k] = $tpl;
    $msg = buildCartToCartInfoMessage($paymentKeys, $mainValues, $priceToman, $userId);
    $GLOBALS['lastCartToCartMessageType'] = 'usdt';
    return $msg;
}

/** an old customer: the card message (amount, card number, holder) as it always was / anyone else: the star line + the support id */
function ctcCardMessage(array $paymentKeys, array $mainValues, int $priceToman, int $userId): string
{
    if (resolveCartToCartMessageType($priceToman, $userId) === 'detail') {
        return buildCartToCartInfoMessage($paymentKeys, $mainValues, $priceToman, $userId);
    }
    $note = ctcWord('ctc_card_note', CTC_NOTE_DEFAULT, $mainValues);
    $GLOBALS['lastCartToCartMessageType'] = 'gift';
    $line = ctcSupportLine();
    return $line === '' ? $note : $note . "\n\n" . $line;
}

/** called first thing in buildCartToCartInfoMessage(): the new text, or null = the old function goes on */
function ctcMessage(array $paymentKeys, array $mainValues, int $priceToman, int $userId = 0): ?string
{
    if (!empty($GLOBALS['ctcBusy'])) return null;      // (we are inside the old function that we called ourselves)
    $GLOBALS['ctcBusy'] = true;
    try {
        if (!empty($GLOBALS['ctcCurrency']) && ctcOn('ctcCurrency')) return ctcCurrencyMessage($paymentKeys, $mainValues, $priceToman, $userId);
        if (!ctcOn('ctcText')) return null;
        return ctcCardMessage($paymentKeys, $mainValues, $priceToman, $userId);
    } finally {
        $GLOBALS['ctcBusy'] = false;
    }
}

/** bot.php, right after check(): the tap on the currency button goes on as a tap on the card button of the same payment, and the flag says which of the two it was */
function ctcStrip(): void
{
    global $data;
    if (is_string($data) && strncmp($data, CTC_PREFIX, strlen(CTC_PREFIX)) === 0) {
        $data = substr($data, strlen(CTC_PREFIX));
        $GLOBALS['ctcCurrency'] = true;
    }
}

/** bot(): a request with a keyboard that has a card-to-card payment button gets the currency button in a row of its own right under it */
function ctcOutgoing(array $d): array
{
    $markup = $d['reply_markup'] ?? null;
    if (!is_string($markup) || strpos($markup, 'CartToCart') === false || !ctcOn('ctcCurrency')) return $d;
    $keys = $GLOBALS['paymentKeys'] ?? null;
    if (!is_array($keys) || trim((string) ($keys['tronwallet'] ?? '')) === '') return $d;      // (no wallet to pay into: no button)
    $kb = json_decode($markup, true);
    if (!is_array($kb) || !isset($kb['inline_keyboard']) || !is_array($kb['inline_keyboard'])) return $d;
    $label = ctcWord('ctc_currency_button', CTC_BUTTON_DEFAULT, $GLOBALS['buttonValues'] ?? []);
    $rows = [];
    $added = false;
    foreach ($kb['inline_keyboard'] as $row) {
        $rows[] = $row;
        if ($added || !is_array($row)) continue;
        foreach ($row as $b) {
            $cd = is_array($b) ? (string) ($b['callback_data'] ?? '') : '';
            if (preg_match('/^(pay[A-Za-z]*WithCartToCart|prePayCheck_[A-Za-z0-9]+_CartToCart_)[A-Za-z0-9]+$/', $cd) && strlen(CTC_PREFIX . $cd) <= 64) {
                $rows[] = [['text' => $label, 'callback_data' => CTC_PREFIX . $cd]];
                $added = true;
                break;
            }
        }
    }
    if (!$added) return $d;
    $kb['inline_keyboard'] = $rows;
    $d['reply_markup'] = json_encode($kb, JSON_UNESCAPED_UNICODE);
    return $d;
}
