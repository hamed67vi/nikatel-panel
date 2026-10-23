<?php
/**
 * The support section of the shop bot (installed beside bot.php by scripts/install-support-section.sh).
 *
 * The owner (2026-10-23, translated): "a support section in the bot's options with both a ticket and a direct chat with the operator; sometimes I want only the ticket to be active;
 * and in the ticket, when the customer writes again, his message must not go up (a new message to the admins)".
 *
 *   - main menu: one button "support" (callback supportSection) -> the screen below;
 *   - the screen: ticket buttons (new ticket, open tickets, all tickets) and a button that opens the operator's own Telegram chat (the bot's supportID setting);
 *     BOT_STATES supportChat = "off" hides that button, then only the ticket is left (the admin's switch is in the ticket menu of the admin panel: "ticketsList");
 *   - a follow-up in a ticket (the customer writes again while the last message of the ticket is still his own, nobody answered): the message is saved in the ticket and the admins get
 *     NO new message; after an answer of the support the next message of the customer is announced again, once.
 * Every function is called from bot.php behind function_exists() and the old code stays in place: when anything here returns false the bot does what it did before.
 */

/** the direct chat is shown unless the admin switched it off (BOT_STATES supportChat = "off") */
function supChatOn(array $botState): bool
{
    return (string) ($botState['supportChat'] ?? 'on') !== 'off';
}

/** the link of the operator's own chat from the bot's supportID setting (@name, name or a t.me link); '' when it is not a link Telegram accepts (one wrong link makes Telegram refuse the whole keyboard) */
function supChatUrl(array $botState): string
{
    $s = trim((string) ($botState['supportID'] ?? ''));
    if ($s === '') return '';
    if (preg_match('~^https?://t\.me/[A-Za-z0-9_+/-]{3,}$~i', $s)) return $s;
    $s = ltrim($s, '@');
    return preg_match('/^[A-Za-z][A-Za-z0-9_]{3,31}$/', $s) ? 'https://t.me/' . $s : '';
}

/** the buttons of the customer's support screen */
function supScreenRows(array $botState, array $buttonValues): array
{
    $rows = [
        [['text' => "✉️ ثبت تیکت", 'callback_data' => "usersNewTicket"]],
        [['text' => "تیکت های باز 📨", 'callback_data' => "usersOpenTickets"], ['text' => "📮 لیست تیکت ها", 'callback_data' => "userAllTickets"]],
    ];
    $url = supChatOn($botState) ? supChatUrl($botState) : '';
    if ($url !== '') $rows[] = [['text' => "💬 چت مستقیم با اپراتور", 'url' => $url]];
    $rows[] = [['text' => $buttonValues['back_button'], 'callback_data' => "mainMenu"]];
    return $rows;
}

/** callback supportSection: the support screen; false = nothing was done (the old handler runs) */
function supHandleScreen(): bool
{
    global $message_id, $botState, $buttonValues;
    try {
        editText($message_id, "🛂 به بخش پشتیبانی خوش آمدید.\n❇️ لطفا، یکی از دکمه های زیر را انتخاب نمایید.",
            json_encode(['inline_keyboard' => supScreenRows((array) $botState, (array) $buttonValues)]));
    } catch (Throwable $e) {
        return false;
    }
    return true;
}

/** the buttons of the admin's ticket menu: the old four and the switch of the direct chat */
function supAdminRows(array $botState, array $buttonValues): array
{
    return [
        [['text' => "تیکت های باز", 'callback_data' => "openTickets"], ['text' => "تیکت های جدید", 'callback_data' => "newTickets"]],
        [['text' => "همه ی تیکت ها", 'callback_data' => "allTickets"], ['text' => "دسته بندی تیکت ها", 'callback_data' => "ticketsCategory"]],
        [['text' => supChatOn($botState) ? "💬 چت مستقیم با اپراتور: روشن ✅" : "💬 چت مستقیم با اپراتور: خاموش ❌", 'callback_data' => "toggleSupportChat"]],
        [['text' => $buttonValues['back_button'], 'callback_data' => "managePanel"]],
    ];
}

/** callbacks ticketsList and toggleSupportChat (admins only): flips the switch when asked, draws the ticket menu; false = not an admin / nothing done (the old handler runs) */
function supHandleAdminMenu(): bool
{
    global $data, $message_id, $botState, $buttonValues, $from_id, $admin, $userInfo;
    if (!($from_id == $admin || ($userInfo['isAdmin'] ?? false) == true)) return false;
    try {
        if ($data == 'toggleSupportChat') setSettings('supportChat', supChatOn((array) $botState) ? 'off' : 'on');
        editText($message_id, "به بخش تیکت ها خوش اومدید\n\n🚪 /start",
            json_encode(['inline_keyboard' => supAdminRows((array) $botState, (array) $buttonValues)]));
    } catch (Throwable $e) {
        return false;
    }
    return true;
}

/** is the last message of the ticket the customer's own (nobody answered it yet)? $connection is the bot's mysqli */
function supLastIsCustomer($connection, int $chatId): bool
{
    $st = $connection->prepare("SELECT `msg_type` FROM `chats_info` WHERE `chat_id` = ? ORDER BY `id` DESC LIMIT 1");
    $st->bind_param("i", $chatId);
    $st->execute();
    $r = $st->get_result()->fetch_assoc();
    $st->close();
    return is_array($r) && (string) ($r['msg_type'] ?? '') === 'USER';
}

/**
 * step sendMsg_<ticket>: the customer writes in a ticket. When the last message of that ticket is his own (a follow-up), the message is saved and he gets the usual
 * "registered" answer, the admins get nothing: true = done (bot.php then skips the old handler). false = the old handler runs and announces the message as before.
 */
function supQuietFollowUp(): bool
{
    global $connection, $userInfo, $from_id, $text, $fileid, $caption, $buttonValues;
    if (!preg_match('/^sendMsg_(\d+)$/', (string) ($userInfo['step'] ?? ''), $m)) return false;
    if (isset($text) && $text == ($buttonValues['cancel'] ?? "\0")) return false;
    if (!isset($text) && !isset($fileid)) return false;
    $id = (int) $m[1];
    try {
        $st = $connection->prepare("SELECT `user_id`, `state` FROM `chats` WHERE `id` = ?");
        $st->bind_param("i", $id);
        $st->execute();
        $chat = $st->get_result()->fetch_assoc();
        $st->close();
        if (!$chat || (int) $chat['user_id'] !== (int) $from_id || (int) $chat['state'] === 2) return false;      // (somebody else's or a closed ticket: the old handler decides)
        if (!supLastIsCustomer($connection, $id)) return false;
        $body = isset($text) ? (string) $text : json_encode(['file_id' => $fileid, 'caption' => $caption ?? '']);
        $now = time();
        $st = $connection->prepare("INSERT INTO `chats_info` (`chat_id`,`sent_date`,`msg_type`,`text`) VALUES (?,?,'USER',?)");
        $st->bind_param("iis", $id, $now, $body);
        $st->execute();
        $st->close();
    } catch (Throwable $e) {
        return false;
    }
    sendMessage("پیام شما با موفقیت ثبت شد.", getMainKeys(), "HTML");
    setUser("none");
    return true;
}
