# One panel instead of Marzban + PasarGuard: can it be done? (study, 2026-10-13)

The question: make this panel the VPN panel itself - its own users, subscriptions and nodes - import the Marzban and
PasarGuard databases into it, drive the nodes it already adds, and retire the other panels. Later: every account
carries everything, WireGuard included. This is the study that was asked for before any code.

## 1. The short answer

Yes, it can be done, and the one fact that makes it realistic: **the PasarGuard node is a standalone agent with a
documented API** (GPL-3.0, Go, gRPC + REST, "works with the PasarGuard panel or on its own", official Go and Python
client libraries). It runs Xray (`BackendType.XRAY`) and WireGuard (`BackendType.WIREGUARD`). Its service is small:

| RPC | what it does |
|---|---|
| `Start(Backend{type, config, users[], keep_alive, exclude_inbounds[]})` | start the core with a full config and the users |
| `SyncUser(stream User)` / `SyncUsers(Users)` / `SyncUsersChunked` | add / change / remove users (`User{email, proxies{vmess,vless,trojan,shadowsocks,wireguard,hysteria}, inbounds[]}`) |
| `GetStats(StatRequest{name, reset, type: UsersStat/UserStat/Inbounds/Outbounds})` | traffic counters, with reset |
| `GetUserOnlineStats`, `GetUserOnlineIpListStats` | who is online, from which IPs |
| `GetSystemStats`, `GetBackendStats`, `GetBaseInfo`, `GetLogs(stream)` | the server, the core, the versions, the log |
| `Stop` | stop the core |

So the panel does not need its own agent on the foreign servers: it keeps installing the PasarGuard node (the Install
view already does that, with the node's API key and certificate) and **talks to it directly**. The panel becomes the
brain; the node stays the muscle. Everything else is data and web: users, limits, hosts, subscription links.

## 2. What the other panels hold (what must be imported)

Both schemas were read (Marzban `app/db/models.py`, PasarGuard `app/db/models.py`). The parts that matter:

| thing | Marzban | PasarGuard | keep in the import |
|---|---|---|---|
| user | `users` (username, status, used_traffic, data_limit, reset strategy, expire [int], sub_revoked_at, note, online_at, on_hold_*, auto_delete_in_days, admin_id) | same + `expire` datetime, `hwid_limit`, groups | yes, 1:1 |
| identities | `proxies` (one row per protocol: type + settings JSON with uuid / password / flow) | `users.proxy_settings` JSON (same content) | yes - **the UUIDs and passwords are what the clients hold; they must not change** |
| which inbounds | `exclude_inbounds_association` per proxy | `groups` -> `inbounds_groups_association` | yes (becomes the panel's groups) |
| hosts (share-link settings) | `hosts`: remark, address, port, path, sni, host, security, alpn, fingerprint, mux, fragment, noise, random_user_agent, use_sni_as_host | same, plus arrays, transport / mux / ech / wireguard_overrides JSON, status filter, subscription_templates | yes - this is what the subscription link is made of |
| inbounds | `inbounds` (tag only; the real inbound lives in the Xray config) | same + `core_configs` (JSON per core, type xray/wg/singbox/mtproto, per node) | yes: the Xray config JSON is the source of the inbounds |
| nodes | `nodes` (address, port, api_port, status, usage_coefficient) | same + `api_key`, `server_ca`, `connection_type grpc/rest`, `core_config_id`, data limits | yes - these are the servers the panel already knows |
| usage | `node_user_usages`, `node_usages` (one row per hour), `user_usage_logs` (resets), `admin_usage_logs` | same + `node_stats` | yes (history), with the hour granularity |
| admins | `admins` (sudo flag, telegram_id, users_usage) | `admins` + `admin_roles` (RBAC), `api_keys` | yes (the shop bot is one admin) |
| templates | `user_templates` + inbounds | + groups, `client_templates` (subscription page/templates) | yes |
| subscription token | JWT signed with `jwt.secret_key` (`/sub/<token>`) | same idea | **copy the secret**: the old links keep working |
| WireGuard | - | `wireguard_subnets`, `Wireguard{public_key, peer_ips}` per user, `wireguard_overrides` in hosts | later (phase 6) |

Nothing here is exotic; it is the same shape as the panel's own `Server`, `Tunnel`, `Config` tables.

## 3. What the panel has to build (the honest list)

1. **Data**: users, identities, groups, inbounds, hosts, core configs, nodes, usage (per node per user per hour),
   admins / resellers, templates, subscription updates (user agent, IP, HWID).
2. **Node driver**: gRPC client of `NodeService` (the official Python library, or 300 lines of grpc on the proto):
   push `Start` with the core config and the users of that node; `SyncUser` on every change; `GetStats(reset=True)`
   every 20-30 s per node into the usage table; `GetUserOnlineStats` for "online"; node health into the Servers page.
3. **User lifecycle**: limits -> `limited`, expiry -> `expired`, `on_hold` that starts at first use, periodic resets
   (day / week / month / year), next plan, auto delete, HWID limit; a limited or expired user is removed from every
   node at once; reminders on Telegram (the panel's alerts already do this for servers).
4. **Subscription server**: `/sub/<token>` and `/sub/<token>/<format>`: v2ray (base64 links), clash / clash-meta,
   sing-box, xray-json, outline, and WireGuard `.conf`; the client is told by `User-Agent`; the headers
   `subscription-userinfo` (upload / download / total / expire), `profile-title`, `profile-update-interval`,
   `support-url`; the share-link rules from `hosts` (fragment, noise, mux, REALITY pbk / sid read from the inbound
   config, flow, ALPN, fingerprint, random user agent). **This is the part that must be byte-for-byte right**:
   tested against what PasarGuard produces for the same user (golden files).
5. **API for the shop bot and the Mini App**: they talk to the Marzban / PasarGuard REST API today (`/api/user`,
   `/api/users`, `/api/user/{name}/reset`, `/api/user/{name}/revoke_sub`, `/api/admin/token`, `/api/system`,
   `/api/inbounds`, templates). The panel offers the **same routes** under `/api/pg/...` (or on the old host name),
   so the bot and the Mini App switch with a base URL and nothing else; later they can move to the panel's own API.
6. **Pages**: Users (list, search, window with usage chart / links / QR / reset / revoke / HWIDs), Hosts, Inbounds
   and core configs (a JSON editor with validation and a "test on one node" button), Groups, Templates, Admins. The
   panel's style and helpers exist; it is work, not research.
7. **Importers**: `nikatel import marzban <db>` and `... pasarguard <db>` (SQLite / MySQL / Postgres via SQLAlchemy):
   idempotent, dry run first, a report of what would change, and the JWT secret copied so the old subscription links
   stay valid.

Size, honestly: about 6-8 k lines of Python and 3-4 k of JS, with tests. At the pace of this project (a release a
day) that is **4-6 weeks**, if nothing else is built in between. The risk is not the code; it is a user who loses
connection because one share-link detail differs. The plan below is built around that risk.

## 4. The plan: strangler, never a big bang

| phase | what | users notice |
|---|---|---|
| 0 | this study; milestone **M12 Unified core**; the freeze of M10 / M11 stays for everything else | nothing |
| 1 | **read model**: import both databases into the panel's tables (read-only), a Users page over them; a sync job keeps pulling from the live panels every minute. PasarGuard and Marzban stay the engines; the bot stays on them | nothing |
| 2 | **node driver + shadow node**: one new foreign node, installed by the panel, driven by the panel (`Start`, `SyncUsers`, `GetStats`), with the same core config and the imported users; a test subscription points at it | nothing (one test device) |
| 3 | **subscription server**: the panel serves `/sub/<token>` for the imported users with the same output as PasarGuard (golden tests on 20 real users, every format); the old secret copied | nothing yet (the old URL still goes to the old panel) |
| 4 | **lifecycle + compatible API**: limits, expiry, resets, on hold, HWID, reminders; the bot and the Mini App switched to the panel's `/api/pg/...` on the test bot first (@nikaspeedbot has the real dump) | nothing |
| 5 | **cut-over**: the subscription host name moved to the panel (same path, same tokens); nodes moved one at a time (same inbounds, same users: a client just keeps connecting); the old panels kept read-only for 30 days as the way back; then retired | nothing, if 3 was right |
| 6 | **WireGuard**: the node's WireGuard backend, a key pair per user, a subnet per node (`wireguard_subnets`), the `.conf` and the `wireguard://`-style entry in the subscription; **the Iran side needs a UDP-capable tunnel** (BackPack Direct UDP / Paqet KCP; the TCP Mux Backhaul does not carry WireGuard) | a new entry in their subscription |

Phases 1-3 can be done without touching anything a user holds. The "go / no go" for 5 is the golden test of phase 3.

## 5. imonitor (github.com/iranmonitor/imonitor), looked at

A Rust single-binary per-server tool: service checks (ICMP / TCP / UDP / HTTP / DNS / SSH / proxy / tunnel), a
public status page, Telegram alerts, backups, tunnel creation in many modes (GRE, GRETAP, IPIP, SIT, GUE, VXLAN,
GENEVE, L2TPv3, ESP, WireGuard, TUN over UDP / TCP / ICMP / SSH), traffic shaping, TCP limits, SNAT / port forward,
eBPF / XDP ingress. Licence: source-available, **modification and redistribution need permission**. 12 stars, 4 commits.

What it is for this plan: **nothing to build on** (licence, Rust, one node = one install, no users / subscriptions /
Xray management - it overlaps with the tunnel half of this panel, not with the VPN-panel half). What is worth
borrowing as ideas, later, under the panel's own code: the tunnel modes that the panel does not have (VXLAN, GENEVE,
L2TPv3, TUN-over-ICMP / SSH as a last resort when UDP and TCP are both filtered), per-server traffic shaping, and a
public status page.

## 6. Decisions needed before phase 1

1. The agent on the foreign servers = the PasarGuard node, as it is (recommended; no agent of our own).
2. The subscription links stay exactly as they are (same host, same path, same token): the old `jwt.secret_key` is
   copied, the subscription host name is moved at the cut-over. (Recommended; the alternative - new links for every
   user - is the thing the shop bot would have to push to thousands of people.)
3. Which panel is the primary source today for each group of users (Marzban "Nikatel-bot" panels, PasarGuard
   "hamkar" panels): both importers are written, but the cut-over order follows this.
4. The bot and the Mini App move through the compatible `/api/pg/...` routes first (no bot rewrite in the critical
   path), the panel's own API later.
5. Scope of the first cut-over: Xray users only; WireGuard is phase 6, after the UDP tunnel question is settled.
