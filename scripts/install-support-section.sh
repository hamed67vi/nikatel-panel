#!/usr/bin/env bash
# The support section of the shop bot: a "support" button in the main menu -> tickets + a button for the operator's own chat (switchable), and quiet follow-ups in a ticket.
#   bash scripts/install-support-section.sh [--dry-run | --uninstall] <bot dir>        e.g. /var/www/bot
# Copies bot/menu/support_section.php next to bot.php and puts SIX marked lines (every one ends with "// [support]") into the bot's own files:
#   bot.php    1) the include of support_section.php at the top (only when the file is there);
#              2) before the handler of the button "supportSection": the new screen (tickets + the operator's chat); when it answers, the old handler is skipped;
#              3) before the handler of "ticketsList" (admins): the ticket menu with the stage of the section and the switch of the direct chat (also the callbacks toggleSupportStage, toggleSupportChat);
#              4) before the handler of the customer's message in a ticket (step sendMsg_<id>): a follow-up (the last message of the ticket is the customer's own) is saved and NOT sent to the admins again;
#   config.php 5) one row "support" in the main menu (getMainKeys), under the row of the apps and "my info" - an empty row for whoever the stage does not cover;
#              6) just before getMainKeys() returns: the support button is put into the row of the "add or see subscription" button (side by side). The button opens the operator's own Telegram chat while
#                 BOT_STATES supportButton is not "section" and the direct chat is on (else the support screen). Every line is looked at on its own: a bot with an older set of lines gets the missing ones.
# STAGED: BOT_STATES supportSection = "admin" (the default: the button, the screen and the quiet follow-ups exist for the admins only; every customer gets exactly the old bot), "on" (everybody), "off" (nobody);
# the admin's ticket menu has the button that turns it admin -> on -> off -> admin.
# Nothing of the old code is changed or removed: every new line calls support_section.php behind function_exists() and the old handler is still there for the case that it returns false.
# The old files are kept in $BACKUP_DIR (default /root/backups); php -l runs on the new files BEFORE they replace the old ones; --uninstall takes the six lines out again (byte for byte) and removes support_section.php.
# The direct chat switch: BOT_STATES supportChat = "off" (the admin's ticket menu has the button); the operator's link is the bot's supportID setting.
set -euo pipefail
MODE=install
case "${1:-}" in --dry-run) MODE=dry; shift;; --uninstall) MODE=uninstall; shift;; esac
DIR="${1:?bot directory, e.g. /var/www/bot}"
B="$DIR/bot.php"; C="$DIR/config.php"
SRC="$(cd "$(dirname "$0")/.." && pwd)/bot/menu/support_section.php"
[ -f "$B" ] || { echo "no $B"; exit 1; }
[ -f "$C" ] || { echo "no $C"; exit 1; }
[ -f "$SRC" ] || { echo "no $SRC"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
NB="$B.support.$$"; NC="$C.support.$$"
trap 'rm -f "$NB" "$NC"' EXIT

python3 - "$B" "$NB" "$C" "$NC" "$MODE" <<'PY'
import sys
b_src, b_dst, c_src, c_dst, mode = sys.argv[1:6]
TAG = " // [support]"
# (anchor, inserted text, where): "before" puts the text in front of the anchor, "after" behind it, "top" after the line "<?php"
BOT = [
    ("<?php\n", "if (is_file(__DIR__ . '/support_section.php')) require_once __DIR__ . '/support_section.php';" + TAG + "\n", "after"),
    ("if($data==\"supportSection\"){\n", "if($data==\"supportSection\" && function_exists('supHandleScreen') && supHandleScreen()) $data = 'support-handled';" + TAG + "\n", "before"),
    ("if($data==\"ticketsList\" and ($from_id == $admin || $userInfo['isAdmin'] == true)){\n", "if(($data==\"ticketsList\" || $data==\"toggleSupportStage\" || $data==\"toggleSupportChat\") && function_exists('supHandleAdminMenu') && supHandleAdminMenu()) $data = 'support-handled';" + TAG + "\n", "before"),
    ("if(preg_match('/^sendMsg_(.*)/',$userInfo['step'],$match)  and $text!=$buttonValues['cancel']){\n", "if(function_exists('supQuietFollowUp') && supQuietFollowUp()) $userInfo['step'] = 'none';" + TAG + "\n", "before"),
]
CONF = [
    ("        [['text'=>$buttonValues['application_links'],'callback_data'=>\"reciveApplications\"],['text'=>$buttonValues['my_info'],'callback_data'=>\"myInfo\"]],\n",
     "        (function_exists('supMenuRow') ? supMenuRow() : [])," + TAG + "\n", "after"),
    ("    return json_encode(['inline_keyboard' => $mainKeys]);\n", "    if (function_exists('supJoinMenuRows')) $mainKeys = supJoinMenuRows($mainKeys);" + TAG + "\n", "before"),
]

def load(p):
    s = open(p, encoding="utf-8", newline="").read()
    crlf = "\r\n" in s
    return (s.replace("\r\n", "\n") if crlf else s), crlf

def save(p, s, crlf):
    open(p, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)

def run(src, dst, edits, name):
    """every edit is looked at on its own: one that is there (its exact line x1) is kept, one that is not is put in - so a bot that has an older set of lines gets the new ones, and nothing is doubled"""
    s, crlf = load(src)
    present = [e for e in edits if s.count(e[1]) == 1]
    missing = [e for e in edits if s.count(e[1]) == 0]
    if len(present) + len(missing) != len(edits) or s.count(TAG) != len(present):
        print(f"{name}: the marked lines are not as the installer wrote them ({s.count(TAG)} marked, {len(present)} known): not touching it", file=sys.stderr); sys.exit(3)
    if mode == "uninstall":
        for anchor, ins, how in present:
            s = s.replace(ins, "", 1)
        print(f"{name}: {len(present)} line(s) taken out" if present else f"{name}: not installed")
    else:
        if missing:
            if not s.startswith("<?php\n") and name == "bot.php":
                print("bot.php does not start with <?php + newline: not installing", file=sys.stderr); sys.exit(3)
            for anchor, ins, how in missing:
                if (s.count(anchor) != 1) and not (how == "after" and anchor == "<?php\n" and s.startswith(anchor)):
                    print(f"{name}: the place for  {anchor.strip()[:70]}  is not as expected (x{s.count(anchor)}; want 1): not installing", file=sys.stderr); sys.exit(3)
            for anchor, ins, how in missing:
                if anchor == "<?php\n":
                    s = anchor + ins + s[len(anchor):]
                elif how == "before":
                    s = s.replace(anchor, ins + anchor, 1)
                else:
                    s = s.replace(anchor, anchor + ins, 1)
            print(f"{name}: {len(missing)} line(s) put in" + (f", {len(present)} already there" if present else ""))
        else:
            print(f"{name}: already installed")
    save(dst, s, crlf)
run(b_src, b_dst, BOT, "bot.php")
run(c_src, c_dst, CONF, "config.php")
PY

php -l "$NB" >/dev/null || { echo "php -l failed on the new bot.php - nothing replaced"; exit 1; }
php -l "$NC" >/dev/null || { echo "php -l failed on the new config.php - nothing replaced"; exit 1; }
if [ "$MODE" = install ] || [ "$MODE" = dry ]; then php -l "$SRC" >/dev/null || { echo "php -l failed on support_section.php"; exit 1; }; fi
if [ "$MODE" = dry ]; then echo "(dry run: the three files pass php -l, nothing replaced or copied)"; exit 0; fi
if [ "$MODE" = install ]; then
    install -m 644 "$SRC" "$DIR/support_section.php"
    chown --reference="$B" "$DIR/support_section.php" 2>/dev/null || true
fi
for pair in "$B:$NB:bot.php" "$C:$NC:config.php"; do
    OLD="${pair%%:*}"; rest="${pair#*:}"; NEW="${rest%%:*}"; NAME="${rest#*:}"
    if ! cmp -s "$OLD" "$NEW"; then
        mkdir -p "$BACKUP_DIR"
        cp -p "$OLD" "$BACKUP_DIR/$NAME.before-support-section-$STAMP"
        chown --reference="$OLD" "$NEW" 2>/dev/null || true
        chmod --reference="$OLD" "$NEW"
        mv "$NEW" "$OLD"
        echo "done: $OLD ($(grep -c '\[support\]' "$OLD") marker line(s)); the old file: $BACKUP_DIR/$NAME.before-support-section-$STAMP"
    else
        echo "no change in $NAME"
    fi
done
if [ "$MODE" = uninstall ]; then rm -f "$DIR/support_section.php"; fi
php -l "$B" >/dev/null && php -l "$C" >/dev/null && echo "bot.php and config.php pass php -l"
