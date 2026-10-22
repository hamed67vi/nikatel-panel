<?php
/* The apps screen of the shop bot ("نرم افزارها و آموزش های لازم" = callback reciveApplications) by system.
   The list is needed_sofwares (title, link). A title with (پیشنهادی) is a recommended app (a star in front, on top); the words iPhone / iOS, Android / اندروید, Windows / ویندوز, Mac / مک
   in a title say its system. The first screen shows one button per system with the number of apps (and the rows that name no system - the tutorials - as they were), a system button opens the apps of
   that system. Put next to bot.php by scripts/install-apps-groups.sh; the bot's own handler calls shopAppsKeyboard($mysqliResult, $callbackData) and adds its "back to the main menu" button. */
function shopAppsOs(string $title): string {
    if (preg_match('/iphone|\bios\b|آیفون/iu', $title)) return 'ios';
    if (preg_match('/android|اندروید|آندروید/iu', $title)) return 'android';
    if (preg_match('/windows|ویندوز/iu', $title)) return 'windows';
    if (preg_match('/macos|\bmac\b|مک/iu', $title)) return 'mac';
    return '';
}

function shopAppsKeyboard($res, $data): array {
    $rows = [];
    while ($f = $res->fetch_assoc()) {
        $t = (string) $f['title'];
        $rec = (bool) preg_match('/[\(（]\s*پیشنهادی\s*[\)）]/u', $t);
        $t = trim((string) preg_replace('/\s*[\(（]\s*پیشنهادی\s*[\)）]/u', '', $t));
        $rows[] = ['title' => $t, 'link' => (string) $f['link'], 'rec' => $rec, 'os' => shopAppsOs($t)];
    }
    $names = ['ios' => '📱 آیفون / iOS', 'android' => '🤖 اندروید', 'windows' => '🪟 ویندوز', 'mac' => '💻 مک / macOS'];
    $kb = [];
    if (preg_match('/^reciveApplications_(ios|android|windows|mac)$/', (string) $data, $m)) {
        $list = array_values(array_filter($rows, fn($r) => $r['os'] === $m[1]));
        usort($list, fn($a, $b) => (int) $b['rec'] <=> (int) $a['rec']);        // (stable: the order of the list stays inside the recommended and the others)
        foreach ($list as $r) $kb[] = ['text' => ($r['rec'] ? '⭐ ' : '') . $r['title'], 'url' => $r['link']];
        $kb[] = ['text' => '🔙 دسته‌بندی برنامه‌ها', 'callback_data' => 'reciveApplications'];
        return $kb;
    }
    foreach ($names as $k => $n) {
        $c = count(array_filter($rows, fn($r) => $r['os'] === $k));
        if ($c > 0) $kb[] = ['text' => "$n ($c)", 'callback_data' => "reciveApplications_$k"];
    }
    foreach ($rows as $r) if ($r['os'] === '') $kb[] = ['text' => ($r['rec'] ? '⭐ ' : '') . $r['title'], 'url' => $r['link']];
    return $kb;
}
