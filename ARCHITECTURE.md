# Architecture

## 1. Landscape

```
 browser ── HTTPS ──► panel (FastAPI + SQLite) ── SSH (own key, reviewed scripts + actions) ──► Iran servers
 Telegram ◄── alerts ─┘       │                                                                 foreign servers
                              ├── HTTPS ──► Marzban / PasarGuard panels   ── HTTP ──► probe (inside Iran)
                              └── SSH ──► shop bot server (PHP bot + MariaDB + Mini App)  ◄── Telegram ◄── customers
```

One Python process. SQLite for state. SSH is the only way the panel touches a server, and in
read-only mode only reviewed scripts run. The tunnels themselves are systemd services on the
servers; the panel observes and repairs them, it never carries traffic. The shop bot is a separate
PHP application on its own server; the panel administers it over SSH through the same reviewed
scripts, and the Mini App (`bot/miniapp/`) is deployed next to the bot and talks to the bot's
database directly, after verifying Telegram's signature on every request.

## 2. Code map

```
nikatel/
  config.py        Settings - the only place that reads the environment (NIKATEL_*)
  log.py           log format, request id, secret masking
  db.py            engine, sessions, numbered migrations (MIGRATIONS, _add_column)
  models.py        tables: Server (+extra, profile_id), Profile, ServerSample, Tunnel, TunnelCheck, Event, Job, AlertState,
                   Maintenance, Config, ConfigSample, Subscription, Panel, ActionLog, Gateway, RepairCase, ApiToken
  ssh.py           Node, run_script(name, params) over the reviewed SCRIPTS and ACTIONS; run_raw only read-write
  auth.py          one admin, scrypt, signed cookie
  gate.py          the secret entry path: the panel answers 404 to everyone who does not know it (a live API token passes it); it works on the HTTP request, so it sits beside auth.py and not in services/
  jobs.py          background loops with their status in the jobs table; stop_all waits for in-flight SSH
  services/        the logic, importable without HTTP
    facts.py         server resources (parse_facts is pure)
    profiles.py      profiles: what is on the Lab page - an Iran server, a foreign server or a PasarGuard node (1.49.0); a server that is made gets its profile (ensure_server), the servers the panel already has are picked in Add profile; api/profiles.py, web/lab.js
    uptime.py        the strip of cells that shows how a server looked over the last hours, read from the samples only (build_strip / up_percent are pure); api/servers.py and api/profiles.py serve it
    tunnels.py       tunnel health with hysteresis (plan_for / parse_side / judge / settle are pure)
    linkmodels.py    the links a Backhaul tunnel can run over: the nine plain L3 models (1.69.0: GRE, GRE over FOU / GUE, IPIP, IPIP over FOU / GUE, WireGuard, IPsec ESP, IPsec in UDP; simple-tun-link) and the four kernel L2 carriers (1.69.180: L2TPv3, VXLAN, Geneve, GRE-TAP; C-Tunnel - docs/l2-carriers.md): the registry, the kinds (`fou:tcpmux`, `ipsec:tcp`, `l2tpv3:tcpmux` ...), what must pass between the servers, the scripts of one end - pure
    linkprobe.py     the link probe (1.69.0): which of the nine links pass between two servers RIGHT NOW - a temporary `lp-<link>` link of each, ten pings each way, all at once, about 7 s a link and 23 s for all nine; the answers go to the Doctor's memory of the pair, and a cut tunnel does not try a kind whose link does not pass
    builder.py       create / remove / rebuild a Backhaul tunnel over one of the nine links (`link_scripts`, docs/simple-tun-link.md); set_ports; pending rebuilds that survive a
                     restart of the panel; follow_server_rename / follow_server_host (a server's new name or address)
    build_other.py   the other engines: BackPack (reverse and direct), Paqet, Chisel (reverse, or forward = the Iran server dials, transports fws / fwss) - plan, install, units, proof, rollback
    create.py        the Create story: precheck of ports and conflicts, Smart choice of the Iran side, replace, DNS follow
    detect.py        find tunnels already running on the servers and adopt them; auto choice of the Iran side
    trash.py         removed tunnels kept for a while with everything needed to put them back
    retune.py        apply newer tunnel defaults one tunnel at a time, checked, put back when worse
    pulse.py         a 5 s heartbeat that marks a switched-off server off within seconds and wakes the fast lane of the tunnel checks; before it calls a server off it asks for a second opinion (an Iran server saw it, a jump server reaches it, or a PasarGuard node of its address is connected in a panel read in the last 90 s - `node_connected`)
    sentinel.py      every 5 s, ONE light command per server (ssh.py `sentinel`, `run_light`) for all its tunnels: the state of every unit, of every GRE card and three quick pings of every GRE peer; a tunnel that is up and loses one of them gets a full check at once (never a state of its own; when a unit or the card shows the fault the check starts with a QUICK read of the units and the card - `tunnel_side` with `QUICK=1`, a second or two - and a stopped unit is `down` without the pings, the request through the port and the look from another server; the full check asks the two servers and that look at the same time, and a peer that answers none of three quick pings is not pinged twenty times); the repair job (autopilot `repair_cycle`, every 5 s) is woken by a tunnel turning down and by the end of a repair; the sign-reading is pure (`parse`, `suspects`, `plan_reads`); the warm standby of a tunnel, which takes the users' ports when it is cut, is `standby.py` below
    carry.py         the Doctor asks every port of every tunnel that is up whether it really CARRIES a connection (ssh.py `ports_carry`, every 3 minutes, one light command per Iran server; a port the service behind answers but the tunnel does not carry is repaired by itself: restart, rebuild, another Iran server; the record `carry`)
    purpose.py       what a tunnel is FOR: the PasarGuard hosts of the economy (coefficient) and AI configs are placed on the tunnels they end on like the configs of the probe, and the row of the tunnel says ECO / AI (job every ten minutes, the record's `purpose`)
    overhead.py      the WIRE COST of a tunnel type (bytes on the wire for every byte the users move): measured by the speed test (the card of the Iran server before / during / after the streams) or by the type's design; a benchmark in knowledge.ranked and the reason a heavy running type (KCP, xDi) is changed for a lighter one (1.69.223)
    testbudget.py    the daily volume of the Doctor's own tests (what probe_run's streams moved): routine measurements and surveys slow down from half of the budget and stop at 80 %, urgent ones go on until it is used (1.69.223)
    retypewatch.py   after the Doctor (or the owner) changes the TYPE of a tunnel the users are watched at 30 minutes, 2 and 6 hours against the fleet's own movement; a fall puts the old type back by the no-cut change (the new one is entered as failed for that pair)
    orphans.py       the Doctor's sweep of what nothing owns: the standby / strands of a removed tunnel and the idle units, links and test doors of the engines on the servers that no tunnel of the panel names (two looks apart, a backup first, never a DNS record; job every half hour)
    janitor.py       the Doctor's sweep of duplicate tunnels (two that go to the same foreign server with the same ports, e.g. the old one a move kept): the busiest that is up stays, a quiet old copy that no DNS record is tied to goes to Recently removed, one every half hour, three a day; otherwise it only tells, once a day
    nodedns.py       the Doctor's look at the NAME LOOKUPS of the foreign servers that end a tunnel (ssh.py `dns_look`, every 10 minutes): a dead resolver first in /etc/resolv.conf makes every new site wait seconds while the tunnel is fine; two slow looks and the Doctor's mode allows it -> the action `dns_heal` puts the resolvers that answer first (host file and node container, nothing restarted), measured, put back when it did not help; userq names the cause
    warpwatch.py     the WARP WATCH (1.69.228): an AI config (AI-1, AI-2) the monitor shows down for 7 minutes (both vantages) whose tunnel is not down and whose PasarGuard node is connected gets that node reconnected (`panels.node_reconnect`, a few seconds): its running Xray keeps a stuck WARP session and nothing else notices; the placement is the one `purpose` uses (config -> tunnel -> foreign server -> the one non-ECO node of that address), nothing is guessed; up to 6 times in 24 hours per node with a longer wait after each (15, 30, 60, then 120 minutes), every reconnect is an event, a config still down after the day's tries is told with a `bad` event every 3 hours until it is back (1.69.239: it was once a day and the owner's Telegram alerts were dead), every config the watch works on carries a FLAG (reconnecting / needs a hand / not watched) drawn at the top of the Tunnel Doctor page (`/api/doctor` `warp`), a config that stays up 10 minutes drops its flag and an `info` event says so; settings key `warpwatch` (on, done, told, flags), api/configs.py GET|PUT /settings/warpwatch; pure: decide, cooldown_for, flag_of
    witness.py       a SECOND VANTAGE for the Configs monitoring (1.69.227): a witness is a second probe (probe_install `as_witness`) on a server of another network, never a failover and never written to by anything but `mirror` (the `witness` job, every 5 minutes: its config list = the main probe's, ONE bulk call); `read` asks it only while a config is down; a config the main probe calls down but a witness got through in the last 2 minutes is shown UP with the reason (`rescue`, pure; meta["witness"]), a config both fail stays down; a witness that does not answer is skipped for a minute; settings key probe.witness, api/configs.py DELETE /settings/probe/witness/{name} and POST .../witness/mirror
    standby.py       the WARM STANDBY of a tunnel the owner marked (docs/standby.md): a second tunnel of another carrier between the same two servers on shifted ports (`20000 + P`), built before a cut and kept idle (its row is `enabled` False with `record.standby_of`, `models.is_standby`: every list, count, alert, repair and survey of the enabled tunnels leaves it alone), looked at every 15 s by the `standby` job (every 5 s, and at once when a tunnel falls); when the tunnel has been down 5 s and the standby is up, ONE reviewed action on the Iran server (ssh.py `standby_on`: a tagged NAT redirect in PREROUTING, kept across a reboot by a unit) hands the users' ports over in about 2 s - they go back (`standby_off`) after the tunnel has held 60 s; a tunnel under a handover is judged without the look from another Iran server (`tunnels.HANDED`); a standby that falls is mended and rebuilt quietly, never a cut; a handover is an entry of the Doctor's ledger (Moves tab, Back now); the mark is kept by the tunnel's name (settings key `standby`); pure: shifted / shift_mapping / standby_name / carrier / choose_kind / decide / parse_list; api/tunnels.py GET|PUT /tunnels/{id}/standby, POST .../handover and .../back
    lanes.py         the LANES (docs/lanes.md): the warm standby put to work BESIDE its tunnel when the tunnel is nearly full (the users use 85 % of its capacity or more in 7 of 10 minutes): the Iran server sends a random share of the NEW connections to the standby (ssh.py `lane_on`: one NAT rule per users' port, `-m statistic --mode random`, tag `nikatel-lane-<standby>`), share = cap_s / (cap_p + cap_s) between 10 and 90 %; open connections are never touched (conntrack); `lane_off` PARK=1 is the careful end (a RETURN first), PARK=2 removes the parked rules only when no connection is left, PARK=0 with the standby; a lane is parked when the standby falls and put again, the tunnel's cut is the standby's own full handover above it; OFF by default (settings `lanes.on`, the switch on the Doctor page); job `lanes` every 10 s; pure: share_pct / prob_text / capacity / is_loaded / is_idle / decide / parse_list; api/tunnels.py GET|PUT /lanes/settings, GET /tunnels/{id}/lane, POST .../lane/start and .../lane/stop
    trident.py         the TRIDENT (docs/trident.md): one tunnel carried by several tunnels of different kinds between the same two servers (the tunnel = strand 0, its warm standby = strand 1, one more strand of a third kind on its own shifted ports +30000 = strand 2, a hidden row with `record.strand_of`); the kinds are chosen to share no fate (`traits`: family gre/udp/tcp/icmp/esp/ipip, side that dials, packets, TLS; `pick_kind`); the users' NEW connections are shared by live quality (`target_weights`: capacity x quality from loss, delay and the stability of the kind, divided by what the kind puts on the Iran server's line (`wire_overhead`: headers, TLS, KCP, FEC), floor 5 %, ceiling 70 %/90 % - LEAN by default (`lean_on`, `set_lean`, setting `trident.lean`, GET|PUT /trident/lean): ceiling 94 %, floor 3 %, a strand's capacity kept 24 h; the loss seen is the least of the last 3 checks (`smooth_loss`); a strand that comes back is let in by steps 10/25/50 %/full, a strand that is down gets 0 at once; `should_apply`: small drifts are left); the STALL LOOK (1.69.226, docs/trident.md "The stall look"): every 60 s per Trident the read-only ssh.py `strand_probe` makes 8 small GETs on the Iran server to 127.0.0.1:<port> of every strand that is up (a local request never passes the NAT chain: each port measures its own strand), the last 5 looks pooled per strand row (`_STALL`, `stall_of`, `parse_probe`), and `stall_factor` (1 under 1 % stalls and p95 under 1 s, 0.5 at 2 %, 0.2 at 5 % or p95 over 3 s, 0 = the floor only from 10 %) multiplies the share (item `stall` in target_weights); `stalls` per strand in status and the bar; ONE NAT chain `NKTR_<sha1(name)[:10]>` on the Iran server replaced in ONE iptables-restore transaction (ssh.py `trident_set`; `trident_off` PARK 0/1/2; read `trident_list`), `chain_probs` gives each rule its chance given the rules above it; jumps from PREROUTING never leave while the trident stands (an empty NAT chain resets translated connections), the accepts of the shifted ports go in BEFORE the chain; open connections are never touched; the Trident "owns" the users' ports only while marked, applied and its job answered in the last minute (`owns`), else the standby's handover works as before (standby.py `_step`/`handover` and lanes.py defer to it); OFF by default, marked by tunnel NAME (settings `trident`); job `trident` every 5 s; api/tunnels.py GET|PUT /tunnels/{id}/trident, POST .../trident/rebalance, `trident` on every tunnel; the RELAY strand (strand 3, docs/trident.md "When the foreign server's address is filtered"): when the weather says the tunnel's foreign address is cut for tcp, icmp and udp at once (`weather.address_cut`) or no strand has carried for a minute (`relay_due`), a hidden tunnel from the same Iran server to ANOTHER foreign server that this Iran server reaches (`relay_target`: an up tunnel from it, open weather, delay) is built on the ports +40000 (`relay_mapping`, `record.relay`), and a small python3 forwarder unit on that second server (ssh.py `relay_on` / `relay_off`, 127.0.0.1 only, enabled at boot) carries every connection on to the tunnel's own foreign server; it carries TCP only, gets a share like any strand once its unit answers (`_ensure_relay`), and goes 30 min after the own strands carry again (`relay_keep`, share first, then the row)
    weather.py       the WEATHER of the routes (docs/trident.md, "What it learns from the day"): per route (an Iran server -> a foreign server) and per family of carriers (gre · udp · tcp · icmp · esp · ipip · l2tp) the state open · pressure · blocked and since when, fused every minute from the fleet (every tunnel and hidden strand is a sensor: a family down with a cut-like cause while another carries on the route = blocked there; on the Iran server the Doctor's kind_wide rule), the link probe and the read-only reach probe (ssh.py `reach_probe`: icmp + tcp to the foreign server; one Trident pair every 10 min while calm), and builds that failed on a filtered carrier; `fuse` (open evidence newer than every failure wins; a block stands 2 h, pressure 1 h), the 7-day hourly pattern (Iran time, `usual_blocked`); read by the Trident (`factor`: pressure x0.5, blocked x0.1, usual-within-30-min x0.7 in target_weights; pick_kind states; renew_due), knowledge.best_kinds (`weather_last`) and standby.choose_kind (blocked families last); `address_cut` (tcp + icmp + udp all blocked = the foreign ADDRESS is filtered: the Trident's relay strand); the C-Tunnel L2 carriers have their families here too (l2tpv3 = l2tp, gretap = gre, vxlan / geneve = udp; the link probe asks l2tpv3 as well); no page - a few words under the Trident bar; settings `trident.weather/pattern/probed/reach`; job `weather` every 60 s
    swap.py          the SWAP BESIDE THE TUNNEL (docs/standby.md, last section): the Doctor's own change of a tunnel's type without a cut - the new type is built as the tunnel's standby (standby.build(name, kind, by_swap=True)), measured by a speed test through itself (confirm_ok), given the users' NEW connections by a manual handover and watched for 90 s (carry_verdict), and only when the open connections of the old tunnel have ended (utopilot._wait_quiet(loss_counts=False)) is the old tunnel removed and built again as the new type while the users stand on the standby; a worse new type sends the users back (give_back) and the old tunnel is untouched; when it cannot be made beside the tunnel the change is only a suggestion. Called by doctor._run_retype for every automatic change; the owner's click keeps the rebuild in place.
    pathscan.py      the route of a tunnel measured from both servers (where the loss is: this pair, one server's own network, the anchors)
    extras.py        installs started right after a server is added (torrent block, Marzban node): detached on the server, followed by polling
    netwatch.py      the network disturbances of a server (its own route is poor: repair only watches)
    tunnel_test.py   quality of one / all tunnels; pair test over several transports with comparison
    tunnel_speed.py  the speed a CUSTOMER gets through a tunnel: the probe downloads / uploads through a config that rides it (one connection), or - without a probe - one and four connections through the test door (random data); kept in record.speed beside record.quality, with a verdict; api/tunnels.py GET|POST /tunnels/{id}/speed
    bandwidth.py     the bandwidth of every tunnel, kept fresh by the Doctor: a GRE tunnel is measured with two light connections (120 MB at most) over its link from a sink on the foreign server bound to the inner address, a tunnel with a test door through the door (8 connections, 4 s); every 4 hours (1.69.226; was 90 min) and not at all while the users' own peak of the last half hour is 60 % of the usual level and 20 Mb/s (`users_prove`: the owner wants the least test traffic); the usual level (75th percentile of a week), a drop (under 60 %, confirmed by a second sample), the look at why (`explain`: servers' load, the other tunnels of the same servers, loss, delay, users); kept in record.bw; api/tunnels.py GET|POST /tunnels/{id}/bandwidth
    livebw.py        what the users USE of every tunnel right now: the byte counters of the tunnel's own link on its Iran server (the GRE link, the L3 link of a BackPack Direct tunnel) - or, for a reverse proxy with no link (BackPack, Chisel over TCP / WS / WSS / mux), the per-socket byte counters of its TCP connections (`ss -ti`; `sock_spec`: the connections that came in on the control port, or the ones the Iran server opened for a forward tunnel) - read by ONE light command per Iran server (ssh.py `iface_counters`, `run_light`: never a jump search) every 5 s while the Tunnels page asks for them and every 30 s otherwise, counted over the last ~15 s on the server's own uptime clock; the panel's own tests of the link (`quiet`) are left out; what has neither a card nor a TCP connection between its servers (UDP, QUIC, KCP, raw sockets: Paqet, BackPack UDP) is read from the USERS' own connections to its forwarded ports on the Iran server (spec `u<ports>`: what the Iran server sends them is their download); a tunnel with no forwarded port has no number, never a made-up zero; job `livebw`; api/tunnels.py GET /tunnels/live
    userq.py         what the USERS get through a tunnel: the configs that ride it (the label by hand, the address of its Iran server, or the A record of a CDN name in the DNS accounts), each judged against ITSELF (answers of the last 2 hours against its own week, its ping, its speed tests), the grade of the tunnel (good / fair / poor / bad, two looks to change), one event on a change, and a look by the Doctor that says where the problem is (the tunnel, its servers, or BEYOND the tunnel); kept in record.ux; the configs that ride no known tunnel are counted (`linked_state`, GET /doctor `users`)
    dnsheal.py       the Doctor's look at the DNS name of a config that is DOWN while its tunnel is up and listening (1.69.196): the name's A record must point at an Iran server whose up tunnel carries the port; if not, and the Doctor is in the mode "move", the record is pointed at the Iran server of THIS tunnel through `failover.point_names` (the Changes box, with Restore). Never a CDN name, a wildcard-only name, an address, or a name another host dials on another port; one name once in 6 hours, six a day
    userscore.py     what the USER gets as ONE mark 0-100 (1.69.63): the speed of one connection and of eight, then the ping (75 % together), then loss and jitter - used by the tests of every tunnel kind and by the choice of the best kind of a pair
    knowledge.py     what the Doctor KNOWS about every pair of servers (which tunnel types connect between an Iran and a foreign server, how well, which ones failed: kept per pair and kind from every pair test, the quality test of live tunnels and the night survey - the `READY` kinds, one pair at a time, only at night on quiet servers; the verify survey of the running types (1.69.220) one more full test of a kind a day at most, on servers under 150 Mb/s of users - 1.69.226) and what it REMEMBERS of every cut (`remember`: the cause, the steps, what healed it; `playbook`: what healed the pair last time goes first); the order a cut tunnel tries (`cut_order`: who can still dial whom), the neighbours' kinds (`sibling_kinds`); settings key `knowledge`; api/doctor.py GET|PUT /doctor/knowledge, POST /doctor/knowledge/survey|stop; web/knowledge.js
    updater.py       the update of the panel itself: where a run of scripts/deploy.sh stands (the small file `update-progress.json` the script replaces at every step; the percentage creeps by the clock of the SERVER, so a refreshed page or the restart of the panel loses nothing), what GitHub has (one `ls-remote`, kept 30 s), the state of the About card (while a run goes it is never "up to date" and never "a new version"), and the start of a run (git pull, then deploy.sh in its own unit); api/system.py GET /system/update, GET /system/update/progress, POST /system/update
    gateway.py       Iran Xray gateway: ports → node, borrowed configs, exit test
    engines.py       the tunnel cores (Backhaul, BackPack, Paqet, Chisel, Xray): check / update / install on the servers that have not got them, the panel's own cache (downloaded and verified by a job and by `nikatel engines`), install_on one server
    nodes.py         node tools: console presets, connect, add many, launchers, Abuse-Defender, reboot, geo, Iran filter check
    onboard.py       what happens to a server right after it is added: its first reading, its GRE links, the tunnel cores - in the background, every step written on its row (extra.busy), the outcome in extra.onboard; api/servers.py POST /servers/{id}/onboard
    actions.py       repair framework: named, logged, dry-run, rate-limited; Fix with a story
    autopilot.py     the Tunnel Doctor: classify (pure) → ladder restart / rebuild / relink / switch / move, limited by the
                     repair level; _rebuild_safe (the old tunnel comes back when the new build fails); relink probes a new
                     GRE link beside the tunnel first; storm detection; observe (lossy tunnels); repair_cycle (one job);
                     fix_all, explain (the AI reads the whole situation), config diagnosis (SNI, reachability)
    comeback.py      back to the better tunnel type (1.69.5): a cut tunnel swapped to an emergency type is tested again for the type it had, with growing waits, and swapped back at a quiet moment when that type is clearly better - pure rules + the job (every minute) + the look
    commander.py     the phone assistant (docs/commander.md): the owner types one order on his phone, the panel's own AI (ai.py) works it out one JSON step at a time with a closed list of tools that call the Doctor's own functions; the code, not the AI, decides what runs alone (looking, the Doctor's repair of a tunnel that is down) and what waits for his tap (a move, another type, the repair of all); limits, a Stop button, every order and tap kept 90 days
    doctor.py        the Tunnel Doctor's three modes (off / fix / move) and its moves: a dead Iran server (every dead server at the same
                     moment in an outage, 1.69.231), an exhausted server (one tunnel), never for packet loss (suggested instead), the return
                     after one healthy hour (no 2-hour cooldown in the way); the ledger of moves, the suggestions, the rails (pure: is_saturated, may_move, ready_to_return)
    failover.py      HOW a move is done: plans (the dead-server plan, the route plan that is only suggested, replace_plan after Create),
                     THE MASS OUTAGE (1.69.231): the `failover` job (every 10 s, `tick`) and the Doctor's round both call `check` (one round at a time, `_CHECKING`); every dead server whose
                     minute is over is planned at the same time (`plan_dead`: the ranking, no target) and then, one plan after the other and at once, `assign_target` picks the target with the
                     tunnels already on their way counted against a server (`with_reserved`: -12 resources and -5 quality per tunnel, `_RESERVED`, given back by `release`, forgotten after 30 min) -
                     so with two servers left both carry their share; every plan runs in its own story and a dead server's tunnels are built together (`PARALLEL_TUNNELS`); `simulate` /
                     POST /api/failover/simulate = the same without building or writing anything; `_busy` forgets a failover when the tunnels went BACK to the server since;
                     THE QUIET TIME (autopilot.quiet_now = storm or `mass_on`: a dead server that carries tunnels waits its minute, a failover story runs, 5 minutes after the last): the Doctor's own affairs
                     (bandwidth, knowledge surveys, retype / comeback, janitor, orphans, core and PasarGuard updates, nodedns, balance, quality pings, returns, resource / quality moves, new standbys and strands)
                     stand still, the checks, repairs of other tunnels, handover, Trident, carry and the Configs monitoring go on; `follow_up` counts the configs behind the moved names 4 minutes after the DNS (event, message, one more look);
                     _work = make before break when the source is on (new tunnel first, DNS, TTL wait, old tunnel last), the DNS
                     records of EVERY account re-pointed in their own account, dns_log with restore / undo, Telegram notices
    capacity.py      a tunnel whose users use nearly all that it can carry (1.69.54): the state of every tunnel each minute from the checks and its measured capacity (`saturation`: full / warm / ok / unknown), told once after three minutes; the Doctor's story for a full tunnel: the SWAP OF THE TYPE WITHOUT A CUT through its ready standby (the new connections are handed to the standby, the tunnel is built again as the better type once the open connections end, the new connections go back; `better_kinds` takes only a type that carried clearly more in fresh FULL pair tests), else the move to a lighter Iran server (`lighter`, the failover's move) or a suggestion; run from `doctor.run`
    arvan.py         ArvanCloud CDN API client: records of every domain, swap_ip (pure), apply with read-back, test; create / edit / delete A records
    cloudflare.py    Cloudflare DNS API client (one address per record, proxied flag); dnsprov.py: the DNS accounts (several ArvanCloud, Cloudflare) under one list;
                     dnscache.py: the ONE copy of the records of every account that the DNS page and the Outbound page share (read in the background, forgotten on every write)
    tokens.py        API tokens for machines: hashed at rest, shown once, scoped read / act / admin / field (`allowed` is pure; `field` = the Iran test kit, one route), revocable
    field.py         the FIELD TEST (1.69.231): the result of the Iran test kit (a folder a person in Iran runs on a laptop: DNS, TCP, TLS, ping, path MTU, and what every config really carries from THEIR network - a SIM's hotspot, a home line: ten sites, the delay of a tap, video at 720p to 4K, pictures at once, an upload, for the DIRECT configs whether the IP, a port or the SNI is filtered, and the panel's own subscription names and addresses: does the NAME or the ADDRESS open here) arrives at POST /api/field/ingest with a token of scope `field`; `compact` keeps a bounded copy (table field_runs, 60 days, 500 runs, 20 runs an hour per token), `judge` (pure) is the Doctor's rules: blocked / closed / stall / partial / slow / lag / video (not smooth at 1080p) / images / dns (fails or answers an address no server has) / unstable / flaky from run to run / low path MTU, for the direct addresses ipblock / portblock (dropped or reset) / refused (the server says no: not a filter) / tlsblock / sniblock (direct_verdict), for the panel panelip (an address does not answer) / panelname (a name opens only through another address) / panelblock (a name opens through none: filtered) / panelnotserved (no site for it: not a filter) / paneldns, and the comparison of TCP-3, TCP-4 (VLESS TLS 8680 straight to a node) with the kit's LAB configs of the same node: another type works = `protocol`, nothing works = `path`; one event per finding once in 12 hours (with what to do), one when a network is fine again; `story_for` adds the field's word to the Doctor's look at a suffering tunnel (userq.investigate); nothing is changed by itself; api/field.py GET /field, /field/runs, /field/runs/{id}
    fieldkit.py      the LIVE LIST of the Iran test kit (1.69.233): what the kit tests today, made from the panel when the kit starts (GET /api/field/manifest, a token of scope ield may call it; cached a minute) so the kit never goes stale: the vless links of ONE test user (settings key ield, user) become ready Xray clients, the LAB configs (Reality 1845 / xHTTP-Reality 2082 of the core to the nodes of the direct configs, ield.BASES), the panel's subscription names and addresses (settings), the newest kit number; the zip keeps its own list only as a fallback; it also tells once a day when the test user expires in under 7 days, is not active or has used 80 % of its volume; pure: parse_links, build, user_info, warnings
    fieldtesters.py  the TESTERS of the Iran test kit (1.69.238): the people the kit is sent to in other cities - a zip of their own made by the panel (the common files in data/field-kit, the list of this minute, `who.json` with their name, city and SIM, `upload.json` with a token of their own, scope field), add / rebuild (a new token, the old one revoked) / remove, and what each has sent (their runs are signed with their token); api/field.py `/field/testers`; the Testers button of web/field.js
    web/field.js, web/field.css   the FIELD TEST tab of Network (1.69.236): ONE screen without scrolling, drawn like a Mac app (GET /api/field: metrics, labels, panel.rows) - a bar (networks, findings, newest run, the Runs list with each run's JSON), a card per network (configs that work; Direct, Names and Panel as numbers, only what fails is named), one line per finding (click it for what would help), a line of what the networks have in common (fails on every network = the config's own problem; only in one city or on one network = a local filter) and every config as a list with a dot and the delay per network (hover for the numbers); nothing is changed from here
    license.py       owner / licensed / free: a signed NK1 token (Ed25519), features, the gates the rest of the panel asks
    ai.py            the AI models of the assistant (1.59.0): Anthropic, OpenAI, Gemini, Grok, DeepSeek and every OpenAI-compatible service - a key and a model each (keys in settings, never in the repo), ONE in use; advice inside Fix, Explain, the Doctor; api/ai.py
    balance.py       CPU on the cores: a busy server whose packets sit on one core gets RPS (probe / judge pure, apply with before-after and rollback)
    analyst.py       what the history says: tunnel profiles (availability, outages, latency, weak hours), outages that come together, what fixes what,
                     servers at the peak (idle / redundant); hints the smart repair asks before it climbs (RepairCase rows); `stability_view` / `stability` = the Stability box of
                     a tunnel window (GET /tunnels/{id}/stability: the share answered and the cuts over 24 h and 7 days, the last cut, up for, verdict, the strip of the last day)
    reviewer.py      the Reviewer, the teacher of the Doctor (docs/reviewer.md): after a smart repair that went badly or took long it writes FINDINGS by its own rules, a REVIEW by the AI (when a key is saved: a few calls an hour,
                     facts as data, IPs masked, the answer parsed into a fixed shape), a REPORT for the developer, and proposes LESSONS (skip a rebuild / relink on a tunnel, avoid a type on a pair for some days) that the owner
                     approves; approved ones reach the Doctor through nalyst.hints_for and knowledge.plan_for. It never touches a tunnel or a server
    diagnosis.py     reads a failure: is it the panel, a service on your server, or outside it? (pure rules; a repair's plain summary and the log summary use it)
    underuse.py      a server that carries little is looked at (net_health probe): what the kernel settings can fix is fixed (sysctl_tune, measured,
                     rolled back when worse); a fault of the provider's network is reported once
    besttoday.py     the best tunnels of the day: ranked from the checks of the last hours (up, latency, jitter, loss, breaks); a job measures every tunnel again each half hour - pings only since 1.69.226 (30 pings through the door or over the GRE link, no data moved; the speed is the bandwidth job's)
    checkall.py      check every server in one go (answers, GRE through a temporary link, filtering in Iran): ONE action on the panel server, progress from the
                     action log, Stop ends it, a page reload does not
    setup.py         the first-run checklist of the Dashboard (1.57.54): the API keys and the rest a new panel still needs (panel, Telegram, AI key, DNS account, servers, probe, backup passphrase, two-step sign-in), each with what is done; reads only, the owner can hide it; api/setup.py, web/app.js (setupCard)
    housekeeping.py  the growing tables trimmed (events 60 days, finished stories 90 days), the write-ahead log folded back
    demo.py          the public demo: invented data, no sign-in, every change refused (NIKATEL_DEMO=true; docs/demo.md)
    alerts.py        every 5 s: the states the checkers wrote → one DOWN / UP message after `down_seconds` / `up_seconds` (the clock starts at the checker's own `state_since`), one message for a mass event (`MASS`: many of a kind falling together wait, then are one line with the shared error), flapping control, an outbox that sends again what Telegram did not take; report.py daily report; telegram.py; backup.py (encrypted)
    configs.py       configs on the probe, subscriptions, group sync (the full round every 2 min; `sync_fast` reads the probe every 15 s and counts DIFFERENT tests of the probe, two must agree); probe.py probe API client; probe_install.py
    panels.py        Marzban / PasarGuard client: how a panel is connected (API key / token / sign in once, docs/panels.md), status (checked every 15 s: down after two failed checks, a node's reconnect asked at most every 30 s), nodes, users, hosts, test config per panel
    hosttls.py       TLS copies of the plain WebSocket hosts of a panel (port 443, security tls, SNI = the Host header): the plan, a real test from the panel server (TCP, TLS with the SNI, WebSocket upgrade), the copies with a backup file, the removal
    pg_access.py     PasarGuard groups and user templates (roadmap #94), api/pg_access.py, web/pg_access.js
    pg_nodes.py      PasarGuard nodes joined with the servers of this panel by address (add / install a node from the Add server form, web/pg_srvnode.js), api/pg_nodes.py, web/pg_nodes.js
    pg_nodes_extra.py  PasarGuard nodes, the rest of the API: bulk enable / disable / reset / reconnect / delete, usage and latency curves, user counts, clearing usage; routes in api/pg_nodes.py
    pg_update.py     PasarGuard updates (Settings → PasarGuard, roadmap #103): the newest releases from GitHub, what every panel and node runs, nodes updated ONE AT A TIME (a node that does not come back stops the run), the panel itself on its own server with `pasarguard backup` + `pasarguard update` over SSH (a detached action of ssh.py, followed by extras._run_item); api/pg_update.py, web/pg_update.js
    vantage.py       is an Iran server ON when the panel's host cannot connect to it: once a minute its tunnels are asked from the FOREIGN end (open connections, ping over an L3 link - the spoof tunnel keeps answering in a cut), then the other Iran servers of the panel (a TCP connection to its SSH port); the pulse keeps a server that answers on and says who saw it
    costs.py         the Costs page (the owner, 2026-10-31): the prices he types (a rent per month, a domain per year, an invoice, a price per TB with the tax, in toman / USD / EUR / lira) added up for a Jalali or Gregorian month and divided by the traffic - the nodes from the PasarGuard usage (pg_usage), an Iran server from the last week of the panel's own samples - what the month cost so far / in all and what 1 GB costs, a finished month kept as it was, today's rates from chand.online; the shop bot's income through a restricted key (bot_income) and the profit; read only, api/costs.py, web/costs.js
    pg_safe.py       PasarGuard Safe update: ONE button for the panel and its nodes - the nodes first, the one that carries the least traffic first, one at a time (the run stops at the first node that does not hold), the panel itself last
    pg_auto.py       the Safe update by itself once a night (the switch under the two buttons, off by default): a time budget, the users of every node looked at after its update (the configs that ride it, read again from the probe), the panel kept (image, compose files, a database dump) and put back when it does not hold; a release that did not hold is held back; the story in the Doctor's history, one event, one Telegram message; a rehearsal that changes nothing; a run cut by a restart of the panel goes on after it with the next node (where it is lives in the settings), never a node twice
    pg_cores.py      PasarGuard cores (Xray configs): make / duplicate / delete (also several at once), inbound add / change / delete with a REALITY builder, dry run, save with a backup and a read-back, restart, rollback, api/pg_cores.py, web/pg_cores.js
    pg_admin.py      PasarGuard admins (also several at once through the panel's bulk calls), API keys and settings (7 sections as forms), api/pg_admin.py, web/pg_admin.js
    pg_investigate.py  Investigate one PasarGuard user (the owner, 2026-11-03): usage by hour and node, devices against their limit, subscription fetches, live addresses, a plain verdict (pure `judge`) and a short message for the customer, read only; the route is in api/pg_users.py, the window in web/pg_users.js
    pg_watch.py      Watch one PasarGuard user for a few hours (the owner, 2026-11-03): every 2 min his used traffic and the addresses he is connected from are read into user_watches / user_watch_samples, summarised (pure `summarize`) as 10-minute bars, addresses first / last seen and plain findings; job `user-watch`; routes in api/pg_users.py, window in web/pg_users.js
    pg_roles.py      PasarGuard admin roles (permissions, limits, access, device rules) with backups and restore, api/pg_roles.py, web/pg_roles.js
    pg_client_templates.py  PasarGuard client (subscription) templates - Clash, Xray, sing-box, User-Agent lists - with backups, delete several in one call, api/pg_client_templates.py, web/pg_client.js
    pg_system.py     what the panel keeps about itself - version, resources, user counters - api/pg_system.py (the System tab was removed on 2026-10-15; its Users box is in Usage > Users, read through /usage/overview)
    pg_users.py      PasarGuard users in full: the whole user (groups, on hold, devices limit, next plan, owner), devices, subscription fetches and the subscription in each client format, bulk by name, shift of end dates and data limits, the Shadowsocks method of many, clean-up, users from a template; api/pg_users.py, web/pg_users.js
    pg_hosts.py      PasarGuard hosts in full: every field, the advanced settings as checked JSON, make / change / clone / delete / bulk, backups; api/pg_hosts.py, web/pg_hosts.js
    pg_kinds.py      One step makes a whole KIND of account on a PasarGuard panel (seventeen kinds in drawers with a short name and an emoji each, plus protocols the owner adds himself - kept in the settings: the builder's protocol, transport and security, or the JSON of an inbound; only the protocol is asked: the rest is chosen): the inbound (core), the group (access), the host and - when asked - a user template together, checked first, read back, taken back when a step fails; api/pg_kinds.py, web/pg_kinds.js
    pg_usage.py      Usage analytics of a PasarGuard / Marzban panel, read only: counters, usage by node and Tehran day, admin / user lookups, top users, users over time (online / expired / limited), the apps that fetch the subscription, Jalali calendar; api/pg_usage.py, web/pg_usage.js
    cfgstats.py      the usage of the CONFIGS (hosts) and inbounds of a PasarGuard panel (docs/config-stats.md): the map's numbers read back as 24 h / 7 / 30 days with the trend, little used and falling judged, the servers' meters and the CDN accounts; the job `cfgstats` (5 min); api/pg_configs.py, web/pg_configs.js
    cfgmeter.py      the config METER: ONE counting-only nftables table `inet nikatel_meter` per server (the owner switches it on; policy accept, no drop / NAT / mark, no file, no boot unit), its counters read every five minutes and stored as HOURS with reset / reboot / gap handling (`cfg_buckets`); ssh.py `meter_read`, `meter_apply`, `meter_remove`
    cfgmap.py        pure: which counter belongs to which host - a server's port, an Iran server's forwarded port (tunnel), a name behind a CDN (its report; the origin port shared), `{SERVER_IP}`, none - from the panel's hosts, cores and nodes, our servers and the DNS accounts' records
    cfgcdn.py        the CDN side of the config statistics, read only, once an hour: ArvanCloud `reports/traffics` with a host filter (the first read is a test of the filter) and Cloudflare GraphQL by host; tolerant parsers, the state of each account
    accounts.py      one account over many panels: the users of every panel asked through their API and put side by side, one action / a new user on every panel, the signed merged subscription link (/sub/<token>, api/sub.py)
                     (same username, or a hand-made link); nothing is copied out of the panels (roadmap M12)
    outbound.py      the Outbound section: a pool of exit configs (vless / vmess / trojan / ss) and forwards to the foreign nodes, put on the
                     chosen Iran servers through gateway.py (spread / fast / auto), a Shadowsocks relay on a foreign server behind the exits, the ArvanCloud
                     records of the tunnels pointed at the forwards (manual or automatic, put back through the DNS log), the pool measured from each Iran server; exit links never listed
    core.py          Xray core outbounds of a panel with backup / rollback
    radar.py         Arvan Radar (state of the Iranian data centres), cached, refreshed in the background (API only)
    dcs.py           the hosting providers our servers sit in (from the geo lookup's provider / domain), servers and
                     tunnels per provider, the provider's website checked from the panel server (cached)
    shopbot.py       the shop bot's server over SSH: services, switches, users, SQL, backups, DB import
    shopbot_admin.py the bot's administration: payments (approve / decline like the bot), orders, users, plans,
                     categories, servers, discounts, gifts, broadcasts, tickets, texts (overrides), settings
                     documents, apps, top-ups, backups, command menu, Mini App status / deploy
    bale.py          the Bale relay (docs/bale.md): installs relay/bale_relay.py on ONE Iran server over SSH (reviewed actions relay_prepare, put_file, unit_enable_restart, relay_remove), writes its config, reads its status
                     (the read script relay_status) and queues a notice; the relay itself talks to the customers in Bale and tells them by itself when the international internet is cut
    importer.py      the old panel's JSON -> rows, idempotent
    settings.py      typed settings in the database (secrets masked in responses)
    events.py        the sentences the operator reads
  api/             FastAPI routers, one file per resource; thin: validate, call a service, answer
                   (auth, tokens, servers, tunnels, nodes, gateway, engines, actions, autopilot, ai, configs, panels, accounts, outbound, radar, shopbot, bale,
                    alerts, events, jobs, failover, doctor, dns, license, balance, analyst, reviewer, commander, checkall, best, setup, system = health, logs, update of the panel itself)
  web/             plain JS and CSS, no build step. index.html loads everything; app.js: PAGES.<name> per page, SUB tabs, glass modal windows (showModal), themes (LOOK),
                   a loading box on every page change and automatic retry when a load fails, a status bar for messages; pg_*.js: one file per PasarGuard tab, each
                   registers itself in pgTabs (pg_common.js) and is drawn by the pages of the PasarGuard group (see 2b); app.css + pg_*.css: the looks.
                   The fluid layer (1.44.0, roadmap #101): early.js picks the layout (`html[data-fluid]`), fluid.css is the desktop and the tablet (bars are one row,
                   windows, the lock at 560 px), fluid.js fits a table to its box (columns shrink, the less important ones drop; a header can say its own
                   importance with data-prio, 0 = never drops - for a table whose first column is a tick box), mobile2.css is the phone,
                   mobile.css the old card layout (`?fluid=0`); i18n*.js the Persian words
  main.py          create_app(), lifespan, security headers (CSP), `nikatel` CLI (serve / import / set-password / run-job / restore)
relay/             the Bale relay (bale_relay.py, Python standard library only): a Bale bot on an Iran server that talks to the customers while the international internet is cut; installed by services/bale.py
bot/miniapp/       the Telegram Mini App of the shop bot (api.php + index.html + app.js + app.css), deployed
                   next to the bot by the panel (Shop bot → Tools → Mini App) or scripts/deploy-miniapp.sh
tests/             pytest; services tested pure, the API through TestClient, ssh never real
scripts/           deploy.sh (secrets → compile → libraries, only when pyproject.toml changed → imports + migrations on a copy of the live data → unit → restart → health → rollback of the files AND the branch;
                   the tests run on the build server before a version is published, FORCE_TESTS=1 runs them here too; it keeps update-progress.json for the page), backup.sh, deploy-miniapp.sh,
                   unit, secrets check, pg_coverage.py (which routes of a PasarGuard panel the tests send: a recorder on the calls to a panel
                   matched with the OpenAPI document of a live panel)
install.sh         the one-line installer (also used by the panel's own "Update" button through deploy.sh)
docs/              operations, development, logging, roadmap, adr/
```

## 2b. The UI, page by page

The left menu (`web/index.html`): **Dashboard · Servers · Tunnels · Panel**, Shop bot (switched off) and **Settings**. Every page with tabs is a group like the sidebar of PasarGuard: `navBuild()`
(app.js) puts the tabs of the page - `SUB.<page>`, or `SET_TABS` for Settings - under its head, the arrow opens and closes the list (`navGroup`), `navMark` says which item is the open page and tab, and
a wide window does not draw the bar of tabs under the header while the group is open (see app.css; the rail and the phone show the head alone). `PAGES.<name>` draws a page, `SUB` holds the tabs of a
page, `go(page, tab)` opens a page on a tab, `NAV_ALIAS` keeps the old page names working (`"page/tab"` = on that tab).

* **Dashboard** - health rows, Problems box (live state, Fix with AI, Fix all), activity feed with
  categories; auto-refresh.
* **Servers** - Foreign · Iran · **Nodes** (the PasarGuard nodes of the picked panel beside our servers). More / Edit / Delete on every row; the window of a server has its buttons
  (console, install, connect, reboot, Abuse-Defender, geo), the line of its nodes (with the **Node** view for them, 1.48.0), three boxes (System, Resources, Access & status) and the charts.
* **Tunnels** - List (More window with quality and ports, Health / Restart / Rebuild), Create, Test (pair
  test over several engines and transports, Advanced for the transports), Core update, Tunnel Doctor
  (attention list, live repair stories, history), DNS · IP switch (ArvanCloud records, Point, failover plans,
  the DNS change log with Restore).
* **Panel** (Marzban panels have Users, Hosts and Usage only) - ONE page with a tab for each section of PasarGuard (`SUB.pgpanel`): **Users** (users, accounts over every panel) · **Hosts** ·
  **Access** (Groups, Templates, client templates) · **Core** · **Admins** (admins, API keys, roles), and **General** as the FIRST tab (Usage, System, Settings; the owner, 2026-10-14). Every tab draws the bar of the panels (`pnBar`:
  which panel) and its own tab(s); those are `pgTabs` entries of the `web/pg_*.js` files, each a route first (`/api/panels/{id}/pg/…`). `PG_SUBS` (app.js) says which `pgTabs` each tab holds,
  `pnTab(t)` opens the tab that holds one (a test fails when a registered tab has no place). **The nodes of a PasarGuard panel are not a tab** (1.48.0): a node and the server it runs on are one row of Servers -
  `pg_nodes.js` is the **Node view** of a server window (and of the window of a node that runs on no server of ours), `pg_srvnode.js` the chips, the ticks, the Nodes… menu and the Add server tick;
  the page asks `GET /api/servers/panel-nodes` (the health job's copy of every panel's nodes, placed on the servers by `pg_nodes.placement`) and the Node view `GET /api/panels/{id}/pg/nodes`
  (which also refreshes that copy). Configs (the probe's client configs) are switched off.
* **Shop bot** - Overview · Payments · Orders · Users · Plans · Categories · Servers · Discounts ·
  Gift codes · Broadcast · Tickets · Texts · Settings · Tools (Mini App, backups, apps, top-ups, tables,
  SQL, stats, webhook, commands, restart, dump, logs, import).
* **Settings** - Account · Alerts · Backups · Probe · Repairs & AI · Shop bot · Appearance · About & update
  (version, update, license, tunnel cores).

## 2d. The license

`services/license.py` answers one question for the rest of the panel: *is this feature on?* The modes are
owner (`NIKATEL_LICENSE=owner`), licensed (a token under Settings → About, checked with the vendor's Ed25519
public key, cached a minute) and free (no token: Backhaul TCP Mux only). The gates: `builder.start_build`,
`build_other.start_build`, `tunnel_test.compare_start`, `autopilot._recommend_now`, `ai.ask`,
`failover.has_key` and the `/api/shopbot` router (402). The check is honest about its limits -
`docs/licensing.md` explains why the valuable parts (metered AI, releases) belong on the license server.

## 2c. The shop bot and its Mini App

The bot is a PHP application (WizWiz-based) with a MariaDB database on its own server. The panel
never imports its code: it reads and writes the database through two reviewed scripts
(`bot_query` for SELECT / SHOW, `bot_sql` for one UPDATE / INSERT / DELETE), with every text literal
as `FROM_BASE64("…")` (`shopbot_admin.lit`), talks to Telegram through `bot_tg_json` on the bot
server (the token stays there), reads the bot's texts with `bot_texts` (`php -r` over `values.php`)
and writes overrides as JSON files that one line in `config.php` merges (`bot_texts_hook`). The
semantics the panel mirrors - payment states (`pending` → `have_sent` → `approved` / `declined`),
what approve does (wallet, admin messages, user message), the order columns, the settings
documents (`BOT_STATES`, `PAYMENT_KEYS`) - are documented in `shopbot_admin.py` next to the code.

The Mini App (`bot/miniapp/`) is a static page plus `api.php` living in `<bot dir>/app/`. It
verifies Telegram's `initData` (HMAC with the bot token), then uses the bot's database and the
panels' HTTP APIs directly, writing the same rows the bot writes (see `bot/miniapp/README.md`). The
panel installs and updates it through the `bot_miniapp_put` action (base64, `php -l` before the
old file is replaced, previous copy kept) and reads its state with `bot_miniapp_info`.

## 3. Rules

1. **Layers call downward only**: api → services → (db, ssh). Services never import FastAPI.
2. **Pure before impure**: parsing and judging are functions of strings and dataclasses, tested
   without a server. SSH and the database sit at the edges.
3. **Named scripts only** over SSH. Something new to read on a server = a new entry in
   `ssh.SCRIPTS` with parameter validation, reviewed in the PR. No API handler builds a shell
   command.
4. **Secrets stay in**: `Tunnel.secrets` and `auth.json` never reach a response; the log
   formatter masks tokens; `.gitignore` and `scripts/check_no_secrets.sh` keep keys, data and
   real addresses out of the repository. The bot token and database password never leave the bot
   server (see `SECURITY.md`).
5. **State changes are events**: a transition (up→down, offline→online) writes one Event; a
   check that changes nothing writes none.
6. **Jobs never die**: `jobs.run_once` is the boundary; a failure lands in `jobs.last_error`
   and the UI, not in a dead thread.
7. **Errors are sentences**: 400 bad input · 401 login · 404 no such thing · 409 conflict ·
   502 a server or panel did not answer · 500 our bug (logged with the request id the browser
   shows). Never a traceback to the browser. One handler gives every error the one shape
   `{"ok": false, "error": <sentence>, "code": <slug>}`, and work longer than a few seconds answers 202 with an
   action id (`docs/api.md`).
8. **Every change ships with a test**; `scripts/deploy.sh` and CI refuse a red suite.

## 4. Data

SQLite in WAL mode at `<data_dir>/nikatel.db`. What is kept: server and config samples 7 days, tunnel checks 14 days,
events 60 days, finished repair stories 90 days, the last 2,000 repair cases (`services/housekeeping.py` runs every 6 hours). Schema changes are entries in `db.MIGRATIONS`, applied on start.

## 5. Adding a feature

1. Model: columns or a table in `models.py`, a migration in `db.MIGRATIONS`.
2. Service: logic in `services/<name>.py`, pure parts first, tests in `tests/test_<name>.py`.
3. SSH: if the servers must be read, a named script in `ssh.SCRIPTS`.
4. API: a router in `api/<name>.py`, registered in `api/__init__.py`.
5. UI: `PAGES.<name>` in `web/app.js`, a nav entry, styles under a `/* ---- <name> */` block.
6. Docs: a `CHANGELOG.md` line; `docs/operations.md` if it changes how the panel is run.

## 6. Decisions

`docs/adr/`: 0001 fresh start beside the old panel · 0002 FastAPI + SQLite + plain front-end ·
0003 separate key, read-only named scripts · 0004 public repository without infrastructure data.
