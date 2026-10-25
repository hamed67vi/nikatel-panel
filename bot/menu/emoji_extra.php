<?php
/**
 * The emoji list of the shop bot's "edit buttons" panel (premium_emoji.php: peEmojiList) is built from the labels of settings/values.php and the servers' flags. The bot also draws buttons whose words are written in its code
 * (the admin panel, the statistics, the tickets, the rating, the paging ...): their emoji were not in the list, so the admin could not give them a premium icon. This file (installed beside premium_emoji.php by
 * scripts/install-emoji-extra.sh) adds them: peEmojiList() calls peExtraLabels() and treats every label like a label of values.php. It only fills the LIST of emoji; no button text is changed by it
 * (the premium icon of an emoji is applied to every button that has that emoji, wherever the words come from).
 *
 * The owner (2026-10-25, translated): "check that the list of the bot's emoji is complete, add the support's too" - the support buttons are keys of $buttonValues now (support_section ...), so they are in the list by themselves.
 * The labels below are one or two examples for every emoji that the code uses and the list did not have on 2026-10-25; the buttons the admin made himself (main-menu buttons, ticket categories, the categories of the
 * buy menu) are read from the database every time.
 */

/** labels (with their emoji) to put into the emoji list: the examples of the code and the admin's own buttons */
function peExtraLabels(): array
{
    static $fixed = [
        '🔰 دریافت پنل همکاری',
        '🚩 پرچم سرور',
        '📣 ریمارک سرور',
        '🔅 تغییر اطلاعات ورود',
        '✂️ حذف سرور',
        '✂️ حذف',
        '🎗 بنر بازاریابی 🎗',
        '🎗 تعداد اکانت های فروخته شده',
        '👥 آمار کاربران',
        '👥 کاربران دارای اشتراک',
        '📦 آمار اشتراک‌ها',
        '📦 کاربران با بیشترین تعداد اشتراک (50 کاربر)',
        '🔙 بازگشت به مدیریت',
        '🔙 بازگشت به آمار',
        '🧾 کل اشتراک‌های کاربران',
        '🧾 وضعیت عملیات‌ها',
        '🚫 کاربران بدون اشتراک',
        '🚫 خریدهای نهایی نشده',
        '🤝 تعداد کاربران همکار',
        '🤝 فروش همکاری امروز',
        '🔂 کاربران جدید امروز',
        '🔂 ارسال remark جدید',
        '📅 کاربران جدید هفته',
        '📅 50 بیشترین مبلغ پرداختی ماه گذشته',
        '🗓 کاربران جدید ماه',
        '🗓 50 بیشترین مبلغ پرداختی 7 روز گذشته',
        '📊 کاربران جدید ۳ ماه',
        '📈 کاربران با بیشترین موجودی (50 کاربر)',
        '📈 فروش کل',
        '🧑‍💼 لیست کاربران همکار',
        '🧑‍💼 همکاران',
        '📝 تعداد پلن‌ها',
        'پاسخ به تیکت 📝',
        '🛒 آمار اشتراک‌ها بر اساس خرید جدید',
        '🛒 آمار فروش همکاری',
        '💼 تعداد فروش با کیف پول',
        '💼 مجموع فروش با کیف پول',
        '🌙 آمار فروش ماهانه هر سرور',
        '🌛 آمار فروش 5 پلن برتر ماه',
        '⏱ 50 بیشترین مبلغ پرداختی 24 ساعت گذشته',
        '❤️',
        '✏️',
        '✏️ ویرایش توضیحات',
        '⬅️',
        '⬅️ قبلی',
        '➡️',
        '➡️ بعدی',
        '🔮 نام پلن',
        '🚪 تغییر ظرفیت کانفیگ',
        '🚪 سطر کانفیگ',
        '📤 ارسال رسید',
        '▶️',
        '◀️️',
        '◀',
        '🔀 جابجایی موجودی',
        '🔋 افزایش حجم اشتراک',
        '🔋 حجم کلی',
        '⛔️ لغو ادامه ارسال',
        '🆔',
        '🗃',
        '🛠️',
        '🗑 حذف پیام از همه',
        '🗑 حذف سفارش',
        '🎟 افزودن کد تخفیف',
        '📥 دریافت رایگان',
        'آخرین پیام ها 📩',
        'بسیار بد 😠',
        'بد 🙁',
        'خوب 😐',
        'بسیار خوب 😃',
        'عالی 🤩',
        '☑️ تیکت توسط کاربر بسته شد.',
        '🔘 وضعیت اکانت 🔘',
        '↕️ آپلود + دانلود',
        '🪫 حجم باقیمانده',
        '💡مشاهده مشخصات اشتراک',
        '💡 مشاهده مشخصات اشتراک',
        '🖥 تغییر Server ID',
        '🧬 تغییر UUID',
        '🎖 پورت اختصاصی',
        '➖➖➖',
        '⤵️ برگرد به منوی اصلی'
    ];
    $out = $fixed;
    global $connection;
    try {
        if (isset($connection) && $connection instanceof mysqli) {
            $q = @$connection->query("SELECT `type` FROM `setting` WHERE `type` LIKE '%MAIN_BUTTONS%'");      // the main-menu buttons the admin added (the title is after the word)
            if ($q) while ($r = $q->fetch_assoc()) $out[] = trim(str_replace('MAIN_BUTTONS', '', (string) $r['type']));
            $q = @$connection->query("SELECT `value` FROM `setting` WHERE `type` = 'TICKETS_CATEGORY'");
            if ($q) while ($r = $q->fetch_assoc()) $out[] = trim((string) $r['value']);
            $q = @$connection->query("SELECT `title` FROM `server_categories`");
            if ($q) while ($r = $q->fetch_assoc()) $out[] = trim((string) $r['title']);
        }
    } catch (Throwable $e) {
        // the fixed examples are enough
    }
    return array_values(array_filter($out, fn($v) => is_string($v) && $v !== ''));
}