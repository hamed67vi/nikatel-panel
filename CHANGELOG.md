# Changelog

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
