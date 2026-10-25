<?php
/**
 * The customer's own numeric id in the main-menu text of the shop bot (installed beside config.php by scripts/install-menu-user-id.sh).
 *
 * The owner (2026-10-25, translated): "the menu text ends with the customer's numeric id" and "the numbers must be monospace" (a tap copies them).
 * The word MENU-ID in a text (settings/values.php: start_message, reached_main_menu) becomes the id of the chat the message is sent to, written for the parse mode of that very message:
 * <code>id</code> for HTML, `id` for Markdown / MarkdownV2, the bare number when there is no parse mode. It is done inside the bot's own bot() function, the one place every message passes, because the same
 * menu text is sent by sendMessage (Markdown) and by editText (HTML) in different places of the bot. A text without the word is not touched.
 */

/** replaces the word MENU-ID in the text / caption of one Telegram request (the array bot() is about to send) */
function menuIdFormat(array $d): array
{
    $chat = (string) ($d['chat_id'] ?? '');
    $id = preg_match('/^-?\d+$/', $chat) === 1 ? $chat : (string) ($GLOBALS['from_id'] ?? '');      // (a menu text goes to a private chat: its id is the customer's id)
    $mode = strtolower((string) ($d['parse_mode'] ?? ''));
    foreach (['text', 'caption'] as $k) {
        if (!isset($d[$k]) || !is_string($d[$k]) || !str_contains($d[$k], 'MENU-ID')) continue;
        $rep = $id === '' ? '' : ($mode === 'html' ? "<code>$id</code>" : (($mode === 'markdown' || $mode === 'markdownv2') ? "`$id`" : $id));
        $d[$k] = str_replace('MENU-ID', $rep, $d[$k]);
    }
    return $d;
}
