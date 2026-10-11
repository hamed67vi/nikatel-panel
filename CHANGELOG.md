# Changelog

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
