# Changelog

## 1.69.52 - The failed-update note in the About card is one row and says whether updating fixes it

> **به زبان ساده:** در کارت About، پیام «آپدیت قبلی تمام نشد» حالا فقط یک ردیف کوتاه است (دلیل کوتاه، متن کامل با نگه‌داشتن ماوس). اگر نسخهٔ جدیدتری منتشر شده باشد، همان ردیف می‌گوید «آپدیت به نسخهٔ ... باید درستش کند».
>
> **In short:** the failed-update note is one short row; it says "updating to vX should fix it" when a newer version than the failed one is out.

The owner (2026-10-20, a picture of the About card showing the note of a failed update as a red paragraph in a box that scrolled): "show the error briefly in one row, and if the update fixes it, say so". (He first asked to switch the update button off until a stable update is ready, then withdrew it in the same hour; the button is as it was.)

- **One row** (`app.js aboutLoad`, `lab.css .abt-fail`): "⚠ Last update did not finish (7m ago): could not download the new version - updating to v1.69.50 should fix it": the short reason is what stands before the first colon of the server's words; the whole reason, "the version that was running stays" and the tail of the update log are in the tip. The row is nowrap with an ellipsis, so the box no longer needs its scroll bar.
- **"Should fix it"** only when a newer version than the one that failed is out (`u.newer` and the latest is greater than the failed target; a download that failed has no target: any newer version counts). A failed update to the very version that is offered again says nothing - the same update is not a fix. "Should", not "will": the panel cannot know the cause.
- **Proved**: `tests/test_about_failed_row.py` (the row's markup, the short reason, the tip, the hint, the CSS; the rule of the hint run through node with the cases: download failed, the failed version again, a newer one, nothing newer, 1.69.10 after 1.69.9), `tests/test_web_menu.py` (the text). **Not proved**: in the owner's browser.

## 1.69.51 - The Bandwidth column of the tunnel list is empty while the tunnel is fine

The owner (2026-10-20, in Persian, with a picture of the list): "this column has become too busy - I think it should show something only when the bandwidth drops, or has a problem, or the tunnel is cut; put the bandwidth inside the box of each card."

- **The cell** (`app.js bwCell`): nothing while the tunnel runs well. It shows a **red chip** - the number and one word (**dropped**, **no data**, **very slow**) - only when the Doctor sees the capacity dropped, stalled or very slow (the Doctor's own lines, unchanged); a **cut** tag when the tunnel is cut; **repairing…** while the Doctor works on it; and the short reason of an unstable tunnel, as before. The bar and the two numbers are gone from the row; the header's tooltip says where they are.
- **In the tunnel window** (click the row): **Link → Capacity** (the number, what it usually is, "measure now") and **Status → In use now** (↓ ↑ and the part of the capacity they use) were already there; **In use now now changes by itself** every few seconds while the window is open (before, it was the number of the moment the window opened). The live numbers are asked only while a tunnel window is open - the list does not ask any more. The peak and the busy hours are in its Monitor tab (1.69.48).
- **Proved**: `tests/test_web_menu.py` (the cell is empty/red chip/cut tag/repairing and has no bar; the live row of the window, its poll only from the window; the header's tooltip). **Not proved**: how the list and the window look in a browser; the next step the owner asked for - the nload-like Live view - is not in this version.
## 1.69.50 - After an update the page reloads by itself; the update's state files follow the panel's own data directory

> **به زبان ساده:** بعد از آپدیت، صفحه خودش تازه می‌شود: صبر می‌کند تا پنل با نسخهٔ جدید جواب بدهد، اگر بارگذاری گیر کرد دوباره امتحان می‌کند و آخرش یک لینک نشان می‌دهد. فایل وضعیت آپدیت دیگر مسیر ثابت پنل زنده را نمی‌نویسد.
>
> **In short:** the page reloads by itself after an update (it waits for the new version, retries a hung load, ends with a link); the update's state files follow the panel's own data directory.

The owner (2026-10-20, a picture of the About page after an update): "after the update the page does not refresh by itself, it seems stuck" - the card said "Updated 100%", the version under it was still the old one and the tab was loading.

- **The reload waits for the NEW version** (`app.js aboutReload`): the old code asked for the page 0.9 s after the percentage said 100 (`location.reload()`), and a request that met the restarting panel (the old process needs about 4 s to stop) hung in the tab. Now the page asks `/api/system/update` until the panel answers as another version than the one that was running, twice in a row (at most 90 s: a rollback keeps the old version), then loads with a fresh address (`?r=<time>`, taken off the address bar when the new page starts); a load that is still pending after 10 s is asked again, and after 20 s the card shows a link ("the page did not reload by itself: click here").
- **The update's state files follow the data directory** (`updater.STATE_DIR` = the parent of the configured data directory; `deploy.sh` gets it as `STATE_DIR`): the path was fixed to `/var/lib/nikatel`, so a second panel or a test run with its own data directory that called the update route wrote the progress and the log of the LIVE panel - on 2026-10-20 a robustness run left "could not download the new version: process spawn blocked by fuzz harness" in the live file and the About page said "the last update did not finish". For the live panel the path is the same as before.
- **Proved**: `tests/test_update_reload.py` (the page: waits for the new version twice in a row, 90 s limit, fresh address, a second try after 10 s, the link after 20 s, the `?r=` taken off; the state directory follows the data directory and is not the live path under the tests; the update unit is given `STATE_DIR`). **Not proved**: in the owner's browser (the hang itself was not reproduced here: the panel answered at once after the restart when it was looked at); a record left by that run stays in the live file until the next update replaces it.

## 1.69.49 - A cut server or tunnel shows at once; the cut tags do not say GRE; the Type column is two rows

> **به زبان ساده:** قطع‌شدن سرور یا تانل حالا تقریباً در همان لحظه (حدود ۲ تا ۸ ثانیه) در صفحه دیده می‌شود. نوشتهٔ قرمز تانل قطع‌شده دیگر «GRE» نمی‌گوید. ستون نوع تانل دو ردیف شد و برچسب‌ها کوچک‌تر شدند.
>
> **In short:** a cut server or tunnel shows within about 2-8 s instead of up to a minute; the red cut tag no longer says GRE; the Type column is two rows with smaller tags.

The owner (2026-10-20), looking at a tunnel that still showed as connected after its foreign server had stopped answering: "it detected very late - I want it shown at once when a tunnel or a server is cut; the error must be corrected, GRE removed from it; my tags smaller and in two rows".

- **Why it was late** (read on the live data): the pulse marks a server off in about 12 s, but the tunnels that end on it were not looked at: the fast lane (`tunnels.check_unhealthy`) takes only the tunnels that are NOT up, so a tunnel of the server that had just gone off stayed "up" until the slow cycle (every 60 s). Then the page itself redrew only every 15 s (tunnels) or 8 s (servers) and the Lab every 30 s.
- **The tunnels of a server that goes off or comes back are looked at at once** (`pulse._wake_tunnels`, `tunnels.check_of_servers`): every ENABLED tunnel that ends on it, whatever its state, with a fresh look from another Iran server. The pulse runs every 3 s instead of 5 (two misses: a server is called off after about 8 s).
- **The page notices a change in about 2 s** (`GET /api/tunnels/brief`, `app.js briefSig`): one signature of the states of the servers (on / off) and of the tunnels (up, down, unknown ...), a few bytes, read every 2 s while the page is open; when it changes the round of the page is made due at once (it redraws under the same guards: no window open, nothing being typed). No names, no numbers that change by themselves go through it.
- **The tags of a cut tunnel say no GRE** (`CUT_TAGS`): "foreign link", "Iran link" and "link cut" in place of "GRE foreign", "GRE Iran" and "GRE cut" (the tunnel may be any engine; the words of the tooltip are unchanged).
- **The Type column is two rows with smaller tags** (`td-ty2`, `lab.css`): the engine tag above (9 px), the link below (10 px); the red cut tag is 16 px high.
- **Proved**: `tests/test_instant_state.py` (every tunnel of a server that goes off or comes back is looked at, also the ones that are up, not the disabled or the ones of other servers; two misses before a server is off; the signature changes with a server or a tunnel and carries no name; the page code: the 2 s ask, the tags, the two rows), `tests/test_web_menu.py` (the tags). **Not proved**: on the owner's panel and in a browser (the redraw within 2 s was not watched); a tunnel that is cut while both servers answer keeps its own faster path (the sentinel, every 5 s), which this release does not change.

## 1.69.48 - Real use by the users: the peak, when, the busy hours of the day and the part of the capacity used at the peak

The owner (2026-10-20, in Persian, on the tunnel list): "I want to see the real bandwidth the user gets and the real bandwidth the user uses - not something made up, something really useful - for example the peak times: how much a tunnel really uses."

- **What already existed**, and is not repeated: the list shows the capacity (the Doctor's little test, or the users' 24-hour peak when it was higher) and, live, what the users use now (the counters of the tunnel's link on its Iran server, `livebw`); the Monitor tab draws the minute-by-minute download and upload charts.
- **New: the card "Real use by the users"** in the **Monitor** tab of a tunnel (`app.js tunPeak`, `tunPeakHtml`; computed in the page from the checks the tab already reads - no new table, no new request): **Peak ↓ / Peak ↑** with the day and hour of the busiest minute; the **busiest hour** of the day (by its average); **the part of its capacity used at the peak** (amber from 85 %: the users may be limited at the peak); and, from 12 hours of readings on, the **24 hours of the day as bars** - the light part is the busiest minute of that hour, the dark part its average - over the range chosen on top (24 h or 7 days; the checks are kept 14 days). A hover on a bar says the numbers. Persian in `i18n.fa.ui2.js`.
- **How real it is**: a reading is the average of ONE minute at the Iran end of the tunnel (what the users moved through it), so a "peak" is the busiest minute, not a spike of a second; a single spike does not make the busy hour (it is found by its average). "What the user gets" is what really crossed: at the peak it is the most the tunnel carried; it is compared with the capacity only when the Doctor has measured one.
- **Proved**: `tests/test_tunnel_peak.py` - the functions run through node: the peak and when, the average and the busy hour against a single spike, the readings of the hours, the missing values; the card with no data, with 5 hours (tiles, no bars, amber at 90 % of the capacity), with a day (24 bars) and with no capacity; the card stands before the timeline, the style and the Persian words. **Not proved**: how it looks in a browser (not opened); real numbers (it needs a tunnel with a day of users' traffic); the user's own speed: the panel counts the tunnel, not each user - a single user's download cannot be known from here.
## 1.69.47 - The automatic type goes on to the next type, a tunnel that carries no download is not ready, and a running tunnel gets a suggestion when a better type is known for its pair

The owner (2026-10-20), after the automatic best type built a Paqet that carried 0 Mb/s: "fix what is not fixed"; and, looking at his tunnels (most of them the default Backhaul TCP Mux): "did it set the best ones? after it sets them it must test whether a better and more stable one exists".

- **A tunnel that carries no download is not called ready** (`build_other.dead_download`, in `_build`): when the measurement through the tunnel's test door - eight connections and, when it was measured, one connection - moved less than 0.5 Mb/s toward the customers, the build stops, everything it made is removed and the story says so. Only the tunnel the owner asked for is judged: a pair test (a temporary tunnel) and a warm standby keep their number. (Before: a Paqet with "one connection: down 0.0 up 99.0" ended "Tunnel is ready".) The Backhaul build is not changed: its data check does not measure a speed.
- **"Best of the day" tries the next type** (`knowledge.best_kinds`, `create.start(fallback_kinds=...)`, `tunnel_test.build_args`): the automatic type no longer builds ONE type and stops; it builds the first of up to three (the types that connect on the pair, then the weak ones by what they carried), and when that build ends badly - it did not come up, or carried no download - the next one is built on the same Iran server, then the next Iran server of the smart choice. The story says each step. A pair with ONE known type goes straight to it as before.
- **A running tunnel gets a suggestion when the Doctor's memory knows a clearly better type for its pair** (`knowledge.upgrade_kinds`, `bandwidth._upgrade_check`, `doctor.suggestion_valid`): after the measurement of a tunnel that is fine (state ok, learning or low), at most once in 6 hours per tunnel and reading only the Doctor's memory (no server is asked), the Doctor compares the FULL pair test of the type the tunnel runs with the other types of the same pair: a type that carried at least 3 times as much and 100 Mb/s more, lost at most 1 % and scored at least as well, measured within 14 days and allowed by the licence. It writes ONE suggestion on the Doctor page (kind "type", marked `upgrade`; one click "Change type", the old type is put back when the new one does not build); it does NOT change a running tunnel by itself. The suggestion stands while the tunnel runs the same type, for a week. Nothing when the Doctor is off, nothing when the running type has no fresh full pair test (to find one: Doctor, Knowledge, test this pair; the night survey keeps the memory fresh), nothing when the tunnel already has another kind of suggestion.
- **Proved**: `tests/test_auto_fallback.py` (the rule of a better type with the owner's real numbers - 160 against 1293 and 2218, a type that lost 4 % or scored worse is left out; the order of the best types; a build that ends badly goes on to the next type; the arguments of a build from a key; a dead download is not ready and a pair test keeps its number; the suggestion stands, is withdrawn and is written once; nothing when the Doctor is off), `tests/test_best_of_the_day.py` (the API routes a pair with several known types through `create.start`). **Not proved**: on the owner's servers (the suggestions appear after the next measurements of his tunnels; a build that is refused for carrying nothing was seen only in tests); and that the types suggested are better for real users: the pair test is one download through the test door, not the traffic of the customers - after a change, measure with a config labelled to the tunnel.

## 1.69.46 - The log of the Create tunnel window is one row

The owner (2026-10-20, in Persian, with a picture of the Create window whose log box of 150 px covered the form): "the log must not push things up - put one row inside the box and show the log."

- **One row** (`app.css .bld-log`): the log under the Create form shows the LAST step on one line (cut with …) and a small spinner while it works - not a box of 150 px that covers the form. A **click** on the row opens the whole log (it grows and scrolls inside 40 % of the screen, so it never pushes the form out of the way by more than that); a second click folds it again. The tooltip says so. The Persian text is in `i18n.fa.ui2.js`.
- It is only the look of the log: the steps, the follow of the build and the tests of the pair are unchanged (`followStory`, `storyHtml`).
- **Proved**: `tests/test_build_log_row.py` (the rules of the one row, the spinner, the open state, the click handler). **Not proved**: how it looks in a browser (it was not opened) - the rules use `:has()`, which the owner's Chrome has.
## 1.69.45 - "Best of the day" on a pair where nothing scored well no longer picks the freshest weak type

The owner (2026-10-20), after a tunnel built with the automatic best type came up as Paqet: "didn't you say Paqet is weak? and in the tests BackPack was the best - why did it choose Paqet?"

- **What happened** (`knowledge.best_kind`): on the owner's pair every type that carried data was only "poor" (a long route, 167 - 190 ms; the Doctor's memory held 24 weak types and none good). With no good type the function did not look at the weak ones: it fell through to the Doctor's "first choice for a new pair" (`plan_for`), whose first entry sorted the weak types by "a fresh FULL measurement before a latency-only one" - so a Paqet that had carried 4.9 Mb/s a moment before won over types that had carried 66 and 76 Mb/s some days earlier. The sentence said "nothing is measured between these two servers yet", which was also wrong: 33 types were measured.
- **Now**: when no type is good, the best of the weak ones is the one that CARRIED the most (a measured download first, by Mb/s, then by score), among what the licence allows; the sentence says "no type scored well between these two servers - this is the best of the weak ones (76.2 Mb/s, measured N h ago)". A good type still wins over every weak one; a pair nobody measured still gets the first choice for a new pair.
- **Not changed (said so, to be done separately)**: a build of such a type is still called "ready" when its test door carried 0 Mb/s download (the Paqet of that pair showed "one connection: down 0.0 up 99.0"); the build should refuse it and the automatic type should try the next one. This release only makes the automatic choice not pick the weakest.
- **Proved**: `tests/test_best_of_the_day.py` (the weak ones: the one that carried the most wins over a fresher weaker one; the licence filter; a good type still wins). **Not proved**: on the owner's panel (the choice for the pair of that tunnel can be seen in Create tunnel, Type, Automatic, Best of the day, after the update).

## 1.69.44 - WireGuard is complete: the owner installs the tools with one press, every part says "installed / missing" and "not tested" instead of "blocked", the link checks its handshake and restarts on a stale one, the MTU counts the 80 bytes

The owner (2026-10-20, through the other session): "make WireGuard COMPLETE". WireGuard existed as the link `wg` (`wg:tcpmux`, `wg:tcp`, from 1.69.40 the whole `wg:*` grid) but was never really used: `wireguard-tools` is on none of the owner's servers, and the probe, the night survey and the Doctor never install a package - so every probe said "unknown" and the survey counted WireGuard as **blocked** on all 21 pairs it looked at. The rule stays: a build the Doctor or the survey makes by itself never installs a package. Details in `docs/wireguard.md`.

- **The owner installs the tools** (reviewed action `wg_install`, apt only, nothing copied from another machine; it now also loads and checks the kernel module and says plainly: no `apt-get` on the server / apt failed + its last lines / the kernel cannot load the module): **Settings -> About & update -> Tunnel cores -> WireGuard tools -> "Put on N servers"** (`POST /api/engines/wireguard/install`, background, four at a time, the result per server) and **a server's menu -> Install -> WireGuard tools…** (the onboard step `wg`; a new server never gets it: `onboard.STEPS` is unchanged, `ALL_STEPS` has the extra one). A build the owner starts with a WireGuard type still installs them (`install_wg`). The tools are not in the cores of the first tunnel (`ensure_all_cores`): apt in Iran can take minutes or be blocked, and it changes the server's packages.
- **The cores box says it per server**: `engine_versions` reads `WGTOOL`, `WGMOD`, `WGAPT`; the card shows installed / not installed / the kernel cannot load the module / no apt-get / unreachable for each server (`engines.wg_state`, `wg_summary`).
- **The probe says installed / missing per server**, not a bare unknown: `wireguard-tools: installed on the Iran server, MISSING on the foreign server (Servers -> the server's menu -> Install -> WireGuard tools; the probe never installs a package)`, with `skipped: "tools"` and `tools: {iran, foreign}`; the Doctor leaves out a link the probe could not try for this reason.
- **"Not tested" is not "blocked"** (`knowledge`): a kind whose build stopped because a server lacks the tools (`tunnel_test.tools_missing`, in the pair test, the survey and the Doctor's own builds) is **untested** - not failed, never in the blocked list, not kept out for 14 days; it is asked again after a day (`TOOLS_RETRY`) and **at once when the owner installs the tools** on one of its servers (`forget_tool_skips`). The probe keeps the same mark for the link (`ok: null`, `link_state` unknown). The Doctor page, Knowledge, shows "⚠ not tested: wg:tcpmux · tools missing".
- **The survey, the pair test and the walk really use it** once the tools are there: the night survey builds `wg:tcpmux` like any kind; a build the owner starts from Create tunnel whose link does not pass is followed by WireGuard when he asked for an install **or the tools and the module are already on both servers** (`builder._wg_ready`; nothing is installed).
- **Health**: the check reads the age of the last handshake (`WGHS` in `tunnel_side`, also in the quick look); none yet, or older than 300 s, is added to the sentence of a link that loses packets or does not answer. The check script of a WireGuard link (`builder.wg_check_script`, also for the carried links) restarts the link after **three** missed checks, after **two** when the handshake is older than 300 s.
- **MTU**: `linkmodels.inner_mtu` = the path MTU minus what the link adds (WireGuard 80, the worst case; at most 1360, at least 1200). A path of 1500 bytes: 1360 as before; a path of 1400: a WireGuard link gets 1320 (it got 1360: its packets were cut in two). The story says "the link adds up to 80 bytes".
- **Keys, removal, rebuild, standby**: tested, not changed: the private key is made on its server (0600) and only the public half is printed; no key is in a command line, a script or the record; the key and the scripts go with the tunnel; a rebuild keeps the link, the port and the MTU; a standby over WireGuard without the tools fails at once and installs nothing; all four transports (`wg:tcpmux`, `wg:tcp`, `wg:wsmux`, `wg:wssmux`) build.
- **Proved**: `tests/test_wireguard_complete.py` (the answer of a server read, the install faked: installed / already there / no apt / failed / no kernel module, read-only refused, one button for all servers with one failure not stopping the others, a second press refused, the API, the onboard step; untested vs failed vs blocked, asked again, forgotten on install, the page, the probe's sentence per server, the Doctor leaving out the link; the check script **run for real against stub programs**: restart after 3 misses, after 2 with an old handshake, never when it answers; the MTU numbers; the handshake in the verdict; the walk with and without the tools; all four transports built against a faked pair of servers, the MTU of a 1400 path, removal, rebuild, a standby). On the build server, in two network namespaces with the real files of the previous release: the reviewed `tunnel_side` script printed `WGHS 6` for a live link, and the reviewed `wg_install` action said `WG_OK present` after checking the module. **Not proved**: an apt install of `wireguard-tools` on a real server (none of the owner's servers has it; the action is the one the build already used, now with a module check), a handshake that goes stale on a real route and the restart that follows (only the script logic is run), the speed of WireGuard on the owner's pairs. **What still needs one press after updating**: Settings -> About & update -> Tunnel cores -> WireGuard tools -> **Put on N servers** (or the menu of one server). After that, "Test now" on a pair (Doctor -> Knowledge) tests WireGuard for real.

## 1.69.43 - A link carried INSIDE another tunnel: WireGuard in a Chisel TLS WebSocket, for the pair where GRE, ESP and every UDP port are filtered

The owner (2026-10-20), the idea of the author of simple-tun-link: "the main tunnel can always be Backhaul; in normal conditions plain Backhaul with the least overhead; if it gets harder, we can create the local IP with packet (paqet) or wstunnel, then run Backhaul on top." Every one of the nine link models needs something to pass DIRECTLY between the two servers (IP protocol 47, 4 or 50, or a UDP port); on a route that drops all of them none passes. A carried link puts the UDP packets of a WireGuard link inside a tunnel the route lets through.

- **Two new link models** (`services/linkmodels.py`: `CARRIER_LINKS`, `carrier_files`; the design and the table of carriers considered are in `docs/carried-links.md`): `wgchisel` - the foreign server dials the Iran server - and `wgchiselfwd` - the Iran server dials the foreign one. WireGuard runs between 10.x addresses as always; its UDP packets go through a loopback "door" into a **Chisel TLS WebSocket on ONE TCP port** (Chisel forwards UDP over the same connection; the key of the Chisel server is pinned, a password is in a file of 0600). The Iran end starts the talk, the foreign end answers whoever it hears from (roaming): **one UDP flow per link**. One number (52000-52099) gives the WireGuard port, the door and the TCP port (9400-9499). The kinds are named like the others (`wgchisel:tcpmux`, `wgchiselfwd:tcp` ...): `split_kind`, `kind_link`, `kind_parts`, Compare, Create tunnel (a "WireGuard in Chisel" entry in the guide and in the Backhaul transports), the Doctor's knowledge and the tunnel row ("WG in Chisel") keep working.
- **Why Chisel**: it forwards UDP, and it is a core the panel installs already (wstunnel would be a new core - download, checksum, update; paqet needs raw sockets, which is what fails where this feature is needed).
- **Build** (`builder._prepare_link`, `carrier_ends`): wireguard-tools is installed **only when the owner asked** (`install_wg`, the existing rule: Create tunnel, Compare, a survey started by hand; the Doctor and the night survey never install a package); Chisel is put on a server that lacks it. The end that runs the Chisel server is made first (key and certificate, unit, fingerprint read), the other end dials with it pinned. MTU is **8000** and no path MTU is measured (the packets travel in a TCP stream; Chisel carries up to 9012 bytes, WireGuard adds 60): one stream measured 120 / 170-190 / 235-245 Mb/s at MTU 1380 / 4000 / 8800. The link is checked with up to fourteen single pings first (the carrier opens its UDP flow with the first packets; WireGuard repeats a lost handshake after 5 s), ten pings each way, and twenty seconds later again. Everything made is noted before it exists and a failure removes it from both servers.
- **Units and files**: the link unit is `nikatel-gre-<name>` like every link; beside it `nikatel-carrier-<name>.service` (the Chisel server or client, no `[Install]`: the link unit starts and stops it); `/etc/nikatel-chisel/<name>.*`. The firewall rules (tag `nikatel-gre-<name>`) name the exact peer and the TCP port and let the loopback through.
- **Health, removal, rebuild, standby, move**: `tunnels.plan_for` judges the carrier unit on both ends; `remove_tunnel_files` removes the carrier unit and its files; `chisel_fp`, `purge_test_leftovers`, `link_probe_clean` know the carrier; the record keeps `link`, `link_port`, `wg` (public keys) and `carrier` (no secret).
- **The probe** (`linkprobe`): the two carried links can be probed (a temporary carried link on both servers, pinged, removed); they are asked only when named, never in the default nine. `link_facts` now also says the TCP ports in use and whether Chisel is there.
- **The walk of a build** (`builder.FALLBACK_LINKS`): `wgchisel`, then `wgchiselfwd`, are the very LAST models (after `wg`), and only where the owner allowed wireguard-tools. **Not in the Doctor's standard order** (`knowledge.PRIOR`, `READY`, `CUT_LINKS` do not name them; `tunnel_test.GRID_ONLY` has the 8 kinds): they are the Doctor's **last resort of a cut** (`autopilot._carrier_last_resort`): after every kind failed, the link probe is asked about them and only a kind it SAW pass is built; when wireguard-tools is missing the story says what to do once. The API accepts a transport up to 24 characters and an MTU up to 8000 for these links.
- **Proved**: `tests/test_carrier_link.py` (32 tests, 33 cases, plus 3 in `tests/test_cut_tunnel.py` for the Doctor's last resort; the whole suite, 1780 tests, passes: the registry and the kinds, the ports, the files of every end of both models - the commands, no secret in a command line, the firewall, valid shell -, what a build checks, the whole `_build` against a faked pair of servers: the order of the steps, the record, the rollback of both servers, a carrier that does not start, the probe, the walk, the health units, the API, the Doctor's last resort). **Also, on the build server, with the REAL files and actions and the real Chisel 1.12, WireGuard and Backhaul tcpmux in two network namespaces whose every chain drops everything that the generated rules do not open**: both models pass (10/10 pings each way, 40 MB through a Backhaul port: 16.8 MB/s `wgchisel`, 24.0 MB/s `wgchiselfwd`), keep passing after 30 s of quiet, and the removal leaves no firewall rule, interface, process or file and writes nothing to the host. **Not proved**: a real route between two servers (the namespaces have no loss and 1-2 ms), the speed there (a TCP stream carries the packets; TCP over TCP), installing wireguard-tools with apt on a real server, a reboot of a server that holds a carried link, the Doctor's last resort on a real cut. The owner can measure it after updating: Tunnels -> Compare, pick "TCP Mux over WireGuard in Chisel (foreign dials)" and "(Iran dials)" for a pair, press Start (it installs wireguard-tools on the two servers if they lack it, builds each for a minute, measures, removes).

## 1.69.42 - The "Update" question has the update emoji; the alert's buttons are flat in every theme

The owner (2026-10-20, with a picture of the "Update the panel from GitHub now?" box): "put the update emoji in place of the question mark".

- **The icon** (`app.js glassAsk`): a question that starts with Update (or Upgrade) shows 🔄 in the icon square instead of "?"; the other questions are as before (⚠ for a dangerous one, ✎ for a text, ? for the rest).
- **The buttons** (`lab.css`): in the owner's theme (calm) the blue button still had a gradient, a glow, a gloss line and a text shadow on top of the new alert (1.69.39). They are flat now - blue (red for a dangerous question) and a quiet grey Cancel - in the calm, aurora and classic themes, dark and light.
- **Proved**: `tests/test_fluid_layout.py` pins the icon rule and the flat-button rules; on a private demo panel in the built-in browser the computed styles of both buttons were read in 3 themes x 2 modes: no gradient, no shadow, no text shadow, no border, no gloss, the right colours; the icon was 🔄 for the update question, ⚠ for a delete and ? for another one.
- **Not proved**: a picture of it (the pane is hidden, so only computed styles were read); the box of the shop bot or a phone width.
## 1.69.41 - A server that is being rebooted keeps its place among the servers with problems

The owner (2026-10-20, with a picture of the list: UA with "Filtered" and "Bad route" in the middle): "a server had a problem, I rebooted it, and it went to the middle of the list - every server with a problem must be at the top".

- **Cause** (`lab.js labRank`): the reboot mark (1.69.37) makes the row "busy", and a busy row was ranked with the healthy ones, below the servers with problems - although its own problems (Filtered, Bad route, a tunnel down, CPU ...) were still there and still drawn as tags.
- **Fix**: a busy server that still has a problem of its own keeps the rank of the problems (top of the list). What a restart itself causes - it does not answer, SSH does not answer, no login - is not counted, so a healthy server that is being rebooted does not jump to the top (the no-jump rule of 1.69.32 and 1.69.37 stays).
- **Proved**: `tests/test_reboot_mark.py` - the real `labRank` from `lab.js` run in node: busy + Filtered / Bad route is ranked with the problems, busy + only "does not answer" stays a busy row, a busy node and a red or green row are as before.
- **Not proved**: the owner's real list. By design (1.69.32) a list that is scrolled down away from its head keeps the order it has, so a server that gets a problem while the list is scrolled rises to the top when the list is back at its head, not under the owner's eyes.
## 1.69.40 - Every Backhaul transport over every link model can be tested; the IPIP "exists already" message says why

The owner (2026-10-20): "test the kinds of GRE with the Backhaul tunnel - completely", and "is the 'an IPIP tunnel exists between these servers' answer a wrong detection? check it fully".

- **The pair test (Compare, `POST /api/tunnel-test/compare`) can build every Backhaul transport over every link model** (`tunnel_test.KINDS`): the grid is 9 links x 4 transports (TCP Mux, TCP, WS Mux, WSS Mux) = 36 kinds; until now only TCP Mux was there for the links (and TCP for WireGuard). The 23 new kinds sit in the group "Backhaul over another link", none is a default, and the night survey keeps its own short list (`knowledge.READY`): these are tested when the owner asks, and the Doctor's standard order for a cut tunnel (`knowledge.PRIOR`) does not name them (`tunnel_test.GRID_ONLY`): a repair never walks the whole grid. A build over a link already accepted these transports (`fou:tcp`, `ipsec:wssmux`...); only the test list lacked them.
- **"An IPIP tunnel exists already" was RIGHT, not a wrong detection**: on the owner's pair the interface is the IPIP link of the pair's WARM STANDBY (the standby takes another link model than the main tunnel; the kernel holds one IPIP tunnel per pair of addresses). The check was read on the owner's servers (the script `link_facts` and the names of the links of both servers) and in a throw-away network namespace: `ip -d link show type ipip` lists only IPIP devices, never a GRE over FOU/GUE. The messages (the probe and the build) now say that a tunnel of the pair or its warm standby may hold the IPIP link, instead of leaving the owner to look for a tunnel that is not there. The IPIP-family kinds of such a pair cannot be tested while its standby uses IPIP: by design.
- **Proved**: `tests/test_link_kinds_all.py` (the whole grid is in the list once each, every key is read back as its link and transport, the group and the defaults, the survey list is not widened, the messages). **Not proved**: a build of the new kinds on real servers (the kinds the pair test already had were built on them; a new one goes through the same code, `builder.start_build` with `link:transport`).

## 1.69.39 - The question box (Update from GitHub?, Delete?, Reboot?) looks like the alert of macOS

The owner (2026-10-20, with a picture of the "Update the panel from GitHub now?" box): "make this one MacBook style".

- **What changed** (`lab.css`, only styles): the box is a narrow alert - a rounded-square icon on top (blue; red for a dangerous question such as Delete or Reboot), the words centred below it, and two buttons of the same width side by side: the quiet **Cancel** on the left, the blue **Update / OK** (red for a dangerous one) on the right, no glow. The same for the questions that ask for a text or a password. Light mode has its own colours. The box with the plan of "Move the tunnels" and the lists of choices keep their layout.
- **Proved**: `tests/test_fluid_layout.py` pins the rules; on a private demo panel in the built-in browser the box of a question, a dangerous question, a text prompt, a password prompt and the light mode were measured: 340-344 px wide and centred in a 1024 px window, the two buttons 141-145 px each, nothing wider than the box, blue / red / grey colours as intended.
- **Not proved**: a screenshot of it (the pane was hidden, so pictures came out blank - only measurements); a phone width (the old phone rules still give it the full width).
## 1.69.38 - The Bandwidth column of the Lab is the LINE of the server (not the traffic it carries); three pictures of the shop bot's Mini App are fixed

The owner (2026-10-20): "the Bandwidth column does not show the overall bandwidth of the server - I do not want the used bandwidth, show only the live number of the server's overall bandwidth" and, asked what "overall" means, he chose **the port / line capacity of the server**. Then three pictures of the Mini App on a phone: "fix the size of the text here", "fix this too", "this text is garbled - remove it if it is extra".

- **Bandwidth = the line** (`ssh.py` facts script `LINK=`, `facts.py link_mbps` and `_peak`, `lab.js labCap/labCapCell`, `demo.py`): the sampling job now also reads `/sys/class/net/<card>/speed`; the column shows that number (e.g. 1000 or 10000 Mb/s). Most virtual servers answer -1 (the card does not say its speed): then the column shows the highest speed seen on the server in the last week with a "≥" in front (kept in `extra.peak`, written only when it changes), and "—" until something was seen. The column no longer shows what the server carries now; "Traffic used" (bytes since boot) is unchanged. A PasarGuard node row shows the line of the server it runs on, as every server row (this replaces the interim "upload of the server" of 1.69.37).
- **Mini App, the pay button** (`bot/miniapp/app.css`): "Pay 130,000 Toman from the wallet" was clipped on both sides when two buttons shared a narrow row (the base button is one line with a fixed height). A button row now wraps, the main button gets the larger part, and a label that does not fit goes on a second line (the button grows) instead of being cut.
- **Mini App, the top-up page**: the wallet address (42 letters with no space) came out of its card on the left and pushed the copy button away; the value now wraps inside the row and the button keeps its place. A long word or code inside a note wraps too.
- **Mini App, the invite page** (`api.php bannerText`): the bot keeps the invite text as a message record (`{"type":"text","text":"\u0644..."}`) and the page drew that record as words. The API now takes the words out of the record, puts the invite link where the text says `LINK`, and sends nothing when what is kept is not words; the page then shows only the reward line and the link. The description of a plan keeps its own line breaks. The Mini App files are version 3.0.2 (the phone's cache loads them again) - deploy them from Settings → Shop bot.
- **Also**: the 1.69.37 entry below was missing from this file in that release (the version and this file were left at 1.69.36); it is put back here.
- **Proved**: `tests/test_facts.py` - the script asks the card for its speed, `LINK=1000` is read, `-1` / nothing / garbage give none, the peak is kept for a week, is written only when it changes and nothing is made up from a server that moved no bytes; `tests/test_web_menu.py`, `tests/test_reboot_mark.py`, `tests/test_fluid_layout.py` - both row kinds draw the Bandwidth cell with the one function and the header says it; `tests/test_miniapp_layout.py` - the CSS rules, the descr class, the cache version, and (with PHP) the invite text is words or nothing; Persian for the header and both tips.
- **Not proved**: what the network cards of the owner's real servers answer (a virtual server usually says -1 and then the number is "≥ the highest seen", which can be lower than the real line until it has been busy); the Mini App was not opened in Telegram on a phone (the CSS and the PHP function were tested, not a rendered page); the new numbers appear only after the next sampling (about two minutes after the panel is updated) because the facts script changed.
## 1.69.37 - A server that is being rebooted says "Rebooting" on its row; the PasarGuard servers show the bandwidth of the SERVER

The owner (2026-10-20, two messages): "I rebooted the server - the reboot mark must show on the server, and the page jumps" and, with a picture of the PasarGuard rows: "the bandwidth numbers of the PasarGuard servers are still not right - show the server's own bandwidth in the Bandwidth column, not the bandwidth the users use".

- **The reboot mark** (`nodes.reboot`, `facts.sample_server`, `lab.js`): the Reboot item now writes the same busy mark as an install does - the row says **Rebooting · waiting for it to come back** in blue (Persian: در حال ری‌استارت · منتظر برگشتن سرور) while the server is down. Before, the row turned red "offline" at the first failed SSH, went to the TOP of the list (a problem first) and back when the server answered: that was the jump. The mark goes when the server answers again with an uptime shorter than the time since the restart was asked for (it has started again), or after 180 s when it still answers with its old uptime (the command did nothing: the row is honest again). The stop of its SSH is no "does not answer" alarm while the row says Rebooting; a server that is not rebooting still gives the alarm.
- **The Bandwidth column of the PasarGuard rows** (`lab.js labNodeRowHtml`): the rows of the PasarGuard profile showed ↓ and ↑ of the users of the node (what the panel counts). When the node runs on a server of ours that is on and answers, the column shows what the SERVER gives out - the card of its default route, its upload, exactly as every other row. A node that is not on a server of ours keeps the panel's numbers. The column Traffic used is unchanged (its tip says the panel counted it, for a node).
- **Proved**: `tests/test_reboot_mark.py` - the mark is written by Reboot; the stop of the SSH while rebooting writes no "bad" event and keeps the mark; a server that is not rebooting still gives the alarm; the mark goes with a restarted uptime, goes after 180 s with an old uptime, and another busy mark (an install) is left alone; the bandwidth cell of a node row takes the server's numbers first (source of the page). The facts, layout and alignment suites pass.
- **Not proved**: a real reboot on the owner's server (the SSH stop and the restart were played with fakes); how long his servers take to come back (the mark waits as long as they need, but is ignored by the page after 15 minutes, as every busy mark); the look of a PasarGuard row with the new cell (the test panel had no PasarGuard node rows - checked in the source and by the syntax only).
## 1.69.36 - A DNS record that was changed shows so at once: the page no longer waits for every domain of every account to be read again

The owner (2026-10-20, with a picture of the CDN page: the toast "Record changed" and the old record in the list): "when I change a name or a DNS I must refresh the page for it to update".

- **Cause, found by playing it on a test panel with a Cloudflare account of 20 domains and a provider that needs a second for each request**: the write called `dnscache.forget()`, which throws the whole copy of the records away, and the page then asked for a FRESH read - every domain of every account (the owner has 26 domains and 327 records: 20-30 seconds). The toast came after 2.3 s and the old record stayed on the screen until 22.5 s; a refresh by hand got the copy with the write laid on it at once. (1.69.10 had laid the write on the list for 45 seconds for a lagging provider; that was right and stays, the wait was the problem.)
- **Fix** (`services/dnscache.py stale()`, `api/dns.py`, `app.js`): a record that is made, changed, deleted or has its cloud switched only marks the copy OLD (`stale`): the rows are kept with the write laid on them, the next look gets them at once and the providers are read again in the background. The page does not ask for a `fresh` read after such a write any more (it still does after an account is added, tested or removed). Test panel, same Cloudflare: the new value showed 0.6 s after the toast instead of 20 s.
- **Proved**: `tests/test_dns_lag.py` - after a write the page's read has the new value with NO read of any provider before the answer and the renewal asked for in the background; a delete the same; `stale()` keeps the rows and never makes up a copy that was never read (the DNS, outbound and alignment suites still pass).
- **Not proved**: the owner's real ArvanCloud and Cloudflare (the providers were the suite's fakes and a slow fake); that the background read finishes while the page is open (the page asks again every 15 seconds); the write itself (the PUT) still takes as long as the provider takes (2.3 s with the slow fake).
## 1.69.35 - A build whose link does not pass tries the other link models

The owner (2026-10-20), after a build between two servers stopped with "the GRE link is up but the inner addresses cannot ping each other (0/10)": "there are several GRE models - when the build finds that one does not answer it must test the others".

- **The nine link models are tried one after the other** (`builder.LINKS`, `FALLBACK_LINKS`, `next_link`, `_build_worker`): when the link under Backhaul does not pass - the inner addresses do not answer each other, the link answers and then loses its packets, its unit does not start, the kernel lacks its module, or the model cannot be prepared (no free UDP port, IPIP already used by the pair) - everything that model made is removed on both servers and the next one is built. The order: GRE, GRE over UDP (FOU), GRE over UDP (GUE), IPsec in UDP, IPIP over FOU, IPIP over GUE, IPIP, IPsec (ESP). The ones in UDP come before the raw IP protocols because a route that filters protocol 47 passes UDP most of the time. WireGuard is tried last and only where its tool is on the servers already and the owner allowed it: a build never installs a package by itself for a fall-back. When none passes, the story says which were tried and the build stops as before (the Iran server list of "Iran: auto" goes on with the next Iran server).
- **Who gets it**: a Backhaul tunnel the owner builds from Create tunnel (the API puts `link_fallback` in the options; `start_build` takes it out, it is not written in the tunnel's record). The test of a pair, a build of the Doctor, a rebuild and a standby are NOT walked through the models (they choose a model on purpose or have their own order). A failure that is not about the link (the Backhaul service does not start, the control connection does not come) is never retried with another model.
- The tunnel is recorded with the model that passed (`link` and `link_port` of the record, as for any non-GRE tunnel), and the words of its story and its event now say the model that ran instead of "GRE" for every model.
- **Proved**: `tests/test_link_fallback.py` (the order; a walk that ends on the third model with the first two removed; no walk for a standby or a build without the option; all eight failing says which were tried; WireGuard last and only when allowed; an error that is not the link is not retried; the API option) and `tests/test_links.py` (the old API test with the new option). **Not proved**: on real servers - a pair where GRE is filtered and another model passes; the time the walk takes when many models fail (each failed model costs about 20-40 s of checks on its own; eight can take several minutes - the build's story shows each step); the Create tunnel page does not show the walk any differently from a normal build story.

## 1.69.34 - The box of "Move to a better server…" opens at the click

The owner (2026-10-20, in Persian, with a picture of the box that opened only after the confirm): "this box must open from the moment I click Move."

- **One window from the click** (`lab.js tunMoveAway`, `tunMoveGo`): the window opens at once with "Looking for the best Iran server… the line, the resources and the free ports of each Iran server are checked" (before: a toast, and nothing on the screen for those seconds); when the servers have been looked at, the SAME window shows the plan with a button for each of the best servers and Cancel (before: a second, small box); the pressed button turns it into the steps of the move (1.69.28). A refusal (license, DNS account, no server with room, ...) is shown in it too, with Close.
- It is a "keep" window: only its Close button closes it (1.69.28). Closing it while the servers are still being looked at stops there - nothing is started; closing it during the move changes nothing, the move goes on.
- The generic `html` option that 1.69.25 gave `glassChoice` for the plan, and its CSS, are gone: nothing uses them any more.
- **Proved**: `tests/test_move_server.py` (the page code: the window opens before the plan is asked for, no toast and no second box, the buttons and Cancel inside it, the steps follow in the same window). **Not proved**: how it looks and behaves in a browser - it was not opened.
## 1.69.33 - Third walk of the test panel: the CDN page, the first-run card, the Shop bot page, and ~400 tooltips that stayed English in Persian

Third round (1.69.29 and 1.69.31 were the first two), on a test panel (never the live one): the CDN page with two DNS accounts (the suite's own fake ArvanCloud and Cloudflare, with long names and a TXT record), an empty panel for the first-run card, a copy with the Shop bot page switched on and its data answered by a stand-in, at 1280 / 1024 / 768 / 375 px, in English and in Persian. Measured, not guessed: text cut without an ellipsis, a box wider than its parent, a button pushed out of the window, placeholders wider than their box.

- **The page header ran out of the window on every page at 1280 px** (`fluid.css`): the title block would not shrink below its subtitle (up to 900 px) and the Persian / day-night buttons ended 19 px outside the screen. The block gives way now, the subtitle is cut with "…" and says all of itself in its tip (`app.js`).
- **Tunnels > CDN, the bar**: "Add record", "Records 6", "Changes", "Accounts 2", "Failover" and the three counts were all cut at 1280 px and below (every item of the row shrank alike), the tabs and the search ran out of the box under 800 px, and the domain box was 51 px wider than its label. Now the button, the counts and the tabs keep their words, the domain box and the search give way, the counts go when there is no room, and a narrow bar wraps into two rows (the bar is the container - a container cannot ask about its own width).
- **CDN, the tables**: on a phone the records table kept Type, Name and the buttons - the ADDRESS, what the table is for, was gone with its column; it is shown under the name when its column drops. The Port column was cut ("PO…" / the 443 pill) - the columns say their least widths (`data-min`). A cut value (a long TXT), an account name and a change line tell themselves in a tip; in Changes, "ArvanCloud · by hand · deleted" was cut before the verb - it wraps now. The counts "Domains" and "A records" have tips; the address hint of the form (344 px in a 298 px box on a phone) and the Name hint are shorter and say the rest in a tip; the preview line of the form has a tip.
- **The first-run card ("Getting started")**: the inputs of Telegram and the AI key ran 28 px out of their card, the numbers of the "Infrastructure health" list were cut - both were grids with no column size (one column as wide as the widest text); and the hint of each card ("Marzban or PasarGuard: its address and an API key", the thing that tells a new user what to do) was one cut line - it wraps. The Telegram placeholders are short, the format is in the tip. In Persian this card was English (its lines come from the server): translated.
- **The Shop bot page** (switched off in the menu, `SHOPBOT_UI = false`, by the owner's order - walked with the switch on in a test copy only): the Services box took 732 of 970 px and the four counters were 52 px slivers with their numbers drawn outside (1200-1500 px screens); on a phone the counters made the page 35 px too wide; "Manage" was cut by 5 px; "1 errors" in the bot log chip.
- **Persian**: `i18n.js` skipped a text box with its attributes - the tip and the hint of a TEXTAREA (the probe tokens, the API token boxes) never got Persian; the language button's `aria-label` is Persian in Persian. ~400 tooltips, placeholders and aria-labels, and the words "Network", "General", "Admins", "Last N days", "Top users", the tunnel menu ("Fix…", "Speed test…", "Rename…"), the 24 h timelines ("23:39 - 00:09: answered"), the tunnel's "Users use it now: ↓ … ↑ …" tip and the bandwidth tips are in `i18n.fa.ui2.js`. Texts that carry a number ("2 panels", "1 error") go through a small function that matches a number only - a template `{} panel` would have turned "Demo panel" into half Persian.
- **Proved**: `tests/test_ui_walk3.py` (9 tests): the stylesheet and page source of each fix; the real translation layer run with node on 30 tips (numbers and names are kept, "Demo panel", "My account", "Connection error" stay as they are); and a scan of EVERY title / placeholder / aria-label written as a sentence in the pages' source - a new one without Persian fails the suite (add it to `i18n.fa.ui2.js`). In the browser, measured after each fix: nothing is cut or outside its parent on the CDN page (all four tabs, the three forms, create / change / delete / restore, a wrong Cloudflare key shown inside the form, the failover preview), the first-run card and its windows at 1280 / 1024 / 375, the Shop bot tabs at 1280 and 375, the CDN page in Persian (RTL) and in light mode.
- **Not proved / left**: the real ArvanCloud and Cloudflare (the suite's fakes answered); the Shop bot with the real bot - its data was a stand-in, so the Texts, Settings and Tools tabs were not drawn, and the Plans tab's fields are guessed; **the Shop bot lists scroll sideways between 760 and about 1330 px** (the Action column - Approve, Edit, Delete - is the part out of sight): a sticky column was tried and hid the State column, so nothing was changed - the page is switched off, it needs a decision (cards under 1330 px like on a phone); the PasarGuard pages with data (their tips were translated from the source, not looked at); light mode of the other pages; tips built from a condition are only found when the page shows them (the demo data showed most of them); dates in Persian still use Latin month names; the browser pane that tested this was hidden, so no timing was measured.
## 1.69.32 - The page no longer jumps at the end of an install: the rows of the server list keep their places while it is scrolled

The owner (2026-10-19, in Persian, with a picture of the list while a server was being installed): "the page jumps here while it installs".

- **Cause, found by playing an install on a test panel** (the steps of `onboard` are written to the server's `busy` line, as the real install does): while the install runs the row of the server (the last one, blue) does not move. When it ends the server gets a problem of its own (a yellow or red lamp: a high RAM, a failed check ...), and the order of the list - "a server with a problem first" (1.69.23) - put its row at the TOP; every row under it went down by one row while the list stayed where the owner had scrolled it, so what was on the screen changed (a row went from y = 568 to y = -982, the rows below it by +62 px).
- **Fix** (`lab.js labSorted`): scrolled away from the head of the list (more than 40 px), the rows keep the order they have; a new row goes last; back at the head of the list the sort is fresh and the problems come first there (checked: the row went to the third place when the list was scrolled to the top). The owner's rule "a server with a problem first" is kept at the place where it is looked at.
- **The same page, drawn from scratch** (a row came or went; `labRedraw`) began at the top again: it keeps its scroll now, as the redraw of the other pages does.
- **Proved**: `tests/test_fluid_layout.py` - `labSorted` run with node (a problem makes no row move while scrolled; a new row goes last; at the head the sort is fresh; the departed row is gone); in the browser a test panel with 29 servers: the install of the last server played step by step (Backhaul, BackPack, Paqet, Chisel, Xray) - the list did not move; the end with a problem: before the fix the row jumped to the third place, after it stayed (scrolled) and went up when the list was scrolled to the head.
- **Not proved**: on the owner's real install (the steps and the problem that ends it were played by writing them to the test panel's database); a list that is scrolled by less than 40 px; the phone layout (there the page scrolls, not the list box, so the rule treats it as "at the head").
## 1.69.31 - A cut text tells all of itself in a tip; the Live button stays inside the row of the Doctor's tasks

Second round of the walk through the test panel (1.69.29 was the first).

- **A text that is cut with a dash tells all of itself in a tip** (`fluid.js`): a server named `a-very-long-server-name-for-testing-0123456789` showed `a-very-lon…` and nothing said more (the row's own tip is "Click for the details"). When the pointer comes to a text whose box is cut with an ellipsis, the whole text is written into its `title` (a text that has a tip of its own keeps it). Nothing is drawn in advance.
- **The Doctor's task rows at 1024 px** (`fluid.css`): the route of a row ("ir-north-1 ⇄ ae-edge-1 · backhaul · tcpmux", 232 px) kept its whole width and the Live button was drawn 70 px outside the row's box; the route is the text that gives way now. 
- **The server list**: the header "Problems" was 2 px too narrow (cut), the name column gives 4 px for it; **the users table of a PasarGuard panel**: the date pill of Expires (129 px) was cut at 768 px - the column says its least width.
- **Proved**: `tests/test_fluid_layout.py` (the tip's code and its rule that an own tip stays, the task row rule, the Expires header). In the browser: the long name gets its full title on the pointer, the Live buttons of twelve task rows end inside their rows (950 < 965 px). **Not proved**: a live resize of an open window (the browser pane that tested this was hidden, and a hidden page does not run the size observers: after a resize the table was fitted only by a `resize` event sent by hand); the tip's look (the browser draws it).
- **Looked at in this round and found fine**: an empty panel (no server, no tunnel, no panel: the Servers, Tunnels and Doctor pages say so in one box); Escape closes only the top box (a confirm over a window); the text size "Large"; the long name in the window of a server, on a phone; the double click on a confirm sends ONE request; searching the server list does not move the page (no layout shift, the box keeps its height); the Doctor and the Dashboard keep their scroll over 30 seconds. **Not walked**: the CDN page with real DNS accounts, the Shop bot page, the 2-step sign-in and password forms, the first-run wizard, the tooltips in Persian (many are still English).

## 1.69.30 - A moved tunnel gets the BEST type for its new pair, not the type it had

The owner (2026-10-19, in Persian, watching a move that was building the same type again - a Chisel WSS tunnel with 50 % loss): "at the moment of a move the tunnel type stays the same as before, when the better ones do not answer - here too you must pick the best tunnel type."

- **Before the new tunnel is built** (`failover._best_kind`, used by `_work` for every move: a tunnel of the menu, the Doctor's moves, the return, a dead server), the tunnel types are TESTED between the **target** Iran server and the foreign server (`autopilot._recommend_now`: delay and loss, the order the Doctor knows for the pair, `knowledge.plan_for`; the type the tunnel has is always in the test). One test per pair - the tunnels of a pair share it.
- **The choice** (`autopilot.choose_better`, the Doctor's own rule): the best type that connects is built when its line is clean (under 5 % loss) and it beats the type the tunnel has by 10 points; otherwise the type it has stays. If the old type did not connect on the new pair, the best that connects is built (clean first); if nothing connects, the old type is built as before. A test that cannot run (another test too long, an SSH error) does not stop the move: the old type is built as before, and the story says so. The options of the old type are passed only when the type does not change.
- **A dead server** (the old tunnels are dropped at once): the test stops at the first type that connects, in the Doctor's order - no time for a full comparison.
- **The old tunnel keeps running meanwhile** (a server that is on): the test adds a minute or more per type before the new tunnel is built; nothing is cut. The story shows "tested N tunnel type(s) between A and B: type score, …" and the type built; the box of the menu item says it.
- **Proved**: `tests/test_move_server.py` - the choice for a clean winner, for 5 points more only (stays), for a lossy old type, for an old type that did not connect, for nothing connecting, for a test that cannot run, the test once per pair, between the TARGET and the foreign server, the first-that-connects mode; and the whole move built as `bp:tcpmux` with no old options, the old tunnel removed afterwards. The tests that are not about this switch it off (`failover.PICK_BEST`, `tests/conftest.py`). **Not proved**: on real servers - the real pair test (it builds temporary tunnels), how long it takes with six types, and that the type chosen carries the users better than the old one (the score is delay and loss only, no speed).
## 1.69.29 - Walking the panel on a screen of 1024, 768 and 375 px: names, addresses and buttons are no longer cut, the ⋯ button stays, Persian pages are translated every time

The owner (2026-10-19, in Persian, paraphrased): "start a test panel and test everything like a person would: every page, button, menu, size of the screen, spacing, cut texts, and the page jumps - fix every bug at its cause". This release is the first round: a test panel with invented data, opened in a real browser at 1440, 1024, 768, 375 and 320 px, in English and in Persian, with a script that measures instead of guessing (texts cut with a dash, boxes wider than their parent, empty buttons, scroll positions and layout shifts before and after a change).

- **The server and tunnel lists no longer cut what matters** (`fluid.js`, `lab.js`, `app.js`): the fluid layer gave every column a least width of 72-92 px, but the name cell holds a lamp and an icon before the letters, so at 768 px a name was cut to ONE letter ("i…") and at 1024 px an address to "198.51.1…". A header can now say `data-min` (its least width) or `data-fit` (as wide as its widest cell, up to this: measured with the real font, so Persian text is measured too). The server list: Name fits its cells, IP 168 px, Problems 88, Load 128, Traffic Mbit/s 126; the tunnel list: Tunnel fits, Type 176, Iran node and Foreign node 120, Bandwidth 130. When the box cannot hold that, the less important columns go (as before) - the name and the ⋯ button never.
- **The ⋯ button of a row vanished a few seconds after the page opened** (`app.js morphNode`, `fluid.js`): the server list refreshes its rows where they stand; the mark "fl-off" that the fluid layer puts on a cell of a dropped column was stripped by that rewrite, the cells of dropped columns showed again and pushed the last column (the ⋯ button) out of the box at a width where columns drop (a tablet, a phone). The mark is kept, and the fluid layer now looks again when a table's cells lost it.
- **Two buttons of the tunnel bar were empty circles** (`app.js`, `fluid.css`): "Check all" and "Restart all" held only words, and under 940 px the words go - nothing was left. They have icons now. At 1024 px the whole bar shrank alike and cut "Check all" to "Chec…": the buttons, the counts and the tabs keep their size, the search box is what gives way, and the words "up / down / unstable" go first (a container query) - the dots keep a tip. On a phone the All / Problems tabs were an 8 px sliver.
- **A phone**: the Doctor's cards (`minmax(340px, 1fr)` in a box of 300) were 48 px wider than the screen - nine grids now ask for `min(Npx, 100%)`; the Live button of a Doctor task row was 43 px beyond the screen (the row wraps under 480 px); "No account yet. Add an ArvanCloud account or …" ran past its box in one line (a note over the columns wraps); the line under the load bar ("RAM 57% · Disk 28%") was cut on BOTH sides ("AM 57%") and is now cut at its end with a dash.
- **Persian pages stayed English** (`i18n.js`): `MutationObserver.disconnect()` throws away the changes that are waiting for the observer; a page written in a microtask between the observer's call and the flush (Settings → Api & Key, every time) was never translated until the language was switched twice. The waiting changes are taken first.
- **The keyboard focus survived nothing** (`app.js refreshNow`): while nothing is open the page is drawn again every 8-15 seconds (a new set of elements), so a button that had the focus - the ⋯ of a row, a tab - was gone after a few seconds and the next Tab began at the top. The element is found again (same kind, same words, the same place among those that look alike) and gets the focus back without scrolling. The tab counts of the server list were also rewritten at every quiet refresh even when the number was the same (a text node replaced every 1.6 s): they are written only when they change.
- **The Panel pages (Users, Admins) at 768 and 375 px** (looked at against a PasarGuard panel made of the test suite's own fakes, no real panel): the bar of the panel was a fixed 56 px high, so when its tabs, search and buttons wrapped to a second row the table was drawn OVER the bar (`lab.css .pn-bar`: `min-height`); the panel's own box was squeezed to "P…" by the tools beside it; the status pill ("active", "on hold") was cut, the Traffic cell wrote its "used" and "left" over each other (85 px for a cell that needs 116), the Users and Admins tables gave their name column too little - the headers of those two tables now say their least widths (`data-min` / `data-fit`), so a column goes before it is cut.
- **Persian texts that were English** (`i18n.fa.ui2.js`): walking the pages in Persian showed about 110 texts with no translation - the whole Doctor page (Doctor Mode, Auto Fix Tunnels, Auto Move to Iran Server, Active Tasks, Detail, Live ...), the headers Traffic used / Traffic Mbit/s / Bandwidth, the Security, Backups, Api & Key, Appearance (the theme names and their lines) and About & update boxes, the dashboard's "Tunnels up". They are added (simple Persian, the words the file already uses). The tooltips of many buttons are still English - the list of them is long and was not done in this release.
- **The window of a server with three tunnels** (Access & status): the tunnel chips were one nowrap row cut at 49 px each ("ae1-no", "de1-no", "tr1-no"); they wrap now. In the tunnel list the Iran node and Foreign node columns fit their widest cell (a name with the "⚠ ×2" mark and an address was 8 px wider than its column).
- **A long name**: in the Users list of a PasarGuard panel the name of a user was cut in the middle of a letter with no dash (`.usr-nm > b`); it has its "…" now. The suggestions of the "Add profile" window (Iran, Foreign, Marzban, PasarGuard) were one row of 337 px in a box that held 340: the last one was cut - they wrap.
- **Light theme**: the chips of the task rows of the Doctor ("waiting for the server", "watching") kept the light grey of the dark theme - 1.5 : 1 on white, unreadable (`light.css`).
- A healthy row of the server list had a class called `undefined` (`labTrCls`).
- **Looked at and found fine**: deleting a server in the middle of the list (the scroll stays, no layout shift); the confirm box takes ONE request on a double click; Esc closes a window; a long status message does not move the page.
- **Proved**: `tests/test_fluid_layout.py` - `morphNode` run with node (the mark of a dropped column is kept, an ordinary stale class is still removed), `labTrCls` run with node, the headers, the icons (no button holds only words that go), the bar rules, no grid asks for more than its box, the Persian layer's takeRecords, the focus code's place in the redraw, the long-name and light-chip rules (14 of the 15 tests fail on the code before this release; the fifteenth is a control). The same walk, before and after, in a real browser: 1024 / 768 / 375 / 320 px in English and Persian - no box wider than its parent, no empty button and no sliver left on the pages of Servers, Tunnels, Doctor, CDN and Settings; a focused ⋯ button still had the focus after two redraws (21 s); Settings → Api & Key in Persian is translated at the first open.
- **Not proved**: the panel with the owner's real servers (a longer name or a 255.255.255.255 address may still be cut at the narrowest widths - a column then drops instead); Safari and Firefox (the new rules use `:has()` and container queries, as the old ones did); a real touch screen (the phone was a 375 px window with touch emulation); the page timings (the browser pane was hidden while testing, so animations and load times were not measured).

## 1.69.28 - "Move to a better server…" opens a box that follows the steps and stays; closing it does not stop the move

The owner (2026-10-19, in Persian): "when I press it, a box must open and not close, so we can see how the steps go - and if we close the box the steps must go on."

- **The box** (`lab.js tunMoveProgress`): after the confirm, a window opens with the story of the move line by line - the new tunnel built, the DNS records, the wait for the TTL, the wait for the old tunnel to be quiet, the removal - read every 1.5 s while it is on the screen. It closes only with its **Close** button (it is a "keep" window; the page behind it is drawn again without taking it away: `state.keepWin`); the last line also comes as a toast. A window of a tunnel that was open is replaced by it.
- **Closing it changes nothing**: the move runs on the server in its own thread; the story goes on in the Tunnel Doctor (History / Moves) and is followed again every 6 s while the box is shut, so the toast at the end still comes.
- **The move is now an action of its own** (`failover.run_plan` starts it as `move`, like the Doctor's single-tunnel move): its story ends "Move finished." / "Move did not succeed." - it ended with "All done - the problem is fixed" / "Repair did not succeed", words of a repair. It is listed with the other moves in the Doctor's history.
- **Proved**: `tests/test_move_server.py` (the plan starts as a `move` action; the page holds the box, the follow of 2400 polls, the Close-only window and the end line), the other suites unchanged. **Not proved**: the box on a real screen (the look, the follow while the page redraws, closing and opening again) - it was not opened in a browser; a move on real servers.
## 1.69.27 - A moved tunnel's old half is removed only when nobody uses it any more

The owner (2026-10-19, in Persian, on the item "Move to a better server…"): "when I press it, the old tunnel must not be cut until the new one is in place - the user must not feel a cut."

- **What already held** (`failover._work`, source server on): the new tunnel is built and checked on the target first (the old one keeps running); only when every tunnel of the plan is up are the DNS records changed; the old tunnel then stays for the records' TTL + 5 minutes (a proxied record counts 300 s).
- **What did not hold**: after that wait the old tunnel was removed whatever it still carried - a client that kept the old address (a config with the address itself, a resolver that ignores the TTL) was cut at that moment. Now the old tunnel is first waited for to be quiet (`autopilot._wait_quiet`, the panel's own rule: under 1.5 Mb/s both ways together, up to 30 minutes, `failover.QUIET_LIMIT`). If it is still in use, it is **kept**: the new tunnel keeps running beside it under its temporary name, the story and the history say so (`ok` false, "the old tunnel is still in use, kept") and you remove the old one by hand from its menu when it is quiet. The DNS stays changed. The box of the menu item says it.
- **Not changed**: when the source server is OFF (a dead server) the old tunnels are still dropped at once - nothing can be carried by a server that does not answer.
- **Proved**: `tests/test_move_server.py` - the order build → DNS → TTL wait → wait for quiet → removal; and with a tunnel that stays busy: nothing is deleted, both tunnels run, the DNS did follow, the story and the history say it. **Not proved**: the 1.5 Mb/s rule on a real tunnel in a real move (a few users on the old address may still be under it and feel the cut); how many real clients ignore a TTL; on real servers at all.
## 1.69.26 - "Move to a better server…" is in the menu of a TUNNEL, not of a server; it moves that tunnel

The owner (2026-10-19, in Persian, after seeing the menu of a tunnel without the item): "remove it from the server part - you must add it to the tunnel part."

- **Moved** (`lab.js`): the item is gone from the menu of an Iran server (row and window) and stands in the menu of a tunnel, before Remove: **Move to a better server…** (`tunMoveAway`). It moves THAT tunnel - and, when another tunnel shares a DNS record with it, that one too (listed in the box as "moved with them"). The DNS records that serve only the moved tunnels follow; a record that also serves a tunnel that stays is left alone. The box, the buttons for the best three servers and the rules for choosing the target (line quality, resources, tunnels carried after the move, free ports - the panel's and what listens) are those of 1.69.25.
- **API**: `GET /api/failover/tunnel-move-plan/{tunnel_id}` and `POST /api/failover/move-tunnel/{tunnel_id}` (the plan is made again at the confirm, as before); `failover.move_plan` / `move_server` take the tunnels to move (`failover.server_of`). The routes of 1.69.25 for a whole server (`move-plan/{server_id}`, `move-server/{server_id}`) stay in the API and move every tunnel of the server; no page uses them now.
- **Proved**: `tests/test_move_server.py` - the plan of a tunnel (only its tunnel and the records that serve only it; a tunnel that shares a record pulls the other in), the refusal when the tunnels changed since the plan was shown, the confirmed move through `POST` with the builder and DNS providers faked (one tunnel built first, the wait, the old one removed, one record changed, the other tunnels untouched), and the page (the item in the tunnel menu only, the box, the Persian texts). **Not proved**: as in 1.69.25 - a real move on real servers and DNS accounts, the box on screen, "better than the source server" is not compared.
## 1.69.25 - "Move the tunnels…" in the menu of an Iran server: the tunnels go to a better Iran server and the DNS records follow

The owner (2026-10-19, in Persian, paraphrased): "an option in the menu of every server: when it is pressed on an Iran server, the panel moves the tunnels of that server to a better-quality Iran server that has free ports and free resources, and changes the IP in the DNS records by itself - ArvanCloud or Cloudflare."

- **The menu item** (`lab.js`, the row menu AND the server window's menu, Iran servers only): **Move the tunnels…**. It reads the plan, shows it in ONE box - the tunnels (and any that must go with them because they share a DNS record), every DNS record that will follow with the account it lives in, the servers that were left out and why, and whether anything is cut - with a button for the best server and for the next two. The pressed button starts the move; the story is followed in the background and its last line comes as a toast. The box is built from small elements so the Persian layer translates each part (`glassChoice(text, choices, {html})`).
- **The API reuses the failover**: `GET /api/failover/move-plan/{server_id}` (nothing is changed: the plan, `candidates`, `skipped`, `blocked` = why it cannot run) and `POST /api/failover/move-server/{server_id}` (`target_id`, `tunnels` = what the plan showed; 202 + `action_id`). The plan is made AGAIN at the confirm and it runs only if it is still the same set of tunnels and the confirmed server still passes. The work is the failover's `_work`: when the source is on, the new tunnels are built and checked first, the DNS is changed, the old tunnels stay for the TTL + 5 minutes; when it is off they are dropped at once. Every changed record is in `dns_log` and in the Doctor's Moves, each with Restore.
- **The choice of the target now counts room and free ports** (`failover.room_for`, `rank_targets`, `_ports_problem`; the ranking itself - ping and loss from the foreign server, CPU, RAM, load, tunnels carried, the port of each tunnel - already existed in `detect.auto_iran` and is reused). New for this button: a server is out when its line quality is under 50, its resources under 40, or it would carry more than 8 tunnels once the moved ones are on it (the old ranking looked at ONE tunnel at a time, so three tunnels could land on a server that had room for one); the ports of ALL moved tunnels must be free there - no tunnel of the panel forwards them AND nothing listens on them (read over SSH, `simple_facts`); a server whose ports cannot be read, or when one tunnel could not be ranked, is no target (a move never starts on a guess); an equal rank is decided by the first tunnel's order, not by chance (`pick_target`). The Doctor's own automatic moves keep their rules.
- **Refused, with the sentence that says why**: no failover license, no DNS account saved, the panel read-only, the failover switched off, no server with room, a DNS account that did not answer (its records could not follow), the server carries no tunnel, the tunnels changed since the plan was shown.
- **Proved**: `tests/test_move_server.py` - the pure room and order rules; the plan with an automatic target (a poor line and a program on port 443 leave two servers out and the third is chosen, every record of both accounts listed, nothing changed by a plan); no target when the tunnels together do not fit, when a port is used by a tunnel of the panel, when the ports cannot be read, when one tunnel cannot be ranked; an explicit target judged by the same rules; each refusal above through the API; the whole move through `POST` with fakes for the builder and the DNS providers (new tunnels first, the wait, the old ones to the trash, three records in two accounts re-pointed, `dns_log` + history written, undo puts every record back); the menu item, the box and the Persian texts are in the page; the box's HTML run through node (names escaped). The Doctor, failover, create and menu suites still pass.
- **Not proved**: a move on real servers and real DNS accounts (the DNS providers and the builder were faked, as in the other failover tests); the real listening-ports read of a candidate server; the box's look on screen and on a phone; that the target's quality is BETTER than the server it leaves - the plan picks the best available that has room, it does not compare with the source server (an Iran server that is the best one itself would still be moved if the owner asks); UDP ports are not looked at; an automatic move still does not apply the new room rules.

## 1.69.24 - In the bandwidth cell only the overall number is red; the number of the users never changes colour

The owner (2026-10-19): "when the overall bandwidth is low only its colour must be red"; asked what makes the users' number red, he answered: "the users' bandwidth must never change colour, only the overall bandwidth is red when it is low".

- **The overall (capacity) number is red when it is low**: a drop, a test that moved nothing, and now also the Doctor's verdict "very slow" (it was amber; `lab.css .bwcap.slow`). The amber of a "low" state class is kept for the old rule only.
- **The number of the users and its bar have ONE colour at any load**: the bar was amber from 70 % and red from 90 % of the capacity (`bwbar hot / full`); that colouring is gone (`app.js bwBar`, `lab.css`), the percentage is still in the tooltip.
- **Proved**: `tests/test_web_menu.py` (the classes of the cell and of the bar, the CSS rules). **Not proved**: how it looks on the owner's screen (the colours were checked in the tests, not in a browser).

## 1.69.23 - A new server: no cores at adding, the GRE and spoof tests choose their targets, one total and one live number in the server list; the standby on by itself; a slim bar

The owner (2026-10-19), a run of requests on the pages of servers and tunnels.

- **The tunnel cores come with the FIRST TUNNEL, not with the adding of a server** (`onboard.ensure_all_cores`, called by `builder._build` and `build_other._build`): "when a server is added the cores must not be installed; only when the first tunnel is made - and then, even if it is a Backhaul tunnel, all the cores". The tick "Put the tunnel cores on it now" of the Add form is not ticked any more (the menu Install → Tunnel cores still does it by hand). At the first tunnel built on a server (a temporary test tunnel does not count) every core is put on both servers - what is there already is left alone - and the server carries the mark `cores_all`; a core that fails is told in the build's story, does not stop the build, and the next tunnel tries again.
- **The GRE test of an Iran server goes from Iran toward a foreign server** (`nodes.gre_probe`): it built the temporary link from ANOTHER IRAN server; now an Iran server is the tester and a foreign server (on, enabled) the peer, up to two tried; a foreign server is tested from an Iran one, as before.
- **The spoof test picks its target by itself** (`nodes.spoof_candidates`, `nodes.spoof_auto`; no `target_id` needed in `POST /api/servers/{id}/spoof-test`; the page opens no list): servers of the OTHER kind that are on - a foreign one for an Iran sender - the proven listeners (anyone's last test that said "can") first, then by name; up to three tried: a target that cannot listen or that no ping reaches is left for the next, an answer about the sender (it spoofs / only in part / it does not) is final, a problem of the sender (no iptables) stops at once. The toast says which target was used.
- **The server list**: the **Traffic used** column is ONE total - download and upload added, the two parts in the tooltip; the **Bandwidth** column is ONE number - what the server gives out (its upload), live (the page asks the pulse of the servers as before).
- **"Blocked in Iran" is taken back when the server only closes ping** (`nodes.filter_check`, `_tcp_rows`, `tcp_override`, with the script `tcp_reach` the configs already use): the owner's foreign server showed "Blocked in Iran (0 replies everywhere)" - 13 Iran servers and 7 locations of a public site got no PING answer - while its tunnel ran (0 % loss, 198 ms) and a public site found it open: the server closes ICMP. The filter check judged by pings alone. Now, when the pings say "blocked" or "filtered", up to five Iran servers open a TCP connection to the server's own SSH port; when it opens from at least half of them the verdict is "Not filtered - ... the server does not answer ping (ICMP is closed on it), which is not a filter". A real filter (the connection opens from few or none) stays. **Proved**: `tests/test_server_prep.py` (the override at the lines, the Iran servers asked with the existing script). **Not proved**: on the owner's server (run the filter check again after the update).
- **The order of the server list** (`lab.js labCmp`): a server with a problem (red or yellow lamp) first; then every LOCATION together, the locations in alphabetical order (Iran by its name, a country by its English name, an unknown one last), the servers of a location by name. **PasarGuard servers and nodes** have the ordinary server icon (the lion is gone) with only their profile tag, like the others.
- **The standby is on by itself for every tunnel and its bar is not drawn when it is off or ready** (`standby.auto_pick`, `_auto_mark`): one tunnel at a time, the oldest first, one mark per two minutes, none while a standby is being built, only in the modes fix and move; a tunnel the owner switched off through the API is left alone (`optout`). The bar shows only while it is being built, mended, not built, or in use (*Back now*). **Not proved**: the builds on real servers - every tunnel gets a standby over the next hours; watch the first ones.
- **The bandwidth cell of the tunnels table is one slim bar with the numbers inside it, in the style of macOS** (`lab.css`): 16 px, a faint track with an inner shadow, a fill with a light top edge, the Mac system font, small numbers; the fill is the part of the capacity the users use. The amber of "very slow" now wins over the blue in the table.
- **Proved**: `tests/test_server_prep.py` (the GRE direction, the spoof candidates and the walk through them, the cores once and never into the build's way), `tests/test_standby_auto.py`, `tests/test_web_menu.py` (the page has no list, the Add form, the used and the speed cells, the bar). **Not proved**: the first tunnel's core install and the spoof and GRE tests on real servers.

## 1.69.22 - The row shows what the users get; a tunnel that is already very slow is changed at the next watch

The owner (2026-10-19), with the Tunnels page: "it did not get fixed - and instead of these numbers, can you show how much bandwidth the user gets?". Two things.

- **What the users carried, in the row** (`api/tunnels.py use_summary`, `/api/tunnels/live` → `use24`; `capOf` in `app.js`): the page asks the live route every 4 s, which now also carries, for every tunnel, the PEAK and the average of what its users carried through it in the last 24 hours (the counters of the Iran end that the checks read every minute; at least five readings; kept for a minute on the server). The number of the **capacity** is the Doctor's little test - or, when the users carried MORE than the test reads, that peak (a capacity is at least what the users got: 0.40 beside a user line of 6.3 was a contradiction). The bar is the part of it the users use now. The tooltip says both: "USERS, last 24 hours: peak 13, average 5.2" and, when the peak is the bigger, that the little test reads low. The red of the number follows the shown number (a test of 0.4 with users at 13 is not a stall).
- **Why 1.69.21 did nothing for the owner's tunnel**: the tunnel had been judged "very slow" BEFORE the update; the change of type was only started on the step from "ok" to "slow", so an already-slow tunnel never got it. The watch (the tunnel is measured every 20 minutes while it is slow) now tries it: `bandwidth._retype_later` - when the suggestion for the tunnel stands (the Doctor's memory knows a faster type), the Doctor is not off, the panel is writable and the tunnel was not changed in the last 24 hours, the change is started as its own story.
- **Proved**: `tests/test_bandwidth.py` (the watch starts the change once, not within 24 hours of the last, not without a suggestion; the live route carries peak and average of 24 hours and nothing for a tunnel with fewer than five readings), `tests/test_web_menu.py` (the row cell run in node: users' peak 13 over a test of 0.4 shows 13, a test above the peak says itself). **Not proved**: on real servers. The little test still reads low on that tunnel and the cause is not isolated; tunnels whose users cannot be counted (a test-door tunnel without counters) keep the test number alone.

## 1.69.21 - The automatic change of type really happens (it was waiting for a quiet moment that never came)

The owner (2026-10-19): "still nothing changed - the page says 0.70 but the users use more". The live panel (1.69.20) had done its part: the tunnel was judged "very slow" (1.5, then 0.7 Mb/s), the Doctor decided "fou:tcpmux → tcp", probed the link (GRE passes, 169 ms) - and then **waited for a quiet moment** ("10.8 Mb/s flow now, under 1.5 Mb/s is quiet"), gave up after 15 minutes and never came back; and the once-in-24-hours mark had already been written, so nothing could be tried again until tomorrow.

- **It goes on after five minutes** (`doctor.QUIET_WAIT`): it waits up to five minutes for a quiet moment, then changes the type anyway and says so ("the users are still active after 5 minutes: the change goes on anyway - the users are cut for a minute or two"). A tunnel whose users never leave would otherwise never be changed.
- **The once-in-24-hours mark is written when the change really starts**, not before the wait (and under a new name, `retype_done`, so the stale mark the old version left on the owner's panel does not hold the tunnel back).
- **Users carrying 20 Mb/s or more prove the tunnel is not slow** (`bandwidth._live_peak`, `add_sample(live=...)`): the most the users carried in the last half hour (the counters of the checks); at the line or above, a low little test (two connections for three seconds) is not "very slow". Under the line the tunnel is judged by its test as before.
- **The tooltip of the capacity** says it when the users carry more than the test shows ("the little test reads low on this tunnel, so the real capacity is at least what the users get"). Why the test reads 0.7 on that tunnel while its users carry 5 - 10 is **not isolated** (the test is two TCP connections for three seconds over the link; the users' traffic is many connections inside one multiplexed tunnel).
- **Proved**: `tests/test_doctor.py` (the change goes on when no quiet moment comes, the mark is written when it starts), `tests/test_bandwidth.py` (the peak of the users read from the checks, the guard). **Not proved**: on real servers.

## 1.69.20 - Under 20 Mb/s the Doctor acts

The owner (2026-10-19): "if the bandwidth is under 20 Mb/s the Doctor acts". The line of "very slow" (1.69.14) was 5 Mb/s, and 1.69.19 moved it to 20 only where the Doctor's memory knew a type ten times faster; a tunnel that tested 5.2 Mb/s, just over the old line, was "fine" while another type had given 119 on the same pair.

- **The line is 20 Mb/s** (`bandwidth.SLOW_MBPS`, and `BW_SLOW` on the page, which a test keeps equal): a tunnel with no usual level of 20 or more (new, rebuilt, or one that never gave 20) whose test gives under 20 Mb/s twice in a row (the second a minute later) is "very slow"; it ends at 32 Mb/s (1.6 x the line). What follows is as in 1.69.17: the look, the memory of the pair, and - in the modes fix and move - the change to the type that gave at least five times as much (at least 20 Mb/s), once in 24 hours per tunnel, at a quiet moment, the old type put back when the new one is clearly worse. A tunnel whose usual level is 20 or more is still judged against it (a drop).
- The memory-dependent line of 1.69.19 (`_slow_line`) is gone: one line, easy to say.
- **Proved**: `tests/test_bandwidth.py` (the verdicts at 5.2, 19.9, 20 and 25, the two tests in a row, the end at 32), `tests/test_web_menu.py` (the page's line is the Doctor's). **Not proved**: on real servers; a pair whose types were never speed-tested has nothing to change to (the Doctor says so).

## 1.69.19 - After a cut: the first type that connects, then the faster one - in about five minutes

The owner (2026-10-19): "after a cut the Doctor puts the first tunnel that connects; then it tests, and when another type is faster it changes the tunnel - and the process must not take long". The first half was always so (a cut tunnel is replaced by the first type that connects: connecting comes before speed). The second half had three slow or missing steps; all are closed:

- **The line of "very slow" follows the Doctor's memory** (`bandwidth._slow_line`, `knowledge.faster_kinds(ratio=10)`): a test under 5 Mb/s was "very slow"; one at 5.2 Mb/s where the memory of the pair held a type at 119 was "fine" because it was over the line. Now a test under 20 Mb/s is very slow as well when the memory knows a type that gave **ten times** as much (and at least 20 Mb/s); it ends at 1.6 x the line.
- **The second test comes in a minute, not three** (`SLOW_CONFIRM_AFTER` 60 s; a drop keeps its three minutes), and a tunnel that was just built (the emergency type after a cut) is measured within its first minute (as before: the first test of a tunnel that has none).
- **The change** is 1.69.17's: in the modes fix and move the Doctor builds the tunnel again as the faster type at a quiet moment, measures it, and goes back to the old type when the new one is clearly worse. No new test of every type is run: the numbers are the ones the Doctor's memory already holds (a night survey, a "Test this pair", the tests of earlier repairs) - so it is quick; a pair whose types were never speed-tested has nothing to choose from (Doctor page → Knowledge → test the pair gives it).
- **Time**: first test about a minute after the cut is mended, the second a minute later, the build of the other type one to two minutes, its test seconds: about five minutes in all, against up to an hour and a half before.
- **Proved**: `tests/test_bandwidth.py` (+1: the line by the memory, the end at 1.6 x, the memory read for the tunnel, nothing known = the plain line; the 60 s of the second test). **Not proved**: on real servers.

## 1.69.18 - A tunnel with a problem is measured at once, not at the next turn of 90 minutes

The owner (2026-10-19): "when a tunnel has a problem, do not wait for the next sample - do the tests and move it". The bandwidth test of a tunnel runs every 90 minutes; a tunnel with a problem waited for that turn before the Doctor judged it (and so before 1.69.17 could change its type).

- **What is measured sooner** (`bandwidth._due`): a very slow test that nobody has judged yet (state "learning", under 5 Mb/s - the first test of a rebuilt tunnel, or the schedule that a panel from before 1.69.15 left behind) is measured again 3 minutes after it; a tunnel whose **quality test scored under 40** is measured again 10 minutes after its last test (a confirmed "very slow" or a drop keeps its own 20-minute watch). The rest of the chain is as before: two very slow tests in a row, the look, the memory of the pair, and - in the modes fix and move - the change of type by itself.
- **Proved**: `tests/test_bandwidth.py` (+1: the young slow one and the poor one are due, a good score and a confirmed slow one are not, a test of 100 s ago is not). **Not proved**: on real servers.

## 1.69.17 - The Doctor changes the type of a very slow tunnel by itself

The owner (2026-10-19), about a tunnel that tested 0.7 Mb/s while the Doctor's memory held a type that had given 119 on that pair: "when the Doctor sees it is slow, let it do the other types it tested before - what is it waiting for?". It waited for two things: the next scheduled bandwidth test of the tunnel (every 90 minutes; a tunnel that is new or rebuilt is confirmed within four minutes of its first test), and the owner's click on the suggestion of 1.69.14. The click is gone in the modes **fix** and **move**.

- **What it does** (`bandwidth._suggest_faster`, `doctor._run_retype(auto=True)`): the suggestion is still written (the Doctor page, the story), and then, in the same story, the tunnel is built again as that type - the link probed first, the swap of the repairs (`autopilot._replace`: same name, same ports; the old type put back if the new one does not build), after a quiet moment (`_wait_quiet`: a busy tunnel waits, the suggestion stands), then measured again.
- **Rails**: once in 24 hours per tunnel (`doctor.may_retype`, `RETYPE_GAP`); not when the Doctor is off or the panel is read-only; only a type the Doctor's memory saw give at least five times what the tunnel gives (as in 1.69.14); the fault must be the tunnel's own (not a busy server, not a whole server slow).
- **What it learns**: the speed measured after the change is written into the Doctor's memory of the pair (`knowledge.record`), so the next choice stands on today's number. When the new type measures **clearly worse** (under 80 % of what the tunnel gave) the old type is built again and an event says so; otherwise an event says "changed by the Doctor itself (the tunnel was very slow)" with the two numbers.
- **Proved**: `tests/test_doctor.py` (+2: the rails, the quiet moment, the number kept, the way back when it is worse) and `tests/test_bandwidth.py` (the look starts it once, the second look within a day says why not). **Not proved**: a swap by itself on real servers (only the fake road, as for the click of 1.69.14).

## 1.69.16 - A standby that is ready shows no bar

The owner (2026-10-19), looking at the tunnel window: "do not show it there - if the tunnel gets into trouble it switches by itself". The bar "Standby · Ready - ... idle, looked at every 15 seconds" with *Switch now* / *Turn off* is not drawn any more while the warm standby is ready and idle. The handover itself was and is automatic (the modes fix and move: when the tunnel is down for 5 s and the standby is up, the users' ports are handed to it in about two seconds; they go back after a minute of the tunnel holding). The bar still shows in every other state: off (*Turn on*), building, being mended, not built, and **in use** (*Back now*). **Proved**: `tests/test_web_menu.py` (+1). The way to hand the ports over by hand for a drill is the Doctor's ledger and the standby API; the window no longer offers it while the standby is idle.

## 1.69.15 - The Tunnels page no longer jumps while a tunnel is repaired

The owner (2026-10-19): "when a tunnel is repaired, the page jumps". Measured on the real page (a copy of it served beside the live panel, read only, in a browser, with the numbers of the live tunnels): a repair that only restarts a tunnel moves nothing - the row stays, with its wrench and its "repairing…" chip, in every state it passes (degraded, down, starting, unknown), the 4 s redraws change no position and no scroll. **A repair that REPLACES the tunnel (the Doctor's change of type after a cut route, the comeback to the wanted type) does**: the old tunnel is removed, the new one is not recorded for a minute or more, and in that time the row was not in the list - every row under it moved up 46 px when it went and down 46 px when it came back (and a placeholder of another type landed in another type group, at the bottom of the table).

- **The row stays** (`tunGhosts` in `app.js`): a tunnel that a repair is working on (the "working" list of the Doctor) and that is not in the list has its LAST real row drawn as a placeholder - same place, "repairing…", no buttons, counted as a problem, so the Problems filter keeps it too; the real rows are remembered between draws, a tunnel that is gone and not being repaired is forgotten (nothing is resurrected). A placeholder of a tunnel that is built again as another type stays in the group it was in until the real row is there.
- **Proved**: `tests/test_web_menu.py` (+1, run in node: nothing gone = no placeholder; gone while repaired = its old row, inert, once however many times it is listed; back = no placeholder; deleted = forgotten; and the page code uses the drawn list for the rows, the Problems count, the All count and the sort) and the same drill as the measurement, repeated after the change on the real page: with the old tunnel removed for 14 s there are 20 rows, one "repairing…" chip, and no row moved. **Not proved**: a real swap of a tunnel on the owner's servers (the live panel is not touched by a release; the drill changed only what the page was given); a row still moves once when the NEW row is there with another type - it belongs in another group then.

## 1.69.14 - A tunnel that was never fast is "very slow" (not "dropped"), and the Doctor suggests the type its memory knows to be faster

The owner (2026-10-19), with a picture of one Backhaul tcpmux tunnel to a US server: its capacity showed **0.80 in red**, and "why did the Doctor do nothing?". What the live panel said (read only): the tunnel had been rebuilt a few hours earlier after the Doctor had changed its type three times following cuts of the route, so its bandwidth history was two tests old (0.6 and 0.8 Mb/s); every other tunnel of that panel tested 137 - 1400 Mb/s; its users carried a median of 4 Mb/s (up to 13) through it; and the Doctor's memory of the pair had type `tcp` at 119 Mb/s and the running type among its poor ones.

- **Why the Doctor saw nothing**: a "drop" is a test under 60 % of the tunnel's own usual level, which needs four tests (about six hours) and is not judged at all when the usual level is under 20 Mb/s; a "stall" is a test under 0.5 Mb/s. 0.6 and 0.8 are in none of them. The page, meanwhile, painted red every number under 1 - so the screen said "dropped" where the Doctor said nothing. And even a confirmed drop was only told (and, when only this tunnel was slower, the engine restarted): nothing ever changed the TYPE of a tunnel for its speed; the sentence at the end of the story of a drop that said "it offers another tunnel type" had no code behind it.
- **"Very slow" is a word of its own** (`bandwidth.verdict` / `add_sample`): no usual level (new, rebuilt, or one that never gave 20 Mb/s) and a test under `SLOW_MBPS` (5 Mb/s) - confirmed by a second test three minutes later (like a drop), then watched every 20 minutes, over when a test gives `SLOW_OVER` (8 Mb/s; a gap, so that a tunnel at 5 does not flicker). A stall (under 0.5) is still the worse word and takes its place; a tunnel that has a usual level of 20 or more is judged by it as before. One event tells it once ("the bandwidth is very low - 0.9 Mb/s in two tests in a row (it did not drop: it was never higher)").
- **The page uses the Doctor's own lines** (`BW_STALL` 0.5 and `BW_SLOW` 5 in `app.js`; a test keeps them equal to `STALL_MBPS` and `SLOW_MBPS`): red only for a drop or a test that moved nothing, **amber** for "very slow", a number the Doctor has not judged yet is not painted; the tooltip, the tunnel window (Link → Capacity) and the Doctor page ("to look at") say it in a sentence.
- **The look at a very slow tunnel** (`bandwidth._investigate_slow`, the same story window as for a drop): the same checks (the two servers, the other tunnels of the same servers, the loss, the delay, the users), and its own sentence (`explain_slow`) - never the "full of users" of a drop (users carrying more than the test reads means the test reads low, not that the tunnel is full). No restart of the engine: a tunnel that was never fast has nothing to come back to.
- **A suggestion, never an action by itself** (`knowledge.faster_kinds`, `doctor.suggest(kind "type")`): when the fault is the tunnel's own (not a busy server, not a whole server's tunnels slow together) and the Doctor's memory of the pair knows a type that gave **at least five times** what the tunnel gives (its test, or what its users carry, whichever is more; and at least 20 Mb/s; measured less than 14 days ago; a type that connected, the licence allows) - the fastest - it is written on the Doctor page: *"tcpmux gives 0.8 Mb/s in the test (the users carry 4 Mb/s through it now); the Doctor's memory has tcp at 119 Mb/s on this pair, measured 1 h ago"* with **Change type**. Not when the Doctor is off. The suggestion stands as long as the tunnel is slow and runs the type it was written for (the "up and clean for ten minutes" rule of the move suggestions does not apply: a slow tunnel is up and clean all the time).
- **The click** (`doctor.retype_now`, `_run_retype`): the link of the new type is probed first (a link that does not pass: nothing is built, the tunnel is not touched), then the tunnel is built again as that type - the same swap the repairs use (`autopilot._replace`: same name, same ports, the old type put back when the new one does not build) - measured again, and what it gives is told next to what it gave ("It gives 118.0 Mb/s in the Doctor's test now (before: 0.8)."). The users are cut for a minute or two. A suggestion written for another type than the tunnel runs by now is refused.
- **Proved**: `tests/test_bandwidth.py` (+6 tests: the verdicts at the lines, two tests in a row, the end at 8, a stall over a slow, a drop never turned into a slow, the sentences, the whole look with the suggestion written - with the owner's numbers, nothing suggested when the memory knows nothing / the Doctor is off / the server is on its knees, the job / the button / the quality job all look at it, the event once), `tests/test_knowledge.py` (the five-times line, the fresh and the licence), `tests/test_doctor.py` (+6: the suggestion's life, the click with its story and its event, a build that fails, a link that does not pass, an out-of-date suggestion, the API), `tests/test_web_menu.py` (the same lines on the page and in the Doctor - the page cell run in node with the owner's numbers: 0.6 and 0.8 are not red). **Not proved**: the click was not run on the owner's servers (the live panel is not touched by a release; its tunnels are his); the 119 Mb/s the memory holds for `tcp` on that pair is an older test, not today's; why the test of that tunnel reads 0.8 while its users carry 4 - 13 is not isolated (UDP-carried links were slow towards the foreign server before; the cause stays open). If the swap gives less than the tunnel did, the suggestion's own sentence says what it was based on, and the old type can be built again by hand.

## 1.69.13 - The spoof test installs what a new server lacks

The owner (2026-10-19) pressed the spoof test on a new Debian 12 server (`tr-test`) and got the red banner **"iptables is not installed on the sending server"**: a fresh Debian 12 has no `iptables` (it is only `nft` there) and no `tcpdump`, and the test, which needs `iptables` on the sender (the NAT rule that writes the foreign source address) and `tcpdump` on the listener, stopped at the first check and left the owner to install a package by hand.

- **What the test does now** (`nodes.spoof_test`, new action `spoof_prepare` in `ssh.py`): **before** the listener starts, each of the two servers is asked to have what its part needs - the sender `iptables` and `ping` (`iputils-ping`), the listener `tcpdump` - and what is missing is installed with `apt-get` (a server without apt, or an apt that fails, is still told plainly: *"iptables is not installed on the sending server and could not be installed (no apt, or apt failed): install it by hand, apt install iptables"*). It is done before the listener starts so that a slow `apt` does not eat the listening time. What was installed is said in the verdict ("iptables was missing on tr-test and was installed for this test.") - the owner learns that his server was changed. A server that has everything is not touched (the action installs only what is missing for its role).
- **Proved:** `tests/test_nodes.py` (+3 tests): the whole spoof test with a fake road to the servers - the order (prepare on the listener, then the listener, then the send), the notes of the verdict, and the three "could not be installed" answers (iptables, ping, tcpdump -> 400 with the sentence above); the real shell text of the action run in a real bash whose PATH holds only stand-ins for the tools and for `apt-get`, in nine situations (all there -> `READY` and `apt-get` not even called; iptables missing -> `TRIED iptables`, `READY`; a new Debian 12 -> `TRIED iptables iputils-ping`; no apt -> `NOIPTABLES` / `NOPING`; the listener needs `tcpdump` only; an apt that fails -> `NOTCPDUMP`). **On the owner's server for real:** the rendered action, run over SSH the way the panel runs it, on `tr-test` (Debian 12, no iptables, no tcpdump): `send` -> `TRIED iptables`, `READY` (iptables v1.8.9 nf_tables there afterwards); `listen` -> `TRIED tcpdump`, `READY` (tcpdump 4.99.3); the same again -> `READY` with nothing installed.
- **Not proved:** the whole spoof test through the panel on that server (whether the foreign-source packet is then really let through or dropped by the provider is what the test measures, and it was not run: the owner presses it himself). The panel that runs the spoof test is the live one, which the owner updates from the panel.

## 1.69.12 - A tunnel type "Best of the day"

The owner (2026-10-19): "a tunnel mode that puts the best tunnel of the day on it". **Create tunnel -> Type -> Automatic -> Best of the day**: when Build is pressed the panel picks the type that connects best between the two chosen servers and builds it with that type's defaults.

- **What "best" is** (`knowledge.best_kind`, the same knowledge the Doctor repairs with): the types that connect on this pair, a fresh full measurement before a latency-only one, each by score; what the licence allows; what failed on the pair lately is not there. A pair nobody measured yet gets the Doctor's first choice for a new pair - never a WireGuard type (it needs a package nobody put on the servers). The toast after Build says which type and why ("bp:wssmux: the best that connects between these two servers - score 90, measured 3 h ago").
- **`POST /api/tunnels` with `"kind": "auto"`** (the Iran server must be named: the smart choice of the Iran server cannot know the pair yet - 400 with that sentence); the options of the form are not sent to the chosen type; the answer is `{"ok", "id", "kind", "transport", "why"}`. A Backhaul type goes the Backhaul way (over GRE, or over the link it was measured on), the others through their builders - the same paths as a type chosen by hand.
- **The form**: the first group of the Type list; no advanced settings (a note says why); *Test this pair first* says to pick a type to test, since the chosen one is not known before Build. The knowledge is the Doctor's: a pair that was never surveyed gives the default, and the type "of the day" improves as the Doctor measures the pair (Tunnel Doctor -> Knowledge).
- **Tests.** `tests/test_best_of_the_day.py` (+3): the best kind (best score, the licence, nothing measured, never WireGuard), the API (the pair must be known, a BackPack choice goes through `build_other`, a Backhaul choice through `builder` with the form's options dropped, an unknown kind refused); `tests/test_web_menu.py` (+1): the Type list, its note, the test button, the toast.
## 1.69.11 - The Marzban node: a folder is not a node; a DNS record you changed shows as changed at once

Two things the owner met on 2026-10-19.

**1. The Marzban node did not install from the panel (tested on TR3).** What the server TR3 (a new Debian 12 machine) and the panel say, in UTC: the owner started the official installer **by hand** at 23:45; it installed Docker (23:47:36), made the folder `/opt/marzban-node` (23:47:40) and waited for its questions. At 23:47:55, 23:48:14 and 23:48:58 the owner pressed *Install -> Marzban node* in the panel: the script found the folder, answered `MZNODE_EXISTS`, and the story said **"already installed on this server (/opt/marzban-node), nothing changed. Done."** - it ended green - while no node ran at all. The installer by hand finished at 00:00:26 (compose file, certificate) and the container started at 00:00:44; the Marzban panel connected a minute later. So the panel had told the truth about a folder and a lie about a node.

- **What the script decides by now** (`install_marzban_node` in `ssh.py`): a node that **runs** (a container `marzban-node` that is Up) is done (`MZNODE_EXISTS`: "already installed and running"); the panel's own install that is still going is followed as before; **another installer running on the server** (one started by hand) is not started over and not called done (`MZNODE_BUSY`: the story says so and the install ends red, until the owner presses it again); a **folder and nothing running** is a half install - it is installed again over it, and the answer `y` to the official script's question "override the previous installation?" goes in front of the certificate (the blind answers of the panel would have landed one question late) (`STARTED OVER_OLD`, the story says why).
- **Proved:** the real script (rendered the way the panel does) run with stand-ins for `docker`, `pgrep` and `nohup` in five situations (a node that runs, with or without the folder; an installer by hand; a folder and nothing running; nothing there; a node that stopped), and the three answers through the whole API story (`tests/test_extras.py`, +6 tests). **On TR3 for real:** the new script, run over SSH the way the panel runs it, answered `MZNODE_EXISTS` and changed nothing (the container still Up, the compose file unchanged, nothing written to `/var/lib/nikatel-install`).
- **Not proved:** the clean install through the panel on a server with no folder. It was not re-run: TR3 runs the node now and is connected to the owner's Marzban panel, so a removal there would cut it, and Docker was not installed on another test server. That path is the one the earlier releases tested; only its first lines changed.

**2. DNS: a record that was changed showed the OLD value until the page was opened again.** The owner (a Cloudflare account): "I changed the DNS - it did not change - after a refresh of the page I saw it had". The page asks the provider for the list right after the write, and the list of a provider can still bring the record as it was before the write for a few seconds (the write itself was answered correctly); the panel then kept that old copy for up to a minute and drew it again every 15 s.

- **The fix** (`services/dnscache.py`, `api/dns.py`): what the provider **answered to the write** (the record as it is now, or "deleted") is laid on top of every read of the list for **45 seconds** (`wrote`, `apply_pins`): a record that was changed shows as changed, one that was made is there, one that was deleted is gone - on the read right after the write and on the ticks after it. A pin goes as soon as the provider's list agrees with it, or when its 45 s are over (then the provider is believed again, whatever it says). A change made behind the panel's back inside those 45 s (the failover moving the same record) shows on the page after at most 45 s.
- **Proved:** `tests/test_dns_lag.py` (3 tests): a fake Cloudflare whose list lags one write behind - a record made, changed (value and cloud) and deleted shows so on the fresh read and on the cached reads after it; the pin goes when the list agrees and when its time is over; `apply_pins` changes, adds and removes and never touches the other rows or the list that was read. **Not proved:** against the real Cloudflare - the cause (a lagging list) is the one that fits what the owner saw, not something measured on his account; if the page still shows an old value after this release, the cause is somewhere else.

## 1.69.10 - Doctor -> Knowledge: small type, two lines for a pair, and the page no longer jumps

The owner (2026-10-19, a screenshot of the tab, then a second one): "make the type smaller so that everything fits - the speed of the tunnels and the red texts smaller and in two rows" and "this page jumps - fix the jump". Style and the way the page is drawn only (`knowledge.js`, `app.js`, `lab.css`, `doctor.css`); nothing on a server and no API changed.

- **A pair is two small lines.** First line: the name, how many tunnels, the tunnel types that work between the two servers with their speed, the cuts of the last 7 days, the age of the knowledge and *Test now*. Second line, under the types: what failed lately, **written out in full** (it was cut with "..." at 230 px: "BackPack - UDP + QUIC, BackPack - We..."), in 9.5 px red. The speed chips are 10 px (they were 11), the name 12 px (14), the button 26 px high (38), the row has no minimum height (52). A pair with nothing failed is one line. A narrow window (under 760 px) puts the types, the red line and the cuts under the name.
- **Why the selectors are long.** The Doctor's own row rules (`.ap-tasks .ap-row` with its `b`, `time` and `button`, in `doctor.css`) are more specific than the old `.kn-*` rules, so the Knowledge rows could not be changed from `lab.css` without the same prefix (`html body #page .ap-tasks .ap-row.kn-row`); the light theme rules got it too.
- **The jump, and what caused it.** The Doctor page is drawn again every 15 seconds. For the Knowledge tab that did three things: the box turned into "Reading what the Doctor knows..." (the page shrank), the scroll of the open tab went back to the top when the list came back, and the tab faded in again (a flash) - and the night survey redrew it yet again on every change. Now: the box is filled **at once from what was read last time** (`knHtml()`); the new answer is compared with the old one and **nothing is drawn when nothing changed**; the scroll of the open tab is kept across the redraw (`.ap-tbody` of the same tab), and a tab that was open gets the class `still`, which does not play the fade-in again (`doctor.css`). This is the same for the other tabs of the box (Active Tasks, Completed, Auto Moves, Analytics): their scroll and fade were thrown away in the same way.
- **Looked at in a browser:** the real CSS of the panel (`app.css`, `lab.css`, `doctor.css`, `fluid.css`, `light.css`) with the real `knowledge.js` and the pairs of the screenshot as sample data, in headless Chrome: 1500 px, 700 px and the light theme. Not looked at: the live page with a real survey running and the 15-second redraw - that is the owner's eye after the update.
- **Tests.** `tests/test_web_menu.py` (+1): the cache, the compare, the kept scroll, the missing fade, the two-line grid with the strong prefix, the red text not cut, the light rules.

## 1.69.9 - A server that is off can be deleted with its tunnels, the tunnel of an off server no longer freezes the panel, Abuse-Defender installs on a server without cron and shows its log in a box

The owner (2026-10-19): "this server is off and when I delete it from here it is not deleted". The panel refused (`DELETE /api/servers/{id} -> 409`, three times in the log): **a server is not deleted while a tunnel uses it** (here the foreign server of one tunnel), and the refusal was a toast that went by in a second - nothing on the page said why.

- **The question.** The refusal now opens a question: "<server> cannot be removed while the tunnel <tunnel> uses it. Remove the tunnel too, and then the server? The tunnel goes to the trash. A server that is off is not asked: its files are purged if it ever comes back." Yes removes both in one step. (Every other refusal is still a toast.)
- **`DELETE /api/servers/{id}?tunnels=true`** does it: the tunnels of the server are removed first the way the tunnel window removes one - to the **trash** (they can be put back), each with its warm standby (`standby.forget`), a leftover standby row removed too - **without asking a server that is off over SSH** (`skip_offline`: no 90 s wait per tunnel), then the server itself; the answer lists the tunnels (`{"ok": true, "tunnels": [...]}`) and the event says "removed from the panel ..., with its tunnel <name>". Without the parameter nothing changed except the sentence of the 409: it names the **real** tunnels (not their `-sb` standbys) and says the way out ("delete the tunnel with it, or disable the server instead of deleting it").
- **And the tunnel itself could not be deleted either** (the owner, a minute later: "now even my tunnel is not deleted"). The log of the live panel: `DELETE /api/tunnels/<id> -> 200 in 32679ms`, then again `45627ms`, and `database is locked` in a job in between. Two faults: (1) a BackPack / Paqet / Chisel tunnel was removed from its servers **inside the database session that had just written the action's row** (`builder.delete_tunnel` called `build_other.remove(t)` with the session open) - SQLite lets one writer in, so for the whole SSH wait (30 - 90 s per side when a server does not answer) every job and every page that wrote failed or waited, and the second click on Delete started a second removal that waited behind the first. The servers are now asked **with no session open** (`build_other.remove_args` reads what is needed, `remove_on` asks). (2) the page's delete **asked a server that is off** and waited for it; `DELETE /api/tunnels/{id}` now skips a server that is off (`skip_offline`, the way the failover already did) - the tunnel is gone in a second, and its files on that server are purged when it is back (the result says "skipped").
- **Said plainly.** The same can be done by hand: delete the tunnel in the Tunnel manager, then the server. Nothing is done to the server itself, as before.
- **Abuse-Defender installs on a server without cron** (the owner, 2026-10-19: `bash: line 45: crontab: command not found`). A minimal server has no `crontab`; the install put the firewall rules in and then died on the line that writes the nightly refresh. It now **installs cron first** (`apt-get install cron`, enabled) and, when there is still none (no apt, no package), writes a **systemd timer** (`abuse-defender-update.timer`, every night at 00:00, `Persistent`) that runs the same refresh script; a fresh install and *Remove* take an earlier timer away like the earlier cron lines. A server that already has cron is unchanged. A server where the install died half way (the rules in, no refresh) is put right by running Install again - it always starts fresh.
- **Abuse-Defender shows its log in a box** (the owner, the same minute: "when a node or Abuse-Defender or anything else is installed, put its log in the box"). Marzban / PasarGuard nodes and the system update already opened a window that follows the install; Abuse-Defender answered with a toast that went by (and, when it failed, with a line of shell and an id). It is an **action with a story** now: `POST /api/servers/{id}/abuse` with `"stream": true` answers `{"ok", "id"}` at once (without it the answer still comes when the server has finished, as before) and the page opens the same box - "installing on <server> ...", the **steps of the script** (`STEP 1/5 packages` ... `STEP 5/5 the list is refreshed every night by cron`), then "Done: Abuse-Defender is on, N ranges are blocked" - or "Failed: <what the server said>" with nothing written down as installed.
- **Tests.** `tests/test_servers_logs_api.py` (+1): the 409 names the tunnel and touches nothing; `tunnels=true` sends the tunnel to the trash with `skip_offline`, forgets its standby, removes a standby row that was left, deletes the server and writes the event. `tests/test_web_menu.py` (+1): the question, its words, the call with `?tunnels=true`, any other refusal still a toast. `tests/test_abuse_install.py` (+6): cron is looked for before the list is fetched, the timer fallback and the cleanup of it, both scripts are valid shell (`bash -n`), the install as an action (the steps, the result, the failure in the log, an Iran server and an unknown action refused at once). `tests/test_web_menu.py` (+1 more): the page follows the abuse action in a box. `tests/test_delete_tunnel_offline.py` (+2): a BackPack tunnel is removed with **no write lock held** (each question tries to take the database's write lock from another connection and must succeed), and the page's delete of the tunnel of an off server asks only the server that answers.

## 1.69.8 - The sign-in page and the PasarGuard box in the look of the owner's UI kit sheet

The owner (2026-10-18): "make the sign-in page exactly like the MacBook" - the sheet `docs/design/ui-kit-components.webp` - "and the same for the PasarGuard update". Style only (`lab.css`, loaded before `login.css` and `uikit.css`, so every rule names `.login` / `#page .sf .abt-pg` to win):

- **The primary button is the sheet's "Connect"**: a flat vivid blue (`#1a73ff` to `#0b5cf0`) with a thin light edge and a soft shadow, **no gloss** (the highlight pill that every primary button had is taken away on these two places), steel blue under the pointer and dark when pressed, exactly as the sheet shows its Default / Hover / Pressed. Sign in is 48 px high with 12 px corners. In the PasarGuard box the buttons were the quiet blue tint of the Settings frame (`#page .sf button.pri`); they are the sheet's blue now (Update panel..., Update nodes only..., Update..., Update N nodes...).
- **The fields are the sheet's Text Field**: the icon sits **inside** the field - no box of its own and no dividing line (the user and password boxes had a darker square with a line) - and the ring on focus is a bright blue line with a soft glow.
- **Said plainly**: the rest of the panel keeps its buttons (the glossy primary of the other pages and the quiet tint of Settings); only these two places follow the sheet. The sign-in page was already built from the sheet on 2026-10-16 ("MacBook style"); this takes the last differences away. Asked, not done: the macOS lock-screen look (round avatar, one round password field) - say so if that is what is meant.
- **Looked at in a browser** on a private copy of the panel (the real sign-in page, logged out; the real Settings page with the PasarGuard box filled with a panel and two nodes that are behind): the Sign in button, the two fields with the focus ring, and the box's blue buttons, the secondary glass buttons and the disabled ones.
- **Tests.** `tests/test_web_menu.py` (+1): the rules above, the sheet is in `docs/design`.
## 1.69.7 - Every section has its own address, the PasarGuard box gets its two buttons, the Problems tags are small, pale and wrap, the facts strip is evenly spaced

Changes of the page asked for by the owner on 2026-10-18 (the script and the style of the page - nothing on the servers):

- **Every section has its own address.** `#/tunnels/repairs`, `#/settings/update`, `#/pgpanel/users`, `#/nodes/lab` ... The address follows the page and its tab, the **back button** walks back through the sections, a copied link opens the same section, and a link is kept through the sign-in page (after the sign-in the page it names opens). A tab that does not exist, is switched off or is only a door to an action (Add profile) opens the page on its first tab and the address is corrected in place; an address that names no page (or the Shop bot, which is off) opens the page that was open last. **It costs nothing**: the whole router is one string compare at the start of every draw (`routeWrite`) and the opening of an address (`routeApply`, at the start and on back / forward); it asks the server for nothing and draws nothing twice. (The port in the address - `:47045` - belongs to the web server in front of the panel, not to the panel.)
- **Settings -> About & update, the PasarGuard box: "Update panel..." and "Update nodes only...".** Two buttons under the headline, always there (until now the buttons that update many nodes showed only while something was behind, and the panel's own button sat in its row, greyed when it could not be used). *Update panel...* opens the update of the panel - its plan, the backup, the name typed back - and, when several panels can be updated, asks which one; when the server that runs the panel is not in the Servers list it says so and opens the form that adds it, with the address filled in; when the panel is the newest it says that. *Update nodes only...* takes **every connected node that is behind - its software, then its Xray - through the same question and the same one-at-a-time run** as the buttons of the node list (a node that does not come back stops the run), and never touches the panel; when every node is up to date it says so. A node that is in the run twice (its software and its Xray) is one node in the question and in the title of the run. Read-only panel: both buttons are off.
- **The tags of the Problems column (Bad route, Filtered, GRE x, RAM 99 % ...) are smaller, paler, and never leave their card.** 14 px high (it was 18), 9 px text, a smaller cross; at rest they are at 58 % of their colour (dark and light mode alike - the colours themselves are unchanged); **under the pointer, anywhere on their row, they are as bright as before** (a phone, which has no pointer, shows them at 85 %). When the column has no room for all of them they go on a **second line inside the card**: the list gave every cell `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`, which cut the third tag to a "..." at the edge of the card.
- **The facts strip** (Works by, Mode, Tunnel checks, Server checks, Session, Copy diagnostics) is shared out over its whole width: each fact is centred between its two dividers with the same room on both sides, the room that used to lie empty at the right end is shared by the facts, and the button keeps a fixed gap. A narrow window still scrolls it sideways.
- **Tests.** `tests/test_web_menu.py` (+4): the router **run for real under node** (the first address replaces the empty one; a tab, Settings, an alias and an old page name open the right place; a missing, switched-off or "door" tab opens the page and corrects the address in place; no page, junk and the Shop bot open what was open last; the pop of an address the page wrote itself does nothing; a click on a tab is a new step of the history); the two buttons and what they call (the nodes' run never calls the panel's update; the panel's button handles one panel, several, none and the newest); the size, the opacity, the hover and the wrapping of the tags; the strip. Looked at in a browser on a mock of the places built from the real stylesheets and the real `pg_update.js` (the page needs a panel to run, the mock needs none): the buttons under the headline in both states, the tags at 58 % and, on the hovered row, at 100 %, and a narrow column with five tags on two lines inside the card, the strip's cells 143 - 172 px wide with equal room on both sides.

## 1.69.6 - The warm standby, drilled with real tunnels: two things it got wrong are corrected

The standby of 1.68.1 was proved on a real kernel but never with a real tunnel. This is that drill (a PRIVATE copy of the panel on the build server with its own data and port, the Doctor in mode *fix*, the Iran test server and the build server as the pair; the test panel was left alone, it holds another session's tunnel) - and the two things it showed.

- **Measured** (`docs/standby.md`, "How it is proved"): the standby is built 42 s after the tunnel; a cut tunnel is judged down after **9 s**, its users' ports are handed to the standby 1.2 - 5.8 s later and the **users see 11 - 12 s of outage** (the first answer again 11.7 s after the cut); the users' port answers through the standby while the Doctor rebuilds the tunnel as another type (a GRE filter on the route: the tunnel row did not exist for two minutes and the users were served); the way back follows 60 s after the tunnel is up; a flushed firewall is put right in 24 s; the boot unit puts the redirect back at once; *Switch now* and *Back now* take 2.6 s; turning the standby off leaves nothing on either server; a standby that falls while the tunnel is fine says nothing.
- **A standby on the carrier of its tunnel is built again on another.** The type of a standby is chosen once, when it is built; but the Doctor swaps a cut tunnel to another type and (1.69.5) brings it back, and the drill ended with a Backhaul-over-GRE tunnel and a Backhaul-over-GRE standby - one GRE filter cuts both. Now `standby.better_carrier` asks the Doctor's knowledge for a type of ANOTHER carrier that is **known to connect on this pair** (an untested type is not a reason to remove a working standby); the standby is built again when it has the carrier of its tunnel, the tunnel has been up 60 s with a fresh look, no repair or storm is on, the standby is older than 5 minutes (no loop while the Doctor swaps) and the last way back is older than 15 minutes (removing it ends the connections that stayed on it, and the way back is recorded now: `record.parked_at`). Never while the users' ports are on the standby; asked at most once a minute, and only when the carriers are equal.
- **A stopped GRE card of a standby is started again.** The restart of an unhealthy standby (after 45 s) left the `nikatel-gre-*` units out, so a standby whose card was stopped stayed *mending* until the rebuild after 5 minutes. It restarts both servers' units now, the card first, then the services on it.
- **Said plainly, not changed here:** after `standby_off` a boot unit that has run since the boot is listed by `systemctl` as `not-found, active (exited)` until the next reboot (it is disabled and its files are gone; stopping it would run its `ExecStop`, which takes the parked rule off); and the BackPack Direct PCK type carried the drill's users with 17 % of the requests over 1 s and 2 % failing (median 0.32 s) - a matter for the Doctor's ranking, not for the standby.
- **Tests.** `tests/test_standby.py` (+5 tests): `better_carrier` (clash or not, known or not, failed / skipped / not licensed types, a BackPack tunnel); `decide` in every state of the new rule (calm, a fresh standby, a recent way back, a repair, a storm, a cut tunnel, users on the standby, mode off); the job (builds it again, asks once a minute, does nothing for another carrier); the restart order (GRE card first, both servers); `parked_at` written by the way back and cleared by the next handover.

## 1.69.5 - Back to the better tunnel: a disturbance that was only temporary does not leave the tunnel on its emergency type

The owner (2026-10-17): "keep in mind that the disturbance may be temporary - check whether the conditions got better, and if so go to the tunnel that works better". Until now a cut tunnel was swapped to the first type that connected and stayed there for good - and the robust kinds that are tried first when a route is cut (WebSocket TLS, Chisel ...) are usually the slower ones. A filter that lasted ten minutes left the tunnel on the slow type for weeks.

- **The wish is kept.** When the smart repair swaps a CUT tunnel to another type (`_switch_cut` succeeded), the type the tunnel had is written down as the one **wanted** (`services/comeback.py`, settings `comeback`: the type wanted, the type it runs as now, the cause, when it left, the looks so far, the next look). A second cut while it runs on the emergency type keeps the original wish (and starts the waits again); a tunnel that is swapped back to the wanted type by anything has nothing more to wait for.
- **The looks.** After 5, 10, 20, 30, 60 minutes, 2 hours, then every 4 hours (three days at most), one look: the wanted type is tested **beside** the running tunnel - a temporary tunnel, delay and loss only, about a minute; the users are not touched. A wanted type that runs over a link (WireGuard, FOU, IPsec ...) is first asked of the link probe (seconds): a link that still does not pass costs no test. The look is one story in the Doctor's log (`comeback`), a test of the Doctor's own memory of the pair on the way.
- **What the test decides** (`comeback.outcome`, the same 10-point rule every other swap uses): it connects **clean** (under 5 % loss) and scores 10 points more than the running tunnel by delay and loss -> the tunnel goes back, at a quiet moment (under 1.5 Mb/s, or at once when the loss is past 30 %), as the real tunnel, and the old type is put back if the build fails; it connects clean but is only as good -> the running type stays, the looking stops; it does not connect, or loses packets -> the disturbance is still there, the next look is later. A swap back is an event.
- **What it never does.** Nothing while the Doctor is **off**, the panel is read-only, a repair works on the tunnel, a storm of cuts is on, or before the tunnel has been **up for 3 minutes**. A tunnel that somebody changed in the meantime - another type than the Doctor left it on (a swap by hand, a later repair) - is never put back: its wish is forgotten. One tunnel goes back at most **3 times in 24 hours** (a route that flaps does not make it swing between two types; then it waits 6 hours). One look at a time (the test place is one). While it only tests, the look does not hold the tunnel's repair place: a cut during the test is repaired at once; the tunnel is the look's own only for the moment of the swap.
- **Tests.** `tests/test_comeback.py` (17): the waits; the wish kept from the first swap, kept through a second cut, dropped when the tunnel is back on it; due / expired / the limit of returns; the verdicts of the test (swap / as good / not yet / loses packets); the job (a due look, a tunnel that is down or has been up for 30 s, the Doctor off, a repair, a storm, one at a time and the longest waiting first, a tunnel that is gone or three days old, a tunnel that went back too often); the look (back at a quiet moment with the tunnel held only for the swap, the wanted type not there yet, only as good, changed by somebody, already back, a link that does not pass costs no test, a busy test place is not a look, a busy tunnel waits for a quiet moment, a failed build keeps the old type and the looking on); the smart repair keeps the wish after a real switch; the look is its own action.
- **Proved for real, twice** (2026-10-17; a PRIVATE copy of the panel on the build server - its own database, port and servers list - with the Doctor in mode *fix*, the Iran test server and the build server as the pair; the test panel was left alone, it holds another session's tunnel): one Backhaul tunnel over GRE, then IP protocol 47 dropped between the two servers.
  Second run: the tunnel was *down* 14 s after the cut; the Doctor diagnosed *gre blocked*, kept `tcpmux` as the wanted type and built `bpd:pck` (BackPack Direct PCK) as the first kind that connected - **up 214 s after the cut**; the filter was taken off 11 s later; the first look came **5 min 52 s after the swap** (5 minutes, then the job's minute): the link probe said GRE passes (97 ms), the temporary `tcpmux` beside the tunnel scored **59** (83 ms, 0 % loss) against **16** for the running `bpd:pck` -> it waited for a quiet moment, replaced the tunnel and `ppfor-ppiran` was **back on GRE 678 s after the swap, up, 77.6 ms**. The first run showed the "not yet" and the decision on its own: look 1 (5 min) found the route lossy and scheduled the next look in 10 minutes; look 2 found `tcpmux` 48 against 25, 3.3 % loss, decided to go back - and the script that ran the drill removed the tunnel at its own deadline in the middle of the swap ("Stopped: tunnel not found"), which is also why the drill was run again with a longer deadline. The story of every look now says the numbers (`The test of tcpmux: score 59, delay 83.1 ms, loss 0.0 %; the running tunnel scores 16.`).
- **Corrections to the notes of 1.69.0 and 1.69.2** (changelog and docs; no code): the link probe takes **about 7 s for one link and 23 s for all nine**, not "a few seconds"; and the title of 1.69.2 ("it said WireGuard did not pass; it does") is taken back - WireGuard on the test route comes and goes (it passed in the lab, was refused for ten minutes on port 51820 in every way the probe and the lab were run, and passed again), so the probe was right at the moment it said it. The 1.66.0 sentence "WireGuard does not connect on this route" in `docs/simple-tun-link.md` says the same now.

## 1.69.4 - The guard of 1.69.3 never matched: the IPsec up script really leaves the SAs alone now

1.69.3 made the IPsec up script leave existing SAs alone (the health-check timer runs it every minute and every run used to add the SAs again, which restarts their sequence numbers at 0 and makes the other end drop everything as a replay). The release was proved by unit tests, and the **test panel showed it did nothing**: a 4-minute soak of an IPsec tunnel after the release still carried 5 of 8 downloads (3 of 8 got nothing), an IPsec NAT-T tunnel 1 of 8, and a look at the SAs on the servers after running the up script by hand showed their `add` time had moved - the SAs had been replaced again.

- **The cause.** The guard looks for the two SPIs in the output of `ip xfrm state` with the pattern `spi 0x<hex>(` - the form of `ip -s xfrm state`, which was the form seen while debugging. Plain `ip xfrm state` prints `spi 0x<hex> reqid ...` with no bracket, so the pattern never matched and the script went on to delete and add the SAs. The unit test's stand-in for `ip` printed the form the guard expected, so it agreed with the mistake.
- **The fix.** The pattern takes a space or a bracket after the SPI (`spi 0x<hex>( |[(])`); the test's stand-in now prints the real form of `ip xfrm state` (the SPI on its own line, no bracket).
- **Said plainly.** Between 1.69.0 and this release the two IPsec kinds carried data only in the minutes between the timer's runs; every IPsec tunnel built by those versions must be rebuilt (the Doctor does it by itself the first time one is cut). Whether this is the whole story of the IPsec blackouts is checked by the soak after this release (see the changelog of the release after it).

## 1.69.3 - IPsec links keep their sequence numbers, and a link in UDP is not turned down for the first packets it loses

The first run of the transitions test (2026-10-17, test panel: one Backhaul tunnel replaced by the next of ten links, twice, with a 60 MB download through the users' port after every step and a look at both servers) found two real faults that the unit tests and the lab could not see:

- **Both IPsec kinds carried nothing after a minute** (`ipsec:tcpmux`, `ipsecnat:tcpmux`: downloads of 0 kB, 17 MB and 62 MB on the same link; a debug run: 166 MB in the first 15 s, then 0 bytes, the tiny request after it 0 bytes too). The panel said *up*. The cause: the health-check timer of every link runs the link's **up script every minute** (it is meant to bring back a link that is missing), and the IPsec up script took the two SAs away and added them again every time. A new SA starts its sequence numbers at 0, the other end still remembers the numbers it has seen and drops every packet as a replay until the new count passes the old one (`replay ... failed 454` on the foreign server, `XfrmInStateProtoError` up by exactly 454, TCP stuck with 8 - 16 unacknowledged bytes on the Backhaul connections). The lab never saw it because it has no timer, and the link probe neither. **Fix:** the SAs and policies are made once; a second run of the script finds them (`ip xfrm state` names both SPIs) and leaves them alone. Tested by running the script itself three times against stand-ins for `ip` / `iptables`: the SAs are added once, deleted only by the first run's clean-up. **Still so (and said in the docs):** a restart of ONE end resets that end's numbers; the other end drops its packets until its own check (three silent minutes) restarts it too - the Doctor restarts both ends in one go, so this is only a reboot or a restart by hand of one server.
- **A link in UDP was refused at build for the first packets it loses.** `gue:tcpmux` (7 of 10 pings), `fou:tcpmux` and a WireGuard that needed its 5 s retry were rolled back by the build ("the link is up but the inner addresses cannot ping each other", at least 8 of 10 needed) although they pass 3 seconds later - the same loss of a new UDP flow that the probe's warm-up (1.69.2) handles. **Fix:** the build's check of a link that is not GRE uses the same warm-up script (up to eight single pings first, then the ten that count). A tunnel that was being repaired during a cut would have thrown a working kind away.
- **What the transitions run proved** (ten links in a chain, twice, 18 replacements + 2 first builds, every step looked at on both servers): after every step the link on the tunnel's interface was the new kind, with the right UDP encapsulation, the FOU / GUE port, the WireGuard key, the IPsec keys and SAs and the NAT-T socket only where they belong; after the last step and the deletion **nothing was left on either server, in both chains**; a build that fails (WireGuard, refused by the route at that minute; GUE with 7 of 10 pings and FOU with fewer than 8 in one direction) puts the old tunnel back and it carries its download. Not proved by this run: the IPsec data path itself after the fix (the transitions are run again after this release).

## 1.69.2 - The link probe warms a new UDP flow up before it counts

The link probe of 1.69.1 said **WireGuard does not pass** between the test servers - on the test panel, twice. The lab of 1.69.0 had said it does (19 / 634 Mb/s). What the two did differently was the wait: the probe waited 1.5 s and counted ten pings (3 s); the lab waited 3 s and sent twenty (4 s more).

> **Corrected in 1.69.5.** The title of this entry used to end with "it said WireGuard did not pass; it does". That was only half true. After this release the probe still said WireGuard did not pass - with the eight-ping warm-up too - and the lab then failed five times in a row on port 51820, in every way it was run (both ends up at the same time or one after the other, a 1 s or a 3 s wait), from about 20:33 to 20:42; at 20:43 the same lab passed on 51860 and on 51820 again. Earlier it had passed on 51820 three times of four and on six other ports six times of six. So WireGuard on this route is **there for some minutes and gone for others** (ports other than 51820 were not tried during the gone minutes, so it is not known whether the route refused the port or WireGuard): what the probe said was right at the moment it said it. The warm-up fixes the lost first packets of the UDP links (below); it does not make a refusing route pass.

- **What was found.** On this route the first packets of a NEW UDP flow are lost for a few seconds: in the two probes the UDP links showed 10 - 40 % loss (GRE in FOU 20 / 10 %, GRE in GUE 0 / 20 %, IPIP in GUE 30 / 40 %) while the raw-protocol links showed none, and WireGuard, whose first handshake packet is lost, repeats it only after **5 s** (its `REKEY_TIMEOUT`) - after the probe had given up. Run by hand in the lab, with a long enough wait, WireGuard passed 9 times of 10: on the default port 51820 in four runs (three passed - one of them with 40 % loss at the start - and one did not) and on six other ports in six runs (all passed). A first reading blamed the default port; ten runs do not show that, and **nothing was changed about ports**. What the one failure and the 40 % have in common is a lost first packet. (See the correction above: later the route refused WireGuard on 51820 for ten minutes in a row.)
- **The fix** (`ssh.py` script `link_probe_ping`): up to **eight single pings first** (a link that answers stops the warm-up at its first answer and costs one ping; one that does not pass costs the 8 s), then the same ten pings the verdict has always counted (GOOD = 6 of 10, both ends). The probe waits 1 s after the link is up instead of 1.5 s.
- **What the probe costs**, measured on the test panel: about **7 s for one link, 23 s for all nine** (the links run at the same time; only the three IPIP models wait for each other: they share one pair of addresses). 1.69.0 said "seconds" and "a few seconds": it is 7 and 23 - and the warm-up adds to a link that does not pass. A cut tunnel used to pay a minute or two per kind that could not work; it pays this once, and only for the links of the kinds it would try.
- **Test.** The script is checked as text (the warm-up, the ten pings, the parameters refused when they are not an interface name and an address).

## 1.69.1 - The IPsec key file reaches the server (found by the first live run of the link probe)

The first run of the link probe on the test panel (2026-10-17, right after 1.69.0, Iran test server -> build server) answered for seven of the nine links in 7 seconds each, 23 seconds in all - and said **"bad value for PATH: '/etc/nikatel/ipsec/lp-ipsec.keys'"** for the two IPsec links. The same would have happened to a BUILD of `ipsec:tcpmux` / `ipsecnat:tcpmux`: the key file of the link went to the server through the reviewed `put_file` action, and that action did not allow the directory `/etc/nikatel/ipsec`. The scripts of the IPsec links were proved (shell syntax, the right commands, a real run of them in the lab), the road that carries them to a server was not - the tests faked it.

- **The fix.** `put_file` writes into `/etc/nikatel/ipsec/<name>.keys` (the mode 0600 the builder gives it; the file name pattern is the old one: no `/`, no `..`).
- **The test that was missing.** `tests/test_linkmodels.py` runs every file of every one of the nine links through the same check that `put_file` makes (path and mode, as the builder and the probe give them), and proves that paths next to the key directory (`..`, a subdirectory, another directory under `/etc/nikatel`) are still refused.
- **Said plainly.** Until this release the two IPsec links could not be built or probed on a real server; the other seven could (the probe above: GRE, FOU, GUE, IPIP, IPIP/FOU, IPIP/GUE passed; WireGuard did not complete a handshake in that run - it is the link whose handshake this route has refused before, see 1.66.0).

## 1.69.0 - Nine links under Backhaul, a link probe that answers in about 20 seconds, and what each one measured

The owner (2026-10-17): "simple-tun-link has nine GRE models - add them all and test which is better, especially WireGuard; if one is better, put it in place of our GRE" and "combine all the GREs with the tunnels, test them - on one server one GRE model may answer and another not - the Doctor must keep all of it in its memory". 1.66.0 had taken three of the nine (GRE, GRE inside UDP, WireGuard). Now all nine are there, each was run for real between the two test servers, and the Doctor asks which of them pass **before** it builds anything.

- **The nine links** (`services/linkmodels.py`, a Backhaul tunnel runs over the inner /30 of one of them; every Backhaul transport - `tcpmux`, `tcp`, `wsmux`, `wssmux` - can ride any of them; a kind is `<link>:<transport>`, `tcpmux` alone is GRE):

  | link | what must pass between the servers | encrypted |
  |---|---|---|
  | GRE (`tcpmux`) | IP protocol 47 | no |
  | GRE inside UDP, FOU (`fou:tcpmux`) / GUE (`gue:tcpmux`) | one UDP port (5500-5599 / 5600-5699) | no |
  | IPIP (`ipip:tcpmux`) | IP protocol 4 - the kernel holds ONE IPIP tunnel per pair of addresses, the build checks that (`IPIP_PAIRS`) | no |
  | IPIP inside UDP, FOU / GUE (`ipipfou:tcpmux`, `ipipgue:tcpmux`) | one UDP port (5700-5799 / 5800-5899) | no |
  | WireGuard (`wg:tcpmux`, `wg:tcp`) | one UDP port (51820-51919) | yes |
  | IPsec ESP (`ipsec:tcpmux`) | IP protocol 50 | yes |
  | IPsec ESP inside UDP, NAT-T (`ipsecnat:tcpmux`) | one UDP port (4500-4599) | yes |

  IPsec is not strongSwan: it is the kernel's ESP in tunnel mode on an **xfrm interface**, one SA per direction, **the keys made by the panel** (random SPIs and 160-bit keys, AEAD `rfc4106(gcm(aes))`, 64-bit sequence numbers so the SAs never run out of numbers), kept in a file of 0600 on each server and given to `ip` through its standard input - **never in a command line, a script text or a log**; NAT-T is a small UDP socket unit that decapsulates. No IKE daemon, nothing to install (`ip_gre`, `ipip`, `fou`, `xfrm_interface`, `esp4`, `wireguard` are kernel modules; WireGuard needs `wireguard-tools`, installed only for a build the owner asked for, as since 1.66.0). Every value that goes into a script is checked first, a firewall rule names the exact peer and protocol or port, the link keeps the names a GRE link has (so the units, checks, sentinel, live numbers, bandwidth job, rebuild and trash treat all nine alike), and removing a tunnel removes its interface, UDP port, keys, SAs and socket unit (checked on the servers: nothing is left).
- **The link probe** (`services/linkprobe.py`; the answer to "on one server one model may pass and another not"). For a pair of servers it writes a temporary link of EVERY model (`lp-<link>`, own address, own port), brings them all up **at the same time**, pings the inner address ten times each way, and always removes everything again (on both servers, even after an error; a stale one is also swept by the purge of test leftovers). About 7 s for one link and 23 s for all nine (measured in 1.69.2: the links run at the same time, only the three IPIP models wait for each other). The answers - passed or not, delay, loss, why not - are kept in the Doctor's memory of the pair (`knowledge.pairs[...].links`, `record_link` / `link_state`) and shown with the rest of what it knows.
- **The Doctor uses it where a minute counts.** A cut tunnel is replaced by the first kind that connects, and each try used to be a build of one or two minutes (a kind whose link is filtered fails only at the end). Before the tries, `_switch_cut` asks the probe, in about 20 seconds, which links pass and **leaves out every kind whose link does not** (`autopilot._probe_links`); the story of the repair says which and why. A kind of another engine has no link and stays; a link the probe could not judge stays; when the probe cannot run at all the list is as it was. A cut link does not get its own kinds as replacements (GRE filtered: the other eight are tried; WireGuard filtered: GRE and the raw ones are), as since 1.66.0.
- **The order, from the measurements below** (`knowledge.PRIOR`, `LINK_KINDS`): IPIP, IPIP inside FOU, IPsec ESP, IPIP inside GUE, IPsec NAT-T, WireGuard, GRE inside FOU / GUE. The night survey, the pair test, the Compare form and *Test now* know all of the kinds; a pair whose survey found one connecting tries it first, as before.
- **TCP "ping" no longer reads the cost of starting a handshake.** The "ping" of a tunnel whose provider drops ICMP (1.68.0) is ten TCP handshakes; each one now has the time of a handshake to a closed LOCAL port taken off (the cost of starting `bash`/`timeout`), so a port next door reads about 0 ms and a far one is not a few ms too high. The script is run for real in a test.

**Measured, for real, between the Iran test server (4 cores, kernel 5.15) and the build server (2 cores, kernel 6.8), 72-78 ms apart** - each link brought up with the panel's own scripts on both servers, 20 pings each way, the largest packet that passes (1332 bytes of payload went through all nine: the inner MTU of 1360 holds), TCP throughput over the inner addresses with 4 streams for 6 s, twice (the better run; the two agree), the CPU of both servers during it:

| link | Iran -> foreign (Mb/s) | foreign -> Iran (Mb/s) | note |
|---|---|---|---|
| **GRE** | **1306** | **1352** | the current link |
| IPIP | 1307 | 1290 | as fast as GRE; one tunnel per pair of servers |
| IPIP inside FOU | 1228 | 736 | |
| IPsec ESP (encrypted) | 1055 | 778 | the fast encrypted one |
| IPIP inside GUE | 43 | 620 | |
| IPsec inside UDP (encrypted) | 30 | 668 | |
| WireGuard (encrypted) | 19 | 634 | |
| GRE inside FOU | 2.4 | 648 | |
| GRE inside GUE | 2.0 | 666 | |

**So: GRE stays the default - nothing beat it, and WireGuard is the slowest of the encrypted ones on this route** (19 Mb/s towards the foreign server; the other direction 634). The raw-protocol links (GRE, IPIP, IPsec ESP) carry 1.0 - 1.35 Gbps both ways; every link carried in UDP is fast from the foreign server to Iran (620 - 740 Mb/s) and slow in the other direction, except IPIP inside FOU. That is a property of this route (and of these two servers' kernels), not of the code: the same scripts, the same ports, the same hosts - and a plain-UDP test with a Python sender could not be made to work in the time there was (nothing arrived at any rate, while five small datagrams did), so the cause is **not isolated**. The panel does not guess: the Doctor's pair test measures every kind between the two servers it runs on, and the order above is only the order for a pair nobody measured. The numbers are one route, one afternoon, a 2-core machine at 50 - 70 % CPU during the runs.

**Which cut each link survives** (the same lab; the condition is a firewall rule that drops one protocol between the two servers only - in the `raw` table, before the links' own accept rules - SSH and everything else untouched; "✗" = the inner pings got through with 100 % loss):

| dropped between the servers | GRE | GRE/FOU | GRE/GUE | IPIP | IPIP/FOU | IPIP/GUE | WireGuard | IPsec ESP | IPsec/UDP |
|---|---|---|---|---|---|---|---|---|---|
| nothing | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| IP protocol 47 (GRE) | **✗** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| IP protocol 4 (IPIP) | ✓ | ✓ | ✓ | **✗** | ✓ | ✓ | ✓ | ✓ | ✓ |
| IP protocol 50 (ESP) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **✗** | ✓ |
| all UDP | ✓ | **✗** | **✗** | ✓ | **✗** | **✗** | **✗** | ✓ | **✗** |
| 47, 4 and 50 together (only TCP, UDP and ICMP pass) | **✗** | ✓ | ✓ | **✗** | ✓ | ✓ | ✓ | **✗** | ✓ |
| 47, 4, 50 and all UDP (only TCP and ICMP pass) | **✗** | **✗** | **✗** | **✗** | **✗** | **✗** | **✗** | **✗** | **✗** |

Each filter cuts exactly the links that carry that protocol, and no other: what the Doctor assumed since 1.66.0 for GRE and UDP, now proved for all nine. Where a filter drops every protocol but TCP / UDP / ICMP (the usual "whitelist" kind), only the six UDP links are left, and where UDP is dropped as well, none of the nine is - the TCP engines (BackPack, Chisel, Paqet) are what is left, which is why they stay in the Doctor's list.

- **Tests.** `tests/test_linkmodels.py` (19), `tests/test_linkprobe.py` (13), and the updated `test_links.py`, `test_build_other.py`, `test_cut_tunnel.py`: the registry and the kinds; the scripts of every model (the right kernel commands, valid shell - `sh -n` and a real run in two namespaces where the kernel allows, nothing secret in a command line, a firewall rule that names the exact peer and protocol, idempotent up, a down script that removes only what it owns); what a build checks first (modules, a taken UDP port, an IPIP pair, WireGuard tools); the probe (all at once, always cleaned, one failing link does not stop the others, a probe that cannot run changes nothing); and what the Doctor does with all of it.

## 1.68.2 - One cause, one message: fourteen configs that fall together are one line, not fourteen errors

The owner (2026-10-17, a Telegram screenshot: **DOWN (14)**, fourteen configs, every one with the same `curl: (35) OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to www.gstatic.com:443`): "why did these errors come for nothing - so many errors for one small disturbance?"

- **What it was.** Not fourteen faults. The probe tests every config by fetching one address (`www.gstatic.com`) through it; when the probe's own path to that address has a bad moment, **all** configs fail with the **same** error at the same time - a single cause that the old message told fourteen times. The panel already needed two different tests to agree before it called a config down; what it did not do was notice that fourteen things failing together with one error are one event.
- **Mass events (`alerts.MASS`).** When many things of a kind are bad at the same moment - configs: at least 4 and at least half of them; servers: 3 and 40 %; tunnels: 4 and 30 % - their DOWN messages **wait** (configs 90 s, servers 30 s, tunnels 20 s, counted from the first one; the probe tests configs one after the other, so they fall one after the other). **A disturbance that is over by then is not told at all** (no DOWN, so no UP). Two configs, or one server, are told as fast as before (`down_seconds`, 10 s): the rule starts at the number that makes a mass.
- **One line with the count, the common error, a few names and the likely cause.** Example: `🔴 DOWN (14)` / `• 14 configs fail together, all with the same error: curl: (35) ... gstatic.com:443` / `Turkey-1, Germany-1, Finland, Emirates, France, Poland, … (+8)` / `The same test fails for all of them at the same moment: the probe's own path to its test address is the likely cause, not 14 separate faults.` When most but not all share the error (70 %): "10 of them with the same error". Fewer than a mass are one line each, as before.
- **The way back is one message too.** While the mass lasts the UP messages wait; a group that was told together comes back together (the UPs wait for the last good one to stay good for `up_seconds`, a few seconds): `🟢 UP again (14)` / `• 14 configs work again` / the names. A group never waits for a thing that stays down: under a mass it is told by itself.
- **The events page says it once as well.** Four or more configs that change status in the same read of the probe are ONE event (`14 configs are not working at the same moment, all with the same error: ...`), not fourteen rows.
- **Tests.** `tests/test_alert_mass.py` (9): the pure rules (when a mass is a mass, the error most share, the lines); fourteen configs falling one after the other over 26 s with one error -> nothing for 89 s, then ONE message, once; the same disturbance over within the wait -> nothing at all; the way back waits until the mass is over and is one message; two configs are told one by one at 10 s as before; three servers that stop answering are one line; the events page.

## 1.68.1 - The warm standby: a tunnel that is cut is replaced in seconds, not rebuilt (off until you mark a tunnel)

The owner (2026-10-17): "bring the delay down to the minimum - in every step" and, for the list of nine, "do items 1 to 9". Items 1 - 8 shipped in 1.65.0 / 1.65.1 (no waiting time, the wake-ups, the look after a restart, the retries, the fast lane, the parallel builds, the cold-pair survey) and were sharpened by the sentinel and the quick read of 1.66.0 - 1.67.2: a cut is called *down* in about 5 s.
What waiting less cannot shorten is the last part: **a tunnel of another type takes 60 - 90 s to build** (86 s on average over the real repairs of two weeks on the test panel). Item 9 is a tunnel that is already built when the cut comes.

- **The standby.** *Tunnel window → Standby → Turn on* builds, beside the tunnel and between the same two servers, a second tunnel named `<name>-sb` of **another carrier** (a Backhaul-over-GRE tunnel gets a BackPack / Chisel / ... standby that is known to connect or untested, a GRE one only when nothing else is left),
  on **shifted ports**: the users' port `P` is `20000 + P` on the Iran server (`P - 20000` above 45535), the port on the foreign side stays. It carries no user. Its row is switched off (`enabled` False, `record.standby_of`, `models.is_standby`), so every list, count, alert, repair, survey, bandwidth
  and move of the enabled tunnels leaves it alone by itself; the few places that must see it (ports and names taken, a server that cannot be deleted) read the table as before. The mark is kept by the tunnel's NAME (settings `standby`), so a repair or a rebuild keeps it; a rename moves it; removing the tunnel removes the standby.
- **The handover.** When the tunnel has been **down for 5 s** (`after_seconds`) and its standby is up, ONE reviewed action on the Iran server (`ssh.py` `standby_on`) puts a NAT redirect from the users' port to the shifted port (`PREROUTING`, tagged `nikatel-standby-<name>` so nothing else is touched, an `ACCEPT`
  for the shifted port in `INPUT`, idempotent, kept across a reboot by a small unit): about 2 s. The Doctor's ladder goes on with the tunnel in the background. When the tunnel has been **up for 60 s** (and no repair works on it) `standby_off` takes the redirects off; open connections end on the standby by themselves.
  Every handover is an entry of the Doctor's ledger (*Tunnel Doctor → Auto Moves*, with **Back now**) and an event; it is not a "move" for the rails of the moves. The panel reads the redirects of the servers every two minutes: a handover whose redirects are gone is put again, a redirect nobody asked for is taken off.
  **Not redirected: the request the panel makes on the server itself.** The first design also redirected `OUTPUT`; then the tunnel that is cut would be judged by the standby's answers and look up. A tunnel whose ports are on its standby (`tunnels.HANDED`) is judged without the look from another Iran server, which the standby would answer: by its units, its link, its ports and the request on the server - what it carries itself.
- **Proved on the real kernel (2026-10-17, the build server, two network namespaces joined by a veth pair; the panel's own actions run against the real `iptables`):** the tunnel cut -> refused; `standby_on` -> a new user gets the standby; the request on the server itself still sees the tunnel (not redirected);
  `standby_off` -> new users are on the tunnel again; the boot script puts the redirect again after the rules were flushed and takes it off. **A finding that changed the way back:** a connection opened during the handover was RESET by the way back, because deleting the LAST rule of the NAT chain makes the kernel
  (nf_tables) forget the translation of every open connection. The way back now puts a harmless tagged `RETURN` at the top of the chain first and deletes the redirect after it (the `ACCEPT` becomes an accept of the established connections only): the open connection goes on working on the standby. Those parked rules go with the standby.
- **Never a cut of the tunnel.** A standby is checked by its own job every 15 s with the same check as any tunnel, quietly: no event, no history rows, no wake-up of the repair job, no alert, no repair, no event when it is removed or built again. One that is not up for 45 s has its services restarted, one that is not up for 5 minutes is built again - only while the tunnel is up and no repair works on it.
- **The Doctor's modes.** *off*: nothing by itself, the buttons work (*Turn on*, *Switch now*, *Back now*); *fix* and *move*: built, mended and handed over by itself. The rails: one standby per tunnel; nothing while the panel is read-only, in a storm, while the tunnel is not up or a repair works on it, on a server over 85 % CPU / 90 % memory, or for a pair every type of which failed lately;
  a build that runs when the tunnel falls is stopped so the servers are free for the repair (the standby is built under its own lock beside a repair of its tunnel); a failed build waits 2, 4, 8 ... 60 minutes and its type is not chosen again for 6 hours; a handover is refused when the standby is not up or no longer fits (other servers after a move, other ports after *Ports*: it is removed and built again);
  an automatic build never installs a package.
- **The page.** A bar under the banner of the tunnel window - *Standby · Off / Building / Ready / In use / Being mended / Waiting / Not built* - with the type, the shifted ports and the buttons; `GET|PUT /api/tunnels/{id}/standby`, `POST .../standby/handover`, `POST .../standby/back`; `GET /api/tunnels` carries `standby`. Persian texts included. Design and rails: `docs/standby.md` (replaces `docs/standby-design.md`).
- **Tests.** `tests/test_standby.py` (53 tests): the pure rules; the reviewed actions **run for real** against a fake `iptables` (idempotent, only the tagged rules go, a longer name with the same start is untouched, the way back parks a rule, a server without iptables changes nothing); the hidden row through the list, the counts, the report, the alerts, the Doctor's restarts and smart repair,
  the sentinel, the minute cycle and the fast lane; a standby that falls makes no event; a tunnel under a handover is judged without the outside look; the handover, the way back and the failures; the job with its delay, its modes and its rebuilds; the build, the switch, the API, the rename, the reconcile, the page. Two existing tests describe what the standby changes (a tunnel that turns down wakes the standby job too; the bar in the window).
- **Not done: the proof on throw-away servers with a real tunnel.** The design asked for it before shipping (build a tunnel and its standby, stop the tunnel's unit, measure the time to the first answer through the users' port, bring it back, repeat with GRE dropped and with a reboot of the Iran server during a handover). The redirect itself is proved on a real kernel (above), the rest is proved with the servers stubbed. The release ships with the switch **off everywhere**:
  do the drill on the test panel (*Standby → Turn on*, then *Switch now* / *Back now*, then stop the tunnel's unit) before a tunnel with real users is marked.

## 1.68.0 - "No ping" is measured by TCP handshakes now

The owner (2026-10-17, a screenshot of a BackPack wssmux tunnel that carries its users but shows "no ping" under 87 ms): "find out why it says no ping, fix it, and test that the other tunnels do not have it".

- **Why:** the tunnel is pinged from its Iran server to the public address of the foreign server (a BackPack / Chisel / Paqet tunnel has no GRE link to ping over). That server (or the route in front of it) drops ICMP: the 20 pings all go unanswered while the users' request through the forwarded port is answered, so the panel
  knew the tunnel worked ("the provider drops ping") but had **no loss number and showed the time of the users' request instead of a ping**. On the owner's panel one of 20 tunnels was in that state; the 19 others answered the ping (the Backhaul ones over their GRE link, the BackPack ones over the public address).
- **Fix:** when ICMP is dropped and the users' path works, the "ping" is **ten TCP handshakes to the control port of the tunnel**, made by the server that dials it (the foreign server for the reverse kinds - BackPack tcp / tcpmux / ws / wsmux / wss / wssmux / stealth, Chisel, Chisel TLS -, the Iran server for the forward Chisel kinds): that port is the one place the two servers surely talk through.
  The script `tcp_ping` (ten connections, one every 0.1 s, each waits at most 1 s; a handshake that needs more than 800 ms counts as lost because the kernel repeats a lost SYN after 1 s) gives the loss and the latency; the cell shows the real % and the latency of the handshake (the tooltip says "counted with ten TCP handshakes").
  It runs only when ICMP is dropped (never for a tunnel whose ping answers), only for the kinds whose control port is a TCP listener (not Paqet, KCP, QUIC, raw-packet kinds, nor the GRE kinds, which are pinged over their own link), and not when the dialling server is off. When the handshakes do not answer either, the cell stays "no ping" - honest.
- **Other tunnels:** tested in `tests/test_noping.py` for every direction (reverse from the foreign server, forward Chisel from the Iran server), for the kinds that must not be knocked on, for a ping that answers (no handshake at all) and for a server that is off.

## 1.67.2 - Tests that waited for the clock now wait for a gate

- **Measured after 1.67.1** (the sentinel every 5 s; the same real GRE tunnel, link service stopped, four rounds): the panel said *down* **5, 5, 5 and 2 seconds** after the cut (23 s before 1.67.0, 8-10 s with the 10 s tick).
- **CI.** The second GitHub run was green on Python 3.10 (the cookie test of 1.67.1 did it) and red on Python 3.12 on `test_the_check_is_started_not_awaited`: it asserted that a request returned in under 1.0 s while a fake check took 1.5 s - 1.05 s on a runner that runs the suite on every core
  with the coverage on. A test that proves "it did not wait" now holds the fake at a gate (`threading.Event`) that the test lets go: the answer arrived while the gate was shut. The same was done in `test_long_work_answers_at_once_with_an_action_id`, `test_the_dns_is_asked_once_in_ten_minutes_and_never_for_long` (the lookup that hangs)
  and the bound of the onboarding test was widened. `docs/development.md` already says a test that measures the clock needs a second proof that does not depend on it.

## 1.67.1 - The sentinel looks every 5 seconds; the first CI run found one test that depended on the Python version

- **Measured after 1.67.0, on the test servers** (a real GRE tunnel, its link service stopped on the foreign server, four rounds; the time is `state_since` of the tunnel minus the moment of the cut): **8, 10, 9 and 9 seconds** until the panel said *down* - it was **23 s** before 1.67.0.
  From the line "sentinel: ... checking it now" to the state *down* there are now **0.3 s** (it was 15 s: the pings, the request through the port and the look from another server). Nearly all of what is left is the wait for the next tick of the sentinel, so the sentinel
  now runs **every 5 seconds** (it was 10): one command per server over the SSH connection that is already open, so the cost is small. Expected: a cut is called down after about 4-6 s, and a DOWN message reaches Telegram `down_seconds` (10) later.
- **CI.** The first run on GitHub (the commit of 1.67.0) was green on Python 3.12 (the pinned libraries, with the coverage), the installer on a clean machine, the quick checks and gitleaks, and red on Python 3.10 (newest libraries) on ONE test:
  `test_logout_others_keeps_this_session_and_ends_the_others` set the old cookie into a client that still held the new one, so the client sent both and which one the server read depended on the version of Python. The test empties the cookie jar first now (a browser keeps one cookie of a name).
  The jobs of the matrix are called `tests (python 3.10)` / `tests (python 3.12)`.

## 1.67.0 - Telegram in seconds, not minutes; the CI runs on every push again, and everything that can run at the same time does

The owner (2026-10-17): "check the notifications that come to my Telegram: why does it say the Fastly account is down; an Iran server was switched off, I switched it on and the 'up' message never came; go through the list of errors from the start and bring the delay to the
minimum" and "find out why GitHub has had no update since this version; do all the parallelisations and do not leave the engineering path; update the CI".

**Why a message was late** (read from the owner's panel; nothing was changed there): a tunnel or a config had to fail **3 checks of a job that looked once a minute** (a server 2) **and** stay down **4 minutes** before one DOWN went out - about 5 minutes after the event.
An UP waited until the thing had been good for **10 minutes** (`up_stable_seconds` 600) and then for the next look: a server switched on at 13:15 was announced at about 13:26 - the owner looked earlier and saw nothing. A config had to be read as bad twice with two
minutes between the reads (the probe tests it every ~30 s), then wait the 4 minutes. And a message Telegram did not take (a timeout, "too many requests") was **lost for good**: the state was already marked as told. About Fastly: the panel was right - the probe's test of that
config failed with "SSL connection timeout" for 40 minutes, and the config changed its status 7 times in 2 hours (a CDN address the probe's route reaches only now and then; no tunnel of ours is on it); the message only came late and said nothing more than the probe's own error.

- **Alerts in seconds.** The rules are two numbers now: `down_seconds` (default **10**) and `up_seconds` (default **15**), Settings → Alerts → Alert rules. The checkers confirm a fault themselves (a tunnel after its confirming checks, a server after two missed 5-second pulses, a config
  after two different tests of the probe, a panel after two reads); the alerts job looks **every 5 seconds** (it was every 60) and the clock of a message starts at the moment the CHECKER changed the state (`state_since`), not at the next look. Typical time from the event to Telegram:
  a server off or back ~15-25 s, a tunnel cut by a stopped unit ~15-30 s, a tunnel that only stops carrying data ~45 s, a config ~40-70 s. The flapping guard stays (3 outages an hour -> one FLAPPING message, quiet for 6 hours), reminders stay off by default.
  The rules saved by older versions (failed checks in a row, minutes, stable seconds) become these two numbers: the old defaults give the new defaults, a value the owner had changed is kept (minutes -> seconds). The look no longer rewrites a row of the database while nothing changes.
- **A message is never lost to one failed request.** What Telegram does not take (a timeout, 429, a 5xx) is kept and sent again every 10 seconds, in order, for half an hour (`alerts._OUTBOX`; the event says "sent (after 2 tries ...)"); what can never work (Telegram off, a wrong chat id) is dropped at once with the reason.
  The messages are sent after the database is let go, so a slow Telegram does not hold one of its connections. Durations are told in seconds under a minute ("was down 40 s").
- **Configs.** The panel reads the probe **every 15 seconds** (`configs-fast`; the full round with the groups and the reach question to the Iran servers stays every 2 minutes) and a config changes its status after **two different tests** of the probe agree - the same test read again counts nothing
  (`settle_config(..., seen)`), so the quick reads do not weaken the rule. A direct TCP config the probe calls down is not turned down by the quick read before the Iran servers said whether its port opens: the full round is asked to run at once. The history keeps one point every ~2 minutes as before.
- **Panels.** Checked every 15 s (was 30): a panel or a node that stops answering is called down after two checks (~30 s, was ~60 s); a node that stays disconnected is asked to reconnect at most every 30 s (it was at every check).
- **The Doctor sees a cut sooner.** Measured on the test servers (a stopped link service on a real GRE tunnel): the sentinel saw it **7.6 s** after the cut, but the tunnel was called *down* only after **23 s**: the check pinged a silent peer twenty times (~5 s), asked for one request
  through the forwarded port (~4 s) and then made the look from another Iran server (~6 s) one after the other. Now: a stopped unit or a card that is not up gets a **quick read of the units and the card** first (`tunnel_side` with `QUICK=1`, a second or two) and is *down* at once;
  the full check asks the two servers AND the look from another Iran server at the same time, the request through the port runs while the pings run, and a peer that answers none of three quick pings is not pinged seventeen more times. `tunnels.hard_fault` is the pure rule `judge` already used.
- **GitHub CI.** The workflow had been "run by hand only" since 1.52.2 (the private repository used up its free minutes) - that is why no run followed the commits since then. The repository is public (unlimited minutes): `ci.yml` runs on every push to `main`, every pull request, once a week and by hand,
  with four jobs **at the same time** (quick checks: secrets, ruff, JS and shell syntax; the tests on Python 3.10 and 3.12 spread over every core with pytest-xdist, the 3.12 job on the pinned libraries of `requirements.lock` and with the coverage; the installer on a clean machine; gitleaks), a timeout on each job,
  and a newer push cancels the older run. A red suite piped into `tee` would have counted as green (the default shell has no `pipefail`): fixed.
- **Parallel work in the project's own flow.** The whole suite runs on every core with pytest-xdist (`pip install -e ".[dev]"`, `pytest -n auto`; every worker has its own temporary data directory): **1467 tests in 256 s on the two cores of the build server, 445 s one after the other**.
  On the build server `tfull.sh` starts the tests in the background and runs lint and the JS check beside them; `ship_t.sh` runs lint, JS and the secrets scan at the same time, the tests on every core, and the deploy to the test panel while the tag, the release and the issues are done on GitHub.
  A test that measured wall-clock time with a narrow bound (`test_the_panels_are_checked_at_the_same_time`) had a second proof that does not depend on the clock and a wider bound.
- **docs/simple-tun-link.md** now says what was really measured: GRE and GRE inside UDP work end to end between the two test servers; the WireGuard scripts work (two network namespaces) but the WireGuard handshake does not pass on the real route between them (a filter on the route) - on such a pair the Doctor records it as "not connecting" and tries the next type.

## 1.66.0 - WireGuard and GRE-inside-UDP links for Backhaul (after the review of simple-tun-link)

The owner (2026-10-17): "look closely at this project (github.com/ach1992/simple-tun-link) and add what we do not have, or replace what must be replaced; and I want WireGuard added". The review is in `docs/simple-tun-link.md` (what STL is, what the panel
had, what was taken and what was not, and why).

- **WireGuard.** *Create tunnel → Backhaul → TCP Mux · WireGuard* (also *TCP · WireGuard*): the Backhaul tunnel runs over a kernel WireGuard link on one UDP port (51820-51919, the same on both servers) instead of GRE. It passes where IP protocol 47 is
  filtered - the commonest cut the Doctor meets (two of its three real repairs of the last two weeks were `gre_blocked`) - and it is encrypted and authenticated. The keys are made ON each server (`wg_keygen`: a file of 0600, only the public key comes back
  and is kept); the up script reads the private key from its file, never from a command line. Interface `<tunnel name>`, tagged with the alias `nikatel:<name>`; a firewall rule names the exact peer, local address and port; MTU 1360.
- **GRE inside UDP (FOU).** *TCP Mux · GRE over UDP*: the same GRE link carried in one UDP port (5500-5599) - for a route that drops protocol 47 but lets UDP through, with no package to install, not encrypted.
- **Packages.** The tool `wg` comes with `wireguard-tools`. A build the OWNER asked for (Create tunnel, a test he started, *Test now*, the Compare form, Smart test) installs it with apt on a server that has none; the Doctor and the night survey never install
  a package on a server - they fail with the sentence that says what to do, and the Doctor tries the next tunnel type.
- **The Doctor knows the links.** New kinds `wg:tcpmux`, `wg:tcp`, `fou:tcpmux` (the pair test, the night survey, the replacements of a cut tunnel - last in the static order, first when a survey found one connecting on that pair). A rebuild or a
  switch keeps the link of a tunnel (`kind_of`), a switch to a GRE kind drops it. A tunnel whose own UDP link is cut is not offered the other UDP links; a GRE tunnel cut by a protocol-47 filter is. The diagnosis says "the UDP port of the link is filtered".
- **The list and the window** show `WireGuard` / `GRE/UDP` (and the UDP port) where they said `GRE`.
- **Found on the way:** the "avoid the old inner network" option of a fresh-link rebuild (`avoid_net`) was dropped by the option check and never reached the build; it is kept now. Every `iptables` call of the new links waits up to 5 s for the xtables lock.
- **Removing a tunnel** also deletes a WireGuard link, its key and a FOU port; the test purge removes a leftover WireGuard test link.
- Tests: `tests/test_links.py` (the scripts of every link end, `bash -n` on each; the key never in a script; the facts, the port, the options; the kinds; the API; what a Doctor build may and may not do).

## 1.65.1 - The Doctor has no waiting time: it starts the moment a tunnel is down

The owner (2026-10-17), his picture of *Auto Fix Tunnels* ("If a tunnel is down for 60 seconds, fix it automatically"): "remove this time here too - the Tunnel Doctor must start its work from the very first moment the tunnel has trouble: from rebooting the tunnel to everything else it has to do".

- **The waiting time is gone.** The field (the number of seconds a down tunnel waited before its plain restart, 60 by default, 20 in 1.65.0) is removed from the *Auto Fix Tunnels* card, which now says "The moment a tunnel is down the Doctor starts - no waiting time". The restart begins at the first tick of the repair job after the check that found the tunnel down (the job is woken by that very check, so it is a second or two), and the smart repair follows the same second the restart has ended without healing the tunnel. A value saved by an older version is ignored (the setting stays in the API so that an old client does not break; it always reads 0). What still protects a blip: a tunnel is *down* only when a check found a unit stopped, the GRE card gone, the GRE link silent over twenty pings, or the users' path cut for four checks - an SSH hiccup is *unknown*, never *down* - and a tunnel gets ONE plain restart per cut.
- **A degraded tunnel is repaired after 2 minutes, not 10** (`DEGRADED_AFTER`): one that flaps, loses packets or moves no data. Its repair tests the candidates beside it and swaps only for something clearly better at a quiet moment, so starting earlier costs the users nothing.
- **The look at the tunnels that lose packets runs every 30 s** (it was every five minutes), at most one look per tunnel every half hour as before.

## 1.65.0 - The Doctor's delay, brought down in every step

The owner (2026-10-17), after the step-by-step timing of the Tunnel Doctor (a cut tunnel took about 4 - 6 minutes from the cut to its return): "I want the delay in all the steps to be very low".
Where the time went: up to 60 s to notice a tunnel that was up, 60 s of grace and 15 - 50 s of a restart that cannot cure a silent route, 10 - 20 s at every hand-off between the repair steps, about 90 s per tunnel type tried,
60 s of holding, and 10 minutes before a failed repair was tried again.

- **A sentinel looks every 10 s** (`services/sentinel.py`, job `sentinel`). ONE light command per server for all its tunnels: the state of every unit, of every GRE card, and three quick pings of every GRE peer, in parallel. A tunnel that is up and
  loses a unit (either end), its GRE card or its GRE peer gets a full check at once, with a fresh look from another Iran server - not at the next turn of the minute cycle (a cut was noticed after up to 60 s, now after about 10). It never changes a
  state itself; it leaves alone the tunnels a repair works on, the ones whose provider drops ICMP, and a tunnel looked at in the last 30 s.
- **The repair job ticks every 5 s and wakes at once** when a tunnel turns down or degraded and when a restart or a smart repair ends (it ticked every 20 s: each hand-off between detect, restart, smart repair and retry waited 10 - 20 s).
- **The grace before the first restart is 20 s** (*Auto Fix Tunnels*; it was 60 s, and the field now takes 10 - 3600). The fast lane checks a cut tunnel every ~8 s, so 20 s are two checks.
- **A restart is judged sooner.** The first look after it is 4 s away (was 8) and the next ones 5 s apart (was 10): a healed tunnel is seen about 10 s sooner. A cut that is still the **silent route** (no answer over the GRE link, the port cut in front of
  the server) after three looks is not one a restart cures: the smart repair goes on at once instead of waiting out the rest (about 30 s).
- **The hold after a repair is 25 s** (was 60): the tunnel must still be up then, in a fresh check; a fall later is caught by the relapse rule.
- **Retries come sooner:** a cut tunnel is tried again 1, 2 and 5 minutes after the END of its last smart repair, then every 10 (it was a flat 10 minutes from the start of the last one). The types that failed on the route are not tried
  again for two weeks, so a quick retry costs little. Up to 4 smart repairs run at the same time (was 2): when one route event cuts several tunnels the last one no longer waits for two or three repairs.
- **A degraded tunnel that moves no data is in the fast lane** ("no data comes back", "blocked in front of the server", "port … is not listening"): its failed data probes are counted every ~8 s, so it is called *down* (and repaired) after about a
  minute, not four.
- **A build asks the two servers at once:** the facts, the MTU probe, the kernel tuning, the making of the two GRE ends and the GRE check were asked of the Iran server and then of the foreign server; the second answer was waited for for nothing
  (about 15 - 25 s of every build, and a repair builds up to dozens of times). A built tunnel must still run 5 s later (was 8).
- **A pair nobody has tested is surveyed by day too**, on servers whose tunnels carry under 30 Mb/s (the night survey stays as it was for the rest): without what the Doctor knows about a pair, a cut on it is repaired by trying the tunnel
  types one by one - the longest part of a repair.
- Tests: `tests/test_delay.py` (the sentinel's parser, signs and reads, the wake-ups, the restart look, the retries, the fast lane, the parallel builds, the cold survey); the tests that held the old numbers say the new ones.

## 1.64.6 - The tunnel table's columns share the width evenly; the address cells breathe

The owner (2026-10-17), his picture of the Tunnels table on a 2000 px window: "the parts where the server addresses are written are very crowded"; "the empty space between the cards is too much"; "put the columns right, make the empty space between the columns less, tidy it".

- **The room is shared, not piled up.** The name, the type and the ports had 19 / 19 / 12 % of the width and the middle block (bandwidth, Iran node, ping, foreign node) had fixed pixels, so on a wide window everything that was left landed between the type and the bandwidth (about 230 px), after the foreign node (about 200 px) and after the ports (about 250 px), while the two address cells stayed tight against the ping arrows. Every column has a share now (name 13, type 14, bandwidth 10, Iran node 9, ping 11, foreign node 11, ports 8 %, the "⋯" button 56 px), tuned on a demo panel: on a 2000 px window the gaps between the columns are 110-160 px, nearly equal; on 1440 px nothing is cut, and on 1180 px it is no tighter than before.
- **The address cells are calmer.** The address (monospace) is one step smaller and lighter than the bold name above it (two equally heavy lines were the crowd), the line height is a little more open, and the Iran and foreign cells keep 20 px of air on the side of the ping arrows.

## 1.64.5 - The capacity number is amber no more; only a real drop is coloured

The owner (2026-10-17), his picture of the Tunnels table (capacity numbers 799, 831, 544, 1085, 207 in amber, the rest grey): "do not show these yellow numbers unless there is really a problem or a real drop in bandwidth".

- **A capacity a little under the usual is not coloured.** The Doctor calls a capacity of 60-80 % of the usual level "low" and the row painted it amber, so a fifth of the table was yellow without anything being wrong. The row now shows the number in its ordinary colour for everything but a **drop** (under 60 %, confirmed by a second test) or a **stall** (no data moved), which stay red. The state itself is unchanged - it is still in the tooltip and the tunnel window (Link → Capacity) - only the row stopped colouring it. A test covers it (`tests/test_web_menu.py`).

## 1.64.4 - The Spoof test picks its own target, once one is proven

The owner (2026-10-17), on the target picker (his picture: a list of ten Iran servers to choose from): "instead of this list, can't it use an Iran server that already tested OK and just run the test with that".

- **A proven target is reused by itself.** The list of servers asked "send the forged packets to which server?" every time. Now, if any server has ever come back `can` in a spoof test - from this server or any other - it is used as the target straight away, and only the address to forge is asked. The list only comes back when nothing is proven yet, or the proven one is gone, off or disabled.

## 1.64.3 - The Bandwidth row is one number each; the breakdown moved into the tunnel window

The owner (2026-10-17), his picture of a PAQET row reading "199 ▼259 | ↓9.7 ↑3.8", both red: "what are these red numbers, not clear"; "instead of showing upload and download here, show one number"; "put more detail inside the tunnel box".

- **Capacity — one number.** The row showed the capacity it dropped to and, beside it in the same red, "▼" the usual level it dropped from - two red numbers with no label, read as noise. The row now shows one number, coloured by its state (green healthy, amber low, red dropped or stalled); the usual level, when it was last measured and why it dropped are the tooltip and the tunnel window (Link → Capacity, which already had all three, plus "measure now").
- **Usage — one number, the download.** The row showed ↓ and ↑ side by side; it now shows the download alone, what the users actually get. The upload is still in the tooltip and in the tunnel window (Status → "In use now"), together with the share of the capacity in use.

## 1.64.2 - The cores table fills its width (Status at the right); the ping arrows shorter and the ping number smaller

The owner (2026-10-17): "move the Status column to the right so the empty space fills" (his picture of the cores table); "make these arrows shorter, it is too crowded, and make the ping number smaller too" (his picture of the Ping/Loss column).

- **Cores table — Status at the right edge.** The Status cell was wide with its text on the left, so the row was empty on the right. Now Newest takes the slack and Status (✓ Up to date, or the Update button) sits at the right edge; the row reads Core · Newest … Status across the whole width. Newest is wider too, so a long version is no longer cut.
- **Ping / Loss cell — shorter arrows, smaller ping.** The dotted arrow ran almost the full width of the cell and looked busy; it is capped shorter (and the arrow heads a touch smaller). The ping number is one step smaller.

## 1.64.1 - The IP-spoofing options reach the build; the packet-profile menu no longer clips

The owner (2026-10-17): "fix the size of these too" (his picture of the packet-profile drop-down, every option wrapped and cut).

- **The IP spoofing options are applied now.** On BackPack Direct → IP spoofing the form asks for a packet profile, a forged source IPv4 and a network interface, but the API kept an allow-list of option names and those three were not on it, so a profile or forged source chosen in the form was dropped before the build and the tunnel was always built with the defaults. The three keys are on the list now and reach the builder; a test covers it (`tests/test_build_other.py`).
- **The packet-profile menu no longer clips.** Its options were joined with a plain hyphen, but the drop-down splits an option into a short title and a one-line note on an em dash, so the whole long string became the title, wrapped over three lines and was cut at the top (his picture). Each option is now a short title (`udp`, `icmp`, …) with a single-line description, like the other menus.

The owner (2026-10-17): "show the stability and the quality of the users' internet inside their own box, not in the cards"; "bring these toward the Bandwidth column, there is too much space between them" (his picture of the table);
"the CDN comes down, under the Tunnel Doctor"; "remove Install here too, and when a core has an update show the Update button instead of Up to date"; "make the buttons smaller here too, and keep a space above and below them" (his picture of the PasarGuard nodes); "show the users' traffic in the different tunnels" (his picture: two Paqet tunnels with a dash);
"why did you not add the spoof of the BackPack project?"; "this is how a server is tested for spoofing - add it to Servers" (his picture of the method); "SNI spoofing is not there at all - fix its name or its description".

- **A spoof test in the Servers section** (his method, his picture of it: "on the destination server run tcpdump icmp; on the Iran server put an SNAT rule and ping it; if the packets arrived with the forged address the server takes spoofs"):
  **Servers → a server → ⋯ → Tests → Spoof test…** asks for the target server and the address to forge, then the server pings the target 5 times as it is and 10 times with the source rewritten while the target listens with tcpdump. The rule is
  the narrowest there can be - ICMP echo requests to the target only, tagged, put in after the ordinary pings and taken out again whatever happens (a trap), so the tunnels to that server are never touched. The ordinary pings tell "the provider drops forged
  sources" (does not spoof) from "ICMP is filtered altogether" (no answer), which tcpdump alone cannot. The answer is in the Last results of the server ("Spoofing"). New: `POST /api/servers/{id}/spoof-test`, the scripts `spoof_listen` (read-only) and `spoof_send` (an action: refused in read-only mode).
- **BackPack Direct: IP spoofing is a carrier now, and SNI spoofing says what it is.** The panel had BackPack's "SNI spoofing" carrier under a vague line ("Iran only allows a domain") and not the project's other spoof - **IP spoofing** (raw packets with a source address you choose;
  profiles udp, icmp, tcp, icmpv6, proto58, ipip, gre). Now both are in Create tunnel: *SNI spoofing* = raw TCP that opens with a **fake TLS hello** naming a domain your route allows (the field is "Fake SNI"); *IP spoofing* = a forged **source address**. IP spoofing is built the way the project
  advises - first without forging (the real addresses; the listener gets the Iran server's real address as `spoof_peer_ip`, the same profile on both ends), then a forged source (`spoof_src_ip`, and `spoof_peer_src_ip` on the listener) only when one is typed; the Spoof test above says whether a server can forge at all.
  Tried for real on the test servers (BackPack 1.8.5, unforged): the carrier builds and connects with the `udp` profile (the test door 537 Mb/s) and the `icmp` profile (489 Mb/s), a download limited to 42 Mb/s by curl through it moved at 42.0. The Spoof test, run for real
  from the Iran test server to the build server (10.10.10.10 and 8.8.4.4) and back: the 5 ordinary pings arrived and none of the 10 forged ones - "this server does not spoof" - in 22 s, and no rule was left on either server.
- **Every kind of tunnel has a live number of what the users use - Paqet too.** Paqet (KCP over raw sockets), BackPack over UDP and any tunnel that has no card and no TCP connection between its servers showed a dash. The panel now counts the **users' own connections** to the
  forwarded ports of the Iran server: the users connect over TCP whatever carries their data on, the kernel keeps a byte counter for each of those sockets, and what the Iran server sends them is their download (`via: "users"` in `GET /api/tunnels/live`; no framing in the number).
  Looked at read-only on the real Iran server of his two Paqet tunnels: 109 connections on port 80, 0.68 Mb/s down. Tried for real on the test panel (a download limited to 42 Mb/s by curl through a Paqet tunnel from the Iran test server to the build server):
  **curl 42.0, the panel's live number 41.9 (ratio 1.00)**; the tunnel was removed again. A tunnel with a GRE card is still read from its card, one with a control connection (BackPack, Chisel over TCP) from it; a tunnel with no forwarded port still says "-".
  **BackPack Direct is read from the users' connections too**: its L3 link `bp<N>` counted the headers of its inner packets and the framing of its carrier - 50.3 Mb/s for a download of 42.0 with the IP spoofing carrier (+20 %; +4 % with the icmp profile) - the users' own connections are what they moved.
- **The tunnel window has six boxes.** Row one: Servers, Status, Link; row two: **Users' internet**, **Stability**, Last results. *Users' internet* = the grade the Doctor keeps for the configs that ride the tunnel (Good / Fair / Poor / Bad), how many are fine, what is wrong
  (slow, unstable, down), the two worst configs and, for a poor or bad grade, the Doctor's reading (two lines; all of it in the tooltip); without a config of the probe on the tunnel it says so. *Stability* = the new `GET /api/tunnels/{id}/stability`: the share of the checks that
  answered over 24 h and 7 days, the cuts (two failed checks in a row, the analyst's rule), the last cut and its length, how long the tunnel has been up, the analyst's verdict and the strip of the last day. The "Users" row left the Link box; the rows of the list carry neither.
- **The table.** Bandwidth, Iran node, ping / loss and Foreign node have the widths of their content (150 / 170 / 140 / 170 px), so the gap between the Bandwidth column and the Iran node went from about 370 px to about 120 px on a wide window; what is left goes to the name, the type and the ports.
  The Bandwidth column is an important column for the fitter (it was a "least important" one since it replaced the Status column) and keeps 118 px.
- **Network tabs:** Tunnel manager, Tunnel Doctor, CDN (the CDN was first for three days).
- **The cores box (About & update):** no Install button any more (a server added from now on gets the cores at once; "Tunnel cores..." in the menu of a server puts them on one that has not got them); one cell for the state - "✓ Up to date", or, when a newer release is out,
  the Update button in its place with the number of servers beside it.
- **Small buttons with air.** The buttons of the PasarGuard nodes and panels and of the cores were 32 px tall, as tall as their rows: the Settings rule (`html body #page .sf button`, with `!important`) beat every size set for them since 1.57.51, which is why they were "fixed" three times and never changed.
  They are 22 px (Update and Xray 52 px wide, Update... 60 px) in rows of 36 / 38 px: 7 px of air above and below.

## 1.63.1 - Live use for the reverse-proxy tunnels too; the column is in Mb only; "⋯" at the end of a row; the copy mark is whole

The owner (2026-10-17), looking at 1.63.0 on his real tunnels: "why is no usage shown here (BackPack wssmux / tcpmux)?"; "make the ping numbers smaller and both the packet loss and the ping a bit closer to the arrow line";
"do not write Gig at all, put everything in Mb and do not write Mb/s beside the numbers - write it at the top of the column"; "write only Mb/s in the header"; "the copy icon falls incomplete"; "turn these arrows into three stacked dots
that bring the options, like the server section".

- **A reverse proxy has a live number now.** BackPack and Chisel over TCP, WS, WSS or mux have no network card of their own, so the cell said "-". The panel now adds up the byte counters the kernel keeps for every **TCP connection**
  (`ss -ti`) of the tunnel: the ones that came in on the control port of the Iran server (the foreign server dialled in) or, for a forward Chisel tunnel, the ones the Iran server opened to the foreign server - one light command per Iran
  server, the same 5 s / 4 s rhythm as the card counters, a rate over ~15 s. Each connection is followed by itself (a new one counts what it has, a gone one says nothing more, a count that went back starts again), so a long download
  neither jumps nor is counted twice. The numbers include the framing of the tunnel (a few percent) and say `via: "connections"` in `GET /api/tunnels/live`. What has neither a card nor a connection (UDP, QUIC, KCP, raw sockets: Paqet, BackPack UDP)
  still says "-", never a made-up zero.
  Tried for real on the test panel (a download of 42 Mb/s, limited by curl, from the Iran test server through each kind to the build server, the panel's live number against curl's own): **BackPack tcpmux 41.9 (curl 41.9), wssmux 41.8 (42.0), tcp 42.2 (41.9),
  Chisel ws 42.2 (41.9), Chisel fws forward 42.0 (41.9)** - within 1 %; every tunnel and what it made was removed again.
- **Mb only.** No "Gb/s" anywhere in the column (1234, not "1.2 Gb/s") and no unit beside a number: the header says **"Bandwidth Mb/s"** and nothing else (the "capacity | in use" legend is gone from it; the tooltip keeps the explanation).
- **The ping cell is calmer.** The ping number is smaller (11 px) and the ping and the loss sit closer to the line of the arrows.
- **"⋯" at the end of a row.** The soft "›" is replaced by the three-dot button of the server list; it opens the menu of the tunnel window (Repair, History, Mute alerts, Rename, Ports, Rebuild, Remove), and a click on the row still opens the window.
- **The copy mark is whole.** The mark that shows beside an address when the pointer is on it hung outside the address, in 8 px of cell padding, and the cell cut it. It now lives inside the address box, on the free side (left of an Iran address, right of
  a foreign one); in a tight cell the address gives way with "...", never the mark.

## 1.63.0 - The Bandwidth column shows the capacity AND what the users use of it, live; the ping cell after the owner's picture; the saved password can be read

The owner (2026-10-17): "show the overall capacity and, beside it, the bandwidth the users are using; delete the traffic column; make the bandwidth column more useful and understandable - and the numbers it shows must be real,
reliable and live"; "fix this arrow and the way the ping and the packet loss are placed" (his picture), then "the arrows paler, I do not want that place to look busy"; "fix how the numbers are shown in the white box" (the sign-in page).

- **Two numbers and a bar in one cell.** The **capacity** (blue: what the tunnel can carry - the download speed of the Doctor's last light test), a thin line, and **what the users use now** (↓ down, ↑ up, each in its own unit), with a bar under both that is
  the part of the capacity they use (blue, amber from 70 %, red from 90 %). The unit is written once ("312 | ↓ 87 ↑ 12 Mb/s"). A capacity that dropped keeps its "▼ usual", a test that moved nothing says "no data", a tunnel that has not
  been measured says "measuring...", and a cut tunnel keeps its tag. The **Traffic column is gone** (its number is in this cell; the tunnel window says "In use now" and "Capacity"). The header says "capacity | in use" when the column is wide enough for it.
- **The capacity cannot be live, so it says how old it is; the use IS live.** A test moves data over the tunnel the users are on, so it runs every 90 minutes (every half hour with a test door) and its age is in the tooltip. What the users
  use comes from the **kernel's own byte counters of the tunnel's link on its Iran server** (the GRE link, the `bp<N>` link of a BackPack Direct tunnel) - `services/livebw.py`: ONE light command per Iran server for all its cards
  (`iface_counters`; `run_light` never starts a jump search), every **5 s while the Tunnels page is open** (the page asks `GET /api/tunnels/live` every 4 s and the cells change in place; every 30 s otherwise, so the first number is ready
  when the page opens), counted over the last ~15 s on the server's own uptime clock (the time an ssh answer takes does not move the rate). A tunnel with no card of its own (a reverse proxy without a link) has no counter: its cell says
  "-", never a made-up zero; a server that does not answer is asked less and less; a number older than two minutes is not shown, and the number of the last check fills in until the live one is there (dimmed).
- **The panel's own tests are not the users.** While the Doctor measures the capacity, runs the quality test or a speed test on a link, the live numbers of that tunnel wait (`livebw.quiet`) and start again a few seconds after.
  The check's own rate (`rx_mbps`, `tx_mbps`) is no longer averaged over a gap: two checks more than five minutes apart (the panel was off) give no rate.
- **Ping / Loss after the picture.** The ping above, an arrow at each end of a dotted line, the loss below; pale blue while the link runs, pale red with a cross while it is cut (no numbers then), the loss amber when it is high, "no ping" where the provider
  drops ICMP. The arrows are pale on purpose and nothing glows.
- **The saved password.** The browser paints a field it filled in light blue, and the light dots of the password could hardly be seen on it. The box of the sign-in page now keeps the colour of the field and the dots their own colour (dark and light theme).

Tried for real on the test panel (a Backhaul tunnel from the Iran test server to the build server, a download from the Iran side limited to 5 MB/s by curl): curl itself said **42.0 Mb/s**, the panel's live number was **42.2 - 42.7 Mb/s**
(within 1 %); the numbers followed the 15 s window up (14 -> 33 -> 42 Mb/s) and were never more than 5 s old; while the Doctor measured the capacity (1369 Mb/s) in the middle of the download the live number **did not jump** (it stayed at 42.7 and its
age counted up to 18 s, then went on at 42.3 and 42.1); an unlimited download (curl 171.9 Mb/s over its 19 s) read 42 -> 102 -> 162 Mb/s on its way up. The counters of the card grew by the bytes curl received plus 0.8 % of headers
(126.8 MB for 125.8 MB). The tunnel and everything it made were removed again; the live GRE and Backhaul of the Iran server were not touched.

## 1.62.1 - The update takes seconds, and the card tells the truth while it runs

The owner (2026-10-17), with the About & update card in front of him ("Up to date", "Updating... 2%", the button grey): "the update is very slow; when the page is refreshed the percentage starts again from the beginning;
and while the update is not finished it already shows that a new update exists".

- **Slow: the whole test suite ran on the panel's own server.** `scripts/deploy.sh` skipped the tests only for a commit that carries a release tag, and the panel's `git pull origin main` does not download tags (his copy had stopped
  at v1.57.56), so every update ran about 1,300 tests on the two cores that also watch the tunnels: 7 to 15 minutes. The suite now runs where a version is built, before it is published. The server keeps what only that machine can
  answer: the secrets check (0.3 s), the compile (0.1 s), **the whole panel is imported with the libraries installed there and its migrations run on a copy of the live database** (4 s), the restart and the health check. The libraries
  are installed only when `pyproject.toml` changed. About fifteen seconds in all (measured step by step on the real box); `FORCE_TESTS=1 bash scripts/deploy.sh` still runs the suite there.
- **A stop leaves the files and the branch of the version that runs.** A check that failed before the restart used to leave the new files on the disk (a reboot would have started them), and a rollback after a failed health check
  left the branch on the new commit with the old files in the tree - so the next `git pull --ff-only` refused ("local changes would be overwritten") and the Update button was stuck. Both now go back with `git reset --hard` to the
  last good commit (uncommitted work is put aside first, `git stash pop` brings it back), the manual `--rollback` no longer detaches the head, and the libraries are reinstalled only if `pyproject.toml` differs.
  The compiled files of the version that was put away are deleted too: found in the real test, a file of the same size rewritten in the same second looks unchanged to python, and the panel printed the version that had failed.
- **The percentage is the server's.** `deploy.sh` writes one small file (`update-progress.json`: the step, the time, the version it goes to) at every step; the percentage creeps by the **clock of the server** between two steps, so a
  refreshed page, a second tab or the restart of the panel in the middle of the run shows the same number, and the bar goes on from there instead of from 1 %. The page asks only this file (`GET /api/system/update/progress`),
  every second, not GitHub; a run whose last step is older than seven minutes counts as stopped, not as running for ever.
- **While a run goes the card says neither "Up to date" nor "a new version is available".** The pill says "Updating to 1.62.1", the button is off, the bar shows. After the files are pulled the commit on the disk equals the newest
  commit on GitHub, which is what made it say "Up to date" while the old version still ran; "up to date" is now decided by the **version that runs** against the newest version tag, only when GitHub answered and no run goes, and
  after a failed update the card offers the version again. When GitHub cannot be reached it says "Could not reach GitHub" instead of claiming anything. A failed run stays on the card for a day: why it failed, and the log in its tooltip.
- **Smaller.** The card asks GitHub once (`ls-remote` for the commit and the tags together, kept 30 s, 10 s limit) instead of twice at 1.4 s each, a second click while a run goes is refused, and a download that fails answers at once with
  git's own words instead of failing later in a unit nobody watches. The older log markers are kept in the script's output, so an update started by an older panel still draws its bar.

Tried for real on the test panel with a throw-away origin (the Update button, a real restart, the real health check): an update from 1.62.0 (the old page, the new script) took **13 s**, from 1.62.1 to 1.62.2 with the new page
**10 s** (the bar: 14 % -> 39 % -> waits for the restart -> 92 % -> 100 %), a version with a syntax error stopped in **under a second** with the old files back and the panel not touched, and a version that does not start was
rolled back after the 90 s the health check waits (**101 s** in all) with the old version running again. The three states of the card were checked in the browser against a scripted server (running at 45 % and refreshed at 52 %,
failed with its reason, up to date, GitHub not reached, a new version).

The new `deploy.sh` runs at the first update that pulls this version (the script is the one that was just downloaded); the new card and progress file work after that update.

## 1.62.0 - A cut tunnel gets a tunnel that connects, the Doctor knows its pairs before a route is cut, and remembers every cut

The owner (2026-10-16), with two real tunnels in front of him: a tunnel whose foreign server is filtered stayed cut for hours while the Doctor said it had fixed things, another one flapped, and the table showed
"no ping" for a tunnel that carried 552 Mb/s and "connected" for one that moved nothing. "When the foreign server is filtered the Doctor must go for a method that connects, not try Backhaul again; it must know before
the cut what answers and is more stable; the problem must be found and solved in seconds; it must keep in its memory why a tunnel was cut; it must fix the tunnel itself when its engine drops traffic; no case may stay unsolved."

What was found in the real logs, and what is different now:

- **Three tunnel types connected and none was built.** The Doctor tested four types between the two servers (BackPack TCP Mux 159 ms, BackPack WSS Mux 150 ms, Chisel TLS 165 ms all connected), scored them by delay alone (36 - 42,
  a route of 150 ms cannot reach more without a speed number), compared them with a line of 45 and said "no other tunnel type works well". A tunnel that is **cut** now takes the first type that connects, whatever its score.
- **No Backhaul retry where GRE is filtered.** A route where ping passes and GRE does not no longer tests a new GRE link (a filter on the protocol does not care about the key): the ladder goes straight to another type. A restart is
  tried **once** (every unit of both sides in one go, one look after it - about 50 s; it was four restarts with a look after each, three minutes), and not at all for a silent route that was cut like this in the last 14 days.
- **Built as the real tunnel, one type after the other.** There is nothing to test beside a cut tunnel and nothing to lose, so each candidate is built as the tunnel itself (same name, same ports) and the build checks that data passes;
  the first that carries data stays. What each type answered is kept: one that failed here is not tried again for two weeks, one that connected goes first.
- **Who can still dial whom.** The diagnosis asks both servers whether they can still open a TCP connection to the other one. When only the foreign server can (its address is filtered from Iran) the reverse kinds - BackPack
  WebSocket TLS Mux, Chisel TLS, BackPack TCP ... - go first; when the Iran server can, the fast forward kinds go first. A route cut (the two servers cannot reach each other) now climbs to *switch* too; it was only *move*.
- **"Fixed" now means connected.** A repair counted as healed after one check; the tunnel was cut again a minute later and the same restart was tried again and again. Now two checks in a row, a 60 s hold after a restart / rebuild,
  and a step that healed the tunnel less than 30 minutes ago is not repeated when it is cut again. The repair also stopped dead with "no data passes through the tunnel right now" when the live measurement of a flapping tunnel
  refused; the busy-test-slot wait caught the wrong error (`ValueError` where the tests raise `RuntimeError`) and a repair collided with a running test.
- **By rules, not by luck.** The smart repair starts after one failed restart (none for a route cut like this before), a tunnel that is down is tried again every 10 minutes (it was 30), no tunnel waits more than 5 minutes past its
  grace for a restart that never came, and the climb is looked at every 20 s (it was every 5 minutes).
- **The Doctor knows its pairs before a route is cut (Knowledge, a new tab of the Doctor page).** Every pair test, the quality test of every live tunnel with a test door and a **night survey** (02:00 - 06:00, one pair at a time, only
  on quiet servers - under 60 % CPU, under 150 Mb/s of users - and never while a repair needs the place; ten tunnel types per pair, delay and loss only; *Test now* tests all 24 with a speed test) feed one memory per pair; the night survey is on by default and can be switched off.
- **It remembers every cut.** The cause it found, the steps in order with the type each built, what healed it and how long it took (`knowledge.incidents`, shown in the Knowledge tab). The next cut of the pair starts with the type
  that healed it; the other pairs of the same foreign server lend what they know.
- **It fixes the tunnel itself.** When only one tunnel is slower (the servers idle, the line clean, its delay as usual) the Doctor restarts its engine on both servers, measures again and says whether the speed is back (at most
  once in six hours).
- **A tunnel that answers every check and moves nothing is not "connected".** Two tests three minutes apart that carry no data are a stall: the tunnel turns *degraded* ("no data moves through the tunnel under load"), the Doctor repairs
  it, and a repair is judged by a new measurement. The bandwidth cell shows a red 0 with an empty bar, never a green one.
- **The table.** What is cut is a **tiny tag** that says which end: *GRE foreign*, *GRE Iran*, *Iran service*, *port blocked*, *no data*, *flapping* ... (instead of a red dot and "GRE link cut"). A tunnel that was not measured yet says
  *measuring...* (the first round after an update measures four tunnels a turn instead of one; unstable tunnels and tunnels with a test door are in the watch too). A provider that drops ping no longer means "no ping": the loss is counted
  with **UDP packets** through the bandwidth sink (an older sink is replaced at the next measurement).

Tested for real on two test servers (docs/tunnels.md): GRE filtered - the users were connected again after 3 min 12 s once the Doctor remembered the pair (6 min 27 s the first time, before the last two changes: one plain restart per cut,
and a diagnosis that asks everything at once - 9 s instead of 51 s); the foreign server filtered from Iran - 3 min 36 s, no restart, the first reverse type connected on the first try.

Seconds need a tunnel that is already built and waiting; a type built from nothing takes about a minute to connect, and a cut is seen within a check cycle (about 40 - 50 s). That standby, and a faster pulse, are the next step.
## 1.61.0 - The Doctor reads what the USERS get, not only what the tunnel does

The owner (2026-10-16): "I want the Doctor to truly say whether the user gets a quality internet". A tunnel can be perfect - up, no loss, its whole bandwidth - and its users can still suffer: the foreign server's own internet, its Xray, the CDN in front of the Iran server. The probe tests every config from inside Iran every few minutes, and that is the only place where the user's view exists, so the Doctor now reads it (`services/userq.py`, a job every five minutes):

- **Which configs ride which tunnel.** In this order: the label the owner put on the config (Tunnel), the address of the config when it is the address of the tunnel's Iran server (and its port is forwarded by that tunnel), or - for a CDN name - the A record of that name in the DNS accounts (the Iran server it points at, and the port the record sends to). A config that cannot be placed for sure is left out: nothing is guessed.
- **Each config is judged against itself.** Its tests of the last two hours against its own week: *down* (off for 10 minutes), *unstable* (answered under 85 % of its tests), *slow ping* (twice its usual and 200 ms more), *slow speed* (its last speed test under half of its usual - the probe's speed tests now keep a history of thirty per config). A config that was never quick is not blamed for staying so.
- **The grade of the tunnel** - good / fair / poor / bad / unknown - changes only after two looks in a row; *bad* only when every config through it is down or unstable. One event when it changes, one when it is good again.
- **Where the problem is.** When a tunnel falls to poor or bad the Doctor starts a look by itself (the Live window, the story in *Completed*) and says it in one sentence: the tunnel is down, slow, loses packets, its delay rose, a server of it is busy - or, when all that is fine, that the problem is **beyond the tunnel** (the foreign server's own internet or Xray, or the CDN in front of the Iran server).
- **Shown** in the tunnel window (*Users*), in the tip of the bandwidth cell and on the Doctor page (a row *users*). `GET /api/tunnels` carries `ux`.
- **The links the Doctor lacks.** The Doctor page counts the configs that ride no known tunnel ("N of the M configs ride no known tunnel ...") and its button *Link them...* opens a window to pick the tunnel of each (the Configs page is switched off, so it is done here). `GET /api/doctor` carries `users`.
## 1.60.2 - Found by throttling a real tunnel: the Doctor no longer calls a test of its own "users", and two scales never mix

The real test of the bandwidth watch (2026-10-16): a Backhaul tunnel between an Iran server and a foreign server, its link shaped to 40 Mbit on the foreign server. The watch saw 37 Mb/s against a usual 474, confirmed the drop with the second sample, wrote the story, the events and the *slow* row - and showed three faults that are fixed here:

- **"The tunnel is full of users" was wrong.** The traffic of the link was read as the average of the last minute, and that minute held five tests of the panel itself (a few seconds of 500 Mb/s each). The traffic users carry is now the **middle of the last ten minutes' readings** (at least five readings, none older than 15 minutes) and the tunnel counts as full only when that reaches 80 % of the bandwidth it can give.
- **Two scales were mixed.** The usual level was made of the one-connection samples of 1.60.0 and the two-connection samples of 1.60.1, so a tunnel that was back looked "low" for a while. A sample now carries its **method**, and a sample of another method - or of another kind of tunnel under the same name - **starts the history again**.
- A click on *measure now* while the tunnel is dropped looked at it again and wrote the same event twice; it now says *still dropped* and what was found when it dropped. A tunnel is never **measured twice at once** (a click while the job measures it). The line of the story that counted "1 of 1 tunnels dropping" says "this is its only tunnel".

## 1.60.1 - The bandwidth is measured with two connections (the real test of 1.60.0 on a GRE link)

Found by the first real run of the bandwidth watch on a Backhaul tunnel (2026-10-16): one connection over the GRE link scattered by 16 % from run to run and once stalled (a 4 s run gave 31 Mb/s between 350 and 420), two connections by 9 %, four by 7 % (mean 240 / 345 / 543 / 842 Mb/s for one 6 s run with 40 MB, one 3 s, two 3 s, four 3 s). The measurement now takes **two connections, 3 s, 60 MB each at most (120 MB per sample)**: steadier, and still a few seconds of traffic about every 90 minutes per tunnel. The first samples of that run were also low and uneven because the machine was busy with a test run - the reason a busy server (CPU 85 %) is skipped.

## 1.60.0 - The loss of every tunnel is measured and read honestly, the Doctor's old suggestions go away by themselves, and the bandwidth of every tunnel is watched

The owner's request (2026-10-16): the Tunnel Doctor must always be up to date and decide from what the user really gets - uptime and speed first - and, in the table, "the lamp goes before the name of every tunnel, and instead of the status show the bandwidth, very simply; when it drops the Doctor looks at it by itself and we understand". Four parts:

- **The Tunnel table: the lamp before the name, the bandwidth in place of the status.** The status lamp (green / amber / red / grey, its word in the tooltip) sits at the start of the name, placed the way the lamp of a server is in the Lab list. The Status column is now **Bandwidth**: the download the tunnel can carry, as a number with a bar against its usual level; while it is dropped the number turns red with *▼ usual*; a tunnel that is cut shows the reason in two or three words, as the status did; while the Doctor works on it, *checking / repairing*.
- **The bandwidth is measured and watched by the Doctor** (`services/bandwidth.py`, job every minute, one tunnel per turn). A **GRE tunnel**: two connections pull at most 60 MB each (3 s) over the link from a small sink the panel keeps on the foreign server, bound to the link's inner address (nothing public; removed with the tunnel), about every 90 minutes per tunnel, never while a server of the tunnel is busy (CPU 85 %) or the panel is read-only. A tunnel **with a test door** (BackPack, Paqet, Chisel): the quality test it already has every half hour is taken as the sample. The usual level of a tunnel is the 75th percentile of the last week (after four samples); a sample under **60 %** of it is a drop once a second sample, three minutes later, says the same; the drop ends over 80 %. `GET|POST /api/tunnels/{id}/bandwidth`, *measure now* in the tunnel window.
- **The Doctor looks at a drop by itself and says where the problem is - in one sentence.** It reads what the panel knows: the load of both servers, how many tunnels of the same Iran / foreign server dropped too, the loss and the delay of the line against its usual delay, the traffic users carry now. *Busy Iran / foreign server* · *all the tunnels of that server dropped* (its line or provider) · *the tunnel is full of users* · *the line loses packets* · *the delay rose* · *only this tunnel* (with what to try). The story is in the Doctor's Live window and History, the sentence in an event and on the Doctor's page (**slow** rows). Nothing is repaired because of it yet: it tells.
- **The Tunnel Doctor lists a tunnel as lossy only when data is lost; BackPack, Paqet, Chisel and BackPack Direct show their loss** - see below.

Three things the owner showed on his own panel (2026-10-16), with their cause found in the data of that panel:

- **"The BackPack tunnel shows no packet loss."** Only the GRE tunnels were measured (a ping over the link). Every other tunnel is now measured too: **BackPack Direct over its own link** (the interface `bp<N>` and the other end's inner address - its traffic shows as well), **BackPack, Paqet and Chisel by a ping from the Iran server to the foreign server**; the number is the loss of that path (the tooltip says what the pings crossed), the latency is the ping, and the state of the tunnel is still judged only by its services and its data path.
- **"The Doctor says 100 % of the data is lost on this tunnel, and the Tunnel Manager says it is fine."** The tunnel carried its users; the provider drops the pings inside GRE. Every ping lost **while data passes through the tunnel's port** is now *ping blocked* (`detail.ping_blocked`): the row shows **no ping** instead of a red 100 %, the Doctor's page no longer lists it as lossy, and the Doctor no longer opens a look at it every half hour. Without that proof of data, or with some pings answered, the number stays what it was measured.
- **"These three do not lose packets, yet the Doctor suggests moving them."** The suggestions were written two days earlier, while those tunnels lost packets, and were never taken back. A suggestion now **stands only while its problem does**: it is withdrawn when its tunnel has been up without loss for 10 minutes (route suggestions), when the server is no longer exhausted (resource suggestions), or when the tunnel is gone - by the Doctor's job every minute and whenever the page reads the Doctor; one event says it.

## 1.59.3 - The table of the real tests (every engine, one pair) and the numbers corrected

Documentation and wording only, from the rest of the same tests (2026-10-16, one Iran server and one foreign server 114 ms away, 78 ms through GRE; what a customer downloads, one / four / eight connections, Mb/s):

- **Backhaul · TCP Mux over GRE (the default): 174 / 601 / 1079** - the best aggregate, and the GRE path was shorter (78 ms against 114). **BackPack Direct · PCK: 665 / — / 666** (UDP: 432 / — / 324; QUIC did not come up on this route). **Chisel forward: 110 - 159 / 526 - 730 / 668 - 748** (TLS: 134 / 400 / 700). **Paqet with the new defaults: 165 - 210 / 308 - 351 / 301 - 327**. **Reverse BackPack and Chisel: 9 - 142 / 19 - 130 / 44 - 130.** The table and the reading of it are in `docs/tunnels.md` (*Which side opens the connection - real tests*); the guide of *Create tunnel* and the text under a speed test say 20 - 130 Mb/s for all connections together instead of "about 10 for each", which was too exact for a slowed direction that varies from run to run.
- **Paqet is bound by one CPU core on each server** (about 320 Mb/s here: the process uses 80 % of a core on both sides while eight connections run; the cipher costs nothing, more sessions change nothing). To go beyond that a faster core is needed, not a setting.
- The BackPack tests (three presets, a pool of 16 to 64, mux 8 / 32, TCP / TCP Mux / WebSocket TLS) found **no setting that lifts a reverse BackPack above about 130 Mb/s in all**; the defaults stay.

## 1.59.2 - Paqet tuned by real tests, a Chisel that the Iran server dials, a score that counts what a customer downloads

The owner (2026-10-16): "run all the tests so that we reach the best settings for the user on the tunnel - especially Paqet". Dozens of real tunnels were built between an Iran server and a foreign server 114 ms away, each measured through its test door with random data (**one connection, then four together**, Mb/s down) and removed again:

- **Paqet: a window and buffers that fill a long route.** With Paqet's own settings (window 1024 packets, 4 MB / 2 MB buffers) one connection got **52** and four together **184**. A window of 2048 doubled the first (92), the stream buffer did the rest (2 MB over 114 ms carries at most 140): **window 4096 with 16 MB / 8 MB gives 200 on one connection and 330 on four**, and the jitter fell from 37 ms to 5. Window 8192 with 32 MB / 16 MB gave nothing more (185 / 309). These are the defaults now (*Advanced settings*: **Window** and **Buffer**). With them the mode *fast* stays the best (normal 153 / 193, fast3 127 / 155), one connection of Paqet tops out near 170, two sessions carry 308, more than four change nothing, the cipher costs no speed (without one: 99 / 289 against 90 / 271 with Paqet's old window) and an MTU of 1200 or 1400 is the same as 1350 - **an MTU of 1450 carries nothing at all** (the packet no longer fits one frame), so the panel stops at 1400.
- **Which side opens the connection decides what a customer downloads.** On this route the data that flows away from the side that OPENED a TCP connection is slowed, whichever of the two servers that is. A *reverse* tunnel (the foreign server dials the Iran server: BackPack TCP / WebSocket / Stealth, Chisel) gives one customer connection 10 - 140 Mb/s down and all of them together **20 - 130 whatever the settings** (pool, mux and preset were all tried) - while the "upload" of the same tests, the fast direction, showed 400 - 1000 and made BackPack look the best; a tunnel that the **Iran server dials** (Paqet, BackPack Direct, a forward Chisel) gets the download the fast way.
- **Chisel forward (new): the Iran server dials.** *Chisel · Forward HTTP / TLS WebSocket* (`fws`, `fwss`): the Chisel server and its key are on the **foreign** server, the Iran server runs the client and opens the user ports itself (no `R:`), the control port is chosen and opened on the foreign server, the log that is read when nothing passes is the client's. Built by the panel and measured the same way, four customer connections together got **526 - 730 Mb/s** (eight: 668 - 748, one: 110 - 159) through the forward tunnel and **19 - 109** through the reverse one. It is in *Create tunnel*, in the pair test (`chisel:fwd`, `chisel:fwdtls`), in the Doctor (`kind_of`, the control-port check dials from the right side) and in the table (the type reads *forward*).
- **The score counts what a customer downloads.** Speed is the download of eight connections and of one together, the upload counts only up to the download. (A reverse tunnel's "upload" runs the fast way and had made BackPack look the best.)
- **The speed test says it.** A reverse TCP tunnel whose one connection gets less than 30 Mb/s is told, under the numbers, that a tunnel the Iran server dials may give much more, and where to try it (*Test this pair*).

## 1.59.1 - Real tests with the door fixed: new random bytes, one connection too, Paqet that says why it fails and takes windows and buffers

Found by the real tests between an Iran server and two foreign servers (2026-10-16):

- **The numbers of the test door were not honest.** First it sent a pile of the same byte (squeezed to nothing by BackPack); then a pool of 32 random blocks over and over (found again by a compressor with a window of a few MB: the upload through BackPack still showed 1 - 2.7 Gb/s over a path that carries 100 Mb/s). Now **every block is new random data**. The pair test also measures **one connection** beside the eight (`down1_mbps`, `up1_mbps`) - a phone downloads over one - and says it in its story.
- **Paqet says why it carries nothing.** After a failed build the panel reads what the firewall of the foreign server counted on the raw port: *not one packet arrived* (the route or the provider drops the raw packets: Paqet works only where they pass - it passed from one Iran server to a foreign server of one provider and not to a server of another), *arrived but nothing answers* (the key), or *both ways but no data* (the answers are dropped on their way back).
- **Paqet takes the KCP windows and the SMUX / stream buffers** (`rcvwnd`, `sndwnd`, `smuxbuf`, `streambuf`, the cipher `kcp_block`) as options of a build; nothing changes unless they are asked.
## 1.59.0 - Line icons on the Add table, a host window with no overlapping switches, every API in its own box, nine AI models, prettier themes, one box of Backups, Usage cards that never move, and the speed a customer gets

The owner (2026-10-16), nine requests:

- **"Emojis in the MacBook style, black and white."** The drawers of the Add table (VLESS, Trojan, VMess, Shadowsocks) carry thin line icons of the kit (a bolt, a horse, a speech bubble, a ghost) in one colour; a drawer you make has a picker of 15 icons, and an emoji you type is shown in black and white.
- **"Make this window better and more professional - and fix the size of my switches so they do not overlap" (the Modify window of a host).** Two grouped cards (*Where* and *Security*), a name with a line of help on the left and the field on the right, the ticks (*For users*, *ALPN*) as pills that light up, **every switch 40 × 24**, the buttons stay in view when the window scrolls. The cause of the overlap: an old rule squeezed every `label.chk` in a row of a window into an 18 px round badge, so the 40 px switches inside drew over each other - it now holds only the little tick / cross badges, so **the same overlap is gone from the Users tools windows** too.
- **"Put each part in a box so it is more orderly" (Settings → Api & Key).** Every connection is one card - a head (the tile, the name with its state, the buttons on the right) and its fields under it, each with its name above: this panel's API, the tokens, the AI models, Telegram, the probe, Install a probe, DNS accounts, the panels.
- **"Add more AI models: DeepSeek, ChatGPT, Grok, Gemini and more."** The assistant works with **Anthropic (Claude), OpenAI (ChatGPT), Google Gemini, xAI Grok, DeepSeek, Mistral, Groq, OpenRouter, Together, Qwen, Kimi, Perplexity, GLM** and any address that speaks the OpenAI chat protocol: one row each (a key, a model - a free field with a list of the models of that AI and the live list of your account - Test, Save, Remove), the first one with a key is *in use* and **Use** changes it; the first-run card asks which AI the key is for. Everything that asked the AI (Fix with AI, Explain, the Doctor's advice) goes to the one in use. Tried against the real endpoint of every one of them (with a made-up key): each answered its own "wrong key".
- **"Make the theme previews prettier."** Each theme is a small picture of the panel in its own colours (sidebar, top bar, two cards, a button), its name and a line under it; the chosen one has a ring and a tick.
- **"Backups: a box, one piece, MacBook style."** One grouped list: the panel (switch, *Back up now*, *Restore*, the last backup and its topic), the backup password, and the schedule's rows (*Every*, *At hour*, *Keep for*, *Send to Telegram*) with a hairline between them.
- **"These boxes load late - the boxes fixed."** (Panel → Usage.) The six cards are in their places from the first frame as grey shapes (no spinner, no wait for the date ranges), the data fills the same boxes (measured: nothing moves), the last picture is kept for the tab so a reload shows it at once, and the server hands out a counter that is a little old **at once** while it asks the panel for the new one in the background (`STALE`, 5 minutes).
- **"A speed test for my tunnels - what speed does a user in Iran get - the closest possible to what the customer holds."** The menu of a tunnel has **Speed test…**: *What a customer gets* = the probe downloads 25 MB and uploads 10 MB through a config that rides the tunnel (one connection, like a phone: the whole path with the real protocol), *The tunnel alone* = one and four connections through the test door. The answer (down, up, ping, a bar per config against what the tunnel can carry, and what it feels like) is kept with the tunnel. **Found on the way:** the test door sent a pile of the same byte, which BackPack squeezes to nothing - it showed 1 - 2.8 Gb/s between two ordinary servers; the door now sends random data.

## 1.58.1 - Putting the tunnel cores on a server never replaces one that is there

Found by the first real run on a server that already had a BackPack tunnel of the old panel (2026-10-16): the cores that the panel puts on a new server (and *Install → Tunnel cores…*, and the **Install** button of Settings → About) are put **only where they are missing**; a core that is there is left exactly as it is (another build may be what its tunnels run). Changing a build stays the job of **Update** (and of building a tunnel, which keeps the node on the panel's build as before). *Install* in About no longer also updates the outdated servers: it is for the servers that have not got the core, nothing is restarted, and it copies the panel's own cached build instead of downloading the release again.

## 1.58.0 - Adding a server moves nothing and says what is being done, the tunnel cores install by themselves, the Add table gets short names, an emoji per protocol, protocols of your own and a menu on every config

The owner (2026-10-16), five requests:

- **"When you add a server the page jumps several times."** Measured and fixed: the empty page is now a box of the same kind and size as the list (the first row used to switch the page from a scrolling page to a locked one: it grew 522 px and shifted 9 px at once), the table is in its box from the first frame, the numbers of the chips and the counts of the tabs have a room of their own (a `0` that became a `1` moved the search box), and the page is no longer drawn again every few seconds: a server that is busy or was never read is followed with a quiet look every second and a half, and **only the rows that changed are rewritten where they stand** (`labPatch`). Also found: the tag *Iran / Foreign* of the Add form never decided the role (the helper was a top-level `const`, not on `window`), so the panel waited for the geo lookup at the moment of Add.
- **"The status of the server is not clear - from the very first moment show what is being done."** A server that was never read says *Waiting for the first reading…* from the first frame; after the login the panel prepares it in the background (`POST /api/servers/{id}/onboard`, `services/onboard.py`): *Reading the server*, *Checking the GRE links*, *Installing the tunnel cores · Paqet · 3 of 5* - each step is on the row (the blue lamp and the spinner) while it runs, and the first reading no longer waits for the next round of two minutes. The *Add server* button says *Adding…*; a panel key that does not log in is now told (the check used to answer "✓ Server added" for a server that did not answer).
- **"Why are Paqet, Chisel and Xray not installed? They should install by themselves."** They were only copied to a server when a tunnel of that type was built. Now: a new server gets all five cores right after its login (a tick in the Add form, on by default); the panel keeps its own cache of the cores and fills it by itself (a job a few seconds after the start and every six hours, or `nikatel engines` - an Iran server with no internet gets them over SSH); *Settings → About → Tunnel cores* has an **Install** button for the servers that have not got a core, a server's menu has *Install → Tunnel cores…*; Xray can now be put on a server (its directory is made first) and an update keeps the panel's cache at the newest build (before, a tunnel built after an update could put the older build back).
- **Add table (Panel → Configs → Add):** the buttons carry short names (*ws+tls*, *grpc+tls*, *xhttp+reality* …; the long name is the tooltip), every protocol drawer has an emoji of its own (⚡ VLESS, 🐴 Trojan, 💬 VMess, 👻 Shadowsocks), and **＋ Add protocol** lets you add your own: a name, the drawer (one of the four or a new one with its own emoji) and either the builder's protocol / transport / security (flow, HTTP header, xHTTP mode, usual ports) or the JSON of one inbound (hysteria, wireguard … - the tag and the port are put in when an account is made; a secret in it is refused). It works like a built-in kind (Check, Make it, a pasted link finds it) and has *Edit* and *Delete* on its note.
- **Subscription configs:** every row has an **on / off switch** and a **⋯ menu: Modify, Disable / Enable, Duplicate, Remove** (red) - the same windows as the Hosts tab; only the list is read again afterwards, so what was typed in the form stays. Closing a window no longer lets the page draw itself again over the Add form.

## 1.57.56 - The report of script errors keeps quiet about what is not an error

- The page's error report (1.57.55) ignores the browser's harmless "ResizeObserver loop" note and the failed fetch of a panel that is restarting; the cores box of Settings → About no longer leaves an unhandled rejection when the panel is down at the moment it asks again. Its empty text and status no longer tell you to *press Check* (the check runs by itself since 1.57.52).
## 1.57.55 - Review: sign-out that really signs out, nothing leaks from an error, the panels are asked at the same time, and a report to copy when something is wrong

Security (found by reading every layer, 2026-10-16):

- **Sign out ends the session on the server.** Every cookie now carries an id that is kept in a registry; *Sign out* removes it, so a copied cookie is dead too (before, only the browser forgot it and a stolen cookie lived until its own end). A password change, the two-step switch and *Sign out of other devices* still end every other session. A cookie from before this version has no id and lives until its own end.
- **A 500 carries only its request id** - the exception text (a path, a host, a query) is in the log under the same id, not in the answer. A request body above 8 MB is refused with `413` before it is read; the backup upload is read piece by piece and stopped at its limit (a body with no length could fill the memory).
- **A panel's saved key / login is never sent to another address.** Changing only the address of a Marzban / PasarGuard panel to another host, port or scheme now asks for the key (or the login) again, and it is tried at the new address first - before, the next check sent the saved one there. Adding, changing or removing a panel is `admin` for an API token (an `act` token can still read and use every panel).
- `GET /api/health` tells a stranger only `{"ok": true}` (the version and the uptime need a session or a token); the data folder is `0700`; a fresh install pins its libraries to the versions the tests ran on (`requirements.lock`, used as constraints by `install.sh`); an engine release with no published digest is said in the log.

Parallel and structure:

- **The panels are checked at the same time** (`panels.check_all`): a panel that does not answer cost its own 20 s *and made every panel after it wait*. **A server sample no longer holds a database connection** while the server is asked (up to 35 s), and the pool is 20 + 40 (it was SQLAlchemy's 5 + 10: the 16th thread waited 30 s and failed when slow servers held theirs).
- The severe packet loss line of the Tunnel Doctor is one named constant (`LOSS_SEVERE`).

Finding what went wrong:

- **Script errors of the page are told to the panel** (`POST /api/client-error`): one warning line in the log with the request id, at most 5 per page load and 20 a minute - before they vanished in the owner's browser.
- **Settings → About → Copy diagnostics** (`GET /api/system/diagnostics`): versions, the health checks, every job (last run, failures, last error), the counts and the newest problems with their request ids - no key, no password, no server address (the log lines have theirs cut to two numbers) - one click, to paste where help is asked for.
## 1.57.54 - The dashboard redesigned, a getting-started card that asks the API keys first, the light theme made clear

- **Dashboard:** the number chips are slim, the three boxes (*Infrastructure health*, *Problems*, *Activity*) are one size with rounded rows in the look of the kit, the empty *free space* slots are gone.
- **Getting started (new, `GET /api/setup`):** a card on the Dashboard for a panel that is not set up yet - **the API keys first** - *Connect your panel* (a button that opens the Add panel window), *Telegram alerts* (the bot token and the chat id typed right there), *AI assistant* (the Anthropic key typed right there), *DNS account*, then *Add your servers*, *Probe*, *Encrypted backups*, *Two-step sign-in*; what is still to do first, what is done after it, a progress bar; open while fewer than three are done, a thin strip after that, and *Hide* puts it away (a small *Setup* chip at the top brings it back). It only reads; it never carries a key. `POST /api/setup/dismiss` and `/api/setup/reopen`.
- **Light theme, every element and font clear (the owner: "check them all, change what is needed"):** a new sheet **`light.css`**, loaded after every other one, holds only light-mode rules (a test keeps it so; the dark theme is untouched). It was found by an audit that measures every visible text against the colour under it (4.5 : 1, 3 : 1 for large text) on every page and window, with the animations off, **in the hover state too**, and by a second pass that builds a sample element from every colour rule of the sheets. What was wrong and is fixed: the colours of meaning as text (*green, red, amber* and the grey of secondary text are darker); the sidebar footer (the signature was 22 % opaque); the **hover of the toolbar buttons** (dark text on a navy fill: *Check all*, *Restart all*, *All*, *Problems*, the Panel tabs) and of the *Create tunnel* button (white text on a pale fill), of the Doctor tabs and pills (white text), of a Dashboard row and a protocol chip; the **Add** tab (it was dark inside a light frame); the Doctor's number box and its *Active Tasks* tab (blue text on blue); the About boxes (engines rows, the *Up to date* pill, the install id); the kit menu and row menu headings; the status bar time; the primary button (its top edge was too light for white text) and **the primary button in a window** (it was pale with dark text, now the blue one with white text); the nodes sparklines; and about sixty more small colours made for the dark theme.
- **Dashboard, Infrastructure health:** the line under a name is one line with an ellipsis (a long list of stale jobs ran over the next row), the whole text is its tooltip.
## 1.57.53 - The window of a tunnel is built like the window of a server

- **Tunnel window (Network → Tunnel manager → a tunnel):** rebuilt after the window of a server - the same size and the same pieces: in the header the avatar with the state, the name, the two servers (*Iran ⇄ foreign*) and the type; the *Alerts* switch (mute / unmute) and ONE ⋯ menu (Fix…, Dry run, Health check, Restart…, History, Mute alerts, Rename…, Ports…, Rebuild…, Remove tunnel… in red) in place of the row of seven buttons and the three in the header; the tabs **Overview** (the banner - *Healthy* or *Needs attention* with the reason in plain words and Fix / Dry run / Health check -, the three boxes Servers, Status, Link, the strip *Answered the checks* of the last 24 hours, the last results) and **Monitor** (the range, the four tiles, the charts and the times the tunnel was down).
- **The sign-in page, built again from the start in the look of the kit** (the owner's picture): a centred card with the mark and the name, labelled boxes with a small picture, **an eye on every password box** (show / hide; shut again at every step), a Caps Lock hint, the button shows that it is working and cannot be pressed twice, the answer is a box (red = wrong, green = done), the wrong password is selected so typing again replaces it, one step at a time (password -> the authenticator code, or *Forgot the password?* with a recovery code or a Telegram code); a light-theme look; no inline handler on the page. **Sign-in check:** a failed sign-in no longer writes the typed user name into the log and the security events unless it is `admin` (people type the password into the user box by mistake) - only its length.
- **Create tunnel menus:** the cards of the server lists have a gap between them and are rounded cards (they stood flush).
## 1.57.52 - Api & Key one slim line per connection; the About boxes without buttons that ask for a check

- **Api & Key:** every connection is ONE slim box - a tile, the name, its state, and its fields and buttons on the SAME line (they fold under the name only on a narrow page): *This panel's API*, *API tokens* (the line that makes a token is in the head), *Anthropic key* (key, model, Remove, Test, Save), *Telegram bot* (token, chat id, the Send switch, Test, Save; Rules / Daily report / Maintenance windows under it), *Probe*, *Install a probe*, *DNS accounts*, *Marzban / PasarGuard panels*. The tab is about 40 % of its former height.
- **Tunnel cores:** the *Installed* column is gone; an engine that is on no server says **not installed** in the Status column. The *Check now* bar is gone too - the servers are asked by themselves (when the last check is older than ten minutes), with a small spinner in the head of the table while it runs.
- **PasarGuard (About & update):** the *Check* button is gone (GitHub and the panels are asked again by themselves every ten minutes); the panels and the nodes sit in ONE box of a fixed size that scrolls inside (the cores table the same); the *Update* / *Xray* buttons of a node are one size (58 x 24), *Update…* of a panel 66 x 24.
## 1.57.51 - About & update and Api & Key built tight

- **Settings:** the frame of every tab is never narrower than 880 px (it was 880 when the row had eight tabs; with the Alerts, Probe and Shop bot tabs gone it had shrunk to 726 / 789).
- **About & update**, after the owner's picture of 1.57.34: the four boxes are two equal columns again, **no extra descriptions** (the text under the name of this panel, under *Tunnel cores* and *PasarGuard*, the note under the install id and the explaining half of the cores line are gone - the words stay in the tooltips), the cards inside them are smaller (the cores table, the PasarGuard panels and nodes as one-line rows, the head of PasarGuard as one line) and **no empty room inside the boxes**: the top row is 156 px, the bottom row 316 px, the two boxes of a row always equal; a longer list scrolls inside its box.
- ***Update from GitHub*** is off while this is the newest version (and while the check runs); it is on when a newer one is there.
- **Api & Key:** every connection is ONE compact box - *This panel's API* (the line that makes a token, then the tokens), *AI assistant · Anthropic*, *Telegram bot*, *Probe* (with the install row), *DNS accounts*, *Marzban / PasarGuard panels* - a head with the name, the state and the buttons, one or two rows under it, and no explaining text; the page is about half as tall.
## 1.57.50 - Security: no arrows, a working Two-step switch, no Generate in the password box; the Settings frame as wide as before

- **Settings → Security:** the three arrows (›) of *Two-step sign-in*, *Recovery codes* and *Secret entry path* are gone. The *Two-step sign-in* switch works now (it was only drawn since 1.17.38): it shows the real state, turning it on asks the password and shows the QR and the code box, turning it off asks the password and a code.
- **The *Your password, to confirm* box** has no *Generate* button and no *make a strong one* hint any more (your own password is typed, never made).
- **Sign out of other devices** is a button that stays pressed (a lock, blue when on; on by default): when it is on, changing the password signs every other device out; when it is off they stay signed in. The immediate sign-out is gone from the page (the API route stays).
- **Settings frames:** every tab's frame is one height and a taller one - at least 760 px (a tab with more grows). **About & update:** its four boxes have room - the top row is 276 px, the left boxes are a little wider, the text under the name of this panel runs the whole width, and the cores table keeps its five columns apart (no more headers and versions running into each other); the box never gets lower than 760 px.
- **Add (Panel → Configs):** the plain WebSocket starts at port 80, not 8080 (the note under the field and the check say when another inbound of the core holds it; a call with no port still gets a free one).
- **Settings:** the frame of every tab is never narrower than 789 px (it is as wide as the row of tabs; with the Alerts, Probe and Shop bot tabs gone the row got shorter and the frames of Appearance and About shrank).

## 1.57.49 - Settings → Security back to the single column of the owner's picture

- **Settings → Security:** one column again, as in the owner's picture - the state strip, *Two-step sign-in*, *Recovery codes*, *Secret entry path* and *Password* one under the other, each in its own size; *Sign out of other devices* is a button with an open padlock beside *Change password* (the two-column grid of 204 px rows and the switch are gone; changing the password signs the other sessions out again). *Api & Key* keeps what it gathered (probe, DNS accounts, panels).

## 1.57.48 - The Add tab in one frame, with the subscription configs on the right

- **Panel → Configs → Add:** the paste-a-link row and the three columns sit in ONE frame; the columns are cards of one fixed height (they scroll inside), so opening another protocol drawer (VLESS, Trojan, VMess, Shadowsocks) moves nothing.
- **New third column - Subscription configs:** the hosts of the panel (name, address:port, inbound, on / off) = the lines a user's subscription carries; the head with the count stays, the rows scroll. It reads the same cache as the Hosts tab and shows a made config at once after *Make another*.
- The first boxes of the three columns are one height (56 px), so the three sections read as one piece.

## 1.57.47 - The connections are set in Settings → Api & Key

- **Create tunnel** is disabled while fewer than two servers are added and becomes active with the second one.
- **Settings → Security:** the boxes are ONE size (three rows of 204 px, the password takes two rows) and *Sign out of other devices* is a switch again, beside *Change password*.
- **Api & Key:** the *Manage in …* buttons are gone - the connections are set on this page: the *Probe* (primary, token, standby, install - the Probe tab is gone), the *DNS accounts* (Test, Edit, Remove, Add account) and the *Marzban / PasarGuard panels* (Edit, Remove, Add panel) next to the AI assistant, Telegram and the tokens of this panel's API.

## 1.57.46 - The Add account type form in two columns, the tab named Api & Key

- **Add account type:** two columns after the owner's picture - on the left the drawers of the protocols (a tile, the name, the count; the kinds inside in two columns, each with its little picture, the chosen one in blue; one drawer open at a time), on the right one card with the note of the kind, the fields in pairs with a picture tile (name, address, core, port, group, the user template), *More*, *Check* and *Make it…*; the paste-a-link row on top has its tile and *Use*.
- **Settings:** the tab *API* is named *Api & Key*.

## 1.57.45 - Only the useful facts of the panel, smaller

- **Settings → About & update:** the strip of facts shows only what is of use to the user - *Works by: API based*, the mode, the tunnel checks, the server checks, the session - smaller; the SSH workers, the log level, the port and the env file are not shown (the env file stays in the tooltip of the strip).

## 1.57.44 - The name of the chosen kind is gone from its drawer

- **Add an account type:** the drawer *VLESS (12)* no longer repeats the chosen kind (*REALITY*) beside its name.

## 1.57.43 - The menus of the lists after the picture

- **Kit menu (tunnel type, Iran / foreign server, the AI model):** a dark card with big round corners, groups in spaced capitals with a line between them, every item with a little picture tile, its name and its note under it, a soft blue ground and a blue tick for the chosen one - the texts are the same.

## 1.57.42 - Any model for the AI assistant, from a list that drops down

- **Settings → API → AI assistant:** the *Model* is a field you can type ANY model name into, with a list that drops down as the kit menu - the models the panel knows (Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5) and the ones you used before (kept in this browser).

## 1.57.41 - Telegram and its alerts inside Settings → API, the Doctor header without two pills

- **Settings → API:** the *Alerts* tab is gone - its Telegram part is an API connection and now lives here as a compact row (bot token, chat id, the switch) with the buttons *Test*, *Rules*, *Daily report*, *Maintenance* and *Save*; Rules, Daily report and Maintenance windows open small windows with what the tab had.
- **Tunnel Doctor:** *How it works* and *All nodes* are removed from the header (*Explain*, the state chip and *Fix all* stay).
## 1.57.40 - The Shop bot is off everywhere in the menu, the backup password is a switch, the schedule is one row

- **Shop bot:** its tab of Settings is not in the menu or in the bar any more, and the Backups tab lists only the panel (the Shop bot section itself was already hidden).
- **Settings → Backups:** the backup password is a switch row under the list (not set: the switch opens the field to set it, with Generate; set: it opens the field to replace it); the *Schedule* is ONE compact row - the switch, Every, At hour, Keep for, Send to Telegram and Save.
## 1.57.39 - Security hardening, the AI assistant inside API, the Sign out button like the picture

- **Security (from the review):** a server user or host that looks like an option (`-oProxyCommand=...`) never reaches ssh / scp - every command line takes `user@host` from one checked helper after `--`, and *add many* / the shop bot settings refuse such a user; the parameters of a script must match whole (a trailing newline no longer passes); the SQL of the bot takes no single quote inside; an API token can no longer reach the backups (a restore replaces the sign-in), the private key of the panel, SSH passwords, the console or the bot's payment keys; the public demo does not serve backups, tokens, the security page or the shop bot; the log masks PasarGuard keys and panel tokens; SECURITY.md says what the code does.
- **Settings → API:** the AI assistant is part of it (key, model, test) - no separate sub-tab; its *Use* switches are gone (the Tunnel Doctor decides how the AI is used; a saved key puts it on).
- **Settings → Security:** *Sign out of other devices* is a wide button with an open padlock under the password boxes, like the owner's picture.
## 1.57.38 - About & update after the picture, Sign out of other devices

- **Settings → About & update:** built after the owner's picture - four boxes of ONE fixed size (*About & Update* with the *Update from GitHub* row, *License* with its chips and the install id with a copy button, *Tunnel cores*, *PasarGuard*), what is longer scrolls inside its box, and under them a strip of the facts of the service, each with its little icon (the path of the env file can be copied).
- **Settings → Security:** *Sign out of other devices* is a button under the password boxes (it asks for the password, ends every other session and keeps this one; the password does not change); changing the password still signs the others out. API: `POST /api/auth/logout-others`.
- API tokens live in *Settings → API* (1.57.37).
## 1.57.37 - Settings → API: every API connection in one place

- **Settings → API** (the tab that was *AI*): this panel's own API - its tokens (moved here from Security) and the link to /api/docs - and the state of every connection that works by an API key: the AI assistant (Anthropic), the Telegram bot, the probe, the DNS accounts (ArvanCloud, Cloudflare) and the Marzban / PasarGuard panels, each with the way to the place where it is managed. The AI assistant card stays under it.
## 1.57.36 - The Shop bot tab of Settings is switched off

- **Settings:** the *Shop bot* tab is switched off for now - grey in the menu and in the bar, nothing happens on it (the Shop bot section itself was already hidden, roadmap #79).

## 1.57.35 - Smoother everywhere: one draw at a time, the bar with its page, no spinner between the Panel tabs

- **One draw at a time:** a page or tab is drawn once at a time; a second call while that draw runs (the ticker, the Lab's busy timer, a window that closed) asks for one more draw after it, with the newest state - never two at once, never out of order.
- **The bar of tabs shows together with its page**, not a moment before (the old page, still on the screen, shifted under it when a section with tabs was opened).
- **Panel:** between its tabs (and its panels) the tab that was open stays on the screen, dimmed, until the new one has its data; a spinner only when the data is slow; a late answer never paints the users of the panel that was left.
- **Engineering (from the review):** one poll per action (a second window on the same action shares it, slower once the window was closed); the engines box follows a check with a flag that dies with the box; the hours of the sample routes are bounded (1-720); the host-key forget has a timeout.
## 1.57.34 - About & update in two columns, with the PasarGuard update inside

- **Settings → About & update:** two compact columns - on the left this panel (update from GitHub), its license and the tunnel cores; on the right the PasarGuard panels and their nodes (the section that was a tab of its own: *Settings → PasarGuard* is gone); under both, what the service runs with, in small print.
- **Create tunnel:** the kit menu of a select opens even in a browser tab that is not on the screen.
## 1.57.33 - The Tunnel Doctor after the picture, tab switches that do not jump, kit menus in Create tunnel

- **Tunnel Doctor:** rebuilt after the owner's design picture - three mode cards (*Doctor Mode* with its Off chip, *Auto Fix Tunnels*, *Auto Move to Iran Server*; the chosen one lit, with a tick, the number of a mode in its own card with little arrows), the box of *Active Tasks / Completed / Auto Moves / Analytics* with *Explain*, the state chip, *How it works* (a window that explains the three modes and the moves) and *All nodes* (the lists narrowed to one server) on its right, and a dashed empty box with a friendly line. A mode is switched in place (no redraw), the box keeps ONE size for every tab.
- **Switching CDN / Tunnel manager / Tunnel Doctor no longer jumps:** three causes - the bar of tabs changed shape on the Doctor page (its top is now one joined box like the toolbars of its sisters), a scrollbar came and went (the page area keeps one width now), and a refresh that had started on the old tab painted it over the new one a moment later (what the old tab waited for is dropped; a tab starts at the top).
- **Create tunnel:** pressing the button opens the window on the page as it is (no redraw behind it); the selects of the form - the servers, the tunnel type, the presets - open a kit menu: a heading for every group, a tick before the chosen item, the highlighted one in blue; the Iran list has *Automatic* and *Iran servers* as groups.
- **A failed build says why, in plain words:** under *Create did not succeed* one simple sentence (no Iran server online, the port is used, the server did not answer over SSH, GRE blocked, ...).
- **Panel menu:** until a panel is added, *Users*, *Configs* and *Admins* are dim and do nothing; *General* stays, it is where a panel is added.
## 1.57.32 - One admin emoji for every admin

- **Admins:** every admin row has the same emoji (a person with a briefcase), not a different face per state - the state is told by the switch and the pill; the API keys all have a key.

## 1.57.31 - Faces instead of strips, a compact roles window, one switch size

- **Admins and API keys:** a face (emoji) stands where the green strip was - a crown for the owner, a cool face for an active admin, a sleeping face for a disabled one, a stop sign for the rest, a key for an active API key.
- **Role window (and the key / admin windows):** rows of 30 px, sections close together; the limits no longer stick out of their card (the label wraps); the rights are one list that scrolls with the window (no second scroll box, no white corner); *Cancel* and *Save* at the foot, to the right, in a bar that stays in view; the *Close* button has a gap after its cross.
- **Switches:** one size everywhere, 40 x 24, like the check boxes of the forms.

## 1.57.30 - The kit switch, a fixed Activity box, Save at the foot, PasarGuard first

- **Switches (the owner's picture):** one look everywhere, pages and windows - off a dark slate pill with a light knob, on the blue pill with a soft glow, the knob slides; the *Add the rest* switch of the user window had the old grey one with its knob off the middle.
- **User window:** the *Activity* box has one fixed height (150 px, the list scrolls inside); *Cancel* and *Save* stand at the foot, at the right, *Save* last, in a bar that stays in view.
- **Add panel:** the default type is *PasarGuard* (Marzban is the second choice).

## 1.57.29 - The other empty Panel tabs: one fixed card, nothing to press

- **Panel > Users / Configs / Admins with no panel (the owner):** the same fixed card as General (an icon, *No panel connected*, one line saying panels are added in General); the *＋ Add a panel in General* button is gone from the middle of the page, and so is the dead copy of the Users table. A test keeps every helper of the Panel bar on its own line (a comment glued to the next declaration had swallowed one).

## 1.57.28 - The empty Panel page in one fixed card

- **Panel > General with no panel (the owner: *a fixed box for this part too*):** one card of a fixed size, fitted to the window - an icon, *No panel yet*, one line and the *Add panel* button; the bar above only says *No panel yet*. The description of General no longer mentions the removed System and Templates tabs.

## 1.57.27 - Usage > Users in fixed, alike boxes

- **Usage > Users (the owner):** the three boxes (*Users*, *Users over time*, *Top users*) are the same kit card as the overview - normal-case titles, 16 px corners - the *Users* box has one fixed height, the two cards below share one fixed height and their content scrolls inside, and every control in them is 36 px.

## 1.57.26 - The user template is back in the Add form

- **Panel > Configs > Add (the owner: *bring the user template back*):** the *User template* tick with its data limit (GB) and days is in the form again, as the sixth cell of the one row (name, address, core, port, group, user template); the drawers and the Core field of 1.57.24 stay.

## 1.57.25 - Settings redesigned

- **Panel > General > Settings (the owner: *redesign it like the other sections, neat and compact*):** the sections (Telegram, Webhook, Notifications, Events, Subscription, HWID, General, User template) are the kit's segmented control with the lifted blue open one; the form is ONE card of a fixed height - a head (the title and its help, without the repeated sentence), the fields that scroll inside it, and the buttons at its foot; every control is 36 px (a switch is a 36 px well too, so a row of fields sits on one line) in 280 px columns; the *read again* button is the kit's icon button. Checked in a real browser: card, 36 px controls, 28 px segment buttons, the foot inside the card, switching sections, the unsaved-edits dot and the way to *User template* all work.

## 1.57.24 - The Add form in drawers, with a Core field

- **Panel > Configs > Add (the owner: *arrange it, VLESS in a drawer, no user template, the core from there*):** the suggested configs are one **drawer** per protocol (VLESS, Trojan, VMess, Shadowsocks; one open at a time, the chosen kind is named on its drawer and its drawer opens by itself, e.g. after pasting a link); the kit's chips; ONE row of five fields - name, address, **core**, port, group. The *User template* checkbox (and its GB and days) is gone - nothing is made but the inbound, the group and the host. The core select moved out of *More* and is always there.

## 1.57.23 - No System tab, its Users box in Usage

- **Windows (the owner):** the Address field of the panel window was yellow with light text: in a window the focus ring (also a shadow) had removed the dark well the autofill rule paints, so the two are one shadow now; in the light theme an autofilled field is white with dark text. *API key / token | Sign in once* is the kit's segmented control with the chosen one lifted and blue.
- **Panel > General (the owner):** the *System* tab is gone (General is *Usage*, *Settings*); its *Users* box - the users by state (online, active, on hold, limited, expired, disabled) and the traffic since the counters were last reset - is at the top of *Usage > Users*. The code of the System tab (pg_system.js) stays, unreachable.

## 1.57.22 - The user template inside Settings, blue gauges

- **Panel > General (the owner):** the *Templates* button is gone; its **user template** is the last section of *Settings* (the row of Settings sections ends with *User template*; *Settings* stays the lit button while it shows). The client templates have no door any more (their code in pg_client.js stays, unreachable).
- **Lab (the owner):** the load of a row is smaller - the number 12 px, the bar 120 x 6 px, the line under it 10 px.
- **Usage:** the gauges of CPU, memory and disk are the owner's soft blue while they are quiet (amber and red when busy); *Active* in the ring of the users stays green.

## 1.57.21 - Smaller traffic type

- **Lab (the owner):** the numbers (13 px), the units (10 px) and the arrows (14 px) of *Traffic used* and *Traffic Mbit/s* are smaller.

## 1.57.20 - The other uptime strip is blue too

- **Lab (the owner):** the strip of the last days in the *Monitor* section has the same bright blue as *Heard over SSH* (1.57.19 changed only the Overview one; its test was red - fixed).

## 1.57.19 - Vertical dots, a blue strip

- **Menus (the owner):** the ⋯ button of every row and window has vertical dots now.
- **Server window:** the *Heard over SSH* strip is the owner's bright blue (the quiet cells a slate blue) instead of green.

## 1.57.18 - The look-up card in the kit's look

- **Usage > Look up (the owner: *like a MacBook, an icon in place of its button*):** the card is a kit card with a normal-case title; the segmented control, the field and the button are one 36 px row; the *Show usage* text button became an icon button (a slate rounded square with a magnifier, blue when pointed at; Enter in the field works too).

## 1.57.17 - Usage without the section chips, IP before Problems

- **Usage (the owner):** the row of chips (*Overview, Traffic, Nodes, Users, Apps, Look up*) is hidden; the overview is the page and the **›** on each card opens its section; inside a section the row shows *‹ Overview* (the way back) and the name of the section, and the range button stays at the right. 
- **Server window (the owner: *instead of this big box a more compact thing in one row*):** the banner (*Needs attention*, *Worth a look*, *Healthy* ...) is one slim row - the icon, the title, what is wrong (cut with an ellipsis when long, the whole text in the tooltip) and the buttons at the end.
- **Look up (the owner):** it has a button of its own in the row beside the range button - the same size and kit look, blue and lifted while its section is open (and a click on a user in *Top users* opens it).
- **Panel bar (the owner: *fix the boxes here too*):** measured in a real browser, the segment button and the icon buttons were 32 px high inside a 36 px track and stuck out of it; they are 28 px now.
- **Lab (the owner):** the *IP* and *Problems* columns swapped places - the address is right after the name again.

## 1.57.16 - The problems in a column of their own, a fixed Lab box

- **Lab (the owner):** the problem tags (*GRE ✕*, *Filtered*, *Offline*, *CPU 91 %* ...) leave the name and stand in a fixed *Problems* column between Name and IP, a clear step away from the name; they are smaller (18 px high, 10.5 px type). The list is a box of ONE fixed height, whatever the number of its rows.

## 1.57.15 - Quiet arrows

- **Lab (the owner):** the arrows of *Traffic used* and *Traffic Mbit/s* have the colour of the small text under the load (green and pink are gone).

## 1.57.14 - No node version line, quiet icons in the Doctor tabs

- **Lab (the owner):** the version line under a node's name is gone (one node had it, two did not).
- **Doctor tabs:** the icons had a ring around each (the class of the round tunnel flags was reused by mistake); they use the quiet tab-icon class now.

## 1.57.13 - Row geometry in three numbers, Traffic used, the Panel bar like the profile bar

- **Lab geometry (the owner: *the distance from the lamp exact and engineered, checked with a test*):** the lamp is now inside the name cell; one set of three numbers decides everything - the lamp is 16 px from the edge and 14 px wide, the icon starts 20 px after it (50 px from the edge); a test keeps the header, the colgroup and every kind of row at the same number of columns. Measured in a real browser on seven rows: lamp 14 x 14 px at 16 px (17 with the cell's border), 20 px to the icon, icon 26 x 26, 9 px to the name, lamp and icon both exactly centred.
- **Flags:** they fade more on all four sides; when a row is pointed at they get only a little colour (so the text and the icon stay readable) and the name and the icon get a soft shadow.
- **Problems as tags (the owner: *GRE x, Filtered ...*):** a server with a problem shows it as tags after its name - the kit's Error and Warning chips: *GRE ✕*, *Filtered*, *Offline*, *Bad route*, *CPU 91 %* ... (three at most, the rest as *+n*); a node that is not connected says its state the same way; the search finds a server by its problem.
- **Traffic used (new column):** the bytes a server's network card has carried since it started (a node: what its panel counted), value over unit, between Load and the speed. The probe now also reads the byte counters (
x_bytes, 	x_bytes in the facts).
- **Panel bar (the owner: *like the Profile section, no edit button, a locked box*):** the panels are the same segmented control as the profiles with the same + and bin icon buttons; the edit button is gone (the API key pill still opens the edit window); the box has one fixed height and the panels scroll inside it.

## 1.57.12 - Flyout menus, the Usage range in the lower row, autofill, the Doctor buttons

- **Menus (the owner: *as much as you can like Move to profile, only the important actions on their own*):** the menu of a node has Overview, Check now, Edit and Restart on their own and *Move to profile*, *Monitor*, *Manage* and *Update* as flyouts; a server's menu keeps Check now, Console, Edit and Reboot on their own and opens *Access*, *Tests* and *Install* (and *PasarGuard node*) as flyouts.
- **Usage:** the range button moved into the lower row, to the right of the section chips, and both rows are alike - the same segmented track and the same lifted blue open segment (the panel tabs above use it too).
- **Lab:** the Status column shrinks to its content and has no word over the lamp, so the icon and the name stand close to their lamp.
- **Autofill (the owner):** a field the browser fills in (a saved or suggested password) keeps the kit's dark well and light text - the browser's yellow made the text and the eye unreadable.
- **Doctor page (the owner):** the two rows of tabs and the range (*In progress / Finished / Moves / Analyst*, *Best today / Tunnels / ...*, *6 hours / 24 hours / 7 days*) are the same kit segmented control, one size; the icons are quiet; *Explain* and the chips have the kit's corners.
- **Panel page:** the two rows - the panels and the tabs - are one surface of the kit (the same dark card, edge, corners and height); the *Refresh* button of Usage is gone (the page reads itself again by itself).
- **Panel tabs (the owner):** the tab buttons (Usage, System, Settings, Templates, Users ...) are ONE size on every tab - a 36 px track of the kit and 28 px buttons with the kit's corners; the open one is lifted and blue with no halo around it.

## 1.57.10 - Closer to the lamp, flags that fade on both sides

- **Lab rows (the owner):** the Status column is a fixed 80 px, so the icon and the name stand close to their lamp; the flag is as wide as its picture (cover, no stretching) and its mask fades BOTH sides of it (before, the picture began where the fade had already ended, leaving hard edges).

## 1.57.9 - The load in the middle, a longer bar in the owner's blue

- **Lab (the owner):** the number, the bar and RAM / Disk stand in the middle of the Load column; the bar is longer and is the thick round bar of his picture - a flat soft blue (amber or red when busy) on a slate track, no glow.

## 1.57.8 - Status column, flags, panel tabs, windows in the kit's look

- **Lab:** the first column is called *Status*; the flag behind a name keeps its own proportions, fades on all four sides, is grey and turns to its colours when the row is pointed at.
- **Usage:** the dates beside the range button are gone (they are in the button's tooltip); the tabs of a panel (Usage, System, Settings, Templates, Users ...) never change size when chosen and the open one is lifted and blue.
- **Windows (the owner: *Add a server* like a MacBook = the UI kit sheet):** dark wells with a soft blue ring, no number spinners, square header buttons, the flat blue primary button.

## 1.57.7 - Usage cards in two fixed rows

- **Usage overview (the owner):** the six cards stand in two rows of three, every box the same fixed size, in the kit's look (a dark card, normal-case titles, a rounded-square › button that turns blue when pointed at); two columns on a narrow window, one on a phone. (1.57.5 / 1.57.6 below went out in the same push: the Panel bar in the kit's look without the refresh button and with a fixed box of panels, and the Usage range as one soft panel.)

## 1.57.6 - Usage range as one soft panel, the Panel bar in the kit's look

- **Usage range (the owner's picture):** the six range buttons are ONE button with its label (*Last 7 days*, *This month*, *Custom range* ...) and the dates beside it; a click opens a small panel softly under it - tab *Relative* (Days 1-10, Weeks, Months up to 3, and the two Jalali months, the chosen chip in blue) and tab *Absolute* (two dates). One question still covers at most 93 whole Tehran days, so there is no hours row.
- **Panel bar:** no *Check now* (refresh) button any more; the panels are kit tags (the open one lifted), *add* is the kit's icon button, and the box of the panels is fixed - many panels scroll inside it and never push the rest out of the page.
- **Tunnels bar:** the kit look of 1.57.4 now lives in lab.css (the kit file sets looks only, never sizes).

## 1.57.4 - The bar of a page in the kit's own look

- **Bars (the owner: Tunnels and the same bar of the other pages):** the primary button is the flat blue of the sheet's *Connect* with a line plus (no gloss, no circle); *Check all*, *Restart all* and the like are the dark card of *Settings*; the counters are the chip; *All / Problems* is the segmented control with the lifted open segment; *Search* is the text field with the blue ring. Light theme has its own colours.

## 1.57.3 - Flags that fade on both sides, no Check, the Add server of the kit

- **Lab (the owner):** the flag behind a name fades on both sides; the chevron button at the end of a row and the *Check* button of the bar are gone; *Add server* is the button of the UI kit sheet - the blue of the primary button, a drawn plus, no sweep of light.

## 1.57.2 - The small CPU charts are gone

- **Lab rows (the owner):** the small chart of the CPU is removed again (the number, its thin bar and RAM / Disk stay); the GET /api/profiles/spark route of 1.57.1 is removed with it.

## 1.57.1 - The Lab row like the drawing

- **Rows (the owner's drawing):** a round lamp of its own at the left (green, yellow, red, blue), the flag and the icon, the name with its *provider* under it (not the country), then the address with a globe, a small chart of the CPU of the last hours with the number, a thin bar and RAM / Disk, the traffic in two lines (value over *Mb/s*), and the two buttons at the end - the menu and a chevron. The profile tag is a small chip beside the name.
- **Backend:** GET /api/profiles/spark - the CPU of every profile's server averaged in cells over the last hours, from the samples (one grouped question).

## 1.57.0 - Usage as round stats; the lamp ignores the way to the server

- **Usage (the owner):** rebuilt as round stats on ONE screen - a ring of the users by status (with the ones online now), three gauges for the server (CPU, memory, disk), the traffic with a strip of bars, a ring of the usage per node, a ring of the subscription apps and the five biggest users. Every card has a › that opens its own section.
- **Sections:** chips under the range bar - *Overview*, *Traffic*, *Nodes* (only the consumption of the nodes: a big ring and the sortable table), *Users*, *Apps*, *Look up*; the chosen one is remembered. Marzban has Overview, Users and Look up.
- **The lamp of a server:** a filtered address, a GRE fault, a tunnel down or a bad route no longer turns it yellow or red; it follows the server itself (not answering, login, CPU, memory, disk, node).

## 1.56.27 - A flyout for Move to profile, Make a PasarGuard node

- **Move to profile (the owner):** it is a flyout now - a small menu opens beside it with the names of the profiles (each with its picture, the current one checked); one click moves the server. Menus can have such submenus anywhere (hover, click or the right arrow).
- **Make a PasarGuard node…:** a server that is not a node yet (added under *All*) gets this item in its menu: the panel installs the node, adds it to the PasarGuard panel, and the server goes to the PasarGuard profile.
- **Rows:** the icon and the name stand a little to the right; the lamp is behind the icon, peeking out, not on top of it.

## 1.56.26 - Long profile names no longer push the buttons away

- **Profile tabs (the owner):** the tabs give way when the room runs short - each box gets shorter and a long name ends with an ellipsis - and the buttons beside them (add, sync, rename, delete) always stay in view.

## 1.56.25 - Network: the tabs under the header

- **Network (the owner):** when it is opened, its three tabs - *CDN*, *Tunnel manager*, *Tunnel Doctor* - stand in the bar under the header (as before they were only in the menu list).

## 1.56.24 - The lamp of a server is one small dot

- **Server icons (the owner):** no disc and no ring behind them; the icon stays black-and-white and its state is ONE small round lamp on its corner, like the dots of *up / down / unstable* - green, yellow, red or blue.

## 1.56.23 - Fixed boxes, a quiet lamp, a finer load bar

- **Fixed boxes (the owner):** the tunnels, every tab of the DNS page (Records, Changes, Accounts, Failover) and the box of the Doctor keep one size, fitted to the window; the list scrolls inside.
- **Nothing there, nothing to press:** without a DNS account only the *Accounts* tab works; without a tunnel *Check all*, *Restart all*, the tabs and the search are switched off.
- **Server icons:** black-and-white only (white in the dark theme, dark in the light one, the lion too); behind them a small lamp - a soft disc with a thin ring - green, yellow, red or blue.
- **Lab table:** the *Serves* column is gone; the address now stands right after the name, the profile (the tag) has its own column, and what the panel is doing with a server (the steps of an install) is written under its name.
- **The load bar:** bigger, finer, in the blue of the UI kit's slider (amber when busy, red when overloaded).

## 1.56.22 - A status lamp behind the server icons, a fixed box for the tunnels

- **The icon of a server is never coloured (the owner):** it is white in the dark theme and the colour of the text in the light one; behind it a soft lamp says how the server is - green well, yellow with a problem, red cut, blue while the panel works on it.
- **Tunnel manager:** a fixed box (it keeps its size, the list scrolls inside); *Check all* and *Restart all* have names only; the tabs All / Problems and the search stand in one row, the same height.

## 1.56.21 - The Lab looks again by itself

- **A server that was just added does not stay with dashes (the owner):** the Lab page looks again every 3-4 seconds while a server is busy or has not been read yet, and every 30 seconds otherwise (never while a window is open), so the first numbers appear by themselves.

## 1.56.20 - One list of the menu open at a time

- **Opening a list of the left menu closes the one that was open (the owner):** press Servers while Network is open and Network closes. Of several lists remembered as open only the first stays after a refresh.

## 1.56.19 - One PasarGuard profile

- **The profile of the nodes of a PasarGuard panel is called PasarGuard (the owner), not PasarGuard Nodes:** the nodes found by themselves go to PasarGuard, and the upgrade (migration 19) renames the old name; when both names exist they become one and the name is listed once. (On the main panel the empty PasarGuard profile was deleted and PasarGuard Nodes renamed by hand, after a backup of its database.)

## 1.56.18 - "new" goes after 15 minutes

- The "new" pill of a server stays 15 minutes (it stayed an hour).

## 1.56.17 - The update card looks by itself

- **No Check button (the owner):** the card looks for a new version by itself every minute. **An update that finishes is seen:** if the panel comes back as another version the bar runs to 100 % and the page reloads, whatever the log says (it used to stay on a few percent until the page was refreshed).

## 1.56.16 - The update takes a minute, and the bar moves like a MacBook's

- **Why it was slow (the owner):** every update ran the WHOLE test suite on the server that updates - about nine minutes - although the author had run it before publishing. scripts/deploy.sh now skips the tests when HEAD is a released version (a tag); the secrets check, the migration check on a copy of the database, the install, the health check and the rollback stay. FORCE_TESTS=1 runs them anyway. An update is about a minute now.
- **The bar:** it grows smoothly (an easing step ten times a second, a soft light runs along it) and creeps on between the markers of the deploy, never promising the next one; the number rounds along with it, and at the end it runs to 100 % before the page reloads.

## 1.56.15 - The address writes Copied

- A click on an address copies it and the cell itself says **✓ Copied** (in green) for a moment, then shows the address again.

## 1.56.14 - The actions of the node are back in the menu of its server

- **A server that carries a PasarGuard node keeps every action of the node in its menu (the owner):** when the node became one row with its server (1.56.3) its Log, Usage, Resources, Latency, Certificate, Edit, Restart, Disable, Resync users, Reset counters and the three updates went from the list; they are in the menu of the server row again, under the groups *Node · Monitor*, *Node · Manage* and *Node · Update*. Its Delete is the tick in Delete server.

## 1.56.13 - Delete server has the node tick; Abuse-Defender starts fresh

- **Delete server... has the tick (the owner):** for a server that carries a PasarGuard node the box says **Also delete the node NAME from the PasarGuard panel itself** - ticked, the node goes from the panel after the server; unticked, it stays (no second question after the server is gone).
- **Abuse-Defender starts fresh:** one that is on the server already (ours or the old one: its chains, cron lines and scripts) is removed first, and the ranges go in with ONE iptables-restore. Found on the way: the nightly cron line was never written on a server with an empty crontab (the grep that finds nothing ended the subshell under `set -e`); fixed.

## 1.56.12 - The Copied pill of the old list on the addresses

- The addresses of the Lab are `ip-copy` cells like the old list's: a click copies, the address flashes and a green **Copied** pill pops at its right.

## 1.56.11 - The address says Copied where it was clicked

- A click on an address of the Lab copies it and marks the address itself (a solid mark for a moment), as the old list did - not a message at the top.

## 1.56.10 - The torrent block takes a minute, and blocks something

- **Why it stuck (found on a real server):** the public script makes six iptables calls for each of its 4634 tracker names - 27 000 process starts, 5 to 7 minutes - and then maps the names to 0.0.0.0 in /etc/hosts, so its rules only ever dropped 0.0.0.0 ("drop rules=7" was three rules and four).
- **Now:** the tracker list is theirs, how it is applied is ours - every name is resolved once (in parallel), the addresses go into one ipset, three iptables rules drop it; about a minute. An address that is shared (a name pointing where more than three others point, or any Cloudflare address) is never blocked, and if the list of Cloudflare ranges cannot be had nothing is. It runs daily and at boot; the leftovers of the old script (the /etc/hosts lines, the rules for 0.0.0.0) are removed.

## 1.56.9 - The Doctor of a server names the cause

- **A refused login says so (the owner):** "Wrong SSH password: the server does not accept the login" with the button **Enter the right password** (and Check again). **A server that cannot be reached** says why (no route to host, timeout, refused) and that a password would not help, with **Check again** and **Change address / port**.

## 1.56.8 - The SSH password box is short and honest

- **A server that does not answer is not a wrong password (the owner):** when SSH cannot reach the server (no route to host, timeout, refused) a short message says so and no password is asked. When it is the password, the box says "SSH password of NAME?" - no long error, no Generate button, no "at least 8 characters" (that is for a new password).

## 1.56.7 - The install says short steps from the first moment

- **The row of the server says what is going on in one short line, from the first moment (the owner):** Connecting to the server... -> Checking the server... -> Starting the install... -> 1/4 Installing Docker and the node (minutes) -> 2/4 Reading the certificate -> 3/4 Adding the node to the panel -> 4/4 Waiting for the panel to connect (up to 2 min); the torrent block says "Torrent block: adding the rules (minutes)". The long lines of the installer are in the story only; the install inside the steps no longer overwrites them.
- **The Profile field of Add a server is always Any when the box opens (the owner):** it no longer takes the profile of the open tab.

## 1.56.6 - The pictures of the profiles are grey

- The flag of Iran and the emoji before a profile are not coloured any more (the owner): they are grey, like the logos of PasarGuard and Marzban.

## 1.56.5 - A taken port sends the node to a free pair by itself

- **When 62050 or 62051 is taken on the server the node goes to a free pair by itself (the owner):** the plan reads the listeners of 62000-62300 over SSH, picks 62060/62061 (then 62070/62071 and so on), says so in a message ("open them in the firewall"), the installer is given those ports, and the node is added to the panel with them. Only when there is no free pair at all is it a problem.
- **A message that cannot be missed:** when the node cannot be made, a dialog says why (it was a toast that went by).
- **Every profile has a picture (the owner):** the flag of Iran for Iran, a globe for Foreign, the logos of PasarGuard and Marzban, and an emoji for any name of his own (the same one for the same name).

## 1.56.4 - A failed install is no install

- **A certificate alone no longer means the node is installed:** the plan also asks whether a PasarGuard node container runs. A failed install leaves its certificate behind (found on a real server: the plan said "installed" after "port 62050 is already in use"), and the next make skipped the install and added a node that never started. Now such a server is not installed: the plan says an earlier install did not finish and does it again (the ports are checked first).

## 1.56.3 - Add a server to a PasarGuard node: checked from the start to the end

- **The plan looks at the ports first:** before anything is installed it asks the server who listens on 62050 and 62051; a taken port is a problem the first line of the message names ("port 62050 by python (pid ...)"), so an install that would stop after minutes does not start. Nothing is changed on the server.
- **The node first, the torrent block after:** when both are ticked the PasarGuard node is made first (it is what was asked for), then the torrent block (thousands of rules, minutes) runs and the row says "installing - torrent block"; no big window - the steps are on the row, the end says in a message.
- **A node that runs on a server of ours that is on the page is that server's row** (with the lion icon), not a second row; Sync still brings a node in that is not on any row.

## 1.56.2 - The IP column: no port, a click copies

- The IP column of the Lab shows the address only (a node showed its port after it), and a click on an address **copies it** (the toast says what was copied), as the old list did.

## 1.56.1 - The update card waits for the new run

- After the click on Update the percentage ignores what the log says of an older run (a finished or a failed one) until the new run has started (at most 20 s), so it never says Updated or Failed too early. Found by the full walk.

## 1.56.0 - The full test of everything so far

- The whole suite, the real test of all 17 kinds of account on a throw-away PasarGuard, a walk through every page and a smoke test of every read route of the live test panel. Found and fixed: the Lab page that did not load (1.55.22), a timer kept on `window` by the update card, and six tests that still described the old Add server box.

## 1.55.25 - A stray piece of a comment is gone

- Cleaned a leftover in the comment of the busy line of the Lab (no change in what the page does).

## 1.55.24 - The Lab page loads again

- Fix: 1.55.22 used `labPgSrv` (the lion icon of a PasarGuard server) without defining it, so the Lab page said "This page could not be loaded". It is defined now. Found by the full walk through every page.

## 1.55.23 - No jump in the Servers menu after a refresh

- After a refresh the menu under Servers no longer shows "All" first and the profiles later: "All" is hidden from the first paint and the profiles of the last visit are drawn at once (they are read again when the page is).

## 1.55.22 - A flag on every row, the lion on PasarGuard servers

- **Every row has its country flag, whatever the profile (the owner):** a node that runs on no server of ours (like a node added in PasarGuard by hand) gets the flag of the country of its address, found once in the background and kept for a week.
- **A server in a PasarGuard profile - or one that carries a PasarGuard node - has the lion of PasarGuard as its icon,** like a node row.

## 1.55.21 - The steps of the node install are on the row of the server

- **While a PasarGuard node is installed, the row of the server says what is happening (the owner):** a spinner and the step - 1/4 Installing the node (Docker) with the lines of the installer, 2/4 Reading the certificate, 3/4 Adding the node to the panel, 4/4 Waiting for the panel to connect. The page looks again every 3 s. There is no big window any more; a stop says its last line in a message.

## 1.55.20 - No Checked switch in the server form

- The "Checked by the panel" switch is gone from Add a server (a new server is always checked; an existing one keeps its state, and its window has the switch).

## 1.55.19 - The PasarGuard part of Add a server shows at once

- The PasarGuard part opens with the box when the open profile is a PasarGuard one (it waited for the first draw).

## 1.55.18 - Add a server: the profile is a drop-down, the install options belong to the PasarGuard profile

- **The Profile field of Add a server is a drop-down of the profiles that were made** (Any = none); the open tab is chosen by itself.
- **Install after adding is gone.** Choosing a **PasarGuard** profile brings the PasarGuard part: **Block public torrents** and **Install the PasarGuard node** (on by itself; everything automatic, a random API key, the options folded). Other profiles have no install options. **Marzban is switched off** for now (only PasarGuard is developed).

## 1.55.17 - The menus like a MacBook's

- **Every menu that opens (a server, a node, the Users More...) is small and in the style of a MacBook (the owner):** compact rows, a soft dark glass, the row under the pointer in the accent colour, delete in red text (filled red under the pointer), a small grey name for each group (Profile, Monitor, Manage, Update; Server, Access, Tests, Install) and a thin line between the groups.

## 1.55.16 - The update shows only its percentage

- **No log box in About & update (the owner):** while the panel updates, the card shows only **Updating... NN%** with a thin bar (the steps of the deploy script are the percentage; the tests take most of it); the bar waits while the panel restarts, the page reloads at 100 %, and a failed update says so. The server reports it as `progress` in `GET /api/system/update`.

## 1.55.15 - Add a server, designed again

- **Add a server is a small window of three light sections (the owner):** Server (name, profile, host; where it is is found from the address), SSH login (user, port, password in one row) and Install after adding; the foot has the Checked toggle and the button.
- **The PasarGuard node tick does everything by itself:** it says so in one line (Docker and the node over SSH, a random API key that is never shown, the certificate, added to your panel); the panel, node name, core and coefficient are folded under Options (the panel is shown only when there is more than one).

## 1.55.14 - The update card says the version and links to what changed

- **About & update (the owner):** instead of the commit numbers and the long explanation, the card says **Version 1.55.14 is available** (or Up to date), with a link **What changed in v... on GitHub** that opens the release notes of that version. The panel reads the newest version tag of the repository for it.

## 1.55.13 - Servers opens on All

- **"All" is gone from the left menu under Servers (the owner):** pressing **Servers** opens the Lab on its All tab; the profiles stay in the menu, and the tabs of the page hold them too.

## 1.55.12 - Every PasarGuard profile is locked while a panel is connected

- The lock is not only for "PasarGuard Nodes": a profile called **PasarGuard** (or any name that starts with it) cannot be renamed or deleted either while a PasarGuard panel is connected - the two buttons are off with a tip, and the server refuses it.

## 1.55.11 - The traffic column of the Lab

- The arrows are drawn (one size, one weight; green down, pink up) and the numbers are in the font and weight of the rest of the row, with tabular figures and a clear gap between the two.

## 1.55.10 - The plus beside the tabs offers the suggested names

- The **+** beside the tabs of the Lab opens the same Add profile box as the menu (the name, and the suggested tags under it), not a plain question.

## 1.55.9 - Sync in a PasarGuard profile

- **A PasarGuard profile has a Sync button (the owner: a panel is there but its nodes are not shown):** beside the tabs (and in the empty page of the profile) - it reads every PasarGuard panel NOW and puts each node that is not on the page into the open profile, the ones taken off the page on purpose too; it says how many were added, or why a panel did not answer.

## 1.55.8 - The suggested tags are calmer

- The suggested tags of Add profile are ONE quiet row: four (Iran, Foreign, Marzban, PasarGuard) as small outline pills of one height, no gloss, no second line (Hetzner and Backup are gone).

## 1.55.7 - The delete box is shorter

- The tick of the delete-node box has no long explanation under it (the owner).

## 1.55.6 - Delete a node: a box with a tick

- **Delete the node... opens a box with a tick (the owner):** by itself it takes the node off the Lab page of this panel (it stays in its PasarGuard panel and keeps working; the check does not put it back); tick **Also delete the node from the PasarGuard panel itself** and the node is deleted there first (saved to a file, Backups can make it again), then the profile goes. A node that has no profile on the page keeps the plain question.
- The glass confirm box can carry a tick (an option of glassAsk; the answer is {checked}).

## 1.55.5 - A prettier Add profile box, suggested tags, the PasarGuard profile stays

- **The Add profile box** is a small card (it was as wide as the window): a big field and the **suggested tags** under it (Iran, Foreign, Marzban, PasarGuard, Hetzner, Backup - the ones that exist are left out); one click fills the name. The first page uses the same form.
- **The profile of a connected PasarGuard panel (PasarGuard Nodes) cannot be renamed or deleted** while a PasarGuard panel is connected (the owner): the two buttons beside the tabs are off with a tip, and the server refuses it too. Once the panel is removed it is a plain profile again.

## 1.55.4 - Move a server to any profile

- **A server or a node can be moved to another profile (the owner):** ⋯ → **Move to profile…** opens a list of the profiles - one click moves it (the one it is in says "here"; Any takes it out of its profile; **+ New profile…** makes one and moves it there) - or **drag the row onto a tab** of the list. It replaces "Change tag…", where the name had to be typed. Nothing is done to the server or the node itself.

## 1.55.3 - A node deleted in PasarGuard goes off the Lab page too

- **The Lab follows the node list of the PasarGuard panel both ways (the owner: he deleted the node in PasarGuard and the row stayed as "node 1"):** at the next good check (30 s) the profile of a node the panel no longer has is taken off the page, with an event; a check that could not read the node list removes nothing.

## 1.55.2 - A node added in PasarGuard is found by the panel by itself

- **The panel detects the nodes of a PasarGuard panel on every check, not only when the panel is connected (the owner: a node he added in PasarGuard was not seen in the panel):** a node that has no profile yet gets one at the next check (30 s), tagged PasarGuard Nodes, and an event says so. A node he takes off the page on purpose stays off (the check remembers it as skipped); adding it by hand again, or removing its panel, clears that note.

## 1.55.1 - The Add form is one row, Top users fits

- **Add tab:** the form was squeezed into one column of the page grid (and the fields a kind does not use were shown); it is a block now - one row of Name / Address / Port / Group / template, the rest folded, the family name on the line of its chips.
- **Usage:** Top users has the admin under the name and the usage and limit in one column; the age of the counters is said only when they are old (the bar stays in one row); the chart scale shows plain numbers (20, 15, 10, 5).
- The real test of ALL 17 kinds of account on a throw-away PasarGuard passed: each kind makes its inbound, group and host, the subscription carries a link with what the kind promises (flow, pin, xHTTP, header, ML-KEM), and the cleanup leaves the core as it was.

## 1.55.0 - The Usage dashboard, Add by pasting a link, the UI kit, menus in one column

- **Usage is a dashboard (the owner: "very professional"):** ONE bar (the tabs, the range as buttons Today / 7 days / 30 days / this month / last month / Custom, the dates in both calendars, Refresh); a band of counters in two groups (users, with the colour of what needs a look; server, with meters that turn amber and red); then two columns - **Traffic** (total, average per day, busiest day, busiest node, a chart with a scale, the nodes with their share) and **Top users** (usage and limit in one column with a meter) | **Users over time**, **Subscription apps** and the **lookup** of an admin or a user (Admin | User buttons). The page scrolls; the old one-screen squeeze (empty areas, tiny charts) is gone.
- **Add is simple (the owner):** ready kinds as chips grouped by family, one row for the few things you may change, the rest folded - and **paste a link** (vless:// trojan:// vmess:// ss://) of your own: the kind, port, path, Host, SNI and fingerprint are read from it and the form is filled; the keys and ids are made new.
- **The UI kit (the owner's sheet):** `uikit.css`, loaded last, gives every button (primary, secondary, ghost, destructive), field, segmented control and menu ONE look; it sets colour, border, shadow and radius only, never a size, so no page moves. `docs/design/ui-kit.md` says it: everything new is built from these elements.
- **Every menu that opens is the one-column grouped menu (the owner's picture):** the Users "More..." is now that menu (Overview-style groups, a line between them, the destructive item red) through `nkMenu(btn, items)`, the same opener the node rows use.
- **A Panel tab with no panel connected keeps its boxes:** the bar and the table are drawn empty, with "Add a panel in General", instead of one line of text.
- The "Add a server" window fits its box; the Core cards show the selected count (0/N) in the Select all row.
## 1.54.6 - Six more kinds in Add (the owner's own links)

- **Add has the other kinds of the owner's link list (17 in all):** VLESS TCP with the HTTP header (headerType=http, a path, a Host), VLESS xHTTP (plain, TLS, REALITY) with the three tuning settings his links carry, VLESS HTTPUpgrade over TLS, and VLESS Encryption (ML-KEM: the two strings of `xray vlessenc` are pasted - the panel cannot make those keys). The WebSocket, REALITY, Trojan and VMess kinds were there already. (This release also carries 1.54.5, the Core cards, which did not go out because of a quote error that the JS check caught.)

## 1.54.5 - The Core tab is the cards of PasarGuard

- **Configs → Core looks like the Core Configs page of PasarGuard (the owner):** the cores are cards (a green dot, the name, what is in it, a ⋮ to open) with a search, reload, Select all and a switch between cards and the table; the bar of chips (Cores 1 · Default Core Config · All cores…) is gone. Opening a card shows the editor with a ← Cores button to come back; one core is no longer opened at once.

## 1.54.4 - The first page asks the profile's name in its own card

- **Add profile on a new panel asks the name in the same page (the owner):** the card turns into a name box (the name is also the tag) with Make the profile / Cancel; no window.

## 1.54.3 - Add profile makes the profile first; the Panel item is always open

- **Add profile asks for a profile name first (the owner):** on a new panel the page says "Start with a profile" with **＋ Add profile** (the menu item does the same): a box asks the name, makes the profile and opens it; the servers are added inside it (its tag is already filled in).
- **The Panel item of the menu is never locked any more (the owner: "keep this section active entirely").** Without a connected panel its page says so and shows how to connect one.

## 1.54.2 - A new panel's Servers menu is only "+ Add profile"

- **No category and no "All" from the start (the owner: "delete all, only Add profile stays - the user makes a profile first, then can add a profile"):** the Marzban / PasarGuard Nodes / Iran items are not made for you any more. A new panel's Servers menu shows only **+ Add profile**; a category (a tag) appears when a profile that carries it is added, or with the + beside the tabs, and "All" appears when there is something to list. A panel that already has profiles keeps the tags they carry.

## 1.54.1 - The menu of a node's row is the full menu

- **The ⋯ menu of a node in the list is the same menu as the node window's (the owner: "like the other one, in one column with groups"):** icons, a line between the groups - Log, Usage, Resources, Latency, Certificate…; Edit…, Restart…, Disable…, Resync users…, Reset counters…; Update the node…, Update Xray…, Update geo files…; Off the page; Delete the node… - with Overview, Check now and Change tag… on top (they are about the profile).

## 1.54.0 - Hosts, Access and Core are ONE section: Configs

- **One section to add an account to the panel (the owner: "make these three sections one"):** the menu of the Panel has General · Users · **Configs** · Admins now. Configs holds four tabs in the one row at the top: **Add** (choose a protocol; the inbound, the group, the host and a user template are made for you - the form that was a window is in the page now and the result stays in it, with "Make another"), **Hosts**, **Groups** and **Core**. An old link to Hosts, Access or Core opens its tab in Configs.

## 1.53.5 - Templates are a tab of General

- **The Templates tab (user templates and client templates) moved from Access to General (the owner):** General is now Usage · System · Settings · Templates; Access holds the groups only.

## 1.53.4 - The Account type button is not shown

- **The ＋ Account type button is gone from Access, Hosts and Core (the owner: "remove this completely").** Nothing else was deleted: the window, its eleven kinds and its routes stay in the code, and `PG_KINDS_UI` at the top of `pg_kinds.js` brings the button back.

## 1.53.3 - Outbound and the Shop bot are hidden for now

- **The Outbound tab of Network and the Shop bot item of the menu are not shown (the owner: "do not show them in the panel until we get to them later").** They were grey; now they are not there at all - not in the menu, not found by the search of the dashboard, and the Shop bot's page opens the Dashboard. Nothing is deleted: each has one switch at the top of `app.js` (`OUTBOUND_UI`, `SHOPBOT_UI`) that brings it back.

## 1.53.2 - The traffic numbers are right, an IP column in place of the state column

- **The traffic of a server shows the right numbers (the owner: "show the numbers of this part right").** Two faults were found on the real servers: (1) on the servers whose awk is mawk (Ubuntu's) a big network counter was printed as `2.91282e+10`, so the speed over three seconds moved in steps of 1 MB or 10 MB - the strange 2.67, 5.33, 26.67, 266.67 of the list - and the CPU windows of a long-running server in steps of minutes; every counter is printed whole now; (2) the sum of every interface counted the bytes of a GRE link or a tunnel interface a second time (one server showed 3 TB more than its card carried); the speed is the card of the default route now. The numbers have two decimals under 10, one under 100, and the column says Mbit/s (it read "MB/S" in capitals, which is megabytes). They are right from the next sample of each server.
- **＋ Account type needs only the protocol (the owner: "I only give the protocol, you do the rest"):** the window shows the protocols and one button. The name, the address (the first node of the panel), the port (the first usual one that is free in the core), the WebSocket path or gRPC service, the certificate files (the ones the panel's TLS inbounds use), the REALITY site (the panel's own, else the first usual site the panel finds usable), the group, the host and a user template are chosen for you; the details are folded away - open them to change something, and **Check** shows what was chosen.
- **Three kinds from the owner's own links:** **VLESS · REALITY · Vision** (the xtls-rprx-vision flow, a SpiderX path such as /api, a fingerprint such as firefox) and **Trojan · TCP · TLS · own certificate** / **VLESS · WebSocket · TLS · own certificate**: the panel makes the certificate itself (for the name the network sees, e.g. a pish-data-31.myket.ir), writes it into the core and puts its hash into the link (`pcs`), so there is no domain and no file on the nodes. Tested for real: the links of the throw-away PasarGuard carry flow, spx, sni and pcs like the ones in the owner's list, and a real Xray (25.6.8) reads the config (`Configuration OK`).
- **The list of servers has an IP column instead of "State · last 24 h" (the owner):** the IP (or the address of a node) is a column of its own; the state is still the colour of the icon at the front of the row, and the 24-hour strip is in the Overview of the server.

## 1.53.1 - The account type can bring its user template, every tab's bar is in the top row

- **A kind of account can bring its USER TEMPLATE (the owner: "we only choose the kind and the panel builds it"):** a tick in the window of ＋ Account type ("Also make a user template", with the data limit and the days) makes a fourth step - a template that gives the new group - so that a user of this kind is ONE choice (Users → More… → New user from a template). It is checked with the other three and taken back with them if it fails. Tested for real on the throw-away PasarGuard: the template was made, a user made from it got 5 GB, 3 days and the group, and his subscription held the new link.
- **The own bar of every tab is in the top row too (the owner: "do it in all sections"):** Access (search, count, Backups…, ＋ Account type, ＋ New group), Core (the cores, Deleted…, ＋ New core), Admins (the sections, ＋ Add admin) and Hosts now sit beside the panel switch in ONE row; what does not fit wraps under it instead of being cut off, and the panels keep their size.

## 1.53.0 - One step makes a whole kind of account, the top of the Panel page is one row

- **＋ Account type (the owner: "to make an account we only choose its kind and the panel builds it"):** a button in Access, Hosts and Core opens ONE window. Pick one of eight kinds (VLESS REALITY, VLESS WebSocket with and without TLS, VLESS gRPC, Trojan over WebSocket or TCP, VMess, Shadowsocks), write a name and an address, press **Check** (the three steps are checked together, nothing is written) and then **Make it**: the inbound goes into the core (backed up first), the group holds it (a new one, or one that is there) and the host points at the address. Each piece is read back from the panel; if a step fails the earlier ones are taken back; a core that already listens on the port, a certificate the panel cannot read and a link to an IP behind TLS are said in plain words. Tested for real on the throw-away PasarGuard 5.4.1: all eight kinds were made, a test user got the groups and his subscription held all eight links, then everything was removed and the core was as before.
- **The top of the Panel page is ONE row (the owner):** outside General the bar only switches between the PasarGuard panels, and in the same row come the tab buttons, the search, the filters and the buttons of the tab (Users: search, statuses, Enable / Disable / Days, More…; Hosts: search, inbound, state, count, buttons). Adding, editing and removing a panel is General's alone; General is compact too - its tab buttons and the range of Usage share one row, Check now and Edit are icons.

## 1.52.2 - Usage fixed, the CI no longer eats the free minutes

- **Usage (Panel → General) is repaired:** in 1.52.1 the cards of the new one-screen layout collapsed to their titles. They fill the three columns now, and the tab leaves room at the bottom so the page does not scroll.
- **GitHub Actions minutes (the owner: "do whatever, the limit must not fill"):** the CI workflow ran on every push and every pull request, three jobs each (90 % of the 2,000 free minutes of the private repository were gone). It runs by hand only now (Actions → ci → Run workflow); the same checks run on the server before every release. Public repositories have unlimited minutes: put the triggers back then.

## 1.52.1 - Usage in one screen, a server without tunnels is green

- **Panel → General → Usage is rebuilt (the owner: "compact it all, tidy and unified, so tight that nothing needs a scroll").** On a desktop window the tab is ONE screen: the bar, one row of counters, then three columns that share the height - the usage of the range with its nodes, users over time with the subscription apps, the top users with the lookup. A narrow window stacks them as before.
- **Every menu of a server is like the node's** (the owner): one column, an icon before each item, a line between the groups.
- **A server that no tunnel uses is not marked for its GRE (the owner: such servers are green):** the GRE tag and the yellow icon appear only for a server whose tunnels have a GRE problem.

## 1.52.0 - PasarGuard Nodes, the old Add server box, one-column menus

- **The category is called PasarGuard Nodes** (the owner); the upgrade renames it (migration 18) and the nodes that came by themselves carry it.
- **Add server is the box the Servers page always had** (the server, how the panel reaches it, what to install after) with one more field, the **Tag**; the tag Iran / Foreign says where the server is, the tag PasarGuard Nodes brings the node settings.
- **Every menu of a server is one column** (the window's menu was two).
- **A server that carries no PasarGuard node has its Node section switched off** (grey, with the reason).

## 1.51.3 - A small GRE tag

- **The GRE of a server is a small tag beside its state (the owner):** green when its GRE links answer, yellow when some do not, red when none does; it no longer makes the state pill long (the server's icon still turns yellow for a GRE fault).

## 1.51.2 - Servers, All, a macOS-style choice of profile

- **The section is called Servers again** (it was Lab), the first tab is **All**, the menu item at the end is **Add profile** with a proper plus icon like the other icons of the panel (the bar button stays Add server).
- **The choice of the profile is a segmented control like macOS** (a soft grey track, the open segment raised) with icon buttons beside it - add, rename, delete - in place of the emoji.
- **The colour of a server's icon (the owner):** red only when the server is entirely cut (offline), yellow when it answers but something is wrong (a GRE or tunnel fault, load, no login), green when all is well.

## 1.51.1 - The logos have no background and are sharper

- The winged lion (PasarGuard) and the barbed wire (Marzban) are cut out with a clear background, cropped tight and larger (160 px), so they stay sharp in the tabs, the menu and the rows; the screen blending is gone.

## 1.51.0 - Add server, a + beside the profiles, the logos of PasarGuard and Marzban, a grouped node menu

- **The button is called Add server** (bar of the Lab, the first page, the menu, the box) - the owner. A **"+" beside the tabs** makes a new profile (a category by hand: `POST /api/profiles/tags`, kept in the settings key `lab`, it stays while no server carries it).
- **The logos the owner sent:** the winged lion for PasarGuard and the barbed wire for Marzban, in the tabs, in the menu and on the rows of the nodes (`web/logos/`).
- **The menu of a node is grouped like the owner's picture:** one column, an icon before every item, a line between the groups - Log, Usage, Resources, Latency, Certificate / Edit, Restart, Disable, Resync users, Reset counters / Update the node, Xray, geo files / Off the page / Delete the node (red).
- **A profile (category) can be renamed and deleted** (✎ and 🗑 beside the tabs while one is open; `PUT` / `DELETE /api/profiles/tags/{tag}`): its servers follow the new name, or go to Any when it is deleted; the three categories are the owner's own list from then on. **Every row shows the flag of its server** - the nodes too (the server they run on).

- **The box that adds a profile is one form with no tabs (the owner: "remove these tabs; remove adding several servers at once and Already in the panel").** The tabs *Several at once* and *Already in the panel* are gone (their code too); the Name and the one Tag field stay, with the address and the SSH login of the server below.
- **Choosing the tag PasarGuard brings the settings of a PasarGuard node (the owner: "for the tag put only one box, and when the tag PasarGuard is chosen the settings of a PasarGuard node come").** The panel first (its address and API key or login, when none is connected - the reason is said), then the nodes of it to pick; Back returns to the form.
## 1.50.0 - A profile has a name and a TAG; Marzban, PasarGuard and Iran are the categories

- **One box adds a profile (the owner: "first we add a profile; a box opens and asks its name and its tag - Any unless I choose one, a name of my own is allowed, Iran and Foreign are suggested").** The choice of three kinds is gone: the box has the **Name**, the **Tag** (empty = Any; a list suggests Iran, Foreign and every tag there is) and what the profile is - *A new server* (address, SSH, an optional password), *Several at once*, *Already in the panel* (the servers the panel has, with a search and Select all) and *A PasarGuard node* (grey until a PasarGuard panel is connected). The tag Iran or Foreign says where a new server is; with another tag the panel finds it from the address. A tag is changed from the card's "⋯" menu (**Change tag…**, `PUT /api/profiles/{id}/tag`).
- **Categories (the owner: "make me three categories: Marzban, PasarGuard, Iran").** They are on the page from the start, as tabs of the list and as items of the Lab menu (Any, Marzban, PasarGuard, Iran, then **+ Add profile**); any other tag a profile carries joins them, and one that no profile uses any more goes. `profiles.tag` (migration 17), `GET /api/profiles` has `tags`, `POST /api/profiles`, `POST /api/servers` and `POST /api/servers/add-many` take a `tag`.
- **A PasarGuard panel puts its nodes on the page by itself (the owner: "if a PasarGuard panel was added, a profile is added in the Lab by itself").** When a panel is connected, every node it has gets a profile tagged **PasarGuard**; the upgrade does the same once for the panels that are connected already. A node that is taken off the page does not come back by itself.
- Tests: the tags (free text, Any, the three categories, a tag joins and leaves), a server made, several at once and picked ones under their tag, the nodes of a connected PasarGuard panel and the one-time step of the upgrade, the new box and the menu.

## 1.49.2 - "+ Add profile", the node option waits for a panel, the add flow on a phone, the sheet of UI elements

- **The last item of the Lab list is written "+ Add profile" (the owner)**: the + is in front of its name, where the icon of the other items stands - 1.49.1 had it at the far end of the row.
- **The PasarGuard node option of Add profile is grey until a PasarGuard panel is connected (the owner: "this option is active when we have added a PasarGuard panel").** Asking for it says why and leads to connecting one - the panel's address and its API key, or a username and password - on the same screen the locked Panel item leads to (its button used to say "Lab → Add profile → PasarGuard node", which the grey option would have made a circle); once the panel is connected the option is live and the Panel item opens.
- **The add flow on a phone:** its three tabs (A new server · Several at once · Already in the panel) are one row with short names (New · Several · In the panel), the window uses the whole width, and the list of the servers the panel already has shows each address under its name (a phone hid it).
- Docs: the owner's sheet of UI elements - the buttons, switches, fields, chips and the action row - is kept in `docs/design/ui-kit-components.webp`, and building the whole panel from them is roadmap **#105**, deliberately the last job.

## 1.49.1 - The section is called Lab, the old lists are out of the project, the lock follows the panel

- **The section that was called Servers is called Lab; its list is Any, the kinds, and Add profile + at the END (the owner: "Add profile comes below the profiles; foreign, iran, pasargaurd; the profiles item is called any", then "Add profile at the end of the list, no arrow in front of it, a + after it").** The group of the menu is **Lab**: **Any** (every profile), an item for each kind that has a profile - **Foreign · Iran · PasarGuard**, only while there are two kinds or more, like the tabs of the page (a click opens the Lab on that kind, and the open one is marked in the menu and on the page alike) - and **Add profile +** as the last item: an action, drawn in the accent colour with a + after its name and no arrow; it is no tab and never the open one (an "act" of the tabs of a page; the bar of a page draws it as a button). A click on it opens the choice of what to add.
- **The old Foreign and Iran lists are out of the project (the owner: "take them out of the project, but let us have them in case we want them somewhere else").** No tab, no page and no code of them is in the panel any more (the Servers page, the Add-server form, "Several at once"); a copy of the lists as they were is kept outside of the project. A shortcut to a server (the dashboard, a diagnosis, the chip of a node) opens its window on the Lab; "Add to servers" of a node outside our servers opens Add profile with its name and address filled in (it finds from the address whether the server is in Iran or abroad).
- **Nothing moves the servers the panel already has onto the page (the owner: "I did not say to move the cards, I only said to bring the information of the servers").** The page starts empty. **Add profile → Iran server / Foreign server** opens on **Already in the panel** when there are servers that are not on the page yet: their name, address and provider, with a search and **Select all**; the first page and the two cards say how many there are. A server that is *made* - a new one, several at once (one per line: *name address [user] [port]*, one password for all), the API, the importer - is on the page at once.
- **The test servers of 1.49.0 are off the page** (the servers, their tunnels and everything on them are as they were: a profile taken off the page only unlists the server, the panel still watches it).
- **The lock of the Panel item follows the panels, not the nodes (the owner: "we have a panel now, it must not be locked; the lock is for when no panel is connected").** The item is grey with a lock only while the list of panels is empty and opens at once when one is connected (also from the add flow); the message says no panel is connected and its button leads to the PasarGuard flow.
- Tests: no step of an upgrade puts servers on the page; the menu (Add profile as an act, the old lists gone, no archive marks, the lock follows the list of panels), the tabs of Add profile.

## 1.49.0 - Profiles and the Lab page, one "⋯" menu, the section is called Network

- **Profiles, from the very start (the owner: "instead of choosing the Iran and the Foreign tab, a profile - made first - and the add-profile section has Iran server, Foreign server, PasarGuard node; and a tab for everything").** A profile is one thing added by saying what it is. A new table `profiles` (`services/profiles.py`, `api/profiles.py`): an **Iran server** or a **foreign server** points at a server of ours (the kind must fit the server: an Iran server is not added as foreign), a **PasarGuard node** at a node of a connected PasarGuard panel; nothing is copied and nothing is changed on a server or a panel. Deleting a server or removing a panel takes its profiles with it. `GET/POST /api/profiles` (several at once; what does not fit is skipped with the reason), `DELETE /api/profiles/{id}`, `GET /api/profiles/strips`, `GET /api/servers/{id}/uptime`.
- **The Lab: the first tab of Servers, to try the new design with your own servers.** The old **Foreign** and **Iran** lists stay, marked **archive** in the menu and the tab bar, and work exactly as before (a shortcut to a server - from the dashboard, a diagnosis - opens the tab of its role). The Lab: the tabs **Any · Iran · Foreign · PasarGuard** with their counts - a tab of a kind is there only while a profile of that kind is, and the bar only when there are two kinds or more (nobody is made to have a PasarGuard node); **the cards and icons of the old list** (the flag over the name, now for Iran servers too; the server with its two lights, a node drawn the same way) with **new columns**: *Profile* (name, address, provider), *Serves* (the nodes that run on the server, its tunnels, the panel of a node and the server it runs on), *State* (one soft pill - Online, Offline, No login, CPU 91 % +1, Node ✕, Disabled ... - and **a strip of the last 24 hours** read from the samples: green answered, amber now and then, red silent), *Load* (CPU bar, RAM, disk - or what the node reports) and *Traffic*. The fluid layer drops the less important columns when the box is narrow, and a phone keeps Profile, State and Load.
- **Add profile → Iran server / Foreign server / PasarGuard node.** A server: a new one (name, address, SSH user and port, an optional password that is used once to put the panel's key on it) or one the panel already knows (a list with search and ticks). **A PasarGuard node first asks for its panel:** one that is connected, or a new one with its address and **an API key** (made in the dashboard under API Keys) **or a username and password** (used once to get a token, forgotten unless "keep the login" is ticked); the credential is tried on the panel before it is kept; then the nodes of the panel are listed to pick (or "a new node in the panel…").
- **One "⋯" menu in place of many buttons (the owner's picture: Overview, Rename, Power off ... Delete in red).** On every card, in the header of a window and over the Node view; a long one is in two columns and stays short. A card: Overview, Check now, Console, Edit, Reboot, Off the page, **Delete server** (red). A window: the access tools, the tests (GRE, filtered in Iran, Abuse-Defender), the installs, reboot. A node: Log, Usage, Resources, Latency, Certificate, Edit, Restart, Disable / Enable, resync, reset counters, update the node / Xray / geo files, **Delete the node**. The eight buttons of the Node view are hidden in the Lab (they are in the menu).
- **A window of three sections:** *Overview* (the state in plain words with the button that fixes it, System / Resources / Access, the strip, the nodes that run here, the last results), *Node* (the Node view of PasarGuard, and the traffic of the last 7 days and the users online of the last day), *Monitor* (the strip over 1 h - 7 days and the charts). A node profile opens the Node view.
- **The Panel item of the menu is grey until a PasarGuard profile exists (the owner: "for now full compatibility with the PasarGuard panel; maybe other panels later")**, shows a small lock and says so when it is clicked (a button takes you to the add flow); every way into the Panel passes the same check; the public demo is not locked.
- **The section Tunnels is called Network (the owner), and CDN is its first tab, above the Tunnel manager.** Every shortcut that used to reach the Tunnel manager by being the first tab now names its tab.
- **DNS page:** the big "Add an account" button of the empty page is gone and the round "+" beside Accounts is a button that says **＋ Add account**.
- Tests: the typed profiles (kinds, the kind must fit, duplicates, nodes from a panel that knows them, cascades, strips), the menu (Lab first, archive marks, Network, the lock), every handler of `lab.js` calls a function that exists.
## 1.48.3 - The stories name what they tell

- The story of an update, of making a node and of an install ends with "Update finished" / "Making the node did not succeed" / "Install finished" instead of the "Repair ..." sentence of the Tunnel Doctor.
- The plan of "Make a node" no longer says "The panel accepts node software from v1.0.0 on" (see 1.48.2).
- Docs: the detached update of a panel was run for real on a test server against a fake ``pasarguard`` command (written in 1.48.0's entry and in roadmap #103).
## 1.48.2 - The Node view says nothing it does not know

- **Fixed (seen on the real panel):** the Node view marked every node **"old"** because it compared the node's version (0.5.4) with the panel's `min_node_version` setting (v1.0.0), which the real panel keeps while its nodes run 0.5.4 and are connected: the mark and the row "Panel accepts" are gone, and so is the sentence "The panel accepts node software from v1.0.0 on" in the plan of "Make a node" (the panel keeps that number for nodes that run 0.5.4 - it says nothing the operator can act on). The software and the Xray core of a node are two rows now (a long value was cut at its left end: "Xray 26.3.27 node 0.5.4 old"), and the core config moved to the Traffic box.
- The "Nodes 2 / 2" window of the Panel bar finds its panel whichever way the list of panels was kept.
## 1.48.1 - The rest of the tabs of Tunnels, and no panel-supplied name inside a button

- **The tabs of Tunnels that are switched off are in the menu again (the owner: "when we click Tunnels show the rest of the tabs"):** the group of Tunnels shows **Tunnel manager · CDN · Tunnel Doctor** and **Outbound** in grey (switched off for now, like the Shop bot), where 1.47.0 left the grey one out. A click on it does nothing; the groups of Servers, Panel and Settings have no tab that is switched off.
- **A name that comes from a panel is never put inside a button's script:** `esc()` turns a quote into an entity and the browser decodes an attribute *before* it reads the script in it, so `onclick="f('${esc(name)}')"` with a node called `x');alert(1);//` would run code. The buttons of the node views, the list of nodes outside our servers, the Settings update card and the old node log button of the Panel bar now carry numbers (ids, the number of a row) and look the name up when they run. A static test reads the three files and fails on the pattern.
- The version cells of the nodes in Settings → PasarGuard stay inside their column.
## 1.48.0 - Foreign and Nodes are one list: the node and everything about it are in the window of its server

- **One row for every server, and the node with it (the owner: "merge Foreign and Nodes into one list, each foreign server one row, the node's data and ALL its operations in the window of the server"):** the **Nodes tab of Servers is gone**. The Panel column of the list names the panels whose node runs on the server; a click on that name (or the new **Node** button of the window) opens the window of the server on its **Node view**:
  - three boxes of six rows like the rest of the window - **Node** (panel, state, address, API port and type, Xray and node version with a mark when the node is older than the panel accepts, core config), **Traffic** (since the reset, all time, data limit, coefficient, keep-alive, the oldest node software the panel accepts) and **Reported by the node** (CPU, RAM, speed, how long it runs, how it was matched with the server, when it was read) - and the sentence the panel says about a node that is not connected, in red above them;
  - one bar with **every operation**: Log · Usage · Resources · Latency · Certificate · Edit · Restart · Disable / Enable and **More…** (resync users, reset counters, update the node, update Xray, update the geo files, delete). The same questions as before are asked first and the panel's refusals are shown in the window;
  - **the log, the usage, the curves, the certificate, the edit form and the "make this server a node" plan open INSIDE the window with a Back link** (no second window over the first); a change that worked goes back to the node by itself;
  - a server that carries nodes of several panels has a chip for each; a Marzban node shows its state and the way to its panel; a foreign server that is no node yet has the offer **Make a node…** (with a choice of panel when there are several).
- **Ticks and a Nodes… menu on the list:** once one server carries a PasarGuard node, every server has a tick in front of its name and the bar has **Nodes…**: *Ticked servers* (restart, enable, disable, reset the counters, update the software, delete the nodes - one question for all of them, one call to each panel), *Panel …* (add a node by hand, users online over time, backups of the nodes, restart every node, clear the usage data) and *Servers* (tick every server with a node, **Nodes outside our servers**). The menu is amber when a node of one of your panels runs on a machine that is none of your servers.
- **Nodes outside our servers** - a window of their own with the same Node view for each node, "Add to servers" (the Add server form opens with its name and address) and "Add a node by hand". The bar of a panel (Panel → "Nodes 2 / 2") lists the nodes of that panel, each beside its server.
- **The list shows a change at once:** reading the node list of a panel (what the Node view does after every change) also refreshes the copy the Servers page is drawn from, so a node that was deleted or switched off is not shown as it was for the next two minutes. `GET /api/servers/panel-nodes` has a new `outside` list and the Xray version of each node.
- **Menu (the owner's screenshots):** the settings of the panel are **General** now (it was "Panel settings"), the **first** tab of Panel, with a cog wheel instead of the sun; **the menu is wider** (246 px instead of 218) and a label is never cut by its edge any more - the whole menu was one column of 214 px in a box of 192 px, and the end of "Tunnel manager" was hidden.
- **Settings → PasarGuard: the update of the nodes and of PasarGuard itself (the owner: "add the update of the node and of PasarGuard to Settings").** A new tab of Settings (and a line of its menu) shows the **newest releases** of PasarGuard, its node and Xray from GitHub (asked once an hour, **↻ Check** asks now) beside what everything runs, and marks what is behind (a node that reports no version - it is not connected - is never called up to date):
  - **Panels:** each PasarGuard panel with its version and **the server that runs it** (found by the address of its URL among your servers, or through the DNS). **Update…** shows the plan, needs the name of the panel typed back, and runs on that server the command you would type over SSH: a backup first (`pasarguard backup`, when that version has one - a backup that fails stops everything before a change), then `pasarguard update`; the panel then waits until the panel answers again and tells the version before and after, the users and how many nodes are connected. A server that is not in the Servers list is not touched (the line says to add it); nothing runs while the panel is read-only.
  - **Nodes:** one row each (node, server, software, Xray, the version it runs → the newest) with **Update** and **Xray**, and one button for every connected node that is behind. Several nodes are updated **one at a time**: each must be connected again with its new version before the next, and one that does not come back stops the run - the nodes after it stay as they were; a node that is not connected is not touched. Every update is an action with a story you can follow (and read later in the history of the Tunnel Doctor).
  - Written and tested against a fake panel, a fake server and a throw-away PasarGuard (the plan, the refusals and the first, read-only look at a server were run for real). **The detached update was run for real on a test server against a fake `pasarguard` command** that logs its calls: backup then update (`help, backup, update`), a backup that fails stops before the update (`help, backup`; the story says so), no backup asked for (`update`) and a command that has no backup (`help, update`). **The update of a panel was not run with the real command on a real PasarGuard host yet** - none is in the Servers list.
- Tests: the placement of nodes (outside, hints, DNS), the copy refreshed by a read, the order and the icon of the tabs, no Nodes tab left, and every inline handler of the new files calls a function that exists.
## 1.47.0 - The menu opens like PasarGuard's, Panel is one page of tabs, the user window is tight

- **Every section that has tabs is a group in the left menu (the owner: "design a dropdown for the other sections too"):** **Servers** (Foreign, Iran, Nodes), **Tunnels** (Tunnel manager, CDN, Tunnel Doctor), **Panel** (below) and **Settings** (Security, Alerts, Backups, Probe, AI, Shop bot, Appearance, About & update). The little arrow at the right of the name opens and closes the list; the name opens its page and the list; a page opens its group by itself and the group closes again when another page is opened (a group you opened with the arrow stays open, and the browser remembers it). While a group is open in a wide window its tabs are in the menu and the bar of tabs under the header is not drawn (it would say the same twice); when the group is closed, in the rail and on a phone the bar is there as before. Every list in the menu is made from the tabs the page already has, so a new tab shows up in both.
- **Panel is ONE page with a tab for everything (the owner: "when we click Panel, show everything as tabs"):** **Users · Hosts · Access · Core · Admins · Panel settings**. *Panel settings* is the old Panel page (usage, system and the settings of the panel, with its own three tabs), renamed as asked. Users, Hosts, Access, Core and Admins are not pages of their own any more but these tabs: an old link, a reload, a link from another window and the search of the dashboard open the right tab (`go("pgcore")` and the others still work). The bottom bar of a phone has 6 items instead of 11, the rail 6 icons, the tabs are the bar under the header.
- **Fixed: the card of a menu item no longer moves or grows under the mouse.** It slid 3 px to the right, under the edge of the menu, and the arrow of a group stayed where it was (the owner's screenshot, light theme). The name of an item is a tooltip only in the rail, where its words are not on the screen.
- **The window of a PasarGuard user, rebuilt and tight (the owner's screenshot: it was taller than the screen):** the buttons (Reset usage, New link, Disable / Enable, Next plan, Delete) are in the header; *Account* is two columns of 30 px fields with the usage beside it; *Next plan* and *Groups* (chips to tick) are one row; the activity list is short. 583 px instead of about 850, no inner scroll at 1000 px; under 700 px the boxes stack, on a phone it is one column; nothing was left out (on hold, next plan, devices limit, owner, formats, devices, fetches, online now).
- A static test reads the menu, the tabs of the Panel page and the tabs every `pg_*.js` file registers: a tab that no tab of the Panel page holds would be unreachable and fails the suite.

## 1.46.1 - The left menu follows PasarGuard: Users, Hosts, Access, Core and Admins are under Panel

- **The sections of the panel are one group, like in the sidebar of PasarGuard itself (the owner's request, with a screenshot of PasarGuard's Nodes group):** **Panel** is the head - it opens the Panel page (usage, system and the settings of the panel) - and **Users, Hosts, Access, Core, Admins** hang under it on a thin line. The little arrow at the right of Panel opens and closes the group (the choice is kept in the browser); opening any of the sections opens the group by itself, and while the group is closed the head shows that the open page is one of its sections. In the rail (a window under 1180 px) the six are icons in one quiet box, and every item of the menu has its name as a tooltip now (the rail had none); on a phone the bottom bar is the same flat row as before. The label "PasarGuard" over the old list is gone. Checked in a wide window, the rail, a phone, the light theme and Persian (right to left).
- A new static test reads the left menu and the titles of the pages: every item of the menu has a title, and the group holds exactly the sections above.

## 1.46.0 - The last routes of PasarGuard, and a check of the architecture

- **Every route of PasarGuard that had no button has one now (roadmap #99 is closed; the owner asked for the panel to be in step with PasarGuard):**
  - **Admins - several at once:** a tick box in the first column and one menu in the bar, "With the N ticked…": switch them on, off (never the admin Nikatel is signed in as), reset their usage, switch their users off or on, delete all their users (the word `delete` is typed, and a record of each admin is saved first). They are the panel's own bulk calls (`/api/admins/bulk/*`): all of the admins or none.
  - **Cores and client templates - delete several in one call:** tick them and press "Delete N selected…" (never the first core; a system template and the last template of a type are refused; for cores the question lists what they take with them - nodes, hosts, groups - and with warnings the word `delete` is typed; each is saved here first, "Deleted…" and "Backups" make them again). The API keys list uses the same tick box now.
  - **Users → More… → Shadowsocks method…** sets the cipher of many users in one go (`aes-128-gcm`, `aes-256-gcm`, `chacha20-ietf-poly1305`, `xchacha20-poly1305`) for whoever the same filter as the date and limit shifts matches - counted first, applied after a question. Each user keeps its own password; the clients take the new method when they fetch the subscription again.
  - **The user window has a Formats tab:** what the subscription of this user answers in each client format (links, links in base64, Xray, sing-box, Clash, Clash Meta, Outline - as text to read and copy; WireGuard is a zip, so its size is shown) - to see why a link does not work in one app.
  - **Panel → Usage has two new cards:** **Users over time** (how many were online, how many expired, how many ran out of data, per hour or per Tehran day, for everybody or one admin) and **Subscription apps** (which apps fetch the links and their share). Side by side from 1100 px, one under the other below.
  - The usage of one user is asked by id (the by-username routes of PasarGuard are deprecated and go away in 6.0).
- **A check of the architecture, module by module, after the many additions of the last days:** the layers (services work without HTTP, the API is thin, the web files only call the API) were checked by a script that reads every import. Two things were in the wrong place and are moved, nothing else changed: the **cache of the DNS records** lived in `api/dns.py` and a service imported it (now `services/dnscache.py`, the one copy that the DNS page and the Outbound page share), and **`gate.py`** (the secret entry path) sat in `api/` although the application's own entry point uses it (now `nikatel/gate.py`, beside `auth.py`). The architecture test knows both.
- **Tables:** a column header can say its own importance (`data-prio="0"` = never drops). Needed because the name column is the second one when a tick box comes first - the key column of the API keys list was the first to drop in a narrow window.
- **Checked against the PasarGuard API:** every route of its OpenAPI document was matched with what this panel sends - by running the whole test suite with a recorder on the calls to the panel and every new write against a throw-away PasarGuard 5.4.1. The matrix and what is left out on purpose: `docs/pasarguard-coverage.md`.

## 1.45.0 - PasarGuard has its own place in the left menu

- **A PasarGuard group in the left menu (roadmap #102, the owner's wish: the panel follows the structure of PasarGuard):** the ten tabs of the old Users page are pages now - **Users**, **Hosts**, **Access** (groups and templates), **Core**, **Admins** (admins, API keys, roles) and **Panel** (usage, system and the settings of the panel). The bar of the panels (which panel) is on top of each page, and picking another panel keeps the page. A part that a type of panel does not have (a Marzban panel has no groups) says so and asks for another panel. In the rail (under 1180 px) the group is a thin line, on a phone the bottom bar scrolls sideways (the open page stays in view), and in a short window the menu scrolls inside the side bar so that Sign out stays in view.
- **Nodes are a tab of Servers:** Servers → Foreign · Iran · **Nodes** shows the nodes of the panel you pick beside our servers (the old Nodes tab; the nodes of a Marzban panel are listed there too); a click on the panel chip of a server opens it there. **The window of a server has a line for its nodes** - which panel, its state, "Open in Nodes" - and, on a foreign server that is no node yet, "Make a node…".
- **Fixed: the side bar of a narrow desktop window.** Under 820 px an old rule of `app.css` put the menu in a row inside the 56 px rail, and its icons were 2 px wide (the owner's screenshot at about 600 px); the rail is a column again in every window narrower than 1180 px. The number cards of a server window are wide enough for their words (the end of "peak 32.7%" was cut).

## 1.44.0 - The whole panel shrinks with the window and stops where it would break

- **The owner's rule, built (roadmap #101):** a narrower window makes the boxes smaller; **rows stay rows**; a bar of buttons, chips or tabs is **one row** that shrinks (the words of a button go first, then the labels in front of numbers, then the extras); a table keeps its columns, cuts a long text with "…" and, when the columns still do not fit, drops the **less important ones** (the first column, the status and the buttons at the end last of all). Nothing is turned into cards any more, on a phone too. The old card layout stays as a fallback: `?fluid=0` once (or `localStorage nk.fluid = 0`) brings it back.
- **The lock (the owner's idea):** the page shrinks while everything still fits, down to 560 px, and keeps that width in a narrower window - it scrolls sideways instead of laying texts over each other; a window keeps its size under the lock too. A phone (a touch screen up to 760 px) is not locked: it has its own fluid layout (`web/mobile2.css`).
- **A table looks at its box, not at the window** (`web/fluid.js`): every column has a wished width (the colgroup), a least width (its header decides, or what its buttons need, measured) and an importance (its header). The columns that never drop come first, the others in the order of their importance, each if its least width still fits; then every column gets its wished width, or is shrunk towards its least. So the same table is right in the page, in a window, with the side bar as a rail. Found on the way: under 760 px the old CSS gave every table a width of 720 / 860 / 900 px and made it scroll sideways inside its box, and the new PasarGuard tabs had text columns of 0 to 9 px at 820 px (Cores, Templates).
- **Windows:** the header of a window goes on a second row when it does not fit - the switch of a server window no longer lies over its name (the screenshot of the owner); under 1000 px the words of its buttons are gone (the tip stays); **the server window has no role tag and no host name in its header** (the flag says it, and the line went under the switch); the number cards of a server or tunnel window and the three boxes of a server window fit at every width (they were squeezed to two columns on a phone); the user window and the password row of an admin no longer scroll sideways; the Doctor's rows keep the row (the reason, the servers and the state drop by the width of the list); a Settings row with a field and buttons wraps; the pills of a group or a template are counted again after the columns change.
- Small things on a phone: the activity filter is one row that scrolls, the small buttons of a row have a hand-sized target, a switch row of Settings no longer touches its text.
- Tested by measuring every page and window (servers, tunnels, the Doctor, CDN, Settings, every PasarGuard tab and its windows, the forms) at 375, 560, 600, 700, 820, 1000 and 1280 px, on a touch phone and on a narrowed desktop window: nothing wider than its box, no cut text without "…", no two boxes laid over each other.

## 1.43.2 - The Add server button has its name

- The round blue ＋ of the **Servers** page says **Add server** beside the plus while there is room (a window wider than 1000 px); in a narrower window it is the round plus alone again, as before. The other add buttons (Add panel, Add account) stay the plain round plus.

## 1.43.1 - The phone is a phone again

- **Fixed (a mistake of 1.43.0):** the new `fluid.css` (the narrow-window layer that is still being built) was meant for windows that are not phones, but its first rule, `min-width: 560px` on the page, was not fenced: on a phone the page was laid out 560 px wide and shown small. The whole file now applies only to "not a phone" (a touch screen up to 760 px keeps `mobile.css` alone, as before 1.43.0). Found by measuring the phone layout (375 px) page by page.

## 1.43.0 - Every section of PasarGuard, Configs off, Panels is called Users

- **Configs is switched off and not shown** (menu, dashboard, search) until the project grows; one flag brings it back (`CONFIGS_UI` in `web/app.js`). The **Panels** section is called **Users**. Add server, Add panel and Add account are the same round blue ＋.
- **PasarGuard from one place, now every section of its API** (the route matrix: `docs/pasarguard-coverage.md`, roadmap #99):
  - **Add server has a tick "also a node of PasarGuard"**: the server is made, and when it is installed the node is added to the chosen panel (or the node is made on its own from the Nodes tab); the Servers page says which server is a node of which panel, and deleting a server asks about its node. A node of the panel that is not in the Servers list has an "add to servers" link that opens the Add server form with its name and address filled in.
  - **Nodes**: tick several and enable / disable / reset / reconnect / delete them (a delete saves each node first), the usage curve and the latency of a node, the user counts over time, clear the stored usage by table and date range, reconnect every node.
  - **Core**: make a core, duplicate it, rename it, delete it (core 1 is never deleted, a core that nodes run is refused with the nodes named, a deleted core can be made again); **add, change or delete ONE inbound from a form** (protocol, network, security, port, path, TLS files, REALITY with a fresh key pair whose public half is shown once) with a dry run first; a REALITY scan of a destination.
  - **Admins → Roles** (permissions no / yes / own / all, limits in GB and days, access, device rules, who has the role; the owner role is read only, built-in roles and roles that admins hold are never deleted) and **Templates → Client templates** (Clash, Xray, sing-box, User-Agent lists; the panel checks the content and its sentence is shown), both with backups and a restore. A new **System** tab (version, resources, user counters).
  - **The whole user**: status, data limit, reset period, end date or **ON HOLD** (days after the first connection, a start-anyway day), devices limit, groups, the **NEXT PLAN** (from a template or its own data and days, "start it now"), the owner, and the activity: the devices the user used (remove one or all), the last fetches of the subscription, where the user is online now. The Users tab has a **More…** menu: for the ticked users set an owner, apply a template, reset usage, make new links, delete; for many users **shift the end dates or the data limits** of whoever a filter matches (statuses, groups, an admin, names, an end-date range; the dry run only counts, the real run asks), **clean up** the expired, limited, on hold or disabled users (listed first; the state is typed back to delete), and **new users from a template** (one, or up to 500 with random or numbered names; the links come back).
  - **Hosts**: every field of a host and the advanced settings (HTTP headers, transport, mux, fragment, noise, ECH, final mask, WireGuard overrides, the subscription template) as JSON that is checked key by key, a new host, clone (the copy is switched off), delete, enable / disable many, and **Backups** that make a deleted host again.
- **Found by running every write against a real PasarGuard 5.4.1 (a throw-away one in docker) and fixed:**
  - the old host editor, the clone and the TLS copies sent a host back as it was read, and the panel **reset the xray mux's xudp settings** (read with `xudpConcurrency`, written with `xudp_concurrency`);
  - **a host clone and the TLS copies never made anything on PasarGuard**: `POST /api/host` without the slash only gets a 307 and the empty answer was taken for success; a redirect is an error now;
  - **the TLS copies skipped every PasarGuard host** (they looked for the inbound in `/api/core/config`, which only Marzban has) and sent `alpn` as a text (422);
  - **an edit of a user deleted its next plan** (the panel's rule: no `next_plan` in the body = none wanted) and an empty end date kept the old date; edits send the plan the user has and `expire: 0` now;
  - the panel drops an unknown key of an advanced setting without a word: the keys are checked here; a reset of the usage deletes the next plan (the windows say so first);
  - the Hosts list did not read the panel again after a save, a clone, a delete or an enable / disable.
- API: `GET /api/servers/panel-nodes`; under `/api/panels/{id}/pg/`: `roles`, `client-templates`, `system`, `nodes/bulk` · `reconnect-all` · `clear-usage` · `user-counts` · `{nid}/stats` · `{nid}/latency`, `cores` (template, create, deleted, delete-check, meta, inbounds/dry-run, inbounds/apply, reality-scan), `user/{username}` (+ action, devices, sub-updates, online), `user-tools/*`, `hosts/*` (docs/api.md).

## 1.42.0 - A panel is connected by an API key or a token, not by a password

- **Panels → Add panel** asks for the panel's **API key** (PasarGuard: the *API Keys* page, `pg_key_…`, sent as `X-Api-Key`) or an **access token**; "Sign in once" gets a token with a username and password and **forgets the password**. The credential is tried on the panel first; nothing is saved before the panel accepted it. Found while building it: PasarGuard 5.x has real API keys (their own end date, revocable one by one); Marzban has none, its token lasts as long as `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` says (694 days on the real one).
- **Edit** shows how the panel is connected (key name, the admin and role it acts as, when it ends), replaces the key / token (tried first; the old one stays when the panel refuses it) and has **Keep only a token / Forget the saved login**: it wipes a stored password once a key or a long token is in place, and is refused with the reason for a 24 h token. The bar of a panel has a small mark (`API key`, `token`, `saved login`; amber / red when it is about to end). A key or token kept without a login raises an event before it ends (below 7 days, below 1 day, ended or revoked), once per level; the panel is asked about its key every 12 hours and by *Check now*.
- Panels that were added the old way keep their saved login untouched; the owner decides when to switch.
- **Add is only a plus, remove is a faint cross** (CDN accounts, Panels, Servers): "Add account", "Add panel" and "Add server" are a round ＋ (the name is in the tooltip); on the Panels page the button keeps its name, "Add panel", only while no panel has been added yet. Every account / panel box has a faint ✕ at its right end and every server row at its end, nearly invisible and red under the pointer. It asks first, then takes the account / panel / server off the list (nothing changes at the provider, on the panel or on the server).
- **Selection boxes** of the Users and Hosts lists (every row, the "select all" of the header, the master of a port) are checkboxes again: a rounded square, blue with a white tick when ticked, a dash when only some are (1.41.2 had made them switches: a switch says on / off, a box says picked).
- **PasarGuard from one place (roadmap #94).** A PasarGuard panel gets seven more tabs beside Users and Hosts: **Groups** and **Templates** (make, edit, delete, bulk, give or take groups to users, a user from a template; every change is saved to a file first, with Undo and a restore), **Nodes** (the nodes of the panel beside OUR servers, matched by address or DNS; Log, Edit, Usage, Certificate, Resync, Restart, Disable, Update; "Make a node" on a foreign server: a plan, then install, certificate, add and wait until it is connected), **Core** (the Xray config as text with a live diff, a dry run against the panel, save with a backup and a read-back, restart the nodes, backups and a rollback), **Admins** and **API keys** (create, edit, password, switch off, delete with what happens to its users; keys with a permission matrix, the secret shown once, renew, delete; the key this panel itself uses is protected), **Settings** (the 7 sections as forms, secrets only "set / not set", a dry run, history and restore) and **Usage** (counters, the usage of all users by node and Tehran day with Jalali dates, one admin or user, top users; also for Marzban). Every tab is a route first (`/api/panels/{id}/pg/…`, docs/api.md), reads from the panel only when opened and never shows a secret. Five areas were built in parallel (about 290 tests); each write was tried on a real PasarGuard with test objects only, then removed.
- Found on the way and fixed: the node installer (`pg-node.sh install`) takes every answer as a flag now (its questions had changed order; the node key must be a UUID); the old node Log button hung for ever on PasarGuard (an endless stream: it is read for a few seconds now); saving a PasarGuard core needs `restart_nodes` (the panel answered 422 without it); the stream reader sends an API key in `X-Api-Key`.
- API: `POST /api/panels` takes `token` (or `username` + `password` = sign in once, `keep_login`), `PATCH /api/panels/{id}` takes `token`, new `POST /api/panels/{id}/forget-login`; every panel carries an `auth` object and never a secret; `POST /api/panels/{id}/check` also asks the panel about its key. Tested on a real PasarGuard (owner-level key: added, read, converted from a login, login forgotten) and a real Marzban (694 day token). See docs/panels.md.

## 1.41.2 - Switches in the Users and Hosts lists

- The square tick boxes of the Users list (every row and the "select all" of the header) and of the Hosts list (every host and the master of a port) are the same switch as on the Outbound page: a blue pill with a white knob when on, a dark slate pill when off. What they do is the same: the buttons Enable, Disable, "＋ Days…" and "🔒 TLS copies…" light up as soon as something is on.

## 1.41.1 - Two bugs of the Doctor's moves found by the real test; the Users list lives

- **Found by the real test of the Doctor (an Iran server, a foreign server and a second Iran server, a real ArvanCloud record, a real tunnel moved there and back):**
  - **Return now left the DNS behind.** A DNS record is tied to a tunnel through the probe configs that use its name; a record no config names was never re-pointed in a move of ONE tunnel, so after "Return" the record still led to the server the tunnel had left - and when the old tunnel was removed the users got only errors. A record is now also tied to the tunnel whose port is its ORIGIN port (the CDN records of the real setup have the tunnel port as origin port).
  - **A second move could break the tunnel it replaces.** A moved tunnel keeps the name it was built with on the servers (a rename changes only the panel), so the next move built the new tunnel under the same server-side name: on the foreign server they shared a GRE interface and a unit. The new tunnel now takes a different suffix (-mv, then -mw, then -mv ...).
  - Measured for the first time: a move while the old server is on loses no request (169 of 169 answered through the CDN record during the move). Left open: about 30 seconds of errors right after the old tunnel was removed, 7 minutes after the DNS change - the CDN keeps using the old origin address a little longer than the TTL says (roadmap #95).
- **Users list**: the user count chip and the refresh button are gone. **Live**: a new account made in the panel shows up by itself. While the Users list is open and the page is on screen, ONE small question (the newest user and the total, limit 1) is asked every 15 seconds; the list is read again only when something is new, it is the first page, and nothing is searched, filtered or ticked - otherwise a notice says which account is new.

## 1.41.0 - TLS copies of the plain WebSocket hosts

- **Panels → Hosts → "🔒 TLS copies on 443…"**: tick the plain vless / vmess / trojan WebSocket hosts of a Marzban or PasarGuard panel, and every one gets a COPY with port 443, security tls, SNI = its Host header (the CDN name), alpn http/1.1 and fingerprint chrome. Nothing is changed on the nodes or the tunnels: the CDN (ArvanCloud) ends the TLS and sends the same plain WebSocket on to the same port as before, when HTTPS is on for that domain.
- **A real test decides.** Before a copy is made the panel tests it from its own server: a TCP connect to the address, a TLS handshake with the SNI (the certificate must be valid for it), then a WebSocket upgrade on the inbound's path. The answer says where it stopped (dns, tcp, tls, cert, ws). A copy that passes is ON, one that fails is made OFF (or not made: your choice). The originals are never touched, so users keep both links and you switch the plain ones off when you are sure; the hosts are saved to `host-backups/` before every change and "Remove the copies made earlier" takes the copies away again.
- **The top of the Panels pages is the bar of the CDN page**: a row of chips (one per panel, and the dashed "＋ Add panel" chip) and a row of facts (status, users, nodes, traffic) with Check now and Edit. The "All panels" chip was taken away (its page is still in the code), and the status buttons of the Users list (All, Active, Disabled, Limited, Expired, On hold) are one drawer.
- **Found by the real Doctor test:** "Return now" was refused for two hours after a move (the cooldown of the automatic moves also held the operator's button); it goes through at once now.
- The test is made from the panel server, not from inside Iran: it does not show what the Iranian filter does with the SNI, so give a copy to a few users first (roadmap #93 keeps that open). API: `POST /api/panels/{id}/hosts-tls/plan`, `/hosts-tls`, `/hosts-tls/remove` (docs/api.md).

## 1.40.3 - Outbound: no empty corner, small switches

- **The left column is full.** The Advanced and CDN-records bars moved up into the left column (under the configs, the CDN addresses and a new Relay card), the server lists are taller, and both columns end on the same line; nothing is left lying at the bottom of the page and no empty space is left at the top left.
- **The switches have one small fixed size** (40 x 22) in every list of the page and look like the reference picture: a blue pill with a white knob when on, a dark slate pill when off.
- **Step 3 is a box of a fixed size**; when there are many addresses the list scrolls inside it. **The paste box for the configs is one line high** (the Add button sits inside it) and grows when you paste several lines.
- **Outbound is switched off in the menu for now.** Its tab is the last one of Tunnels, greyed out like the Shop bot, so the important work comes first (roadmap #92). The page, the API and the tests stay as they are; one mark in `SUB.tunnels` brings it back.
- Fixed a test that failed by chance when two calls fell on different seconds (the Doctor's suggestion id carries the second).

## 1.40.2 - Outbound rebuilt after the reference picture

- **Each server list is a pane**: a header with its icon (From: Iran servers, To: foreign servers), a search box ("Search server or address...") with a filter button (show only the ticked ones), a list with a select-all box, columns you can sort (Server, Address), an online dot, an Xray / relay mark and a "..." menu on every row (copy the address, take the outbound off a server, use a foreign server as the relay). A small divider with a handle sits between the two panes. A server that the search hides stays ticked.
- **The ports row** has its icon, the "Use 80, 8072, 8075" suggestion, and Start and Check now beside it. The paste box for the configs is one box with the Add button inside it; the step 3 note is a callout; the addresses for the CDN have a copy icon on every row.
- **Advanced and the CDN records are bars** with a chevron, an icon, a bold title and a quiet hint, instead of the bare "▶ Advanced" lines. On laptop widths the right column is wider so the addresses are not cut.

## 1.40.1 - Outbound moved to Tunnels, wide layout

- **Outbound is now Tunnels → Outbound** (next to the Tunnel manager, the CDN and the Tunnel Doctor), because it makes tunnels; the old address of the page (#gateway) still opens it. The Panels page keeps Configs and Panels.
- **A wide page, the boxes side by side.** Step 1 (your configs) and step 3 (the addresses for the CDN) are on the left, step 2 (tunnels) on the right, so no empty space is left at the sides; below 1280 px they stack. The Iran and foreign server lists look like the Servers page (one box, a sticky header, a row per server; a click on the row ticks it) and scroll inside their box. The addresses for the CDN are a table with a Copy button on every row.

## 1.40.0 - Outbound on one page; one account on many panels (M12)

- **Outbound is one page with three steps** (the Setup and Users tabs are merged). 1 **Your configs**: paste them. 2 **Tunnels**: tick the Iran servers (From), the foreign servers (To) and type the ports; Start makes a tunnel through your configs for every Iran server x foreign server x port (an Iran port that leads, through the exits and the relay, to the foreign server's port), picks the relay by itself (the quietest foreign server) and installs Xray where it is missing. 3 **Behind the CDN**: the list of `Iran address : port` to put in your CDN record by hand, each with a Copy button. Everything else (how the exits are used, all forwards, the CDN records you can move to the pool by hand) is folded under "Advanced".
- **No record is changed by the panel itself, for now.** The automatic switch of CDN records (a tunnel down for N minutes -> its records go through the pool) is OFF until its tests are finished: the mode cannot be set (the API answers 400), the job does nothing. The manual "Connect to the pool / Back to the tunnels" stays, under Advanced. The code is kept (`AUTO_USERS` in `services/outbound.py`).
- **Start makes exactly what the choice says**: a forward that is not chosen any more is removed, a chosen one that exists keeps its port. A removed forward's port is never given to another target (a CDN record that still points at it never leads somewhere else by surprise). With no foreign server and no port chosen, Start uses the foreign servers and ports of the chosen Iran servers' own tunnels, as before.
- **Faster.** The page opened slowly: the relay suggestion read the newest sample of every foreign server out of a hundred thousand rows (1.2 s -> 0.01 s), and the CDN records were read from ArvanCloud domain by domain (5 s). The records now come from the copy the DNS page keeps, shown at once (an old copy is renewed in the background, "read N ago", the ↻ button asks ArvanCloud now), and they are loaded only when you open that part, not with the page.
- **Tested for real** (an Iran server, a foreign server, a real tunnel, a real ArvanCloud record, two real exits, the relay; everything removed afterwards). The new flow: Iran server x foreign server x ports 2095 and 8080 -> Start -> 12 of 12 requests through the tunnel answered by the foreign server (through the exit and the relay), the port nothing listens on answered nothing; the choice changed (8080 dropped, 443 added) -> the kept tunnel kept its port and answered 8 of 8. The automatic switch (before it was switched off): users through a real Backhaul tunnel 8 of 8; the tunnel's service stopped -> 4 of 4 got 502, the panel pointed the record at the pool and 16 of 16 answered; the tunnel back -> the record put back, 8 of 8 through the tunnel. The Doctor's repair brought the stopped tunnel back by itself.
- **One action on every panel (#82).** The account window has an "On every panel…" menu: enable, disable, add days, set the limit, reset the usage, revoke the link, delete - on the user of every panel of the account, with a result per panel (a panel that is down does not stop the others). "＋ New account" makes the same new user (name, limit, days) on the panels you tick; a panel with several groups asks which group; a name already taken on a panel is refused there (it may be another person). API: `POST /api/accounts/create`, `/api/accounts/action` (delete needs `confirm: true`), `GET /api/panels/{id}/options`.
- **One subscription link (#83).** "Merged link" in the account window gives `/sub/<token>`: the configs of every panel of the account in one list, each once, with the traffic and end date added up (`subscription-userinfo`). The token is signed (nothing is stored, nothing to look up); it works without a login and without the secret-path cookie, answers the plain 404 when it is wrong, is cached for a minute and limited to 30 asks a minute per address. "reset all links" cancels every link made so far. The v2ray list is merged; Clash / sing-box are still open.
- **Usage summed (#85).** The Users page and the account window show used of the sum of the limits (no sum when one panel has no limit) and the earliest end.
- API: `POST /api/accounts/sub-link`, `/api/accounts/sub-link/reset`, `GET /sub/<token>` (docs/api.md). Settings: `accounts.sub_secret`. No new table.

## 1.39.0 - Outbound: connecting the users, the relay, manual or automatic

- **One place.** The tab "Panel outbounds" is gone: everything about the outbound is on Panels → Outbound (the API `/api/panels/{id}/core/...` stays).
- **Users tab (connecting the users).** Your users reach the Iran servers through ArvanCloud records (an Iran server's address and an origin port: the port of a tunnel). "＋ Tunnels" makes a forward on each Iran server for every port its TCP tunnels serve; the **Users** tab lists the proxied ArvanCloud records that lead to those tunnels and **Connect to the pool** changes only the record's origin port to the forward's port. Users keep their links, the tunnel stays as it is, every change is in the DNS log (Tunnels → CDN → Changes) with a Restore, and **Back to the tunnels** puts it back. A record that changed since the plan, or a forward that is not listening, is never touched.
- **Manual or automatic**, chosen on the Users tab: **Manual** (you choose the records and press Connect) or **Automatic** (needs the failover license): a tunnel that stays down for N minutes (5) has its records pointed at the pool by the panel itself, but only when the forward is up and the Iran server's pool is healthy; they go back after M minutes (30) of the tunnel being healthy - only what the panel connected itself is put back. One change per minute, a Telegram notice each time.
- **The relay.** An exit is somebody else's Xray, and it reads the Host / SNI of what passes through it and dials that NAME instead of the node: a WebSocket user whose Host is an ArvanCloud name came back as a 502 from ArvanCloud (found by testing the owner's config and a panel's own node: both do it). A **relay** is a foreign server (one or more) that runs a small Xray with a Shadowsocks inbound and no sniffing; the Iran gateway wraps the traffic in Shadowsocks, sends THAT through an exit (an exit sees only ciphertext), and the relay dials the node. Choose it in the Relay strip, press Apply (Xray is installed there, port and password made once, never listed); the job looks at it every 3 minutes and Telegram says when it stops. The gateway's own test doors now go the way the users go (exit + relay).
- Tested end to end on a real Iran server, with a real ArvanCloud record, two real exits and a relay on a foreign server: before, the record's users got 502 (6 of 6); after "Connect to the pool", 20 of 20 got 200 through ArvanCloud → Iran server → exit → relay; "Back to the tunnels" put the record back; everything was removed again.
- Forwards now belong to an Iran server (the ones made from tunnels go only to the server whose tunnel they replace). New tables `outbound_hubs`; settings `outbound.users`. API: `PUT /api/outbound/hubs`, `POST /api/outbound/hubs/{id}/remove`, `PUT /api/outbound/users`, `POST /api/outbound/routes/tunnels`, `GET /api/outbound/connect`, `POST /api/outbound/connect` · `/connect/undo`.
- Fixed: on a hard reload the Outbound page could say "obRender is not defined" until "Try again" (the first render ran before its script).
## 1.38.0 - Outbound, from scratch

- **Panels → Outbound** is a new page (the old per-server gateway page is gone; the old `/api/gateway/...` routes still answer). Three boxes: **Exits** (the pool), **Iran servers** (the ones that carry it) and **Forwards** (a port on the Iran server for a node's address and port).
- **The pool**: paste the configs that still have internet - vless / vmess / trojan / ss links, one per line, up to 30; each is read like Xray reads it, a link that cannot be read or is already there is skipped with the reason. The links are secrets: they have their own table and are never in an answer (only what they point at). Each exit has a switch, and shows what it looks like from the Iran servers (answers or not, how fast, which country it leaves from).
- **The mode**: **Spread** (every new connection goes through the next alive exit: the load is shared, a dead exit is skipped by itself - tested: 15 / 15 over two exits, and with one dead all 30 went through the other without a failure), **Fastest** (the quickest alive exit), **Direct first** (direct while the Iran server can reach abroad, the pool when it cannot).
- **Forwards** are made by hand or for chosen nodes × chosen ports of the Marzban / PasarGuard panels (one click shows each panel's nodes and inbound ports); the listen port on the Iran server is given once and never moves.
- **Apply** puts the pool and the forwards on every chosen Iran server (Xray is installed where it is missing); the story says what happened on each server. **Check now** and a job every 3 minutes look at every exit from the Iran servers themselves, and Telegram + the events say so when the picture changes (every exit answers / some do not / none does).
- Tested end to end on a real Iran server with two real exits: apply, check, the spread, an exit switched off, the mode fast, removal.
- API: `GET /api/outbound`, `POST /api/outbound/configs`, `PATCH` / `DELETE /api/outbound/configs/{id}`, `PUT /api/outbound/mode`, `PUT /api/outbound/gateways`, `GET /api/outbound/suggest`, `POST /api/outbound/routes` · `/routes/auto` · `/routes/drop`, `PATCH /api/outbound/routes/{id}`, `POST /api/outbound/apply` · `/check`, `POST /api/outbound/servers/{id}/remove`. The gateway modes `fast` and `spread` are new.
- Not in this release: the host entries that make the users' links go through the forwards, and the bridge for the tunnels (roadmap #87, #88).
## 1.37.0 - one account over many panels (M12 #81)

- **Panels -> ★ All panels**: one list of accounts over every Marzban / PasarGuard panel. An account is the same person on several panels: the same username (found by itself, case-insensitive) or a link made by hand between users with different names. Each row shows the panels the account is on (a dot per panel: active / disabled / other), the total used and the earliest end; a click opens the account with each user's status, usage and expiry, an **Open** button into the panel's own user window, **Link a user with another name** and **Unlink**.
- Every panel is asked through its REST API when you look (a search asks all of them at once; no search = the newest 40 of each panel). Nothing is copied out of the panels; only the hand-made links are kept here. A panel that is down or does not answer is shown, the rest still answer.
- API: `GET /api/accounts?search=`, `GET / POST /api/accounts/links`, `DELETE /api/accounts/links/{id}`. Tests: grouping, links, a down panel, the API. The goal of M12 in one sentence (from the owner): this panel is the most complete management panel, built on the panels' APIs.

## 1.36.5 - the unified core is API-driven (docs only)

- Decision: the unification stays on the REST API the panel already speaks with Marzban and PasarGuard; the node driver (gRPC), importing their databases and our own subscription server are dropped. M12 is now: one account over many panels, one action for every panel, one merged subscription link, one API for the bot and the Mini App, summed usage. Nothing is built yet.

## 1.36.4 - the study of the unified core (docs only)

- docs/unified-core.md: can this panel replace Marzban and PasarGuard (its own users, subscriptions and nodes)? Yes: the PasarGuard node is a standalone agent with a documented gRPC / REST API, both databases were read, the work is listed honestly (data, node driver, lifecycle, subscription server with golden tests, compatible API for the bot, importers), a strangler plan in six phases, imonitor looked at (ideas only). Roadmap: milestone M12 with #81-#85. Nothing is built yet.

## 1.36.3 - the side menu's icons in the day theme

- Day theme: the icons of the side menu are slate at full strength, the open page's icon is blue (they were pale and the open one nearly invisible on its highlight).

## 1.36.2 - the Analyst, shorter; the day theme of the Doctor's lists

- The Analyst's tabs: Best today · Tunnels · At risk · Servers · **CPU cores** (the cores box moved up here from under the lists). The tables look like the tunnel and server lists (lifted header, zebra rows). Shorter: the "What fixes what" tab, the footnotes under the tables, the summary chips, the "Weak hours" and "Mean repair" columns and the server "Size" are gone.
- The number fields of the mode cards: small fixed boxes with centred figures, no spinner.
- Day theme: the lines of the Finished list, the pods and rows of Best today, the verdict chips, the Analyst's table header and the cores are readable now; the servers list's bars have a visible track and their level colours, the flag behind a server name is a faint tint instead of a grey smear, and the side menu's labels lost their ghost shadow.

## 1.36.1 - the Doctor's settings inside their mode cards

- Each setting of the Doctor sits in the card of the mode it belongs to and is saved as soon as it is changed: "a tunnel down for N seconds is repaired" in **Fix tunnels**, "an Iran server off for N minutes is replaced" in **Fix + move the Iran server**. The settings row and its Save button are gone; Fix all / Explain and the count moved beside the tabs.

## 1.36.0 - the Tunnel Doctor in three modes

- **Three modes**, one choice at the top of the Doctor: **Off** (watch and report), **Fix tunnels** (the ladder: restart, rebuild, a new GRE link, another tunnel type), **Fix + move the Iran server** (the ladder, and - by itself, without asking - the move of tunnels to another Iran server).
- **When a server is moved** (mode 3 only): an Iran server that is off / does not answer for 10 minutes (settable) - all of its tunnels go to the best healthy Iran server; a provider-wide outage (several Iran servers off at once) is handled server by server, the one with the most tunnels first, as long as one healthy Iran server is left; an exhausted server (CPU or RAM >= 90 % for 15 minutes) gives its busiest tunnel to a server with room, one per hour. **Never for packet loss or a poor route**: those climb the ladder, and when no tunnel type works the move is only suggested (one click).
- **DNS follows in every account**: the records that carried the old address are found in ArvanCloud, every other ArvanCloud account and Cloudflare, and each is changed in its own account; every change is in Changes with Restore. A record that serves a moved tunnel and a staying one pulls the staying one along, so the DNS stays consistent.
- **Make before break**: when the source server is on, the new tunnel is built and checked first, then the DNS, and the old tunnel lives on for the record's TTL + 5 minutes before it is removed, so nobody who still holds the old address is cut. When the server is off, its tunnels are rebuilt at once and its leftover files are purged when it is back.
- **Return**: a server that came back and stayed healthy for 3 hours gets back the tunnels that left it because it was off (resource moves do not come back by themselves). The Moves tab has **Return now** and **Undo DNS** for every move.
- **Rails**: a tunnel moves at most 3 times a day with 2 hours between two moves; one move at a time; nothing while the panel is read-only; the mode "move" needs the failover license; Telegram tells before and after every move.
- The Doctor page is rebuilt: three mode cards, two settings (how long a tunnel stays down, how long a server stays off), a **Suggestions** box, the tabs In progress · Finished · **Moves** · Analyst (the CPU-cores work is now inside the Analyst's server list), and **What if … went off?** for every Iran server. The failover settings of the CDN tab are gone (the Doctor's mode decides); its history stays.
- API: `PUT /api/settings/repair` takes `mode` and `server_minutes`; `GET /api/doctor`, `/api/doctor/plan/{server}`, `/api/doctor/moves/{id}/return`, `/undo-dns`, `/api/doctor/suggestions/{id}/apply`, `/dismiss`; `/api/autopilot/move` goes through the same make-before-break path.
- The provider-wide outage, the exhausted server, the return, the TTL wait and the record of each move are tested with fakes (`tests/test_doctor.py`); a real move on a pair without users is the next step before the mode "move" is switched on.
## 1.35.29

- CDN: the *All accounts* chip is gone; the list shows one account at a time (the first one until you choose another).
- Create tunnel, the DNS names to pick: every connected account is listed (each name with the mark of its provider and its account); the search finds a part of the name, the address or the account (separators do not matter); the list scrolls inside itself.
- The Add account and the record forms (CDN) stay open until the account or the record is really saved: a click beside the window or the Esc key no longer closes them and loses what was typed; the Close button still does.

## 1.35.28

- CDN forms (Add account, make / edit a record): an error shows in full inside the form, in a red box above the button, instead of a clipped note on the edge of the window.

## 1.35.27

- The ArvanCloud mark is straight and centred (a dot and a thick bar of the same weight); a two-tone Cloudflare cloud mark beside it; both are used in the domain chooser, the account chips and in **Add account**, where the provider is chosen with two logo cards.
- Day mode: the small animated icons beside the page titles (servers, tunnels, panels, settings, shop bot) are drawn in dark slate and soft blue, so they are seen on the white header.
- CDN records: smaller cards, smaller names and smaller port pills, in proportion to the rest of the card.

## 1.35.26

- Create tunnel: *point these DNS names at the new Iran server* works with every DNS account (ArvanCloud, another ArvanCloud, Cloudflare): the account whose domain matches the name is used, the names to pick come from all accounts, and Restore puts the old record back.
- Adding a Cloudflare account: a pasted Account ID (32 letters and digits) is recognised and the panel says that the API token is the longer text (cfat_ ...) shown once when it is created.
- Day mode: the new look (lists, bars, chips, windows, forms, Settings, Install, the side foot, the themes) has its own day colours - dark text on white and pale blue, soft blue accents - so every text is readable; Pro slate has a real day palette.

## 1.35.25

- CDN record form: the **Port** field is active only when the protocol to the origin is HTTP or HTTPS.
- The ArvanCloud mark is drawn in a dark teal tile with a soft green gradient (the domain chooser and the account chips).

## 1.35.24

- CDN account chips redone: the provider mark (the ArvanCloud mark, the Cloudflare cloud), the name, the number of domains in a small pill, a status light on the right, a glow for the chosen one.
- CDN, make a record: a **Type** chooser beside the name (A, AAAA, CNAME, TXT) on ArvanCloud and Cloudflare; the fields follow the type (address, IPv6 address, host name, text). AAAA, CNAME and TXT records can be made and deleted (Restore works); only A records are edited. Cloud is on by default and is a switch like the others. The domain chooser shows the cloud of the provider (ArvanCloud teal, Cloudflare orange). Not yet tried against the live APIs.

## 1.35.23

- Servers: every second card is a little lighter, like the tunnels and the DNS records.
- Settings → Appearance: all the themes in ONE row; every card has a small picture of its theme (page colour, side bar, accent title bar, a card) and its name under it; the chosen one has a ring.
- The outage box of a tunnel has one fixed size and only its list scrolls (the last 30); the window itself does not scroll.
- Roadmap #80: other DNS record types (a type chooser beside the name) written down as an issue; every DNS path of the panel is built for A records today.

## 1.35.22

- Tunnel window: the *Times the tunnel was down* box has one fixed size (the last three, the rest is in History) and the charts are a little lower, so the window does not need to scroll.
- The foot of the side menu is rebuilt: a status light, *Powered by* over the name, and the version in a chip of its own; a longer version number never mixes with the text.

## 1.35.21

- Adding a Cloudflare account: a token pasted with a space, a line break or a quote is cleaned; a key that Cloudflare does not take as an API token (the Global API Key, a half copy) gets a plain answer with the steps to make a real token instead of *400 Invalid request headers*.

## 1.35.20

- CDN bar: the domain chooser is a clear labelled pill beside **Add record**; the numbers are three quiet figures without coloured lights (Domains, A records, On the panel); the *Accounts connected* and *Automatic switching* chips are gone (the account chips above already show the state); Add record never wraps.

## 1.35.19

- The sub-tab *DNS · IP switch* under Tunnels is now called **CDN**.

## 1.35.18

- DNS · IP switch: an accounts row on top (all accounts, each account as a chip with its state and domain count, **Add account** for another ArvanCloud or a Cloudflare account) to switch between accounts at once; the rest of the bar is ONE row; the *renewing* note is gone.

## 1.35.17

- DNS edit form: the record is read fresh when the form opens, so the protocol to the origin server (and the port) show what the record really has (the list of a domain may leave the protocol out).
- Tunnels: every second card is a little lighter, like the DNS records.

## 1.35.16

- DNS · IP switch, edit and make a record (ArvanCloud): a **Port** field and the **protocol to the origin server** (Default / Auto / HTTP / HTTPS), like the ArvanCloud page. Only what you change is sent: a record with several ports keeps them when you leave the port alone. The form is rebuilt as one compact grid (the name shows its domain beside it, the long help texts are tooltips).

## 1.35.15

- DNS records: the addresses have the same face and size as the addresses of the server list.

## 1.35.14

- A window that is drawn again (after Health, Quality, a check ...) rewrites only the numbers that changed, in place, so the values in the boxes no longer blink or jump; the charts are updated the same way.
- DNS records: every second card is a little lighter.
- The Outages box of a tunnel is a plain small table: `Times the tunnel was down` - when, how long, and why in plain words (the technical message stays in the tooltip).
- The tunnel window has one Health button (the check and the quality measure together, one message); the separate Quality button is gone.
- Restart of one tunnel no longer asks for a confirmation (Restart all still does).

## 1.35.13

- The Shop bot entry stays in the menu but is switched off (grey, not clickable) until the other sections are finished; roadmap #79.
- Addresses in the lists use a fixed-width engineering face with tabular figures and a calm weight.
- The story of Fix / Rebuild floats over the bottom of the tunnel window instead of pushing the content down.
- While a server or tunnel window is open the page behind it is not drawn again after an operation (it was the cause of the page jumping behind the window); it is brought up to date once, when the window closes.
- The look made in these days is a theme of its own, **Pro slate** (first card in Settings → Appearance); the theme cards sit in two tidy rows.
- The logo is bigger and sharper (a glossy tile, soft highlight, ringed nodes) and its ring and glow stand still, the four nodes are glowing orbs, and three soft lights drift slowly along the N (in every animation mode).
- The Mute alerts button keeps its size when a tunnel is muted (only the bell gets the line).
- Settings, every tab: flat quiet buttons like the Install view (no glow, no gloss, one soft blue for the main button), blue switches, neutral icon tiles, tighter buttons and fields. Only colours and sizes changed; the layout of the cards is the same.
- The bar above the Servers and Tunnels lists is rebuilt: quiet buttons, a soft blue Create button, a counter capsule, segmented filter tabs and a search that stays inside the bar; the DNS bar has two clean groups that wrap under each other instead of overlapping.
- Settings status tags: red when a protection is off (two-step, secret path, recovery codes), green when it is on.
- The header bar of the lists is a soft glass strip with rounded ends and fine column dividers; it is a little lighter and stays at the top while the rows scroll.

## 1.35.12

- A window that is drawn again after an operation (Filtering, GRE test, ...) shows the charts it already had at once, so it no longer flashes to *loading* and jumps (server and tunnel windows).
- *Copied* is small and faint.
- A muted tunnel shows a line across the bell (the button reads *Muted*; a click unmutes).

## 1.35.11

- The history of a tunnel in its window: one compact line per entry (time, light, kind, text) with aligned columns; the full text is in the tooltip.

## 1.35.10

- While a window is open, a message (Filtering, GRE test, ...) shows in the window only and no longer in the status bar behind it: the page behind does not jump when an operation ends. The log still keeps every message.

## 1.35.9

- Tunnels: `Recently removed` shows only the newest removed tunnel (a new one takes its place, nothing stacks under it) and leaves by itself after 10 minutes; Forget no longer asks.
- Tunnel window: History opens inside the window (Back returns) instead of closing it; the range tabs and the four number cards sit in one row with shorter notes; smaller type in the boxes.

## 1.35.8

- Server window: smaller type for the three boxes, the number cards (they all fit in the row now) and the chart numbers, so it looks less crowded.

## 1.35.7

- Country flags are grey and turn to colour only under the pointer (server rows, cards, the window header).
- The Enabled switch is blue like every other switch.
- Access & status: every value is one small size on one line; a long provider or site text ends with an ellipsis instead of being cut.

## 1.35.6

- Server window: the three boxes are tighter (all rows the same height side by side, nothing spills out); the range tabs and the five number cards sit in ONE row.
- Install view is quiet: neutral tiles, flat buttons without glow, a soft chip for Installed.

## 1.35.5

- Server and tunnel window boxes after the reference: a title with one soft-blue tile; in Resources long bars, the percent and the detail in two columns with a thin line, and traffic with a green down and a pink up arrow.

## 1.35.4

- Server window, Resources box: the percent and the detail (cores, GB) of CPU, RAM, Swap and Disk each have their own column, so the numbers stand exactly above each other.

## 1.35.3

- Every box looks like the boxes of the dashboard: a small quiet title, no coloured icon tile; the icons and emoji of the boxes, the console questions and the action buttons are one grey tone.
- The timing test of "long work answers at once" has a wider margin (it failed twice on a busy server).

## 1.35.2

- Server window header: a round flag badge with the status light on its corner, the address as a small chip, the role and host name as quiet tags.
- Access & status box: no flag in the Country row, smaller values so the SSH address, provider and country fit on one line.

## 1.35.1

- Tunnels: one table for every tunnel; a type no longer gets its own header and table. Each type has its own soft tag colour (Backhaul, Backpack, Paqet, Chisel, GRE).
- A faint `new` tag only on the two newest tunnels, and only for 30 minutes after they are built.
- The tunnel window shows its messages in a strip under the header, like the server window.

## 1.35.0

- **Fix (urgent):** the DNS page did not open in 1.34.1 (a comment ate the rest of a statement); every page of the panel was opened again in the test copy after the fix.
- **Soft switching everywhere:** a tab, a sub-tab or a range switch brings the new content in over a fraction of a second (no jump); tab panels, boxes, buttons, tiles and windows change colour and state smoothly; nothing moves for those who ask for less motion.
- **The quiet look in every part:** the icons of the side menu, the shop bot menu and the settings tabs are one blue-grey tone; the tabs of the Settings are underlined like the other tabs; the list of configs is one flat table after the design.
- **Switches** (the check boxes of the forms) after the design: a dark track, a light knob, blue with a glow when on.
- **Operations (M10 #64, a part):** `GET /api/health/detail` says what is wrong with the panel itself (database, late or failing jobs, the SSH key, free disk, the last backup, the errors of the last hour); the dashboard shows it as one row.
- Persian for the new tabs, buttons and headings; the page code of the removed Tunnel test is deleted (about ninety lines); `docs/api.md` lists the new routes.

## 1.34.1

- **Fix:** changing the address of an ArvanCloud A record no longer loses its port (the port, weight and country of the record are kept; `a_body`).
- The DNS records list no longer shows the records that cannot be changed (MX, NS ...). The port is smaller and right next to the address.
- **DNS names to point after a build:** a list of our own under the field (the browser's popup is gone): nothing until you type, filtered while you type, it stays open after a pick so several names can be added, chosen names leave the list; arrow keys and Enter work.
- **The check boxes of the forms are small switches** (grey track, white knob, blue when on); the boxes that select rows of a list stay boxes.
- **A tunnel that is being built** says *building tunnel* in the Status column, and a click on its card brings the Create window back with the story of the build so far.
- The sub-tabs of Tunnels: Tunnel manager, DNS · IP switch, Tunnel Doctor.

## 1.34.0

- **Fix:** the DNS page (Records / Changes / Accounts) failed with *fast is not defined* since 1.32.0 (a change meant for the servers list reached its tables too).
- **Create tunnel: Stop.** While a build or a test of a pair runs, Build and Test are off and a **Stop** button is there (`POST /api/tunnels/builds/{id}/stop`; the build rolls back what it made).
- **Check all** of the tunnels works right on its button (`Checking…`), no window: the answer is one line in the status bar.
- **Analyst** has *Best today* as its first tab (the separate Doctor tab is gone); the Analyst header is one row; the podium and the list are compact.
- **DNS records:** the port has its own column next to the address, bigger, in a petrol-blue oval; the server name beside an address is faint; the DNS bar is built like the servers bar (one row, same sizes); no refresh button.
- **The tunnel window** has the size of the full window from the first moment (the charts and the outages no longer make it grow).

## 1.33.2

**Best today is a tab of the Tunnel Doctor's box** (In progress / Finished / Best today / CPU cores / Analyst), not a page of its own; the score bars show (they were hidden by a clashing class name).

## 1.33.1

**Less colour.** The tabs of a page are the quiet kind (the chosen one is underlined, no coloured pill; their icons are in one grey tone, the Iran / Foreign flags too); the emoji of buttons, tabs and headings are drawn as plain one-tone glyphs where the font has them (`font-variant-emoji`); the AI button is the plain button; box title chips are slate, bars one soft blue, charts one blue, numbers white. **Calm slate is the default look** of a browser that has not chosen one yet, and once in each browser this version switches to it (Settings → Look has the other themes). The tunnel list has its earlier look back.

## 1.33.0

- **Tunnel test is gone; Tunnels → Best today is new.** The tunnels are tested all the time (a health check each minute, and a job, `quality`, measures the latency, jitter and packet loss of every tunnel again every half hour, read-only) and ranked by how they behaved in the last 6 h / 24 h / 7 days: how often they answered, how fast, how steady, how often they broke. A podium for the first three, then the whole list with a score bar, and a mark on the best of the tunnels that lead to the same foreign server. A tunnel with fewer than 20 checks has no score yet. `GET /api/besttoday?hours=` (`services/besttoday.py`, the score is a pure function with tests).
- **New theme: Calm slate** (Settings → Look): the look of the tunnel window design - flat boxes, slate icon chips, one soft blue for bars, charts and the timeline, white numbers, flat buttons with a small corner, no glow; only a real problem is red or amber.
- **The tunnel list** is one flat table with a thin line between rows (after the design); the arrows between the two servers stay as they were. The hover arrow is on the tunnel cards too.
- **CPU cores** is the name of the Doctor's CPU tab (what it is for is its tooltip).

## 1.32.0

- **Check all** replaces *Check GRE links* in the servers bar: one action on the panel server that checks every enabled server (does it answer, does GRE work to it through the new GRE test, is a foreign one filtered in Iran) four at a time. The button shows `Checking 3/14` and stays disabled until it ends unless you press **Stop**; reloading the page does not stop it (`POST/GET /api/checkall`, `POST /api/checkall/stop`; the progress comes from the action log).
- **The bar above the servers list is ONE row** (buttons, numbers, tabs, search); the search box takes what is left and never sticks out. **Foreign** is the first tab. Switching Iran / Foreign draws at once from what is known and the rows come in softly (no jump). The count in the tunnel tabs uses the same chip as the servers.
- **Add several servers** has the design of the one-server form (two boxes and a foot).
- **Install view** after the operator's design: two node cards (Marzban: certificate with Paste / Copy; PasarGuard: API key with show / hide and Show certificate), one row per tool (Abuse-Defender with its state, Update the server), icons on every button.
- **Tunnel Doctor, calmer:** the long explanation is gone; one slim bar (a switch *Fix tunnels by itself*, Settings folded away); ONE box with four tabs (In progress / Finished / CPU / Analyst); a card says one thing and keeps the rest under *details*.
- **One design for every box:** windows, cards and boxes share the surface, line and corner; fields are dark with the label above; two kinds of button (blue pill with a glow, dark pill).
- **Create tunnel:** a log box of a fixed size at the bottom of the window, always there.

## 1.31.0

**Servers: disabled looks disabled, a new Install view, a console for anyone.**

- **Install view** (the Install button of the server window, in place of the Abuse-Defender button): one block for the two panel nodes (a switch between *Marzban node*, which takes the panel's certificate, and *PasarGuard node*, which takes the API key the PasarGuard panel gives; it is installed with PasarGuard's own installer, the key is kept only while the installer runs, and *Show certificate* copies the node's public certificate to set in the PasarGuard panel), then one row per tool: Abuse-Defender (Install / Remove, with its state) and Update the server (apt update and upgrade, no reboot). Detached installs with the same story window as before; new reviewed actions `install_pasarguard_node` and `install_os_update` and a read-only `pgnode_cert` (`GET /api/servers/{id}/pasarguard-cert`). **The PasarGuard installer was written from its documentation and has not been run on a real server yet.**
- **Console:** six plain questions to click (Is it healthy? / Is anything broken? / What happened lately? / What is slow? / Is the disk full? / Network and open ports) with the answer under them; the command line is folded away under *Advanced*.
- **Disabled servers:** the Status column says *disabled*, the Issues column is empty, and the row is slate and striped (never red or yellow). The enable / disable switch flips at once and smoothly (the look at the server runs in the background and cannot be pressed twice), has no frame, and is 48 x 28 like the design.
- **Log box:** a click on the status bar opens a log box of one fixed size with two tabs, *This session* and *History* (the events kept on the server; *All* / *Problems*).
- Every view of the server window (details, console, install, edit) has the same size; the hover arrow of a card is softer.

## 1.30.9

**The GRE test tests GRE, not the list of GRE links.** `POST /api/servers/{id}/gre-probe`: an Iran server of the nodes (another one when the tested server is Iran) builds a temporary GRE link to the server, pings the inner address 10 times and removes the link - on both ends, always, also when something fails in between (two new reviewed actions, `gre_probe_up` / `gre_probe_down`: their own interface `nkgpN`, a network from 10.253.x.x, their own tagged firewall rules, nothing on disk, every value validated). It passes with 8 of 10 pings; when it fails a second Iran server is asked, to tell a route problem from one server. The answer feeds the GRE mark in the list and the GRE row of the window (`via <server>`). The window's GRE test button and the mark in the list use it.

**Server window:** the strip for messages sits under the header (above the buttons) and is always there, the window has no empty space below the charts and keeps one size from the first moment (the chart area has its final height before the charts arrive). The pings-from-Iran box is not shown after the Filtering test any more: the places are the tooltip of the Filtering row.

## 1.30.8

**The server window, after the operator's design.** One fixed size from the first moment (the whole height the screen allows; the body scrolls inside), so nothing jumps when the charts arrive or Console / Edit / Back are pressed. The header is in three groups: the name with its address, role and host underneath; the Enabled switch; Console / Edit / Delete; then minimise and close. The three boxes (System, Resources, Access & status) are tighter, with title-case headings, coloured icons, coloured bars with the used / total beside them, a copy button on the SSH address, the provider as a link, the site check with an icon, and a dot for status and GRE (`swap_used_mb` / `swap_total_mb` are new in the facts). The outcome of an action is a small message on the top edge of the window (a notch over the border), never over a button and never changing the size; a click on it shows the whole text.

The GRE test of a server that has no GRE link says so in plain words (nothing to test) instead of a red error. **Edit** from the console now leaves the console: Back returns to the main window.

## 1.30.7

Servers list: the **Status** column (online / offline) is back next to the server name, beside the new **Issues** column.

## 1.30.6

Servers list: the **Status** and **Filtered** columns are gone (the dot on the server's icon still says online / offline). One new **Issues** column shows whatever is wrong with a server as small tags, and a dash when nothing is: Disabled, No login, Offline, SSH ✕, Not checked yet, GRE ✕, Filtered, Bad route, Network cut / ⚠, duplicate name or address, or the work the panel is doing on it. Same columns on the Iran and the Foreign tab.

## 1.30.5

Servers list, second pass: a server is **enabled / disabled** (not turned on / off: the panel never switches a server off). The row buttons are gone; the switch sits next to the name in the server window. A disabled row shows a soft "Disabled - not checked, no alerts" chip inside the card. The bars are long and aligned (they fill their cell, the number has its own column), nothing sits at the end of a card, and an arrow shows there only under the pointer.

## 1.30.4

The small tags after a server name (GRE, NEW, disabled, duplicate ...) have one size and one shape, soft colours, and are dim until the pointer is on the row.

## 1.30.3

**Servers list, after the operator's design (Servers.dc.html).** Four tabs with counts (All / Online / Problems / Disabled); a **Turn on / Turn off** button in every row (Turn off asks first in the row itself, with what stops; Turn on shows *Starting...*, looks at the server at once, and the status bar offers **Undo**); a turned-off row says so in one line and is not counted in the Online total. The GRE column is gone: a `GRE` mark next to the name appears only for a server whose GRE is not ok (click = check again).

## 1.30.2

The Tunnel Doctor lists show the repairs that worked too (the newest 60 actions were mostly "look" runs, so older successful repairs never reached the list: the API takes `kinds=` now), and the cards are smaller: tighter text and spacing, the story link sits in the card's header.

**Servers:** an **On / Off** button in the server window (green when the panel checks the server, grey when it does not), and a **Turn on** button in the row of a disabled server; the server window keeps ONE size for details and the Edit form (Edit / Back no longer make the box jump).

## 1.30.1

Logs page, after looking at it in the browser: refused requests (401 / 404 / 422) are normal answers and no longer count as problems in the 24 h summary or get an origin; a 5xx answer is the panel's bug; the log line grid has a column for the origin and the domain no longer overlaps the text. A smart repair stopped on purpose (another test running, read-only) is logged as one warning, not as a traceback that looked like a bug.

## 1.30.0

**M10 #64 - reading a problem.** Every warning and error the panel writes is now classified as the panel's own, a service on your server, or outside the panel (the route, the other side, a provider), with one plain sentence on what to do (`services/diagnosis.py`, pure rules, tested with the sentences the panel really writes).

- **Logs page:** a summary of the last 24 hours on top (counts by where the problem is, the commonest sentences with their hint), a filter by origin, and the origin on every warning line. API: `GET /api/logs?origin=` and `GET /api/logs/summary?hours=`.
- **Actions** return a `summary` (problem / what was tried / result / origin / hint). The History box reads as a story: *Working on it* and *Finished* lists, the Analyst is a tab of the Tunnel Doctor box, an entry that never finished and is older than an hour is shown as "Stopped early - the panel was restarted".
- **UI:** a small x closes the message at the top of a window; windows keep their size when the tab changes (no jump on Console / Back); the server window texts are uniform; the add-server name check accepts dots and dashes like the API.

## 1.29.0

**M10 #63 - test depth.** CI measures the coverage and shows it in the job summary; `tests/test_upgrade.py` makes the database of the previous release (`git archive <tag>`) and opens it with the current code, so a migration that only works on a fresh database fails in CI, not on the panel server; a new CI job installs the panel on a clean runner with `install.sh` from the checkout, waits for `/api/health`, and runs the installer again as an upgrade. The two small leftovers of the UI round: the "Where" hint wraps instead of being cut, and the install story keeps one height so the window does not jump while lines arrive.

## 1.28.1

**The UI round the operator asked for** (every point verified in the browser on the test copy).

- **A window shows its own outcome.** Whatever was done in an open window (added, saved, failed, "a server named X already exists") is now written at the top of that window, in green or red, and stays there after a redraw. The status bar at the back only keeps the history. Before, the answer landed behind the window where nobody could read it.
- **Add server without a Role box:** where the server is (Iran or foreign) is found from its address through the geo services as soon as it is typed, shown with the country, and can be flipped by hand ("not right?"); the API takes `role` optionally and detects it too. A French server was added as Iranian by hand once.
- **GRE links are checked by themselves** after a server is added and answers; the "check" button in the GRE column is gone (the muted "not checked" text still looks on click).
- **A server being connected to or installed on has its own colour** (blue), not the green / yellow / red of health.
- **The add-server window is complete and compact:** the certificate box shows only when Marzban node is ticked, and the "remember this certificate" switch sits on one line (it floated next to the box).
- **Text in text boxes is white** (and dark on the day theme); placeholders stay grey.
- **One search box everywhere:** the magnifier at 12 px, the text at 36 px, the same height, radius and focus ring on every search box of the panel (servers, tunnels, configs, panels, DNS, the bot, the dashboard).
- The server tool "SSH password" is called **Change password**.

## 1.28.0

**M10 #62 - the OpenAPI document is the manual.** Every one of the 284 routes now has a one-sentence description (232 were written today; 52 existed), so `/api/docs` and `/api/openapi.json` explain what each route does, for people and for programs that drive the panel. `tests/test_openapi.py` fails on a route without a description, so it stays that way. Typed response models for every route are not done yet (most answers are plain dicts with `ok` first); they come with #65 when the page becomes a pure client.

## 1.27.0

**M10 #61 - machine access.** Settings → Security → **API tokens**: a token for a script, a Telegram bot or an AI agent, made with the admin password, shown once (the panel keeps only its SHA-256), with a scope - `read` (GET only), `act` (also the routes that start checks, repairs, builds and DNS changes), `admin` (everything; it may make tokens without a password) - revocable at once, every use timed, making and revoking in the security log. Sent as `Authorization: Bearer nkt_…`; a valid token also passes the secret entry path. The browser keeps its session cookie; a request a token may not make answers 403 `token_scope`. Table `api_tokens` (migration 13). docs/api.md section 3.

## 1.26.1

- **The config hysteresis really holds now.** A round of the configs job looked at the probe twice when a direct config was down (the second look after the reachability answer), and the two looks counted as two agreeing reads, so a config still flipped every two minutes. The second look no longer counts; only a measured override (the port opens from Iran) moves the status at once.
- **A config that flips again and again is called unstable once**, and the feed stays quiet about it until it kept one status for two hours (the status itself keeps moving, the alerts keep their own guard). Fastly and five other configs wrote up to 14 events an hour each.
- Events are never written at ERROR in the log: what happens to servers, tunnels and configs is a finding; ERROR means the panel itself failed.
- **Start-up answers at once:** what a restart interrupted (pending rebuilds, installs, a cut-short tunnel test) is picked up in a background thread two seconds after the start; before, a leftover test tunnel was removed over SSH inside the start-up and the health check of the deploy timed out, so the deploy rolled back a working release. The deploy now waits up to 90 s for the health answer (a graceful stop alone can take 40 s), and the ship records the shipped commit as the last good one (a rollback went two releases back).

## 1.26.0

**M10 #60 - the API contract** (`docs/api.md`).

- **One error shape on every route**, from one handler: `{"ok": false, "error": "<sentence>", "code": "<slug>"}` with the status that fits (`bad_request`, `not_signed_in`, `license_needed`, `forbidden`, `not_found`, `conflict`, `invalid`, `too_many_requests`, `internal`, `upstream_failed`). A body that does not fit the model answers 422 with one sentence per field instead of a list of objects; an unknown path and the secret-door refusal answer the same way. The page reads `error`.
- **Long work as actions:** *Check every tunnel*, *Restart every tunnel* and *Measure every tunnel* answer 202 with an action id at once; the page follows the action and shows its last line (they blocked the request for up to minutes). A read-only check or measurement may run while the panel is read-only; a restart may not.
- **The versioning policy** is written down: `/api` stays version 1 and compatible; a breaking change ships beside the old route under `/api/v2`.
- The contract is covered by `tests/test_api_contract.py`.

## 1.25.5

- The records of the DNS accounts are read once at start, in the background, so even the first open of the DNS page is served at once.
- `scripts/audit_code.py` is in the repository: the static audit of the alignment rounds (docs vs code, settings vs docs, guards, swallowed exceptions, blocking routes, retention, long functions, threads, unused scripts) in one command.

## 1.25.4

**Alignment round 2** (every page measured in the browser and on the server; the slow spots fixed).

- **About & update no longer freezes for half a minute:** the engine check (every server over SSH) runs in the background; the page shows "checking every server…" and redraws when it is done. Before, opening the tab waited 26 s on the check.
- **The DNS page opens at once:** the records read last are shown immediately and renewed in the background when older than a minute ("renewing…"); only the first read after a start, and ↻, wait for the accounts. Before, a cold open waited 8 s.
- **Settings tabs switch without re-reading eleven routes:** one read, kept 20 s, forgotten after any change.
- **Jobs remember their last failure** (`last_failure`, when) even after later runs succeed, so a job that failed 24 times in the night can be understood in the morning (migration 12). The Jobs page shows it.
- **Slow API answers are written to the log** (over 3 s), so slow routes are found instead of guessed.
- Measured on the server: every GET route the pages open with answers in under 60 ms; what the browser feels is the network.
- **Deploy is safer after a real failure today:** the first build of this version shipped a migration with a broken DDL (`DEFAULT '` lost a quote); the service could not start and the rollback was blocked by the uncommitted files, so the panel was down for about three minutes. Now `scripts/deploy.sh` runs the migrations against a copy of the live database BEFORE the restart (`scripts/migrate_check.py`), and a rollback puts the last good files back without switching branches (uncommitted work goes to a stash). `tests/test_migrations.py` runs every ADD COLUMN DDL for real and checks the numbering.

## 1.25.3

**Alignment round 1** (the audit of 2026-10-12: docs against code, the live log, the growing tables).

- **Configs no longer flap.** A config changes its status only after two reads of the probe agree (one SSL timeout from a data centre is not a dead config), the reason of a read that did not move the status is not shown, and events and alerts come only for real changes. The live panel wrote about 400 config events a day for six configs that flipped every few minutes.
- **Housekeeping job** (every 6 hours): events older than 60 days and finished repair stories older than 90 days are removed, the write-ahead log is folded back. Events and repair stories were kept for ever.
- **Install status fixed:** following a detached install (torrent block, Marzban node) raised "STATE running" as an error while the install ran, and after twelve polls declared it failed.
- **Quieter log:** the raw output of a failed SSH call is logged at debug (the caller already says what failed and why); two source files lost their byte-order mark.
- **Docs aligned:** ARCHITECTURE names every module and the real retention; `docs/operations.md` lists every setting; features describe the Tunnel Doctor, the Analyst and the DNS page as they are; the stale version badge is gone; `docs/development.md` names the hotspots.
- **Tests that keep it aligned:** `tests/test_alignment.py` (docs vs modules, settings vs docs, routers behind the sign-in, SSH scripts used, no BOM / CRLF).
- Roadmap: `[~]` marks a line carried by another (#57 → #65, #58 → #67, #59 → #68 / #73); the sync closes it as superseded.

## 1.25.2

- **The way forward is written down:** docs/engineering-plan.md (stable first, API-first, features later; the measured state of the code; four phases; the definition of done) and roadmap milestones M10 (the work: contract, machine tokens, OpenAPI as the manual, test depth, operations, front-end split, pilot, Persian) and M11 (parked ideas, not built now). CI syntax-checks every JS file, not only the first.

## 1.25.1

- The retransmit finding no longer says the kernel cannot help: on real servers BBR lowered the retransmits clearly (2.05 % to 0.35 %), so the tuning is tried first and only what stays is blamed on the route.

## 1.25.0

- **The Tunnel Doctor watches the underused servers.** A server that carries little may be slow. Every 30 minutes the Doctor looks at two of the idle, light or redundant servers (each at most every 12 hours; read only: the TCP settings, the packets sent again while the server downloads a test file for a few seconds, the CPU steal, the drops, the line speed), says what is wrong in plain words, and fixes what the kernel settings can fix (BBR, fq, bigger buffers: the tuning the tunnel builder gives its own servers; measured before and after and put back when it did not help; Undo on the row). A fault of the provider's network (lost packets on the route, a slow line, a stolen CPU) cannot be fixed from here: it is written once a day to the events. Analyst -> Servers shows each server's own network next to its use, with Check now / Fix / Undo, and a switch for the automatic fixing. The History lists these fixes as solved / not solved. New read-only script net_health and action sysctl_untune.

## 1.24.0

- **The analyst (Tunnel Doctor -> Analyst).** A read-only view of what the history says: a profile of every tunnel (availability, outages and how long they last, normal latency and whether it is rising, weak hours, verdict stable / watch / chronic), the tunnels that are at risk, outages that happen together on tunnels that share a server, what fixes what (how often each repair step healed which cause, and how fast), and how the servers are used in the peak hours (idle, redundant, busy). **It helps the smart repair:** every smart repair is now kept step by step (new table, migration 11); before the ladder is climbed the repair asks the analyst, and a restart or a rebuild that failed again and again on a tunnel, or almost never works for a cause, is skipped (the story says why; a move or a switch is never skipped, a dead service is always restarted first, the ladder is never empty). See docs/analyst.md.
- **Tunnel Doctor history shows only problems and how they ended** (solved / not solved / working): looks that found nothing, dry runs, builds and removals are not listed.
- The message of the CPU card that says the cores are balanced is no longer red.

## 1.23.4

- **Reverse proxy on another machine.** NIKATEL_TRUSTED_PROXIES (comma separated addresses): a request from one of them is believed about the client address (X-Forwarded-For) and about HTTPS (X-Forwarded-Proto), like a request from the local machine. The demo uses it behind a TLS proxy so every visitor has their own rate limit.

## 1.23.3

- **The demo is hardened for the public** (the 1.23.2 tag holds the same code but still reported 1.23.1). It no longer serves the operator's key pair (the first read made one), the log, the update check, the API description or the launcher; a client that asks more than 300 times a minute gets a 429; the Persian language is switched off in the demo for now (English only, no language switch).

## 1.23.1

- The demo hides the Shop bot page and the Sign out button (neither has anything to show there).

## 1.23.0

- **A read-only demo with invented data (`NIKATEL_DEMO=true`).** For showing the panel to other people: no sign-in, fourteen invented servers, twelve tunnels (one degraded, one down), configs, a panel, events and repair stories, all from the documentation address ranges; every change (POST, PUT, DELETE) is refused with a clear message, no SSH key is used, no job reaches a machine, and one small job keeps the numbers moving. `nikatel demo-seed` fills an empty database (it never touches one that has servers). A small "Demo" pill shows at the top. See docs/demo.md.
- **Panels: Add panel and Edit open in their own window**, like the other boxes (the form no longer pushes the page down).
- **Panel outbounds fit in one box:** the outbounds in two columns, the add row in one line, routing rules and backups folded; no page scroll.
- **DNS:** the domain's own name-server records ("@" NS) are no longer listed (they cannot be changed here and only filled the table).
- **CPU reading is steadier:** the server facts take three one-second windows and use their median (one burst no longer reads as a full server); the probe from the vantage server asks six addresses at a time; every background job has its own start second.
## 1.22.0

- **DNS · IP switch like ArvanCloud's own DNS page, with several accounts and Cloudflare.** The Records tab is the table of the account: Type · Name · Value (the address, the server of this panel it is, the port) · TTL · Cloud (the orange-cloud switch, turns the CDN on or off at once) · edit and delete; all record types are listed (A can be changed, the others are read only). Add record / Edit: account, domain, name, address (or the address of one of your servers), TTL, cloud - with a live preview. Every record made, changed or deleted by hand is kept under Changes with a Restore (delete -> make it again, change -> the old values back, made -> delete it). **Accounts tab:** add as many ArvanCloud accounts and Cloudflare accounts (an API token with Zone Read and DNS Edit) as you want; the key is tried before it is saved, stays on the server and never comes back to the browser; the domains of all accounts are in one chooser (grouped by account). A Cloudflare record holds one address (one record per address). The main ArvanCloud key is account "arvan" (the failover uses it). The Point button is gone: the address select in Edit does it, with a Restore.

## 1.21.0

- **Tunnel Doctor: CPU on the cores.** On a virtual server the network card often has one receive queue and one interrupt, so every packet is processed on ONE core while the others idle (a 4-core server was "full" at 40 % of its power: one core at 80-95 %, the other three nearly idle). The Doctor now probes every multi-core server above 55 % CPU every 10 minutes (read only: per-core load and NET_RX over two seconds), finds a server whose packets sit almost all on one busy core, and - when "The Doctor spreads it by itself" is on - turns RPS on (the packet steering goes to all cores; a boot unit keeps it; no connection is dropped, no tunnel is touched), waits, probes again, keeps it when it helped and puts it back when the server got worse; one server per turn, a server is tried again only after six hours. By hand: the card on the Tunnel Doctor page (Check a server, Spread now, Undo). Every step is in the History with its story.

## 1.20.8

- The same sensitive search is in EVERY search box: the dashboard's section search, the configs, the panel hosts, the shop bot's plans and texts (client side, one rule: every word anywhere, separators ignored), and the shop bot's users, partners, payments and orders and the log viewer (server side: every word must be found in the id, name, username, phone, remark ... joined, also with the separators left out).

- Search is more sensitive in every box of Nodes, Tunnels, Configs and DNS: each word of what you type may sit anywhere (the middle of a name too), several words must all be found, and separators do not matter (a name with a dash, a dot, a space or nothing between its parts is the same). A server can also be found by its role, provider (a part of the provider name finds all its servers), country, host name, state; a tunnel by the provider of its two servers, its transport, state and ports.

## 1.20.7

- DNS · IP switch: the domains are a drop-down in the top bar (each with its number of records, and All domains); the table below lists the records of the chosen domain only - no drawers. The choice is remembered.

## 1.20.6

- Settings: the boxes of a frame (About & update: the update card, License, Tunnel cores, Service settings) are 12 px apart everywhere; three of them touched each other.

## 1.20.5

- Tunnel Doctor page tidied into one bar: the switch (repair a down tunnel by itself), the delay, how far it may go, Save, Fix all / Explain with AI and the count are one row; under it the list of tunnels and the history side by side; the six steps are a drawer that is closed until clicked. The duplicate switch and the separate settings card are gone.

## 1.20.4

- Mini App 3.0.1: the quick-access buttons of the home page sit in one tidy row (up to 4 buttons) or two rows of three (5-6 buttons) or four (7-8) - no lone button on the last line.

## 1.20.3

- Server window: the CPU line says how many cores the server has ("4 cores") next to the percentage, like the RAM line shows its megabytes.

## 1.20.2

- Servers: only a server that is cut (off) is red. A server that is on but has a problem - high CPU, RAM or disk, no login, SSH failing - is yellow (a CPU at 94 % made the icon red before).

## 1.20.1

- DNS · IP switch: the domain header rows are left-aligned, the page description says the automatic switching is paused.

## 1.20.0

- **DNS · IP switch redone from zero**, like Servers and Tunnels: a toolbar (the ArvanCloud connection, domains / records / on-the-panel numbers, the tabs Records · Changes · Failover, a search, a refresh) and ONE box under it that ends at the bottom of the window. Records: a table where every domain is a drawer, each record with its address, the server of the panel it points at and a select + Point to point it at an Iran server by hand. Changes: every re-pointed record with its Restore. Failover: the preview of a plan, the ArvanCloud key, earlier failovers. The four intro boxes are gone.
- **Automatic IP changes are PAUSED** until they are tested (`failover.AUTOMATIC = False`): no plan for a dead server, no route plan, no follow of a changed server address, mode "auto" of Create does not apply DNS by itself; plans prepared earlier are withdrawn. Pointing a record by hand (Point, Restore) works as before. The Failover tab says so.

Detection speed and the "offline" that was not (found with three live servers shown offline while they were up):

- **Servers called offline while they were on.** Three Iran servers answered on the SSH port and their tunnels carried users, but the SSH session from the panel server stalled right after the banner (a filter or a bad path on that route; a direct ssh waited its whole 35 s time-out, every sample failed, the server was marked offline, and its tunnels showed "Iran server off"). Now: (1) the same server is reached through another Iran server (ssh -W, the panel's own key): after a direct failure that looks like a stalled or dropped session the panel goes through a jump server - the probe server first - remembers it for ten minutes and then tries direct again with a short time-out; (2) a server whose SSH port answered in the last 30 s is never called offline by a failed sample: it stays online and shows a small amber "SSH ✕" with the reason. Measured: all three were reached through the probe server in under 4 s while the direct session timed out at 25 s.
- **A server that came back took up to two minutes to show online** when the full sample had called it off (the pulse put back only the servers it had marked itself). Now the pulse (every 5 s, 2 s time-out, two misses = off in ~12 s) asks the server over SSH the moment its port answers again (once per 15 s), and every change of a server's state wakes the tunnel checks at once.
- **Tunnels: ~155 s cycle -> ~60 s.** One turn of 20 tunnels took 95 s (half the workers, and a turn started its 60 s wait AFTER it ended). All workers are used now, and every job repeats from the START of its turn. A tunnel whose server is off is no longer asked over SSH (each such check waited 10-15 s for a time-out); a second check of a tunnel never runs at the same moment as the first.
- **A fast lane for the tunnels that are not up** (down / unknown / starting): looked at again every ~8 s with a fresh look from another Iran server, and at once when a server goes off or comes back - a tunnel that is back shows up in seconds.
- **Repair starts sooner:** the repair job runs every 20 s (was 60), the grace of a down tunnel before its first restart is 60 s (was 180; the setting is on the Tunnel Doctor page), the smart repair follows after the restarts as before.

## 1.19.15

- DNS · IP switch: the records were read once and kept for five minutes, and the card's numbers (domains, DNS records) came only from the last key test - a record removed in ArvanCloud still showed. The records are kept for one minute now, the ↻ button reads them from ArvanCloud again at once, and every read updates the numbers of the card (119 -> 60 in the test).
- DNS · IP switch: the domains are drawers - one line per domain (the domain, how many records, how many are on this panel), a click opens its records; the open ones are remembered; a search opens the domains that have a match.

## 1.19.14

- DNS · IP switch: the records of each domain are together under a small header (the domain and how many records), the name shows without the domain part; the tag "not a server of this panel" is now "outside the panel" with an explanation on hover (the address is not one of the servers added to this panel: a CDN, another provider, a server you did not add), and such a record no longer preselects the first Iran server in its list - you choose.

## 1.19.13

- DNS · IP switch: the server tag next to an address no longer wraps onto two lines.

## 1.19.12

- DNS · IP switch made compact: the four steps are one row of small cards (two rows below 1300 px), the records sit in a box of their own (36 px rows, fixed columns, scrolls inside) with a search box, the cards are smaller.

## 1.19.11

- Dashboard from Dashboard.dc.html (the rest of it was already in place): the Expand button of the Activity box did nothing (its function was missing) - it now makes the activity the only box, over the whole width, and Collapse brings the three boxes back; the activity loads 60 events first and says "Showing N of M" with a Load more (the events API returns the total); the search box says "Go to a section…".

## 1.19.10

- Day theme, shop bot switches: a switch that is On was grey (the day theme's grey rule beat the green one); it is green now, Off stays grey, and the On word is a darker green.

## 1.19.9

- The question box (SSH password and every other prompt) redone: a dark field with a lock, an eye to show / hide the password, a Generate button that makes a strong password and shows it, a hint line, buttons at the right; no browser autofill colour. A value the browser puts into a search box by itself never filters the list now (the box must have the focus).

## 1.19.8

- **The real cause of the vanishing server list:** when a password box appeared (SSH password, the Edit form), the browser's password manager filled the saved user name ("admin") into the nearest text box - the search box of the page - and the search hid every row. The list was empty because it was filtered by "admin". The question box's password field is now "new-password" (never filled), and the search boxes of Nodes and Tunnels are read-only until you click them, which the password manager does not touch.

## 1.19.7

- Windows and question boxes no longer blur what is behind them: the list behind a window could disappear from the picture while a window or a question (SSH password...) was open - on the operator's browser; it could not be reproduced here (the page's data and layout stay intact), so the blur - the one thing that stacks layers the browser may drop - is removed. The dim layer stays; the window is solid.

## 1.19.6

- The real cause of the list "vanishing" behind a question box (SSH password...): the window under it already dims the page to ~28 %, the question's own dim layer dimmed it again to ~9 % - the list was there but unreadable. A question over a window now adds almost no dimming.

## 1.19.5

- Servers: pressing SSH password (or any question box) over a server window made the server list behind it vanish from the picture - three blurred layers stacked (page, window, question) and the browser dropped the one behind. While a question is open the layers below stop blurring and the question itself is a plain dim layer; nothing is redrawn.

## 1.19.4

- Server window, Edit form: ticking "Marzban node" no longer makes the window grow by 110 px (the certificate box is always there, dimmed until the box is ticked); switching between the details and the edit form no longer re-plays the window's fly-in and the dark layer's fade (the window only changes its content).
- Servers page: the list is drawn BEFORE the window, and a window that fails to draw shows a message instead of stopping the page - a fault in a window can never leave the list empty.

## 1.19.3

- Phone layout of the shop bot lists, continued: every cell carries the name of its column as a small label (the header row is hidden on a phone), so a bare number still says what it is; the Payments and Orders rows fold the same way as the other lists.
- Chaos tests of the Tunnel Doctor on the pair without users, recorded in docs/tunnels.md: a stopped service, a deleted GRE link (the on-server watchdog timer puts it back within a minute, the panel only watches), GRE filtered on the route (classified as such, restarts tried, a new link tested beside the tunnel, then the switch to BackPack · TCP Mux on the same ports - 9.5 minutes from the cut to the new tunnel carrying data).
- Seen in that test and fixed: the smart repair could start while the plain repair was still restarting the same tunnel's services (now it waits for it); a smart repair started by the panel itself was logged as "manual".

## 1.19.2

- A GRE interface that is missing is restarted at the next minute too (no grace): it is as deterministic as a stopped service.
- Mini App, Buy: a category whose name holds a quote broke its filter button (the raw code showed in the button); the name sits in a data attribute now.

## 1.19.1

- Found in a live test (the tunnel service stopped on the foreign server): the automatic restart healed the tunnel, but the check right after it still read the external probe's answer from BEFORE the restart, called the tunnel "still down", restarted the Iran side for nothing and closed the repair as failed. After a restart the repair now asks another Iran server about the users' port again and checks up to four times over about 40 s (a tunnel service needs a few seconds to dial in again); the manual Restart does the same.
- A tunnel whose service is not running (inactive, failed, dead) is restarted at the next minute instead of after the 3-minute grace: a stopped service is no network blip. In the test the outage lasted 6 minutes for a stopped unit; now about 2.
- Mini App of the shop bot in the panel's glass look (3.0.0): pill buttons with a specular edge, glass cards over soft colour fields, a floating glass tab bar, a hero card with the balance and the two things people do most, plan prices as gradient badges, a light scheme that follows Telegram. The structure and the API are unchanged.
- Phone layout of the shop bot lists: a row becomes a card of two columns, eight to a page, no header row and no sideways scroll; search boxes take the whole width; the settings columns and status tiles fold to one or two columns.
- Docs: README (version, DNS · IP switch, license tiers), ARCHITECTURE (the services that were missing from the map, the license), roadmap M9.

## 1.19.0

- Tunnels → DNS · IP switch (new tab): the A records of the ArvanCloud account with the panel server each one points at, a Point button that moves a record to another Iran server (asks first, live story, listed below with Restore), the failover plans and settings, the DNS change log. The Failover tab of the Tunnel Doctor moved here. The failover is on again (it stayed off since 1.8.x), but only when an ArvanCloud key is saved; a plan in mode "ask me first" waits here and sends a Telegram notice.
- Servers: changing a server's address rebuilds its tunnels on the new address (each one safely: the old one comes back when the new build fails) and, for an Iran server with an ArvanCloud key, re-points the records that carried the old address.
- License (Settings → About): the panel now has three modes - owner (`NIKATEL_LICENSE=owner`), licensed (a signed `NK1.` token, Ed25519, bound to an install id or floating, with an end date or none) and the free tier (Backhaul TCP Mux tunnels only). Features: tunnels (every engine), ai, failover, shopbot. The gates sit in the builders, the tunnel test, the smart repair's choice of kinds, the AI and the shop bot API (402). docs/licensing.md says what this does and does not protect and how the license server and AI proxy are meant to work. The `cryptography` package is a dependency now.
- Security: the session and gate cookies are marked Secure behind a TLS proxy too (X-Forwarded-Proto from the proxy on this machine is trusted), not only when the panel itself speaks TLS.
- Real test of every tunnel kind (14 kinds, one pair of servers, 2026-10-12): Backhaul tcpmux 60 · tcp 93 · wsmux 59 · wssmux 60, BackPack tcpmux 81 · tcp 94 · stealth 92 · wssmux 82 · kcp 50, BackPack dynamic udp 74 · pck 72, Paqet 55, Chisel 72 · tls 70 - all built, carried data and were removed again.

## 1.18.2

- Edit plan: the inbounds are picked from what the plan's panel offers (tick chips by protocol, all / none per protocol; PasarGuard: its groups) instead of writing JSON; the JSON stays in a fold. A tick "Also change the accounts already bought with this plan" gives the change to the active accounts on the panel too (asks first, runs in the background, tells how many changed and how many failed); new purchases get the plan's inbounds as before. The plan's server must be a panel in Panels (matched by address).
- The services strip of the shop bot is one row at the left corner.

## 1.18.1

- Shop bot, Plans: sorted by category the list is grouped (a header row with the number of plans for each category, repeated at the top of the next page when a group goes on). The status strip above the tabs is lower: the label over the number and its note on one line, the services in one row.
- The plan descriptions of the bot (what the customer reads) were rewritten short and clear, one per plan, by server kind, with the size and the period; the old ones are kept in a dump of server_plans on the bot server.
- Bot, /buttons: the list shows the premium emoji of a button as its icon and marks a changed button with a pencil; after a text, colour or reset a back button returns to the same page of the list.

## 1.18.0

- Shop bot, one engine for every list (Users, Partners, Categories, Servers, Discounts, Gift codes, Broadcast, Tickets, Tables, Apps, Backups, Texts): whole rows that fit the window, a numbered pager, no scroll and no jump when you type in a search box or add a button (a search box keeps its focus and caret when its tab draws again). Rows are lower; only the height shrank, not the text. Headers and cells start at the same place whatever the script. Toolbars: filters and search at the right, the Add button at the left.
- Users: a real list (all, partners, banned) with a search and paging instead of an empty page until you type. The user window is smaller: the payments sit in a small scrolling box and the discount is edited right in the account box.
- Categories, Discounts and Gift codes from their designs: ON switches (with a visible knob and an On / Off word), expired and days-left tags, a copy button, a used bar, a state chip, a delete that asks inside the row; their Add / Edit windows have the same design as Add plan (live validation, a generated gift code, exact end times). The Edit plan window follows Plans.dc.html. A plan switched off stays in the list until you change the filter.
- Servers: the On switch shows the real state (a server that was active but not selling showed an Off button while it looked off); the Add / Edit server window has the Add plan design.
- Partners: the counters, sort, search and an Add partner button are one row; adding opens a window with the search.
- Settings on one page: every group side by side in columns, the few settings people touch first, the rest behind the arrow of its group; Save changed / Discard at the left with a count of what changed. All setting names and hints are translated.
- Texts and Buttons, Tools (Mini App, Backups, Apps, Top-up prices, Tables, SQL, Stats, the bot actions in one menu) rebuilt in the same style.
- The cron chip opens one window with the bot's five scheduled jobs: tick which run, how often, and the bot settings each job reads; nothing runs until you press Start. The cron file is written by the panel (every line is checked again on the server); a stopped job keeps its schedule.
- Fixed: a plan description with several lines split into junk rows (the plans list showed 139 plans instead of 58; mysql output is now read with newlines escaped and split only on \n); a mysql error that came after the echoed statement was read as a row (the Payments tab broke with a collation error: the user join now has the same collation).
- Fixed: a Smart repair of a tunnel whose GRE is filtered removed the tunnel (the Canada tunnel) and the new build could not work either, so nothing was left. Now a new GRE link is tested BESIDE the old tunnel first and the tunnel is only rebuilt when that works; a rebuild that fails builds the old tunnel again; a restart that meets a tunnel removed meanwhile no longer crashes.
- Server list: the flag under a name is a shorter piece (less stretched). The version moves to 1.18.0: the numbers had stood still while the work of this release was collected.
- Shop bot, Orders tab rebuilt like Payments: All / Active tabs with counts, a search, rows (id, user, remark, server, paid, expires, state, Enable / Disable), a numbered pager and a page size that fits the window; the list endpoint takes a page size and returns the counts. Missing database indexes (users.userid, orders_list.userid and status, pays.user_id and state) are created, which took the Orders and Payments queries from seconds to milliseconds.
- Shop bot, Plans tab rebuilt from Plans.dc.html: Active / Off / All tabs with counts, a category filter with the number of plans in each, a server filter, a search, a sort (category, price, most sold, newest), rows with title, category, server, time, volume, price, sold, an On switch that flips in place and Edit / Delete (Delete asks inside the row). Adding a plan is a button that opens a window: category (with a new-category field), server, time, volume and price, a live title and price preview, a check for price and for a plan that already exists; the new plan appears at the top with a green flash.
- Every shop bot tab sits in one fixed box that reaches the bottom of the page (it scrolls inside when its content is longer); the lists (Payments, Orders, Plans) fill it with whole rows and a numbered pager.
- Smart choice of the Iran server (Create tunnel, Test this pair first, and the failover that uses the same ranking): an Iran server whose GRE is not OK - the last GRE check failed, or one of its tunnels is cut on the GRE link right now - is ALWAYS last, however good its ping and free resources are; and an Iran server whose own tunnel to the same foreign server is down or degraded comes after the ones with a clean history (that tunnel was cut by something on that path, so it is not picked first again). The reasons are written in the order line of the story.
- The Payments box fills the page down to its padding, and the shop bot cards are a little smaller and grow slightly under the mouse.
- Payments tab: a page holds as many rows as fit the window (no scrolling), with a numbered pager at the bottom whose box keeps its size on a short last page, so nothing jumps; names are left aligned whatever their script; the list endpoint takes a page size.
- Shop bot, Payments tab from its design: filter tabs with the count of each state (To confirm / Awaiting receipt / Approved / Declined / All), a search, a pager, and rows (id, user, amount, type, date and time, a state chip) whose Approve and Decline ask inside the row (Credit X T? / a reason for the user) instead of a pop-up. The payments list now returns the counts of every state.
- Shop bot: a Partners tab (People group) for the users the bot treats as agents: how many, their wallets and partner orders, a search, sorting (newest / top buyers / biggest wallet), the discount of each one editable in the row, Open and Remove partner, and an Add a partner search. The panels card of the overview shows panels of the same kind and address as one line with their count and a coloured kind badge (Marzban, Sanaei / 3x-ui, PasarGuard, Marzneshin). Needs-attention items can be closed with a cross (they come back when their count grows).
- Shop bot page built from its design: a Services card (a red cron chip with a Start button, a click on a green one pauses it) next to four number cards, the tabs in four labelled boxes (Home / Sell / People / Setup) with coloured icons, a Needs attention card whose database import asks and runs in place (Cancel / Dump live DB, then import / Importing…), Telegram and panels cards with Copy buttons, and the bot log with the newest backups. New action: the bot's cron on / off (a rename of its cron file). The import of the real database now copies by column name.
- Shop bot page redesigned: one slim status strip, the tabs grouped by what you do (Sell / People / Setup) with line icons and badges, and an Overview that starts from what needs you (payments to confirm, open tickets, a broadcast running, the real database waiting - each with its button), the sales numbers, quick actions, Telegram and panels, and the bot log folded away.
- Configs page like its design: a toolbar card with pill buttons and line icons (Add, Ping all, Test all, Delete all), status chips, a Grid / Compact / List segmented switch, the group chips with counts, and the list as rows of cards (a down config has a red-tinted row) with Test and Delete pills.
- Tunnel test: the Quick and Start test buttons have a gap between them (the button groups of the toolbars are flex rows again).
- Every window and question opens the same soft way: a short fade and a small rise, no blur, tilt or spring (the little jump at the end of the old animation is gone). The Tunnel Doctor page fits the window without a page scroll: the log and the history scroll inside their own boxes.
- Panels, users: a short list (a status filter with few users) keeps the row height of a full page instead of stretching to fill the box; the coloured line at the left of a row is gone, a small tile with a person icon in the colour of the status stands in its place; the Expires column starts at the same place as its header and shows the date beside the days.
- Tunnel Doctor: the log (History) sits at the top right under the settings; the controls of Working now are one joined group and the log box and its note are one piece.
- Settings group cards follow the Alerts screen of the design handoff: a header with a tinted icon tile and a pill, fields 44 px high with the label on top, 44 px pill buttons, tinted pills, maintenance rows with a red Remove.
- The approved macOS-style design (design handoff) is the base look of the default theme at night: window #0f1626 with title bar #151d31, cards #172034, grey buttons with radius 8 (10 and 40 px tall in Settings), a flat #0a84ff main button, a 50 x 30 switch with a 24 px thumb (green when on), fields #0b1120, tab bars with a blue ringed active tab, line icons only. The link window and the config window follow their screens: coloured line icons in the action row, icon tiles on the boxes, coloured charts, three equal buttons in the config window.
- Server window like the reference: flag and copyable address in the header, line icons on every button, blue box titles with icon tiles, compact action bar with Reboot at the right, coloured charts (CPU blue, RAM purple, load blue, download green, upload orange, connections cyan) with icons, a blue-edged window.
- Tunnel Doctor page: the six steps are cards with a coloured icon tile and a blue title, the settings row is tidy; check boxes are blue rounded squares with a tick; the Settings menu item has its old icon back and no text shadow.
- Side menu like the reference (coloured line icons, a chevron on each item, a blue active item with a bar); every tab bar is outlined with a blue active tab; the Add buttons are the blue glossy one; the Servers toolbar fits one row on wide screens and its search has a magnifier; the theme tiles are twelve in a row with a blue ring on the chosen one; Save sits at the right in every settings tab.
- Settings are built like the reference pictures: every group is a rounded row-card (icon tile, title with its sentence, fields with their label on top and a small icon inside, buttons at the right); the Security items have a switch or a button and a chevron; the password card shows its three boxes with a lock and an eye; Backups cards carry Back up now / Restore / Download with icons and the state line under them; the AI and Shop bot tabs follow the same cards; selects have an icon and a thin chevron; the tab bar has line icons and a blue active tab. The main button of the default theme is the blue glossy one.
- Buttons: one soft raised look everywhere - the main button is the theme colour, glossy, with a soft glow; the others are raised glass pills (white in day mode); delete buttons are red; Change password has a lock icon. The switch is the iOS pill (green, oval knob). New theme Soft: white raised surfaces and blue glossy buttons in day mode, a blue night version in night mode; day and night stay separate for every theme.
- A server that is switched off is called off within about 20 seconds: a new pulse job knocks on the SSH port of every server every 10 seconds (one TCP connection, 3 s); two misses in a row mark it off and one hit puts it back. The full SSH sample (every 2 minutes) still reads cpu, ram and disk, and a server whose login is broken keeps that verdict. The Servers page refreshes every 8 s.
- A node that drops off a Marzban / PasarGuard panel shows within half a minute (the panels check runs every 30 s instead of every 2 minutes).
- Settings: no boxes - every group is plain lines (name, state, an explanation underneath the main items, their buttons in one column right after the text; the explanation of a form row shows when the mouse rests on it). The switch is drawn correctly (the knob stuck out of its track). Everything else is a plain line (two-step sign-in, recovery codes, secret path, the two backup parts, the probe install) or plain rows (daily report, maintenance windows, schedule, appearance, cores, service settings). A box is only as wide as its content and the control sits right after its label (small boxes for numbers); groups of many short rows (rules, password, bot server, schedule, AI, daily report) show them two per line. The theme row, the tunnel cores and the service settings take the frame. The About strip says Up to date / Update available in one word with the commits underneath.
- Tunnels list: a cut tunnel says what is wrong in two or three words in place of the state (foreign server off, Iran server off, GRE link cut, service stopped, no data passes), and a tunnel whose server is off is drawn cut (red dashed wire with a cross) like a down one.- Settings, last touches: the recovery codes appear in a text box inside their own line and the secret path has a text box to type your own path (Set / Random / Off); the item buttons are one size in one column; Current (the old password) sits alone on its line with an amber edge, New and Repeat share a green edge; the status strip is the one boxed piece; every row has a fixed label column and a fixed control column, so the controls line up.

- Settings, final form: the rows are grouped under small captions (Connection / Bot files, When it is called down / How often it repeats, Primary probe / Standby probes, Key / Use) and every box in a row has the same size; the password has its name on top, its three boxes in one row and the button under them; each main item (two-step, recovery codes, secret path) is one row: name with its explanation, a text box, its buttons in one column; the status strip is the only boxed piece; the switch is drawn correctly.

- Settings, every tab built like Security: a name on top, each text box under its label, the buttons beside the boxes; the password boxes sit one above the other, each with a show / hide eye, and the button under them; the switch is drawn with its own floating knob. The alert rules are two numbers now (call it down after N failed checks - the same for tunnels, servers and configs - and remind every N hours); the daily report shows only the hour.

## 1.17.37

- Settings rebuilt from scratch: one frame per tab, from the first tab to a little past the last one (the description strip and the tab bar end at the same edge), only as tall as its content; inside it grouped lists like a phone's settings (a header with icon and state, rows with the label left and the control right, switches, one action row), stacked one under the other; inside a group the rows sit two per line, so a wide screen leaves nothing empty. The page header scrolls away and the rows of tabs stay locked at the top. The same system on every tab: Security, Alerts, Backups, Probe, AI, Shop bot, Appearance, About & update.
- Phones: a layout of its own (mobile.css, loaded only up to 760 px): one column, a bottom tab bar with the page icons, every table turned into cards with the column names on the cells, windows as full-height sheets, 16 px inputs (no zoom), 44 px touch targets, and no sideways scrolling anywhere (checked on every page).
- Calm by default: nothing moves by itself any more - no drifting backgrounds, breathing icons, blinking LEDs, running logo lights, button shines or row-entrance animations. What you touch answers with a short soft transition. Appearance > Animations now has Calm (default), Rich (the moving backgrounds and the live logo) and Off. The font stack puts the system font (San Francisco / Segoe) first.
- Lighter and safer: the panel no longer announces its server software (server header off); every setting of the lists is drawn once, not per refresh.
## 1.17.36

- Persian: the Tunnel Doctor stories, repair events, network-watch events, installs, errors raised by the panel's own code, log lines of the Logs page and about 450 tooltips, hints, confirmations and toasts are translated (new files i18n.fa.doctor.js and i18n.fa.ui2.js). The translator now takes leading symbols (checkmark, cross) off a text before it looks it up - before, every text that began with one stayed English.
- Settings redesigned: every tab is one full-width card of the same minimum height; Security (shield strip and four cards, password included), Alerts (Telegram, daily report, rules and maintenance windows in one card), Probe, AI, Shop bot, Backups (panel and bot side by side), About & update (status strip, cores, service settings in one card), Appearance (all themes in one row).
- Fix: the lost copy function is back (the Copied mark of addresses and links).
- The BOM that a version bump had put at the start of nikatel/__init__.py is gone.
## 1.17.35

- Fix: copying an address or a link did nothing visible (and the click raised an error): the copy function had been lost in 1.17.30. It is back, with the solid green "Copied" mark on the address.
- Panels > Users: changing the page (or the search / status) redraws only the list and the pager, never the whole page; the list box is made at once at its fixed size and ends inside the window; the pager has seven slots of one width, so no button moves.
- New theme "Mono glass" (black and white frosted glass; only the special buttons keep their colour). The highlight colour of the active menu entry and tabs follows the theme (it was gold everywhere).
- Tunnel Doctor: "degraded" is shown as "unstable" everywhere; its settings moved from Settings to its own page (Settings keeps only the AI assistant).
- Settings > Shop bot box redesigned in three short rows.
- Iran gateway and Probe boxes redesigned (compact, one line per job); the Probe box says "connected" when the probe answers.
- The configs box keeps one height for every group, so switching groups does not make it jump.
- Configs list: the Alerts button is gone from its toolbar (the flag under the names was tried and taken out again).
## 1.17.34

- Name and logo: NIKATEL in the Unbounded font (static), subtitle "Network operations"; the logo has its own slow animation (turning ring, breathing glow, a light running along the N, signals along the tunnel). The side-menu net is now a live SVG: nodes drift softly, small signals run along the lines.
- Themes: day mode now works in every theme (each has its own light page; before the dark theme colours won). Button colours follow the theme (the "primary" button was gold everywhere). New very slow backgrounds: ocean waves, emerald light beams, royal twinkling stars, sunset sun, neon floor grid, gold light sweep, graphite scan line; all stop with Animations = Off and on reduced-motion. The Appearance box is compact (5 columns, one-line notes) with brighter previews.
- Servers list: a server picture (green online, red offline, amber warning, grey disabled) replaces the coloured edge; a foreign server's flag lies faded under its name and gets richer under the mouse. The "Copied" mark of an address is solid and sits on the address.
- Tunnel Doctor rows: one plain sentence per row and a "Detail" word for the full reason / log; the broken ` ` ` text and the squeezed Live button are fixed.
- Configs list: no Edit button in the rows (it is in the config window); rows grow a little under the mouse (also users, servers, tunnels).
## 1.17.33

- Panels > Users: the list is one fixed box down to the pager, which stays on the bottom edge; the rows are stretched to fill it, so nothing scrolls and no empty strip is left. The pager keeps seven slots of one width, so the buttons no longer jump when the page changes.
- Configs: a group lists its configs in the order of its subscription again (picking a few new nodes had cut the stored order down to those nodes, so the list fell back to the alphabet). The TLS column is no longer green: tls is blue, reality violet.

## 1.17.32 - 2026-10-11 - Sign out above the credits

* In the side menu, *Sign out* is above and the *Powered by* card with the version is under it.

## 1.17.31 - 2026-10-11 - What the Tunnel Doctor does, Settings tabs joined to their frames

* **Tunnel Doctor** has a box above its two boxes that says exactly what it does: it watches every tunnel, restarts one that stays down, repairs up to the level you chose, only looks at loss from 5 % and acts from 15 %, waits while a server is off, and tells every step in *History*. Every place that said *Smart repair* now says *Tunnel Doctor*.
* **The side menu has line icons of one family** (dashboard, servers, tunnels, panels, bot, settings), each in a colour of its own with a faint breath; under the mouse an icon turns brighter and moves (the tunnel arrows slide, the sliders of Settings move, the server lights blink).
* **The icon of the page you are on moves faster** than the others (a quicker beat and glow; the tunnel arrows, the server lights and the sliders run quicker).
* **The configs list** has bigger type in cards of the same size, with a gap between the cards and a clearer edge, so a config is found at a glance.
* **Moving between pages is smooth**: the old page stays (dimmed and still) until the new one has its data, then the new one fades in - nothing collapses into a *Loading* box and nothing jumps. **Typing in a search box no longer moves what is under it**: the list box keeps its height while its rows are filtered.
* **Settings**: each tab is a folder tab joined to the frame under it (its description and its card).
* The *Check configs from Iran* button is gone (a panel's subscription is added to Configs when the panel is added).

## 1.17.30 - 2026-10-11 - The user cards, finer

* A user card: the name with its note under it (the empty *Note* column is gone), the status as a pill, the traffic as *used* and *left* with a bar and its share (*3.2 % of 20 GB*), the expiry as a coloured pill (green, yellow within 7 days, red when expired).

## 1.17.29 - 2026-10-11 - Fewer buttons, a tidier panel bar and Add server

* The *Find countries* button is gone (a server's location is found when it is added).
* The bar of a panel shows only what is needed: the panel, its status (version and age of the check in its tooltip), *Users*, *Nodes*, *Traffic* and the buttons; the Load counter and the version text are gone.
* The *Add server* window is mid-sized with two equal boxes and one foot bar (the button and the help on one line); the empty space is gone.

## 1.17.28 - 2026-10-11 - Server Doctor switched off

* The *Server Doctor* tab is switched off for now (the page stays in the code behind one switch, `SERVER_DOCTOR` in `app.js`).

## 1.17.27 - 2026-10-11 - The users of a panel are cards

* The users list of a panel has no *Edit / Disable / Reset* buttons: a click on the card opens the user (*Disable*, *Reset usage* and the rest are in its window); the cards look like those of the servers and the tunnels, and the traffic column is wide enough to show *left* whole.

## 1.17.26 - 2026-10-11 - Server Doctor like Tunnel Doctor

* *Server Doctor* is two boxes in one frame joined to its tab, like *Tunnel Doctor*: *Needs a look* (a card per server with the cause in plain words and what to do) and *Healthy* (each with its CPU and memory); the extra bits are gone.

## 1.17.25 - 2026-10-11 - A lighter copy of an address

* Over an address the cursor is a modern arrow with a small copy badge and a copy mark fades in beside it; the *Copied* confirmation is faint and light (a pale green outline, below the address, so it no longer covers it).

## 1.17.24 - 2026-10-11 - Tunnel Doctor and Server Doctor

* **Smart repair is now *Tunnel Doctor*.**
* **Server Doctor** (a new tab of *Servers*): a card per server that needs a look, in plain words - off or not answering, its SSH identity changed (*Trust the new server*), the login refused (*Enter the SSH password*), CPU / memory / disk high or full, a failing GRE link, an address filtered from Iran, a bad route from Iran - the tunnels that depend on it, and buttons for what to do; the healthy servers are listed below.
* **Tunnel test and Tunnel Doctor are one frame joined to their tab**: the bar, the engines, the results and the log; *Working now* and *History*.
* The **CDN port column** is gone from the configs lists.

## 1.17.23 - 2026-10-11 - Animated titles, buttons off for a server that is off, a compact Add server

* A small animated drawing beside the title of **Nodes** (a server with blinking lights), **Tunnels** (packets running through a tunnel), **Panels** (bars of a panel moving), **Shop bot** (a bot that blinks) and **Settings** (a slowly turning gear); off with *prefers-reduced-motion*.
* **A server that is off has its buttons off**: in its window *GRE test*, *Filtering*, *Abuse-Defender*, *SSH password*, *Reboot* and *Console* are grey and say why; in the window of a tunnel one of whose servers is off, *Fix*, *Dry run*, *Health*, *Restart*, *Quality* and *Rebuild* are grey and name the server.
* **Nodes is now called Servers** (the side menu, the title, the dashboard counter and the hints that named it); *Nodes* stays the name of the nodes of a Marzban panel.
* **The buttons of Servers say what they do**: *＋ Add server*, *⇄ Check GRE links*, *🌍 Find countries*, *🔑 Download my SSH key* - in groups, the key as a button of the same height.
* **The names of the servers** are set in Poppins (semibold, a little air between the letters) - a face of their own.
* **The two big buttons have a style of their own**: *＋ Add server* is emerald (a light sweeps across it, the plus turns a quarter), *＋ Create tunnel* is violet (a ring of light pulses out, the plus turns half a turn).
* **The Add server window is built again**: two boxes (*Server*: name, host, role, checked; *SSH access*: user, port, password) in the same rows as the other windows, a strip *Install after adding* with the two options, and one foot bar (the button and a one-line help).
* **Switching a tab never makes the page jump**: a page keeps its height while it is redrawn, and a window that is redrawn (the tabs *One server / Several at once*, the tabs inside a window) only changes its content - it does not fly in again and it does not shrink.
* **Closing a window no longer redraws the page behind it** (the page jumped when a window was closed): only the window goes.
* **The duplicate configs of the group *Nikatel-bot* are cleaned**: the 23 copies the import had made (50 configs became 27, the 25 nodes and 2 that exist only once); the import now skips a node whose own name and endpoint the probe already tests.

## 1.17.22 - 2026-10-11 - The tabs of Nodes in one style, a server beside its title

* The two tabs *Iran* and *Foreign* carry the same kind of icon: a round badge - the flag of Iran and a globe (the Windows emoji of the flag showed as `IR`, the other was a different drawing).
* A small server whose lights blink sits beside the title of *Nodes*.

## 1.17.21 - 2026-10-11 - The red counters are always red

* In the bars, *Problems* (Nodes) and *Offline* (Configs) always have a red light and a red number, also at 0: they count what is wrong, so the colour says which counter it is.

## 1.17.20 - 2026-10-11 - Round flags, nothing slides under the status bar

* **Flags are round badges** (24 px, a thin ring) in Nodes and Configs. They are served by the panel (`/flags/xx.png`: the panel server fetches each flag once from flagcdn and keeps it), so the browser no longer waits for a foreign server for them and the page stays whole when that server is slow or blocked.
* **The status bar no longer sticks** under the header on a wide screen: the tabs of a page could slide under it and look stuck.

## 1.17.19 - 2026-10-11 - The rows of Nodes and Tunnels are cards

* **No buttons on the rows** of *Nodes* and *Tunnels*: a click on the card opens its window (the actions are there: for a tunnel *Health* and *Restart* joined the button bar, *Rename* / *Ports* / *Remove* are in the header; for a server *Console* / *Edit* / *Delete* are in the header). A server the panel cannot log in to keeps its *Password* button.
* The cards are quieter and finer: a soft gradient, a lift and a chevron on hover.
* **An address copies itself on a click** (in both lists), with a *✓ Copied* pop and a green flash where it was clicked.

## 1.17.18 - 2026-10-11 - Coloured marks with a meaning on the numbers of a bar

* The numbers in the bar of *Nodes*, *Configs* and a *Panel* have a coloured dot and number colour that mean something: **green** fine (all online, no problem, load under 60 %, average delay under 800 ms), **yellow** take a look (load 60-80 %, delay 800-1000 ms), **red** wrong (a server offline, a problem, load over 80 %, delay over 1000 ms, configs offline, a node not connected), **blue** just information (totals, users, traffic). The tooltip of each says what the colours are.

## 1.17.17 - 2026-10-11 - No second copy of a node already tested

* **Why *Nikatel-bot* showed 50 configs**: *Check configs from Iran* imports the subscription of the panel's test user; the probe already tested the same nodes (from the older import, under their own names), and the import only compared **names**, so every node came a second time as *Nikatel-bot <node>*. The import now compares the **node** (protocol, address, port, transport, security - the user id and the name do not count) and skips a node that is already tested under another name, also inside one subscription.

## 1.17.16 - 2026-10-11 - Gateway & outbounds moved to Panels

* **Gateway & outbounds** is now the third tab of **Panels** (Configs | Panels | Gateway & outbounds), not a tab of Nodes; its tab has a colour of its own (cyan) and its page sits under the tab like the others.

## 1.17.15 - 2026-10-11 - Nodes: no duplicate names, the numbers in the bar

* **A server name is never added twice** (capitals do not count: `TR1` is `tr1`): adding or editing a server, and adding several at once, say plainly what is taken - *a server named X already exists (Iran / foreign)* or *the address A belongs to the server X*. In *Nodes* a name carried by more than one server is marked `⚠ ×N`.
* The `×N` mark of the Iran servers in the tunnel list is gone (it was a mistaken reading); the `⚠ ×N` of a foreign server that ends several tunnels stays.
* **Nodes**: the four tiles (Online, Problems, Load, Traffic) are numbers in the toolbar row.

## 1.17.14 - 2026-10-11 - A server that is off: said plainly, and nothing is done until it is back

* **Smart repair says which server is off**: a full-width box *Servers and network* above the two columns has a card per server - *Iran server X* / *Foreign server Y* - marked **OFF** (it does not answer) or **NETWORK DISTURBED** (its tunnels lose packets together), the tunnels that depend on it, and what the repair does about it. In the list, a tunnel whose server is off says *Iran server X is off or does not answer - nothing is done until it is back* and *waiting for the server*.
* **Nothing extra is done while a server is off**: the automatic restarts, the smart repair and the look at packet loss skip every tunnel one of whose servers is off; they start again by themselves when it answers.
* In the tunnel list an **Iran server that carries more than one tunnel** is marked `×N` (blue, calm) like a foreign server that ends more than one (`⚠ ×N`, yellow); the tooltip lists the tunnels.
* **The panel opens faster** (and the reload no longer waits for anything): the fonts (Inter, Poppins, Vazirmatn) are served by the panel itself - before, the page waited for Google Fonts, slow or blocked from Iran; the versioned files (`?v=`) and the fonts are cached for good, so a reload asks for nothing; the page asks for its data at the same moment as the sign-in check (one round trip less) and the tunnel list reads its three answers together; the page no longer re-animates itself on every refresh (the entrance runs only when another page is opened, 0.14 s).
* **Smooth scrolling** in every scroll box (the wheel glides to its target; a box at its end passes the wheel on to the one around it; off with the animations setting).
* **Dashboard search finds sections** (Tunnels, Tunnel test, Smart repair, Backups, Shop bot tabs…), not servers, tunnels or configs.
* **Hosts by port, tidy**: a row per port - tick, port, count, a bar of how many are on, the inbounds it serves; no switches on the rows (the toolbar menu works on ticked hosts). **Users**: a status switch, the count as a chip. The head of a panel (its bar and the Users / Hosts bar) is one frame.
* **Configs**: the four tiles are numbers in the top bar, the bar of the groups is joined to it; the config window is one bar of tests and three boxes of six rows; the counts of the group chips are readable.
* **Fix**: a server renamed in *Nodes* now really renames its tunnels (the rule of 1.17.9 was written and tested, but not wired into the server edit).
## 1.17.13 - 2026-10-11 - ping/loss% column

* The *Ping* column of the tunnel list is called *ping/loss%* and every row shows both, with a slash: `88 ms / 0%`; the percentage is yellow when the link loses packets.

## 1.17.12 - 2026-10-11 - Ping arrows aligned in every row

* The ping arrow of a tunnel with packet loss is no longer taller than the others: the loss is a small pill on the ms line, so every row has the same height and the arrows sit at the same place. The lines fade out at their tails like a trail; unstable and down tunnels keep their dashes.

## 1.17.11 - 2026-10-11 - Smart repair in two columns

* *Working now* and *History* of the Smart repair page sit side by side, the same height, each scrolling inside its own box.

## 1.17.10 - 2026-10-11 - Slimmer *Recently removed*

* The *Recently removed* box of the tunnel list is slim: one line per removed tunnel, smaller buttons.

## 1.17.9 - 2026-10-11 - Rename a tunnel; tunnel names follow their servers

* **✎ Rename** in the tunnel window (next to *Ports* and *Remove*): lowercase letters, digits and - (up to 15). Only the name in the panel changes; on the servers the tunnel keeps the name it was built with (new ``sname``: the GRE interface, units and files), so nothing there is touched and repairs, checks and removal keep working. Configs that name the tunnel and its alert mute follow the new name.
* **A server renamed in Nodes renames its tunnels**: a tunnel that still carries the name the panel made for it (``<foreign>-<iran>``, with or without a ``-2``) gets the new one; a tunnel you renamed by hand keeps its name.
## 1.17.8 - 2026-10-11 - Create tunnel in layers, replace the tunnel that holds the port

* **Replace the tunnel that holds the port**: when a wanted port is used on the Iran server by another tunnel of the panel (even an *up* one) the check offers a tick *replace it* - the tunnel is removed first (its other ports are named, it stays restorable for 30 days under *Recently removed*) and the new one is built. Until the tick is on, *Build* waits; a port held by a process the panel does not know still stops the build.
* **The Create tunnel window is laid out in layers**: three equal boxes - *Servers* (Iran with *auto*, Foreign), *Tunnel* (type with a one-line hint, ports) and *Check* (what the port check says, where the replace tick appears) - a collapsible *Advanced settings* box (four fields to a row) and a foot bar with *Build* and *Test this pair first*.
## 1.17.7 - 2026-10-11 - Hosts by port

* **Hosts** are shown by port: one row per port (how many hosts, how many on / off); click a port to see its hosts with their inbound, remark, address and state. A tick on the port row ticks all its hosts; *Enable all* / *Disable all* on the row switch the whole port at once; the toolbar's *Do with selected…* works on any ticked hosts. Search and the inbound / state filters stay.
* *Users | Hosts* are back in the top row, as a box of their own beside the search box and the controls.
## 1.17.6 - 2026-10-11 - User window touches

* Clicking the subscription link shows a green *✓ Copied* in the corner of its box for a moment.
* The user window: *Add days* is gone, the rows of the *Account* box have room between them, and the subscription link copies itself when you click it.

## 1.17.5 - 2026-10-11 - The user window rebuilt, the panel test button, matching boxes

* **The user window of a panel** is rebuilt: one bar of actions on top (Add days, Reset usage, Revoke subscription, Disable/Enable; Delete user on the right), three boxes of equal height - *Usage* (used, left, lifetime, expires, created, online), *Account* (status, data limit, reset, expiry, note, the subscription link with Copy) and *Inbounds* (a row per protocol with its own tick) - and Save / Cancel at the bottom.
* **Users and Hosts** search box and controls box are the same kind of box, the same height.
* **Check configs from Iran** (panel top bar, was *Test config on probe*): first looks in Configs for this panel's subscription - it may be there under another name - and only when it is not there makes the small test user and adds its subscription.
## 1.17.4 - 2026-10-11 - Panels: users and hosts tidy, tick boxes, search

* **Users and Hosts** are built the same way: the search is a box of its own beside the controls (they no longer move while typing), a page holds what fits the window (no scrolling), numbered pages and the *Users | Hosts* tabs are in the bottom strip, the range text is gone.
* **Selection**: a tick in the table header selects the page, a counter shows how many are ticked (*select page* / *clear*), and one menu *Do with selected…* (users: Enable, Disable, Add days; hosts: Enable, Disable) replaces the three buttons.
* **Hosts**: search, an inbound filter with the count of each inbound, a state filter, grouped by inbound, the inbound as a pill.
* **Traffic of a user**: *used*, *left* and a bar with the account volume (*of 20 GB*); unlimited accounts say so.
* **Tick boxes** everywhere are real square boxes (gold, a tick or a dash for part-ticked); in the table they are no longer cut by the cell.
* **Every search box** shows a small round cross that clears the text (the shop bot's search boxes became real search boxes too).
* **Inbounds of a user**: each protocol row has a tick that selects or clears all of its inbounds (part-ticked shows a dash).
* A refresh keeps the scroll of a list box (the page jumped to the top while a server was being installed).
## 1.17.3 - 2026-10-11 - The users list without scrolling

* A page of the users list holds exactly the rows that fit the window (measured from the real row height), so the list never scrolls; numbered pages (1 2 3 … last, ‹ ›) and the range are under it.
* *Users | Hosts* moved from the top to the bottom strip, next to the page numbers; the space above the list is gone.

## 1.17.2 - 2026-10-11 - Panel numbers in the top bar

* The four boxes of a panel (Users, Nodes, Load, Traffic) are gone: their numbers sit in one framed strip of the top bar, details in tooltips; Nodes still opens the node list.

## 1.17.1 - 2026-10-11 - *unstable* in one box

* The tunnel count of the Tunnels page is one framed box with a dot and a number per state, and *degraded* is called **unstable** (like the state of each tunnel); the same word in the dashboard line and in the check / restart messages.

## 1.17.0 - 2026-10-11 - Restore from the panel

* Each part of the Backups card has a **↩ Restore** button: pick one of the files kept on this server, or choose a backup file from your computer (a download or a file from Telegram). A file made with another passphrase asks for it.
* **Panel part**: the file is checked (decrypted, the database inside loaded in memory), what runs now is saved first (`…-before-restore.tar.gz.enc`), the new data is staged and the panel restarts; the swap happens at start, before the database is opened, and a bad file leaves the old database in place (`restore-failed/`). Only the known files are restored, never the passphrase.
* **Shop bot part**: a safety dump of the live bot database is made on the bot server, then the backup is loaded (new reviewed action `bot_restore`; the dump is checked with `gunzip -t` before it touches the database).
* A read-only panel never restores. Every restore asks for a confirmation that says what will be replaced.

## 1.16.37 - 2026-10-11 - The certificate tick of Add server

* The *remember this certificate* tick of the Marzban-node option sits in its own row under the certificate box (it was laid out as a grid and floated over the box).

## 1.16.36 - 2026-10-11 - Tunnel test and Network advisor in one tidy view

* The *Network advisor* tab is gone: **Tunnel test** is one view. A bar holds the two servers (and "pair of a tunnel…" to take a tunnel's servers), **Quick: best today** (the five types that matter) and **Start test**; below it the engine chips (*Advanced* opens every transport, *Which one?* opens the situation guide - both closed by default), then the results beside a fixed-height live log.
* The results are best-first with a *best* badge; when a tunnel of the chosen pair exists, every working row has **Use for <tunnel>** (replace it, same name and ports, the old type comes back if the new one fails).
* The diagnosis window's *Network advisor* tile opens this view with that tunnel's servers picked.
* A cut tunnel shows a red cross in the middle of its two arrows in the tunnel list.
* Backups: the list of file names under the schedule is gone (each part already shows its latest backup and a Download button).
* **Stop in Tunnel test really stops**: the running build is cancelled at its next step and everything it made is removed (before, Stop waited for the whole transport, build and measurement); the button is small and shows *Stopping…*.
* Tunnel list: the name, type, status, ping and traffic columns are shown whole (the empty space was moved to them; tighter cell padding).
* The user window in Panels: one row per protocol; click it to see its inbounds with their ports (*all* / *none* per protocol, a count of ticked ones).
## 1.16.35 - 2026-10-11 - The tunnel window like the server window

* One button bar (Fix, Dry run, Quality, Mute alerts, Rebuild, History; Ports and Remove stay in the header) and three boxes of six rows - *Servers*, *Status*, *Link* - with the services shown as small dots (backhaul / GRE, green when active) instead of two long lines each. Nothing is said twice (the latency and the packet loss were in two boxes). The outages card is one wide row of four.

## 1.16.34 - 2026-10-11 - All the numbers of Resources in one column

* In the Resources box every row has the same three parts: the label, the bar (empty for Traffic and Sample), and one right-hand column that holds every number - the percent with its used / total beside it, the traffic, the age of the last sample.

## 1.16.33 - 2026-10-11 - Everything about a server in three boxes

* The server window shows System, Resources and *Access & status* only - three boxes of six rows each; the separate lines for tunnels and for the last results are gone, their facts are rows of the third box (GRE, Filtering or Site, Tunnels). Nothing is said twice: whether the Abuse-Defender is installed is the label of its button (*Abuse-Defender* / *Remove Abuse-Defender*). Short labels on one line.

## 1.16.32 - 2026-10-11 - A shorter button bar

* The server button bar lost its duplicates: *Connect SSH* and *SSH password* were two buttons for the same area (the panel's login of a server without one is the key button on its row in Nodes); *Windows ssh / PuTTY / MobaXterm* are one **Open in…** choice; *Forget host key* only shows, as **Trust the new server**, when the panel refuses a server because it was reinstalled (its SSH identity changed). What is left: GRE test · Filtering · Abuse-Defender (foreign servers) · SSH password · Open in… · Reboot.

## 1.16.31 - 2026-10-11 - One scrollbar

* A page with a list box had two vertical scrollbars (the page and the list). The list box is now sized to end exactly at the bottom of the window (also when the window is resized) and the page itself does not scroll: only the list does.

## 1.16.30 - 2026-10-11 - Small charts, no disk chart

* The charts of a window are three in a row and short; the Disk chart is gone (the disk is a flat line; its number is in the tiles and in Resources), so the whole monitoring part fits without scrolling. The Traffic tile shows both numbers on one line.

## 1.16.29 - 2026-10-11 - One box, one row

* In a server window the tunnels line and the last-results line are two rows of one box; the monitoring tiles (CPU, RAM, Disk, Traffic, Connections) are one row.

## 1.16.28 - 2026-10-11 - The server button bar is always one row

* The buttons of a server window share the width of the bar and shrink their padding and font when the window is narrow; they never wrap into a second row.

## 1.16.27 - 2026-10-11 - The server window, rebuilt from scratch

* **One button bar:** every action of a server (GRE test, Connect SSH, SSH password, Forget host key, Filtering, Abuse-Defender, Windows ssh, PuTTY, MobaXterm, Reboot) is in a single bar at the top, grouped by thin dividers; Console, Edit and Delete stay in the window header.
* **Three equal boxes with the same number of rows** (System, Resources, Access) so nothing is hollow; the CPU / RAM / Swap / Disk bars are four aligned columns (name, bar, percent, used / total). Under them one line of tunnels and one line of the last results (GRE, Filtering, Abuse-Defender, last sample), then the charts.

## 1.16.26 - 2026-10-11 - A dead tunnel that holds the port is replaced; the list boxes do not jump

* **Create tunnel:** when a wanted port on the Iran server is held by an old backhaul tunnel that nobody is connected to (the foreign side was replaced or the tunnel is down / unknown to the panel), the check says so (*Port 80 on ir1 is held by an old tunnel ... nobody is connected to it*) and offers a ticked box *remove it and use the port*; on Build the panel looks again, removes only that unit and its config when still nobody is connected, then builds. A port held by a live tunnel or by another service (nginx ...) still blocks, now naming the process.
* The list boxes show no horizontal scrollbar that comes and goes and a hovered row no longer shifts; the vertical scrollbar keeps its place.

## 1.16.25 - 2026-10-11 - New servers are marked

* A server added in the last hour carries a gold **new** badge in the server list, and *· new* beside its name in the server choosers of Create tunnel and Tunnel test.

## 1.16.24 - 2026-10-11 - Every list is a fixed box that scrolls inside; shorter restarts

* Every list of the panel (server list, config list and cards, panel users and hosts, shop bot tables ...) sits in a box of a fixed height with its own scrollbar and a header row that stays on top; the page itself no longer grows with the list. The scroll position survives the refreshes.
* *HTTP 502 / the panel did not answer* in the Messages window: that was a restart of the panel for an update (14:15:34 - 14:15:52) while a server was being added; the restart took 18 s because the panel waited for open connections to close. It now waits 4 s.

## 1.16.23 - 2026-10-11 - No datacentre name in the server list

* The server list no longer shows the datacentre / provider name beside the server name (it is in the server window); the *network* and *bad route* badges are shown whole instead of being cut off.

## 1.16.22 - 2026-10-11 - The diagnosis list reads on one line

* In the diagnosis window *What was checked* is a two-column list with the tick first and the text beside it (it used to wrap every sentence into five lines in a narrow label column).

## 1.16.21 - 2026-10-11 - A foreign server with a bad route from Iran is named

* Every five minutes each Iran server already pings a few foreign servers; those pings are now also read the other way round: a foreign server that most Iran servers lose packets toward (two or more looked, in the last 15 minutes) is marked **bad route from Iran** in Nodes, listed in the dashboard Problems (with a click to open it and a Test button), and told in the events with the numbers. One lossy Iran server alone never blames the foreign server.

## 1.16.20 - 2026-10-11 - A rebuild never makes the tunnel vanish

* While a tunnel is being rebuilt (removed, then built again - about a minute) its row stays in the list as *rebuilding…*; before, the row disappeared and looked deleted.
* A rebuild keeps a copy of the tunnel in *Recently removed* too, so a rebuild whose build fails can be restored with one click.

## 1.16.19 - 2026-10-11 - Tighter detail windows

* The server / tunnel / config windows are tighter: smaller info rows, the monitoring tiles in one compact row, thinner timeline, shorter charts, smaller tool tiles. The GRE row no longer shows the meaningless *UNKNOWN* link state of a GRE interface.

## 1.16.18 - 2026-10-11 - A job thread can no longer die

* **Root cause of a server shown offline for two hours after it was back:** a *database is locked* error while a job wrote its own status row killed five job threads at the same moment (servers, tunnels, repair, alerts, netwatch); the panel kept serving with no checks. The loop of a job now survives any error, the status row is written with retries and never stops the job, and the dashboard starts a dead job thread again and names it.
* A tunnel that passed its last three checks is *up* again at once; the flap rule no longer keeps it *unstable* for ten minutes after an outage that is over.
* The info boxes of a window no longer stretch to the height of the tallest one (the Access box had a lot of empty space); in Resources the bars, numbers and details are three aligned columns.

## 1.16.17 - 2026-10-11 - A bigger mux stream window

* Real measurement (one Iran ⇄ one foreign server, 100 ms round trip): a multiplexed Backhaul stream was capped at ~37 Mb/s and a BackPack one at ~18 Mb/s, while a plain TCP stream reached 2 Gb/s - the cap is the mux stream window divided by the round trip (512 KB / 0.1 s ≈ 40 Mb/s). New tunnels get a 2 MB stream window and a 16 MB receive buffer (Backhaul and BackPack); *Tune all* applies it to existing tunnels.

## 1.16.16 - 2026-10-11 - The speed test shows its working

* A tunnel test writes what its speed measurement really did (bytes, seconds, who was on the socket; per-stream rates for the other engines), after one real run reported 20 Gb/s through a Backhaul TCP tunnel - a number that can not be true and is being traced.

## 1.16.15 - 2026-10-11 - Lightness first

* No blur anywhere (header, menu, windows, buttons, the dark overlay), small shadows, short transitions, a quick page-open animation, no brightness filters on hover, and the ping packets move by transform (the browser composites them, nothing is re-laid out every frame).
* **Backhaul · wssmux built for real:** the server side listened with no certificate and never started (*failed to listen … no such file*); a self-signed certificate is now made on the Iran server and named in the config (removed with the tunnel).
* A tunnel comparison explains the failures the real tests showed on Iranian routes: clear-text WebSocket (use wss) and QUIC (use UDP / KCP) are filtered.
* A tunnel that is unstable or down changes only its arrow (yellow stepped packets, or a red dashed line without packets and a red number), never the colour of its row.

## 1.16.14 - 2026-10-11 - Slower, fainter packets on the ping arrow

* The packets that run along the ping arrow move about three times slower and are fainter.

## 1.16.13 - 2026-10-11 - A cut-short tunnel test no longer blocks the next one

* Real test of the engines (one Iran ⇄ one foreign server): Backhaul, BackPack, BackPack Direct, Paqet and Chisel all build, connect, measure and clean up. The one failure found: a test that a restart of the panel cut short left its temporary units on the servers, and the next Backhaul test was refused because the test port was in use. Now every test first removes such leftovers on both servers (only test-* units and a unit that forwards the reserved test port), the panel removes a cut-short test at startup, and a failed test says why in the comparison table.

## 1.16.12 - 2026-10-11 - A little life when a page opens

* When a page opens its rows, tiles and cards come in one after the other (never on the refreshes), and cards lift softly under the mouse. Off with Settings → Appearance → Animations off, and for people who asked their system for less motion.

## 1.16.11 - 2026-10-11 - Shared SSH connections the simple way

* The shared SSH connection per server is now started by the first command itself (ControlMaster=auto) instead of a separate starter; the starter could leave a stray background connection for every start that raced another (93 of them were found after a few hours).

## 1.16.10 - 2026-10-11 - Inbounds per user; plain-words problems; a lighter bot page

* **Panels → user:** the user window lists the inbounds of the panel (grouped by protocol) with a tick per inbound; tick or untick and Save. The ids of the protocols the user keeps are not touched (the links of the users stay valid); a protocol with no inbound left is removed, a new one gets its settings from the panel.
* **Dashboard → Problems:** every error in plain words (the raw text is the tooltip), a click on a row opens the tunnel / server / config, *show them* lists every config that is down.
* **Shop bot:** the overview of the bot page is kept for a minute, so opening the page again costs no SSH call.

## 1.16.9 - 2026-10-11 - The ping arrow in the exact middle, thin and faint

* In the tunnel list the Iran node is right-aligned and the foreign node left-aligned, so the ping arrow sits in the exact middle of the gap between them; the arrow is thinner and fainter, the ping number stays as it was.

## 1.16.8 - 2026-10-11 - The ping arrow, twice as big and in the middle

* The ping arrow in the tunnel list is twice as big and sits in the middle of the gap between the Iran node and the foreign node (the Iran column is narrower, the ping column wider).

## 1.16.7 - 2026-10-11 - Tunnels: buttons joined to the list; smaller monitoring

* On Tunnels the row of buttons is one piece with the list box; the groups and their tables sit inside it.
* The monitoring part of a window (config, tunnel, server) is smaller: tighter tiles and charts, no helper text.

## 1.16.6 - 2026-10-11 - Nodes: tiles on top, buttons joined to the list

* On Nodes the four tiles come first (in their frame, with the Iran / Foreign tabs standing on it); the row of buttons sits under them and is one piece with the list box.

## 1.16.5 - 2026-10-11 - Small tool tiles, the explanation on hover

* In the server, tunnel and config windows the tool tiles are one line each (icon and name); the explanation shows when the mouse rests on a tile.

## 1.16.4 - 2026-10-11 - The ping arrow is as wide as its number and centred

* In the tunnel list the double arrow is exactly as wide as the ping written over it and sits in the middle between the Iran node and the foreign node (it used to stretch to the foreign column).

## 1.16.3 - 2026-10-11 - Backups every 15 minutes if you like

* The backup can run every 15 / 30 minutes, every hour, 3, 6 or 12 hours, or once a day at an hour. With a short interval the files can go to Telegram every time, once a day only, or never (the rest stay on the panel server for the kept days).
* The backup password: said in plain words (the files are encrypted with it; typed only on restore) and a **Generate** button makes a strong one.

## 1.16.2 - 2026-10-11 - One view in every detail window

* The windows of a server, a tunnel and a config have no tabs any more: the boxes, the charts and the tools are one scrolling view under thin headings. The things you do most are in the window header: server *Console / Edit / Delete*, tunnel *Ports / Remove*, config *Edit / Delete*. The server console is the only other view, with a *Back* link. Made for people who are new to the panel: nothing is hidden behind a tab.

## 1.16.1 - 2026-10-11 - Boxed tab strips; the user list changes page from the bottom

* The tab strips that stood alone (Settings and its inner tabs, Shop bot, Users / Hosts of a panel) are in the same soft framed box as the others.
* The user list of a panel sits in the framed list box; its pages change from a strip under the list (‹ Previous · 1–40 of N · Next ›) and the list is shown from its top again.

## 1.16.0 - 2026-10-11 - More themes and a redesigned dashboard

* **Themes.** Ten themes in Settings → Appearance, picked from small previews: Aurora glass (default), **Ocean**, **Emerald**, **Sunset**, **Royal**, Gold luxury, Cyber neon, **Graphite** and the plain **Classic** admin look (solid surfaces, thin borders, square-ish corners, a flat blue button, no glow or blur), Minimal. Every theme sets its own colours, corner radius and the art behind the page (waves, a dotted grid, stars, a fine grid), and has a day version.
* **Dashboard.** The search (its list is now solid, no longer printed over the cards) sits on one line with four numbers (tunnels up, nodes online, configs up, problems); the three cards follow, and a row of free slots under them is kept empty for what is added later.

## 1.15.5 - 2026-10-11 - The ping lives on the tunnel arrow; Tools tiles in equal columns

* The Latency column is gone: the ping is written over a longer double arrow between the Iran node and the foreign node, in the colour of the tunnel (green / yellow / red, dashed when down), with a small packet running along it - faster when the ping is lower - and the packet loss under it when there is some.
* The Tools tiles of a server / tunnel window are three equal columns, every tile the same height with the title on one line and the note on two.

## 1.15.4 - 2026-10-11 - Info boxes of the windows: same size, tight

* The compact page density of 1.15.3 (slimmer header, tiles and table rows) is taken back out.
* The info boxes of a server / tunnel window (Servers, Quality, Link, Status, Services ...) are all the same size and tighter, so the whole window is in view; the wide last box keeps its rows in two columns.

## 1.15.2 - 2026-10-11 - A lost SSH connection no longer crashes a job

* An SSH connection that dropped with no message (exit 255, empty output) raised `AttributeError` inside the error text builder and killed the whole job run (seen in the configs job); it is now an ordinary *ssh connection lost* error that the jobs already handle and retry.

## 1.15.1 - 2026-10-11 - A lighter panel

* **One shared SSH connection per server** (kept two minutes after its last use) instead of a new handshake for every short command: the handshakes were most of the CPU the panel used and made the tunnel check run for ~40 s of every minute. If the shared connection can not be started the commands connect on their own as before (switch it off with NIKATEL_SSH_MULTIPLEX=false).
* **Three repair jobs became one** (*repair*: the plain restarts every minute, the smart repair and the look at lossy tunnels every five minutes). The DNS *failover* job is not started while the failover is switched off. Jobs that no longer exist leave the Jobs list.
* The browser has less to paint: no blur behind every button, row and card (only the header, menu, windows and toasts keep it), no endless movement of the up-arrows. The Shop bot and Settings pages no longer refresh themselves every 15 seconds (each refresh of the bot page ran SSH and database queries).

## 1.15.0 - 2026-10-11 - Backups: one file and one Telegram topic per part

* **Parts.** The panel and the shop bot are backed up separately: *Panel* (database, settings, SSH keys) and *Shop bot* (the bot database dump, taken on the bot server and fetched over SSH; the server keeps its last 7 automatic dumps). Each is its own encrypted file, has its own switch and its own **Back up now**; one part failing does not stop the other.
* **Telegram topics.** Every part can have its own topic (a separate page) in the Telegram group: *＋ Telegram topic* makes it (the group needs Topics on and the bot must be an admin that may manage topics) and the backups of that part go there; *unlink* sends them to the main page again.
* **Settings → Backups** is tidier: one tile per part (switch, latest file, topic, Back up now, Download), one line for the schedule, the passphrase and restore hint folded away.

## 1.14.22 - 2026-10-11 - Restore a removed tunnel; Detect on servers is gone

* **Restore.** Before a tunnel is removed on request (Delete, or *replace* in Create tunnel) the panel keeps a copy; *Recently removed* above the tunnel list shows it with **Restore** (builds it again: same name, same servers, same ports, a new GRE key) and **Forget**. Copies are kept for 30 days.
* The **Detect on servers** button and its code (scan, add to panel, remove incomplete) are removed: the checks before building make it unnecessary.

## 1.14.21 - 2026-10-11 - Tidy info boxes and Tools buttons

* The info boxes of a server / tunnel window (System, Resources, Access ...) have one label column and one value style (sizes, weights, units), and long values wrap instead of running out of the box.
* The Tools buttons of servers and tunnels are the same size, with an icon tile, a title and one short line; group headings have a fading line; Danger buttons are tinted red.

## 1.14.20 - 2026-10-11 - Tune all applied and removed

* The **Tune all** button is removed from Tunnels (no extra features for now). It was run once on the tunnels this panel built; new tunnels still get the newer defaults.

## 1.14.19 - 2026-10-11 - No lonely tab

* A page with only one inner tab (Smart repair now that the failover is off: only *Working now*) shows no tab bar at all.

## 1.14.18 - 2026-10-11 - The inner tabs are in a box too

* The tab row inside a page (Tunnel test / Network advisor, and the other pages that have one) sits in the same soft framed box, and the folder tabs above stick to it.

## 1.14.17 - 2026-10-11 - The tiles are in a box too

* The tile rows (Online / Problems / Average load / Traffic and the like) sit in one soft framed box on every page, like the toolbar and the lists.

## 1.14.16 - 2026-10-11 - Bigger centred tunnel arrows; folder tabs on Configs

* The tunnel arrow is bigger and sits in a column of its own, centred between the Iran node and the foreign node.
* The folder tabs (Configs / Panels) stick to the box with the buttons on the Configs page too; the tiles moved under that box.

## 1.14.15 - 2026-10-11 - Folder tabs on Tunnels and Nodes

* The sub tabs (Tunnel manager / Tunnel test / Smart repair, and on Nodes Iran / Foreign / Gateway) stand on the box under them like folder tabs; the open tab is one piece with the box. On Nodes the box with the buttons now comes first and the tiles under it.

## 1.14.14 - 2026-10-11 - The tunnel arrow between the nodes

* In the tunnel list a **⇄** arrow sits between the Iran node and the foreign node, green (and gently moving) when the tunnel is up, yellow when unstable, red when down; hover shows the tunnel.

## 1.14.13 - 2026-10-11 - 3D gold button, no numbers in the menu

* The primary button (Create tunnel and the others like it) has dark text on gold so it reads, a raised 3D look, a light sweep on hover, grows a little when the mouse is on it and sinks when pressed.
* The numbers beside Nodes, Tunnels and Panels in the left menu are gone.

## 1.14.12 - 2026-10-11 - Nodes: Iran and Foreign are separate pages again

* Undone on request: the single mixed Servers list of 1.14.11. Iran and Foreign are separate tabs at the top again; the buttons and the search stay in one line, with All / Problems on the right of that box.

## 1.14.10 - 2026-10-11 - Tunnels and Nodes: one box

* On Tunnels the All / Problems tabs and the search join the buttons in the one top box (the search is its own segment); the second box is gone. Nodes has its search in its box the same way.

## 1.14.9 - 2026-10-11 - Configs: one tidy box, a search that hides at once

* On Configs the buttons, the view switch and the search are in one box, the search as its own segment. Searching hides cards / rows at once without redrawing the page, so the buttons do not jump.

## 1.14.8 - 2026-10-11 - A soft backdrop behind every list of cards

* The rows of Nodes, Tunnels and Configs and the config grid sit on a soft glass backdrop (two faint colour glows, an edge that fades out), in the dark and the light theme.

## 1.14.7 - 2026-10-11 - Soft framed filter strip

* The tabs and the search box of Tunnels sit together in one soft frame whose edge fades out (it fades in when the page opens); the search bar of Nodes and Configs gets the same frame.

## 1.14.6 - 2026-10-11 - Deleting a tunnel asks once

* Delete on a tunnel asks one question only; the second box where the tunnel name had to be typed is gone.

## 1.14.5 - 2026-10-11 - A second tunnel to the same foreign server is asked about

* Create tunnel: when the foreign server already has a tunnel (and it is not ticked for replacing), pressing Build opens a box: **Remove it and replace it** or **Add as a second tunnel** (or Cancel). Nothing is built silently any more.

## 1.14.4 - 2026-10-11 - Search boxes in their own bar, a small search on the dashboard

* The search box of Nodes, Tunnels and Configs now sits in its own bar under the buttons. On Nodes and Tunnels it hides rows at once without redrawing the page, so the buttons beside it no longer jump.
* The Dashboard has a small search: type a few letters and the servers, tunnels and configs whose name (or a part of it) starts with them are listed; a click opens it (arrow keys + Enter work too).

## 1.14.3 - 2026-10-11 - A cut tunnel is tested from one other Iran server; duplicates are marked

* When a cut tunnel could not be healed and moving it is off, the Smart repair now builds ONE temporary test tunnel from the best other Iran server to the same foreign server (hidden from the cards, removed again) and says what it means: the foreign server works from there = the route / the Iran server is the fault; it is poor there too = the foreign server or its provider. Nothing is moved and no server IP is changed (the DNS switch stays off).
* A foreign server that ends more than one tunnel gets a **⚠ ×N** mark in the tunnel list (hover: which tunnels); two servers with the same address get a **duplicate IP** mark in Nodes.

## 1.14.2 - 2026-10-11 - The search box keeps the cursor

* Typing in a search box (Tunnels, Nodes, and the other pages) no longer throws the cursor out of the box when the list is redrawn: the focus and the cursor position are put back after every refresh.

## 1.14.1 - 2026-10-11 - Two builds never share a server; a tunnel must stay up before it is called ready

* **Cause of a tunnel reported as built but DOWN:** a temporary test build and a real build to the same foreign server ran at the same time, both took the same free control port, and when the test finished, its clean-up removed the shared service and config files. Now a build holds both of its servers for its whole run; a second build on the same server waits (one story line says so) and then picks its own port.
* A build is not reported as *ready* until, 8 seconds after the data test, both backhaul services are still running and the control connection is still established; otherwise it is rolled back with the service log.

## 1.14.0 - 2026-10-11 - Smart test goes on to the next server

* *Test this pair first* with **Smart** as the Iran server no longer stops at the first server: the Iran servers are tested one after the other in the order of the ranking (quality first, then free resources); a server whose test scores under 50 is left for the next one, the first that scores 50 or more is the answer and is selected in the form (else the best of those tested is named). Up to 4 servers; every result is a line of one story.

## 1.13.4 - 2026-10-11 - The steps of a test are shown

* A tunnel test now writes the steps of its temporary build into its own story as they happen (preflight, GRE, backhaul, data path), so the Test window never sits on one line.

## 1.13.3 - 2026-10-11 - Re-tune takes the idlest tunnels first

## 1.13.2 - 2026-10-11 - Re-tune can start with one tunnel

* The re-tune can be started for the first N tunnels only (a pilot before the rest).

## 1.13.1 - 2026-10-11 - Smart Iran choice: quality, then resources, then the next server

* **Smart Iran server**: ranked by QUALITY first (ping and loss to the foreign server, in steps of 10 points); among servers of about the same quality the one with MORE free resources, then the one carrying fewer tunnels. When the build on one fails, the next one (up to 4) is tried in the same story instead of stopping.
* **Fix**: a foreign server without the Backhaul program could not be built for - copying the program was refused by the file-mode check (0755). *Test this pair first* stays in the Create window (the Tunnels tab): the progress and the result are shown in it, nothing jumps to the Tunnel test tab.

## 1.13.0 - 2026-10-11 - New Backhaul defaults, applied to the tunnels that exist

* **Defaults for many users over lossy paths**: connection pool 16 → 24 (more parallel TCP connections spread a loss over more flows), stream buffer 256 KB → 512 KB (a long path needs more data in flight for speed). Mux channels stay 8: more channels per connection make one lost packet stall more users.
* **⚡ Tune all** applies the new values to the existing Backhaul tunnels - one at a time, at a quiet moment, only the two lines are changed in the config read from the server, both services restarted (a few seconds), health measured before and after; a tunnel that does not come back or got clearly worse (5 % more loss or 25 ms more latency) gets its original file back. Two roll-backs in a row stop the run. A busy tunnel is skipped.

## 1.12.1 - 2026-10-11 - A disabled server is shown, not hidden

* Create tunnel and the tunnel test list **disabled servers too**, greyed and marked *(disabled: enable it in Nodes → Edit)*, instead of leaving them out without a word.

## 1.12.0 - 2026-10-11 - Plain status words, live

* *DEGRADED* is **UNSTABLE** with a short plain reason under it (*keeps dropping*, *12% packets lost*, *port closed*, *no data*). While the smart repair works on a tunnel the status cell says *checking…* or *repairing…* with a turning spinner, live (the list looks again every 4 seconds).

## 1.11.9 - 2026-10-11 - What is being installed, in the chip

* The *installing…* chip names what is being installed, shortened inside the chip; the full name opens, very faint, under the pointer.

## 1.11.8 - 2026-10-11 - Quieter busy chip

* The *installing…* chip in the server list has no second line under it.

## 1.11.7 - 2026-10-10 - Installs after adding a server survive

* **Fix**: the torrent block and the Marzban node did not get installed when ticked while adding a server. The tracker script applies thousands of iptables rules and takes minutes; the panel waited only 4 minutes, and every restart of the panel killed the install in the middle. Both installs now run DETACHED on the server, the panel follows them (install_status) for up to 45 / 25 minutes and tells how far they are, and an install that was being followed when the panel restarted is picked up again by the same story.

## 1.11.6 - 2026-10-10 - connecting… / installing…

* A new server's row says **connecting…** while the password login runs and **installing…** (with what: torrent block, Marzban node) while the installs run, instead of a dash; the list looks again every 3 seconds until it is done.

## 1.11.5 - 2026-10-10 - Test tunnels leave no trace in the lists

* The temporary test tunnels (test-N) are not listed, not checked, not repaired and no longer write *built* events; the tests themselves run exactly as before (build, measure, remove).

## 1.11.4 - 2026-10-10 - Needs attention becomes Working now

* The first tab of Smart repair is **Working now**: the tunnels in trouble, each marked *repairing* (the turning wrench), *looking at it* or *watching* (the repair starts by itself when needed), with a Live button.

## 1.11.3 - 2026-10-10 - No yellow for history or latency

* A tunnel whose loss is over is **up** again at once: a stretch of loss in the last checks makes it *degraded* only when the link really switches on and off. A high latency alone no longer colours a row.

## 1.11.2 - 2026-10-10 - Working now; quiet colours under 20 % loss

* Smart repair → Needs attention starts with **Working now**: every tunnel the smart repair is looking at or repairing, with the last line of its story and a Live button; the network disturbances sit under their own title.
* Packet loss **under 20 %** is no longer shown on the tunnel list and no longer colours the row (the Iran-abroad paths lose a few percent on a normal day); from 20 % the badge and the yellow row appear.

## 1.11.1 - 2026-10-10 - A wrench on what is being repaired; wording and probe fixes

* A **🔧 wrench that turns and taps** stands in front of every tunnel the smart repair is working on (tunnel list and Smart repair list); a read-only look does not count.
* *The link flaps* is said only when the link really switches on and off; a long lossy stretch says *lost packets in 9 of the last 10 checks*.
* The network probe of an Iran server no longer blames the server when ONE of its two foreign targets is dead or filters ping: it takes most of the targets to be lossy.

## 1.11.0 - 2026-10-10 - Network disturbances, hidden test tunnels

* **Disturbances of the network itself are detected**: every 5 minutes each Iran server pings the internet, other Iran servers and foreign servers (20 pings each); its connectivity is *fine / weak / disturbed / cut off*, a change is written to the events after two probes agree (a cut at once) and shown as a pill in the server list. Besides, when most tunnels of one server lose packets or are down together, the report names that server's network as the trouble, not the tunnels. Shown on Smart repair; the repair then only watches.
* **Temporary test tunnels are hidden**: the tunnels the panel builds to measure a candidate (named test-N) are not listed, not checked, not repaired, and a leftover of a test that died is removed after 40 minutes.
* A degraded tunnel's reason under its state is shown only for loss above 20 %.

## 1.10.4 - 2026-10-10 - Two repairs no longer trip over each other

* Only one tunnel test runs at a time; a repair that wanted to test while another was testing stopped with an error. It now waits its turn (up to 20 minutes). When the diagnosis finds the loss on one server's own network (Iran side, foreign side or both), no candidate is built or swapped - another type would suffer the same - and the tunnel is only watched.

## 1.10.3 - 2026-10-10 - Colours: loss yellow, outage red

* Packet loss of any size shows **yellow** (rows, the loss badge, Check all); only a full outage (tunnel down, server offline or without login) is **red**.

## 1.10.2 - 2026-10-10 - Fix: Nodes page error

* The All / Problems tabs of the tunnel list had landed in the Nodes page ("tmode is not defined", Foreign and Iran tabs did not load); they are now in the tunnel list, where they belong.

## 1.10.1 - 2026-10-10 - Mass disruption

* **Several servers gone at once** (3 or more offline, or a quarter of the tunnels down) is a mass disruption: the repair deletes and rebuilds nothing while it lasts (a tunnel is never removed because its server cannot be reached), says so once per half hour in the events and in a banner on Smart repair, and waits. When the servers answer again the tunnels that did not come back by themselves are restarted, a few at a time (at most 4 restarts, 2 smart repairs at once, the longest-down first).
* A finished repair says **All done - the problem is fixed.**

## 1.10.0 - 2026-10-10 - The repair looks early and swaps gently

* **Looks as soon as loss appears**: a tunnel with 5 % loss or more (or degraded / down) gets a read-only look every half hour: the diagnosis, where the loss is, and the decision, written to the tunnel's *Live* window. It only watches under 15 % (the level users feel).
* **Acts from the level users feel, and gently**: a lossy tunnel that still carries data is never cut on a guess. Candidates - a fresh GRE link and the other types - are built and measured beside it, and it is replaced only by one that scores at least 10 points better, and only at a quiet moment (under 1.5 Mb/s; at 30 % loss or more it does not wait), so customers are not cut. If nothing is clearly better the tunnel stays.

## 1.9.9 - 2026-10-10 - All / Problems in the tunnel list

* The tunnel manager has two tabs: **All** and **Problems** (the red and yellow rows: down, degraded, lossy, slow). The choice is kept in the browser.

## 1.9.8 - 2026-10-10 - GRE filter named as such

* When plain ping between the two servers passes (under 10 % loss both ways) but the GRE link is dead, the cause is now *GRE (protocol 47) is filtered on this route*, not *the link flaps*: the real fault, and the right steps (a new GRE link, another tunnel type).

## 1.9.7 - 2026-10-10 - Where is the packet loss

* **The diagnosis places the loss**: for a lossy, flapping or down tunnel the panel measures packet loss from five sides at once (20 pings each): this Iran server toward this foreign server, toward other foreign servers, the internet and other Iran servers; this foreign server toward this Iran server and the internet; other Iran servers toward this foreign server. The verdict - *Iran side*, *foreign side*, *only this pair's route*, *both* or *none* - is part of the cause, listed in what was checked, and handed to the AI with every number. Rows and small cards grow a little under the pointer.

## 1.9.6 - 2026-10-10 - Severity colours

* Server and tunnel rows are **red** when down or critical (tunnel: down or 15 % loss or more; server: offline, no login, CPU/RAM 92 % or more, disk 95 % or more) and **yellow** when bearable (degraded or 5 % loss or more, slow, CPU 80 %, RAM/disk 85 %, not checked yet). Rows and cards fade up softly under the pointer.

## 1.9.5 - 2026-10-10 - Automatic repair on by default, with a switch

* The smart repair is **on by default** (level: everything up to another tunnel type). Smart repair → Needs attention has an **Automatic repair** switch: off, the panel only watches and shows Diagnose / Fix with AI to work by hand.

## 1.9.4 - 2026-10-10 - Change SSH password

* **Nodes → a server → Tools → Change SSH password**: sets a new login password for the server's SSH user over the panel's key, or generates a strong one and shows it once (the panel keeps no password). Payloads of actions (files, passwords, keys) are no longer written to the log.

## 1.9.3 - 2026-10-10 - One Check all button

* *Quality test all* and *Health all* are one button, **Check all**: every tunnel is checked for real (up / degraded / down, does data pass), then its packet loss and latency are measured (six at a time), and a list shows the worst first. A loss of 10 % or more is marked *users feel it*, 3 % or more *some*. It only shows - nothing is repaired because of it.

## 1.9.2 - 2026-10-10 - The Iran filter check no longer cries wolf

* A clean address showed *Filtered in Iran* because 3 of 21 locations lost a ping. A server is now filtered only when many locations fail: 40 % or more with half or more of the pings lost, or at least a quarter (and 3) fully dead; a few weak routes are reported as such on a clean result. The row shows *GRE ✕* instead of *GRE ✕ not OK*; the footer text is dimmer.

## 1.9.1 - 2026-10-10 - A wrong SSH password is not silent

* Adding a server with a wrong password now asks for the password again (three tries) with the reason, instead of leaving a server that never logs in. A server the panel cannot log in to shows *no login* with the reason under it and a *🔑 Password* button on its row until the key is installed. Also the default Marzban certificate for Add server (Install after adding), kept in the panel database.

## 1.9.0 - 2026-10-10 - Install after adding a server; menu and footer polish

* **Add server → Install after adding** (optional, needs root): *Block public torrents* (the public Heclalava/blockpublictorrent-iptables script: tracker list, iptables DROP rules, refreshed daily by cron; it is run once at the end so the rules are active at once) and *Marzban node* (Docker and the official Gozargah installer; paste the certificate the Marzban panel shows for a node; ports 62050 / 62051). The server is added first, then one story tells what each install did and, for the node, what to enter in Marzban. Both are reviewed actions (only base64 reaches the script) and run only when the panel is not read-only.
* **Menu text in 3D** (a gradient face with a clean extruded edge, tilt on hover, a light running over the letters), a faded picture of connected nodes behind the menu, a footer box with "Powered by" and the version, and a Sign out animation (the panel folds away in 3D with a light sweep).
* Temporary test tunnels are called *test-N* (they were nkt-N). The smart-repair list is a fixed box.

## 1.8.2 - 2026-10-10 - ArvanCloud failover switched off for now

* The ArvanCloud part (Failover · CDN tab, DNS changes box, DNS names in Create tunnel, the move of tunnels to another Iran server) is hidden and does nothing until the tunnels and the smart repair are tested further. Nothing was deleted: the code, the saved key and the settings stay (ARVAN_UI in the web app and ailover.ENABLED on the server bring it back). Smart repair has one tab again and says plainly when a route problem needs a human.

## 1.8.1 - 2026-10-10 - A flapping link is not green; DNS names after the build

* **A link that answers only now and then is shown degraded, not up.** Before, a GRE link that failed and recovered every few minutes turned the row green between two failures (two good checks were enough), while users saw cuts. Now a check that passes on a link that failed 3 of the last 10 checks gives "degraded: the link flaps", the diagnosis names it a route problem, and the automatic smart repair climbs for it (other tunnel types, then the failover) without waiting for restarts that cannot help.
* **The users' path decides the state.** Every minute, besides the request on the Iran server itself, one HTTP request is made to each tunnel's forwarded port from another Iran server (the probe server first; a server never looks at itself). A tunnel whose data passes is *up* even when the ping over the GRE link is blocked (some routes drop ICMP inside GRE while TCP passes - this showed "down · 100 % loss" with 10 Mb/s flowing); the latency then comes from that request. A port that answers on the server but not from Iran is *degraded*, after four minutes *down*, with "blocked in front of the server"; the diagnosis treats it as a cut route, so the failover is the remedy. The Score column left the tunnel list (the quality test stays in the Traffic cell's tooltip); the reason now sits under the state.
* **New repair step - the link again from scratch**: for a GRE link that is filtered or lossy, the smart repair removes the tunnel once and builds it again with a NEW inner network (the old /30 is excluded) and a new GRE key, then checks it; only after that come the other tunnel types and, last, the move to another Iran server.
* **Repairs ask nothing any more**: restart, rebuild and another tunnel type run by themselves (two minutes after the restarts were used up, again after 30 minutes if needed; a manual smart repair no longer blocks the automatic one for six hours); "Fix all with AI" starts at once. Only a move to another Iran server - with its CDN records - waits for approval under Failover · CDN.
* **DNS names after the build**: a tick in Create tunnel points the chosen names (A records of the ArvanCloud account, picked from a list) at the new Iran server as soon as the build finished; the same small form sits under the DNS changes box for any server. A ticked replacement also re-points the records of the replaced tunnel at once (the tick is the approval). Every change is listed with a Restore.
## 1.8.0 - 2026-10-10 - Create tunnel: smart Iran choice, port check, replace a dead tunnel

* **✨ Smart** in the Iran server list: the panel pings the foreign server from every Iran server, looks at free resources, how many tunnels each one carries and whether the ports are free, and builds on the best one (the story says which and why).
* **Port check before the build**: a port already used on the chosen Iran server - by a tunnel of the panel, or, checked for real when Build is pressed, by any process - stops the build with a clear sentence: choose another Iran server or port. Nothing is touched.
* **Replace a dead tunnel**: when another tunnel already carries the port to the same foreign server, the form says so (its Iran server, state, how long down); a dead one is ticked for replacement by default: it is removed first, the new one is built, and the CDN records that served only it follow the new Iran server through the failover (asks first, or automatic). Records that also serve a tunnel that stays are left alone and listed.
* **Fix**: the Create form sent an empty transport (the type list keeps it in memory, not in a field), so every backhaul build was refused with "transport must be tcp, tcpmux, wsmux or wssmux"; the form now sends the chosen transport, and an empty one means the engine's default.
## 1.7.1 - 2026-10-10 - Failover for a broken route, test cleanup, fixes

* **Failover for a broken route**: when the smart repair finds that GRE is filtered (or the route is cut / lossy / the port blocked) and no other tunnel type works, it hands over to the failover: the broken tunnels of that Iran server move to the Iran server that ranks best for them (ping to the foreign server, free resources, how many tunnels it already carries, free ports) and the CDN records that serve only those tunnels are pointed at the new server. A record that also serves a tunnel that stays would break it, so such tunnels are moved as well and the plan says so; records with no known config are left alone and listed. Mode "ask me first" keeps the plan on Smart repair → Failover · CDN (with a Telegram notice); "automatic" runs it inside the same repair story. The chain restart → rebuild → other types → failover runs by itself only when Settings → Repairs allows "switch".
* **Temporary test tunnels are always removed**: a forced name (a test, a rebuild under the same name) is kept by the engine builders; leftover units of an interrupted earlier run are removed before the port check instead of silently renaming the new tunnel (which left a "temporary" tunnel in the list and the test port in use). The test's cleanup also removes any tunnel of the pair that forwards only the test port.
* **Fixes**: the failover forgot to save that a server answered again (the next outage would have skipped the waiting time); the task-dock chips no longer jump every 1.5 s (they are updated in place) and show the text without the "·" marker. A fix that did nothing (refused by the hourly limit, nothing to repair, a server unreachable) no longer counts against the two-per-hour budget - before, the refusals kept each other alive, a tunnel that stayed down got an entry every minute and never a real restart again, and a manual Fix after one automatic one was refused; the automatic smart repair now climbs after two real restarts since the outage began, not only within the last hour.
## 1.7.0 - 2026-10-10 - Failover of an Iran server with ArvanCloud DNS

* **Failover**: an Iran server that stays down (default 5 minutes) gets a plan: all its tunnels move to the best other Iran server (same names, ports and types) and the ArvanCloud A records that pointed at the dead server are pointed at the new one. Only the address of a record changes; every other field is sent back as read, and the result is read back to be sure. The DNS is touched only after every tunnel moved.
* **Starts with approval**: the plan waits on Smart repair (Approve / Dismiss) and a Telegram notice is sent. Mode "automatic" exists but only runs clean plans. A finished failover can be undone (DNS back to the old address).
* **Smart repair → Failover · CDN tab**: connection status (the domains and records the key sees), a preview per Iran server ("what would happen if it went down"), the settings (API key kept on the server and never shown again, mode, minutes, optional domain list, a read-only key test) and a short guide to creating the key at ArvanCloud (machine user with the CDN access rule).
* **DNS changes box** next to the History: every record the failover re-pointed, with old and new address, which server, when, and a *Restore* per record that puts the previous address back (a whole failover can still be undone at once).
* **Pages in tabs**: Smart repair (Needs attention with the History and the DNS changes · Failover) and Tunnel test (the test with its live log · Network advisor, moved here from Smart repair).
* **Task dock**: a running window (a repair, a build, a test) can be minimised with — to a chip at the bottom; it keeps running, the chip shows how far it is, a click brings it back.
* **Clear text for 502 / 503 / 504** while the panel restarts after an update; the Messages list shows the whole text of each message.
* **Windows open with a spring animation**: blur clears, the box rises and settles, a light passes over it once.
## 1.6.0 - 2026-10-10 - Persian and English, glass-gold AI buttons, animated buttons

* **Two languages**: a *فا / EN* button in the header (and on the sign-in page) switches the whole panel between English and Persian, right to left with the Vazirmatn font. One dictionary (`i18n.fa.ui.js` for the interface, `i18n.fa.msg.js` for what the server says) translates everything that reaches the screen - events, repair and build stories, errors; curl, ssh and TLS errors are explained in plain Persian, cause first. The choice is remembered per browser.
* **Buttons**: the AI buttons (anything with ✨) are gold glass and a magic wand flicks in beside the text on hover; every other button, and the sidebar menu, gets a lift with a glow ring and a small white curtain passing over it on hover (nothing happens on click); the Create tunnel button is larger with a slow fade-breathing glow.
* **Branding**: the logo in the README and the sidebar signature *Powered by v2v2H*.
* **Add a config** takes only the link (the name comes from the link, the config goes into *All*).
## 1.5.1 - 2026-10-10 - quiet alerts, data centres on the dashboard, tidier windows

* **Alerts say it once**: a thing must be down for at least `min_down_minutes` (default 4) on top of the failed checks before one DOWN goes out; reminders are off by default (`remind_hours` 0 = never: one DOWN, one UP, nothing in between); a thing that goes down and up three times inside an hour gets one FLAPPING notice and is then kept quiet for 6 hours (Settings → Alerts: `flap_max`, `flap_quiet_hours`); configs have their own `config_fails`. Migration 10 adds the flapping columns.
* **Hosting providers**: found for every server from registry data (name, website, AS; once an hour). The server window's Provider row shows the site as a link and whether it answers (Iranian providers checked from an Iran server, foreign ones from the panel server, every 10 minutes). `GET /api/datacentres` groups servers and tunnels per provider. The dashboard's Arvan Radar box is gone.
* **Shop bot → Plans**: quick add (category, server, time, volume, price → a complete plan with automatic title and the server's panel settings), new-category button, consistent button sizes.
* **Branding**: a detailed SVG icon (favicon, sidebar, login). Docs: `docs/features.md`, `docs/shop-bot.md`, `docs/README.md` index.
* **Server window**: no header buttons while the edit form is open. **Settings**: each tab's card is as wide as its content.
* **Panels page**: Overview (nodes, about, test config) is the first tab; the user list and the hosts are read from the panel only when you open their tab, and kept until you reload (↻) or change the search.
* **Subscription editor**: the nodes come in categories (Countries, families like TCP-1…5 or Cloud-1…4, Other) with tick all / untick all per category.
* **Smart repair** lists tunnels only (down, degraded, lossy); configs and servers stay on the dashboard's Problems box, under Panels and under Nodes.
* **Server window → Tools**: Health check tile removed; every tile the same size with one short line; results (GRE, filtering, Abuse-Defender, last sample) only in *Last results*.
* **UI**: the message bar sits under the header (sticky) instead of the bottom; a polished sidebar (icon column, counts, glowing marker, hover fade); deleting one config asks nothing (Delete all still does); fixed: Settings tabs could show a stale card and two tab rows after a refresh (the page's own DOM changes were mistaken for an unchanged page).

## 1.5.0 - 2026-10-10 - the shop bot run from the panel, a Telegram Mini App, a smoother and mobile-ready UI

* **Shop bot pages** (Shop bot → Overview · Payments · Orders · Users · Plans · Categories · Servers · Discounts · Gift codes · Broadcast · Tickets · Texts · Settings · Tools): everything the bot's admin menu can do, from the panel. Payments to confirm (`have_sent`) with ✅ / ❌ that credit the wallet and edit the admins' messages exactly like the bot; orders with status; user detail with wallet, discount (the bot's JSON kept intact), agent, ban, message; plans with the panel settings JSON (`custom_sni`); categories; the panels the bot sells from (add one from the panels this panel already knows); discount codes with audience and target; gift codes; broadcasts (queue, cancel); tickets with reply and close; every text and button of the bot editable with overrides (`settings/text_overrides.json`, hooked into `config.php` once); every switch grouped (Sales, Wallet and payment, Prices, Support and channels, Users, Links and configs, Extras) and the payment keys; tools: apps list, top-up prices, backups, tables, read-only SQL, stats, webhook, command menu, restart PHP, dump, clear logs. All SQL goes through the reviewed scripts with text as base64 literals; the bot token never leaves the bot server (`bot_tg_json`).
* **Telegram Mini App** (`bot/miniapp/`, installed from Shop bot → Tools → Mini App or `scripts/deploy-miniapp.sh`): the customers' app inside Telegram — services with live usage from the panel, buying from the wallet with the account created on Marzban / PasarGuard / Marzneshin, subscription link, QR and configs, renew, extra volume and days, card-to-card top-up with the receipt photo sent to the admins with the bot's own approve / decline buttons, payment history and cashback, gift codes, lucky wheel, tickets, referral link, connection apps, outage reports. RTL Persian, follows the Telegram theme, deep links (`svc_<id>`, `plan_<id>`, `wallet`, `support`). Every write mirrors the bot's own rows, so the bot and the panel see what the app did.
* **Smart repair, rewritten**: one list of everything that needs attention (tunnels down or lossy, servers offline, configs down grouped by address) with *Fix with AI* on each, *Fix all with AI* for every broken tunnel in one story, *Explain* (the AI reads the whole situation), *Ask the AI* and *Check again* inside a diagnosis; the dashboard's Problems box has *Fix all*. Rebuilds interrupted by a restart resume on start (`builder.resume_pending`).
* **UI**: switching pages never shows the old page again — a loading box appears at once, the previous page's requests are cancelled, a failed load shows an error box and retries by itself (deploy restarts, network blips). Status bar instead of toasts; fixed-height log boxes everywhere (no jumping); hover fade on every button, row and card (not on boxes); compact tunnel-test engine tiles with *Advanced* for the transports; packet loss in red when it matters; More / Edit / Delete buttons on server and tunnel rows instead of row clicks; country codes instead of flags in config lists; Arvan Radar box loads after the dashboard and refreshes in the background (stale-while-revalidate) — the dashboard no longer waits for it.
* **Phones**: bottom icon navigation, one-column tiles, tables scroll inside the page, full-screen windows, the status bar above the nav.
* **Security**: `Content-Security-Policy` on every page; `/api/docs` and `/api/openapi.json` need a session; bot switches sanitised; user discount updates keep the bot's JSON; receipt uploads checked by size and MIME and never stored.

## 1.4.0 - 2026-10-10 - configs like the old panel, tidy settings

* Configs: group bar with chips (All / Manual / one chip per subscription group, with counts and a remove button that asks whether to delete the configs too), country flag, ONLINE / OFFLINE label, inline tunnel select and CDN port (Enter saves), download / upload columns, Edit / Test / Delete per row; grid cards with PING / DOWN / UP stat boxes; "Test all" and "Delete all" act on the current view. Sync of a group reuses the saved subscription link.
* Settings: eight clean tabs (Account, Alerts, Backups, Probe, Repairs & AI, Shop bot, Appearance, Update), one tab = its cards, in a fixed order.
* Gateway & outbounds in two tabs: Iran gateway (settings strip, ports, borrowed configs, exit test) and Panel outbounds.
* Sidebar: Sign out in its own box with the panel version under it.

## 1.3.0 - 2026-10-10 - the rest of the old tunnel tools

* Tunnel test like the old one: tick the transports to try (TCP Mux, TCP, WebSocket Mux, WebSocket TLS Mux); the panel builds a temporary tunnel for each, one after the other, measures latency / jitter / loss / download / score, removes it, and shows a comparison table with the best one. Stop ends after the current transport.
* Tunnel list: Health and Quality buttons per tunnel, "Quality test all", "Detect on servers" (tunnels that exist on the servers but not in the panel, one-click adopt), "Ports" to change the forwarded ports of a backhaul tunnel (the Iran side rewrites its config, restarts, checks every port listens, or puts the old file back).
* Create tunnel: "choose for me" ranks the Iran servers for the chosen foreign server (ping quality, free resources, tunnels carried, wanted ports free) and picks the best.
* Events, Jobs and Logs are no longer shown as tabs.

## 1.2.0 - 2026-10-10 - the old panel's structure

* Navigation like the old panel: Dashboard (infrastructure health, quick access, activity), Configs (configs + Panels in one place, stat tiles, Ping all / Speed test all / Delete all, grid / compact / list display), Nodes (Iran / Foreign with the monitoring view inside, Gateway, Events, Jobs, Logs), Tunnels (list / create / tunnel test / quality / core update / repairs), Shop bot, Settings (Account / Telegram & backup / Probe / Appearance & about / AI & automation). Tabs the size of the old ones.
* Every panel can keep a test config on the probe: a small test user is created on the panel and its subscription imported as the panel's group (automatically when a panel is added, or with one button).
* Tunnel quality: latency / jitter / loss / score over the GRE link of an existing tunnel, one or all.
* Core update: which backhaul / Xray version every server runs against the newest release; update outdated servers with a verified download.
* AI assistant: Anthropic key in Settings; inside Fix the AI reads the facts, gives a diagnosis and may add safe steps.
* Appearance: themes, day / night button in the header, text size, animations off. About: installed vs newest commit, one-click update from GitHub (deploy with rollback).

## 1.1.0 - 2026-10-10 - the old panel's node tools, new navigation, glass look

* Navigation like the old panel: Nodes (Iran / Foreign / Monitoring / Gateway & outbounds / Events / Jobs / Logs), Tunnels (list / create / repairs), Settings in tabs. Liquid-glass look, hover lift on rows and cards, tunnels grouped by type.
* Nodes: console with presets (logs, ports, failed services, busiest processes, disk, network, tunnel units); connect a server with its password once (installs the panel's key); add several servers at once; open a server in Windows ssh / PuTTY / MobaXterm with your own key; Abuse-Defender on foreign servers (install / remove, nightly list refresh); reboot; country of foreign servers by majority vote of four services; "filtered in Iran?" check from your Iran servers and check-host.net, manual and once a day.
* Facts now include swap, the busiest process and the Abuse-Defender rule count.
* The panel runs on the monitoring server behind Caddy (`NIKATEL_LISTEN_HOST=127.0.0.1`); `install.sh` installs sshpass.

## 1.0.0 - 2026-10-10 - the old panel is retired

* All milestones done; the new panel is the only one running. Telegram alerts moved over, auto-repair switched on (2 per tunnel per hour, Iran side first, never when a server is unreachable).
* Tunnel checks: an ssh connection that drops mid-run is retried once; a single failed check no longer turns a working tunnel "unknown" (two in a row do). ssh failures are logged with their output.
* Restarts no longer produce "cannot be checked" events: the service stops with `KillMode=mixed`, running jobs get up to 40 s to finish, and a check that fails while the panel is shutting down is dropped. `deploy.sh` installs the unit file when it changed.

## 0.10.0 - 2026-10-09 - Milestone 6, part 2: shop bot

* Shop bot page: the PHP bot on its own server, over SSH through reviewed scripts — services, cron, Telegram webhook, sales today / 7 d / 30 d, switches (BOT_STATES), users (wallet, discount, agent, ban), tables, a read-only SQL box, stats; actions: set webhook, restart PHP, dump the database, clear logs.
* One-click import of the real database from the staging copy (the live database is dumped first; broadcast queues are left out).
* Settings → Shop bot: where the bot runs and its public URL. Database credentials never leave the bot server.

## 0.9.0 - 2026-10-09 - Milestone 6, part 1: panels and outbounds

* Panels page: Marzban / PasarGuard panels checked every 2 minutes (version, users, load, nodes); a node that stays disconnected gets a reconnect request; alerts for panels and nodes with the usual flapping rules.
* Users: search, filter, paging; edit status / expiry / limit / reset / note; enable, disable, reset, renew, revoke, delete; bulk actions on ticked users.
* Hosts: list with the effective port, edit every field, enable / disable, clone, delete.
* Outbounds: the panel's Xray core config — add an outbound from a link, remove, make default, raw JSON edit; every change validated, backed up to `core-backups/`, read back and rolled back when the panel did not keep it; one-click restore of any backup.
* First start imports the panels from `/etc/nikatel/panels.json` (passwords stay in the database, never in the API).

## 0.8.1 - 2026-10-09 - quieter tunnel health

* The GRE peer check sends 20 pings instead of 5, so one lost packet is 5 %, not 20 %.
* Loss under 15 % is normal for the Iran-abroad paths (measured 3-13 % on a normal day) and no longer makes a tunnel "degraded".
* up ⇄ degraded flips need two consecutive checks that agree (`tunnels.settle`); a stopped unit still counts at once.

## 0.8.0 - 2026-10-09 - Milestone 5: configs and probe

* Configs page: the probe's configs with status, ping, daily speed, 24 h history, grouped by subscription; add a link; edit field by field (v2rayN-style) or paste a new link; delete; speed test on demand (over SSH on the probe server); copy link.
* Subscriptions: preview the nodes of a link, import the ticked ones as a group.
* Probe: primary + standby list with automatic failover; test; config alerts use the same flapping rules as tunnels.
* Probe installer: the probe sources now live in `probe/`; Settings → Probe installs them with cached Xray on any Iran server and registers it.

## 0.7.0 - 2026-10-09 - Milestone 4 complete: tunnel test

* "Test this pair first": builds a temporary backhaul-over-GRE tunnel, measures latency, jitter and loss over GRE and the real download speed through a forwarded port, gives a 0-100 score, and removes everything again. Results stay in the Repairs history.
* Gateway: borrowed mode blocks instead of leaking to direct before the first probe.

## 0.6.0 - 2026-10-09 - Milestone 4, part 2: Iran gateway

* Gateway page: a plain Xray on an Iran server, installed from the panel's cache over SSH (the server needs no internet); ports forward to foreign nodes; borrowed vless/vmess/trojan/ss configs as the way out during internet cuts, with modes auto (balancer + fallback) / borrowed / direct; exit test per config with IP and country; status, log, remove.
* `scripts/fetch-xray.sh` keeps the latest Xray in `/var/lib/nikatel/engines`.

## 0.5.0 - 2026-10-09 - Milestone 4, part 1: build tunnels

* Create a backhaul-over-GRE tunnel from the UI: preflight of both servers, free control port, path-MTU measurement, kernel tuning, GRE scripts + unit + health timer on both sides, two-way ping and a 10 s hold test, tuned backhaul configs, control-connection and data-path checks; a failed build removes what it created.
* Remove a tunnel from both servers (typed confirmation) and rebuild it with the same name and ports.
* New reviewed read-only scripts (`build_facts`, `mtu_probe`, `gre_verify`, `gre_hold`, `estab_check`, `http_probe`, `unit_log`) and actions (`put_file`, `unit_enable_restart`, `remove_tunnel_files`, `sysctl_tune`, `data_test_server`), all parameter-validated.

## 0.4.0 - 2026-10-09 - Milestone 3 (repair)

* Named actions (`ssh.ACTIONS`): the only way the panel changes a server; refused in read-only mode.
* Fix: diagnose both sides → plan → restart only what is broken, Iran first → re-check after each step; dry run shows the exact script; every run stored in `action_log` with its story.
* Limits: 2 repairs per tunnel per hour, never when a server is unreachable, auto-repair off by default and only in read-write mode.
* Repairs page and Fix / Dry run buttons in the tunnel detail; pre-commit hook (`scripts/install-hooks.sh`).

## 0.3.0 - 2026-10-09 - Milestone 2 (alert)

* Telegram: token and chat id in Settings (token never shown again), test message.
* Alerts with flapping control: N failed checks before DOWN, UP only after a stable period, reminders while down, per-tunnel/server mute, maintenance windows.
* Daily report at a local hour; preview and send-now.
* Encrypted backups (openssl aes-256, passphrase set once) kept locally and sent to Telegram; `nikatel restore <file>`.

## 0.2.0 - 2026-10-09 - Milestone 1 (see) complete except the one-week comparison

* Servers: add / edit / disable / remove from the UI; "forget host key" after a reinstall; detail row with host facts.
* 24-hour charts: CPU, RAM, traffic, load, connections per server; up/down strip, RTT and throughput per tunnel.
* Settings page: change password, effective settings (read-only mode stays server-side on purpose).
* Logs page: the panel's own log with level / domain / text filters, request ids, secrets masked.

## 0.1.0 - 2026-10-09 - Milestone 0 and the first half of Milestone 1

* Project skeleton: settings, logging with request ids and secret masking, SQLite with
  migrations, models, SSH with named read-only scripts, background jobs with status, one-admin
  auth, FastAPI routers, plain-JS UI.
* Import of the old panel's inventory (servers, tunnels); secrets kept in a separate column.
* Server facts every 2 minutes; tunnel health every minute with a reason per state.
* One-line installer, deploy with health check and rollback, nightly backup script.
* CI: tests on 3.10 and 3.12, ruff, JS/shell syntax, secrets check, gitleaks.
* Docs: architecture, roadmap, operations, development, logging, ADR 0001-0004.
