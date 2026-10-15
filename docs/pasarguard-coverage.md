# PasarGuard coverage

What of the REST API of a PasarGuard panel (the OpenAPI document of 5.4.1: 203 operations) this panel uses, where, and what it leaves out on purpose. Checked on 2026-10-14 by reading the
OpenAPI document and the panel's own sources, by running every write against a throw-away PasarGuard 5.4.1 in docker (never against a real panel), and by `scripts/pg_coverage.py`,
which runs the tests with a recorder on every call to a panel and lists the operations that no test sends (below). A route that is "deprecated, removed in 6.0" (the `by-username`
ones) is not used: the `by-id` routes are.

Where each part lives (since 1.48.0): the **Panel** page of the left menu is one page with a tab for each - **General** (Usage, System, Settings) · **Users** · **Hosts** · **Access** (Groups, Templates) · **Core** ·
**Admins** (Admins, API keys, Roles) - and the nodes are in the windows of **Servers** (the Node view of the server a node runs on, and the Nodes… menu of the list); every tab has the bar of the panels on top. Every tab is a route first (`/api/panels/{id}/pg/…`, see [api.md](api.md));
the code is `nikatel/services/pg_*.py`, `nikatel/api/pg_*.py`, `nikatel/web/pg_*.js`.

## What is covered

| PasarGuard | Here | Notes |
|---|---|---|
| **Users** `/api/users`, `/api/user/by-id/{id}` (+ `reset`, `revoke_sub`, `active_next`, `disabled`, `set_owner`, `sub_update`), `/api/users/simple`, `/api/user/from_template`, `/api/user/{id}/subscription/{client_type}` | Users tab, the user window (`pg_users`) | the whole user: status, data limit, reset period, end date or ON HOLD (days, start-anyway day), devices limit, groups, NEXT PLAN, owner, note; devices (HWID), the last fetches of the subscription, **what the subscription answers in each client format** (Formats tab: text to read and copy, WireGuard as a size), where the user is online now |
| **Many users** `/api/users/bulk/{enable,disable,delete,reset,revoke_sub,set_owner,apply_template,data_limit,expire,proxy_settings,from_template}`, `/api/users/expired` | the **More…** menu of the Users tab | by name (ids looked up); `data_limit` / `expire` / `proxy_settings` (**the Shadowsocks method**) by a filter with a COUNT first; expired / limited / on hold / disabled users listed, then deleted with the state typed back; up to 500 users from a template |
| **Hosts** `/api/hosts`, `/api/host/{id}`, `/api/host/`, `/api/hosts/bulk/{enable,disable,delete}` | Hosts tab, the host window (`pg_hosts`) | every field and the advanced settings (headers, transport, mux, fragment, noise, ECH, final mask, WireGuard overrides, subscription template) as JSON checked key by key; clone (switched off), backups, bulk; the TLS copies feature |
| **Groups** `/api/groups`, `/api/group/{id}`, `/api/groups/bulk/{enable,disable,delete,add,remove}` | Groups tab (`pg_access`) | create / edit / delete, give or take groups to many users (dry run counts) |
| **User templates** `/api/user_templates`, `/api/user_template/{id}`, `/api/user_templates/bulk/{enable,disable,delete}` | Templates tab (`pg_access`) | GB and days, a user made from a template |
| **Client templates** `/api/client_templates`, `/api/client_template/{id}`, `/api/client_templates/bulk/delete` | Templates tab → Client templates (`pg_client_templates`) | Clash / Xray / sing-box / User-Agent lists; default moved, never switched off; system templates never deleted; **tick several and delete them in one call** (every type keeps one template) |
| **Nodes** `/api/nodes`, `/api/node/{id}` (+ `reconnect`, `sync`, `reset`, `update`, `core_update`, `geofiles`, `logs`, `usage`, `stats`, `outbounds_latency`), `/api/nodes/bulk/*`, `/api/nodes/reconnect`, `/api/nodes/clear_usage_data/{table}`, `/api/node/user_counts/{metric}`, `/api/node/settings` | The **Node view** of the window of the server a node runs on (`pg_nodes`, `pg_nodes_extra`), the **Nodes…** menu of the Servers list (`pg_srvnode`); the Add server form | one row per server, the node matched with it by address (or the DNS); "make this server a node" (plan, install, certificate, add, wait); bulk actions on the nodes of the ticked servers, usage and latency curves, the nodes that run on no server of ours |
| **Cores** `/api/cores`, `/api/core/{id}`, `/api/core` (create), `DELETE`, `/api/cores/bulk/delete`, `restart`, `reality-scan`, `/api/cores/simple` | Core tab (`pg_cores`) | make / duplicate / rename / delete, **tick several and delete them in one call** (never the first core; what they take with them is listed first), the Xray config as text with a dry run, one inbound added / changed / deleted from a form (REALITY keys), backups and rollback |
| **Admins** `/api/admins`, `/api/admin`, `/api/admin/by-id/{id}` (+ `reset`, `usage`, `users/{activate,disable}`, `DELETE users`), `/api/admins/simple`, `/api/admins/bulk/{delete,enable,disable,reset}`, `/api/admins/bulk/users/{activate,disable}`, `DELETE /api/admins/bulk/users` | Admins tab (`pg_admin`) | the single-admin actions; **tick several admins and do one thing for all** (on, off, reset, their users off / on / deleted - the panel's bulk calls, all or none); the bulk delete is used when an admin is deleted |
| **Admin roles** `/api/admin-roles`, `/api/admin-role/{id}` | Admins tab → Roles (`pg_roles`) | permissions, limits, access, device rules; owner role read only, built-in roles never deleted |
| **API keys** `/api/api_keys`, `/api/api_key/{id}` (+ `revoke`), `/api/api_keys/bulk/delete` | Admins tab → API keys (`pg_admin`) | the secret is shown once and never kept; the key this panel itself uses is protected |
| **Settings** `/api/settings` | Settings tab (`pg_admin`) | 7 sections as forms, secrets only "set / not set", a dry run, history, restore |
| **System** `/api/system`, `/api/inbounds`, `/api/inbounds/details` | the forms that choose an inbound (the System tab was removed on 2026-10-15; Usage > Users reads the same counters) | version, resources, counters |
| **Usage** `/api/node/usage`, `/api/users/usage`, `/api/admin/…/usage`, `/api/user/by-id/{id}/usage`, `/api/users/counts/{metric}`, `/api/users/sub_update/chart` | Usage tab (`pg_usage`) | by node and Tehran day, Jalali dates, top users, **users over time** (online / expired / limited per hour or day, all or one admin), **subscription apps** (the share of each client) |
| **Subscription** `/sub/{token}` | the merged link (`/sub/<token>` of this panel, accounts) | read, never changed |

## What is left out on purpose

* `/api/setup/owner` (create / delete / reset the OWNER account, upgrade it): the owner of a panel is never touched from here.
* `/api/users/reset` (reset the usage of EVERY user at once): no button; the bulk reset by name is there.
* `/api/admin/token`, `/api/admin/miniapp/token`: only the sign-in of this panel itself uses the token route (`panels.login`).
* `/api/system/resources`, `/api/system/users`: the numbers are in `/api/system` already.
* The deprecated duplicates by name: `/api/user/by-username/…`, `/api/user/{username}/…`, `/api/admin/by-username/…`, `/api/admin/{username}/…`, `PUT /api/user/from_template/{by-id,by-username}/…` (one user from a template is a bulk of one).
* `/api/node/{node_id}/online_stats/{user_id}` (and `/ip`): per-node variants; the Online-now tab uses the one answer for all nodes, `/api/node/online_stats/{user_id}/ip`.
* `/api/settings/general`: the General section is part of `/api/settings`, which the Settings tab reads and writes whole.
* `/sub/{token}/…` and `/`, `/health`: the links the clients of a panel fetch, and its own pages - not the management API (the merged link of this panel reads `/sub/<token>`).

## Found on the way (real panel, 5.4.1) and fixed

* **The old Hosts editor reset the xray mux.** A host is READ with the names `xudpConcurrency` / `xudpProxyUDP443` and WRITTEN with `xudp_concurrency` / `xudp_proxy_udp_443`; a host put back as it was read silently lost both. `panels.host_input` turns a read host into a writable one everywhere (edit, clone, TLS copy, restore).
* **A host clone and the TLS copies made nothing.** `POST /api/host` (without the slash) only gets `307` from the panel, and an answer without a body was taken for success. Every call now goes to `/api/host/`, and a redirect is an error (`panels._http`).
* **The TLS copies skipped every PasarGuard host** ("the inbound is not in the core config": a PasarGuard panel has no `/api/core/config`): the inbounds are read from `/api/cores` now. `alpn` is a list for PasarGuard (the panel answered 422 for a text).
* **A user's next plan was deleted by every edit** that did not carry `next_plan`, and an empty end date kept the old date. Edits from here always send the plan the user has, and an empty end date is `expire: 0`.
* **The panel drops an unknown key of an advanced setting without a word** (`{"transport_settings": {"fly": 1}}` came back as an empty transport): the keys are checked here and a wrong one is refused.
* **A reset of the usage deletes the user's next plan** (also the reset of many users, and applying a template that resets it): the windows say so before the confirm.
* **The Hosts list did not read the panel again** after a save, a clone, a delete or an enable / disable: it showed the old state until the refresh button was pressed.

## Open

* Nothing of the REST API is without a button any more except what is left out on purpose above (roadmap #99 is closed, 1.46.0). The check is repeatable: `python scripts/pg_coverage.py openapi.json` runs the test suite with a recorder on the calls to a panel (a pytest plugin in the same file) and matches the paths with the OpenAPI document of a live panel; what it lists as never sent is exactly the list above (the 1.46.0 run: 203 operations, every one either sent by a test or left out on purpose).
* The install of a node from the Add server form was read from the official script and tried once on a machine that already ran a Marzban node (its port was taken: the installer stopped and nothing was changed); a real run needs a spare machine.
