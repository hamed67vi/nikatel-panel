# The usage of the configs of a PasarGuard panel

The owner, 2026-10-21: "is a section in the panel that shows the usage of the configs possible?" - "only for PasarGuard" - "add the apps' statistics too". What he wants to know: **which configs are little used or falling**
(configs = the hosts of the panel and the inbounds behind them, not the nodes).

Where: *Panel → General → Usage → Configs* (also a card of the overview). PasarGuard only: nothing here exists for Marzban.

## Why the panel measures it itself

PasarGuard (5.4.1, read on the public OpenAPI) keeps usage **per user** (`/api/users/usage`, `/api/user/{u}/usage`) and **per node** (`/api/node/usage`, `/api/node/{id}/stats`), the online counts (`/api/users/counts/online`),
`/api/inbounds/details` (only tag, protocol, network) and `/api/hosts`. There is **no per-host and no per-inbound traffic anywhere**, and Marzban has none either. So this panel counts it, read only for the PasarGuard panel
(nothing is ever written to it) and without touching a customer:

| What the host is | Where its number comes from | Shown as |
|---|---|---|
| its address is one of our servers (an IP, a name that points at one, a record of a DNS account that is not behind a CDN) | the counter of the port the users dial, on that server (the host's own port, else the inbound's) - a **meter** the owner switches on per server | `fr-1 :443` |
| an **Iran server in front of a tunnel** (the host points at its forwarded port) | the same counter, on the Iran server, for the port the users dial (a forwarded port is counted as well: see below) | `ir-1 :50325` |
| a name behind a **CDN** (a record of a DNS account with the cloud on) | the CDN's own traffic report of that name, read once an hour | `CDN` |
| behind a CDN, and the CDN gives no per-name number | the **origin port** that every host of the inbound shares | `origin :8072`, badge *shared* |
| the address is `{SERVER_IP}` (the node itself) | the inbound's port on every node that carries it | `2 nodes` |
| anything else (an address that is not ours and not in a DNS account) | nothing - the row says why | `—` |

Two hosts that end on the same server and port **cannot be told apart** by any counter: both get the badge *shared* and the number is for both (no verdict is given on it).

## The meter (`services/cfgmeter.py`)

ONE nftables table `inet nikatel_meter` per server, switched on by the owner (*Counters → Turn on*, or *Turn on all*) and removed by *Remove* / *Remove all meters*. It only **counts**:

* its base chains (`input`, `output`, `forward`) have **policy accept** and no verdict but `jump` into a chain per port that holds two `counter` statements - no drop, no reject, no NAT, no mark. Nothing is restarted, no xray core is touched, nobody
  is cut, **no file is left on the disk and there is no boot unit**: after a reboot the panel loads the table again at its next turn (five minutes). Deleting the table is the whole of the way back.
* the port is the port the **user dialled** (`ct original proto-dst`, still right after a REDIRECT or a DNAT), counted ONCE where a packet enters the machine or is made on it: `input` counts connections the user began (rx = what users send),
  `output` counts the replies (tx = what users get), `forward` counts connections that were DNAT-ed to another machine (an Iran server in front of a tunnel), both directions. A connection the machine **opens itself** to some remote port
  (a node reaching a website on 443) is never counted as that port. TCP and UDP.
* the bytes are those of the **IP packets** (headers included, about 3 % over what the users moved). The page says so.
* **connections**: every five minutes the panel reads the open connections of each port - the sockets of the listener (`ss`), or, for a port nobody listens on (forwarded to another machine), conntrack (`/proc/net/nf_conntrack`, else the `conntrack`
  tool). A machine that has neither (kernel 6.8 of Ubuntu 24.04 leaves the proc file out) says **no number** - the cell shows "-", never a made-up zero.

Counters are cumulative and a reboot or a new table zeroes them, so only **differences** between two reads are stored, as **hours** (`cfg_buckets`: bytes in, bytes out, the most connections seen, samples; about two months are kept): a counter that went back,
a new boot id and a port new in the table mean "count what is there"; a gap between two reads is spread over the hours it covers. The old table's last counters are read before the table is replaced or removed, so nothing is lost on a change.

Proved on a real kernel (network namespaces, 32 checks, `meter-netns-proof.sh` in the notes of the work): a local listener counts rx and tx within 0.3 % of the payload plus headers beside a drop-policy firewall; the server's own outgoing connection to a metered
port 443 adds nothing; a DNAT-forwarded port and a REDIRECTed port are counted under the port the user dialled (and the redirect not also under the real port); loading twice is fine; Remove leaves no table and traffic keeps flowing; the panel's own scripts
(`meter_apply`, `meter_read`, `meter_remove`) give answers the parser reads. Traps found: `fwd` and `in` are nft keywords (the chains are `c_in`, `c_out`, `c_fwd`).

## The CDN reports (`services/cfgcdn.py`)

Many hosts behind a CDN reach the same origin port, so no counter on our server can tell them apart. The CDN reports its traffic per domain; the panel reads those reports **once an hour, read only**, through the DNS accounts that are already on the DNS page.
**Not proved against a real account** (none was available on 2026-10-21); what is known:

* ArvanCloud CDN 4.0: `GET /domains/{domain}/reports/traffics` with `period` (`1h 3h 6h 12h 24h 7d 30d`), the series `reports.traffics.total` under `data.charts.traffics.series` (read from ArvanCloud's own Prometheus exporter) and a sub-domain filter
  (the name `filter_subdomain` is taken from the generated client; ArvanCloud's changelog says the host filter belongs to the Growth plan and above). So the first read per account is a **test**: the whole domain, a made-up host (a working filter answers 0) and the real host.
  A filter that does nothing, or that the plan refuses, marks the account *per-host numbers not available* and its hosts fall back to the shared origin port. *Counters → CDN reports → Test* runs it by hand and the page shows what the CDN answered.
* Cloudflare: GraphQL `httpRequestsAdaptiveGroups` by `clientRequestHTTPHost` (`sum.edgeResponseBytes`), one query per zone; it needs a token with **Analytics: Read** (the DNS token has only Zone: Read and DNS: Edit).
* Whether a long WebSocket session is counted by a CDN report while it lasts or only when it ends is not known: the numbers are the CDN's own and approximate for a VPN. The parsers answer "no number" for a shape they do not know; they never invent one.

## What the page shows and judges

The page is **one compact bar chart** (the owner, 2026-10-21, on the first version: "show the usage of all of them as a chart, compact, no extra information"): a row per enabled config - its name, a bar, the number of the window chosen (24 h / 7 days / 30 days),
a trend arrow and, only when it is true, a small mark. The three windows, the peak simultaneous connections and where the number comes from are in the tip of the row. A host that is switched off in the panel is not listed. Under the chart: the **Counters**
(a dot, the server, *Turn on* / *Remove*, and the CDN accounts with *Test*) and the **Client apps** (a ring with its legend).

* **Little used**: the host has its own counter (not shared), is enabled, has at least 2 days on record, and a day of its last week carries under **10 %** of what an average measured host carries (needs at least 3 measured hosts). The tip says the number now.
* **Falling**: the last 7 complete days carry at least **30 %** less than the 7 before, and the days before carried at least 150 MB a day (needs 16 days on record; from 8 days the last 3 days are compared with the 3 before).
* **Trend arrows** (the window chosen against the same length before): up / flat / down at 15 %; they need 2 / 14 / 60 days on record for 24 h / 7 / 30 days - until then the row has no arrow and its tip says how many days are still missing, it does not guess.
* **Shared** (×2): two hosts end on the same server and port, or the CDN gives no number for the host: the number is for all of them and no verdict is given.
* **No history before the collection began.** That cannot be recovered: PasarGuard keeps no such numbers.
* **The apps**: the share of each app (V2Box, v2rayNG, Hiddify, Happ ...) among the fetches of the subscription link, for the range chosen at the top, from `GET /api/users/sub_update/chart` (the panel already had it). PasarGuard records which app fetched a user's
  subscription, **not which config the user then picked**: the apps cannot be split per config. A config on a newer transport (xHTTP, HTTPUpgrade, ...) carries a ◆ in the chart: an old version of an app may not open it.
## What the owner does

1. *Counters → Turn on all* (or one server at a time). It asks first and says what it does. Without it nothing is counted on a server.
2. Nothing for the CDN reports if the DNS accounts are there already (the switch *read them* is on); for Cloudflare the token needs *Analytics: Read*; *Test* says what the CDN answered.
3. Wait: the first numbers are there after five minutes, the trend after days (the page says how many).

Off switches: *Remove* / *Remove all meters* delete the table (the hours already stored stay); *read them* switched off stops the CDN reads.

## API

`GET /api/panels/{id}/pg/configs` (the answer the page draws), `POST …/configs/meter` (`{server_id, on}`; without `server_id` every server of the map), `POST …/configs/cdn` (`{on}`), `POST …/configs/cdn-test` (`{account, fqdn}`).
Reading changes nothing on the panel; the meter routes refuse while the panel process is read-only. The job `cfgstats` runs every five minutes and does nothing while no meter is on and no host is behind a CDN.

## Limits that stay

No history from before the collection; two hosts on one port cannot be separated; the bytes include IP headers; behind a CDN the number is the CDN's and approximate; a forwarded port on a machine without conntrack has no connection number;
the CDN reports were not seen on a real account.
