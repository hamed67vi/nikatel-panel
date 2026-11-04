# The phone assistant (Commander)

The owner types (or dictates) ONE order on his iPhone - "tunnel X is down, find the problem and fix it" - and the panel's own AI works it out with the Doctor's own tools, shows every step, and reports. It is a page of the panel, `/m`, made for a phone: add it to the home screen and it opens like an app.

Code: `nikatel/services/commander.py` (the loop, the tools, the limits), `nikatel/api/commander.py` (the routes), `nikatel/auth.py` (the phone key), `nikatel/web/m.html|m.css|m.js` (the page), `tests/test_commander.py`.

## Using it

1. Open `https://<panel>/m` in Safari (behind the secret entry path when one is set: `https://<panel>/<entry_path>/m`). Sign in with the panel's password and, when it is on, the second-step code. The phone gets a *key* (below).
2. Share → *Add to Home Screen*. The icon opens the page without the browser's bars.
3. First time only: switch the assistant **on** (the page offers the button; it is off until then). The AI needs a key saved in Settings → Api & Key (the same AI every other part of the panel uses).
4. Write an order, in Persian or English, and send. The page shows what the AI says and every tool it uses, in plain Persian (the Doctor's own story lines stay English, in grey). A **Confirm** card appears when a change needs the owner's tap. **Stop** ends the order.

## What it does alone, what waits for a tap, what it never does

| | what | how it is decided |
|---|---|---|
| **alone** | look: `overview`, `list_tunnels`, `tunnel`, `diagnose`, `history`, `servers`, `where_to_move`, `kinds` (all read-only) | `tier_of` = "read" |
| **alone** | the Doctor's repair of a tunnel that is **down or degraded right now**: `restart` (`actions.fix`), `repair` (`autopilot.smart_fix_start`, up to the level it names: restart, rebuild, switch) | "auto" only when the tunnel's state, read from the database at that moment, is down or degraded, no mass outage is on, "careful" is off, and the AI has listed that tunnel in this order |
| **tap** | `move` (another Iran server; the DNS records that serve only the tunnel follow), `replace` (another tunnel type), `repair_all` (every tunnel that is down), and `restart` / `repair` on a tunnel that is up, during a mass outage, or in careful mode | "tap": a Confirm card with the tunnel, the effect in plain words and the exact arguments |
| **never** | delete, ports, DNS by hand, users, money, passwords, keys, settings, a shell on a server, updating the panel | not in the tool list: an unknown name is refused and the AI is told |

The AI decides nothing about the tier. It answers with one JSON object per turn naming a tool; `commander._decide` reads the tunnel's state, applies `tier_of`, and either runs the tool, makes a card, or refuses. What a log line or a server name says to the AI changes nothing: the worst it can do is ask for a tool of the list, and the code decides.

The same guards the Doctor has apply, because the same functions are called: a server that is off, the mass-outage hold, the read-only switch (`NIKATEL_READ_ONLY`), the hourly repair limit, "already being repaired". The assistant adds its own: 12 AI turns and 3 changes per order, 6 changes per hour, 3 per tunnel per hour, 60 AI calls per hour, 25 minutes per order. A refused call is an answer the AI reads ("limit", "server off", "read-only"); it does not retry past it.

A Confirm card carries the tool and arguments; tapping Confirm runs **those** (nothing is read from the request), once, within 15 minutes; Cancel, Stop, a second tap and the end of 15 minutes all leave the change undone. After a tap the AI reads the result and reports; the page also shows the state of every tunnel the order touched, read from the database - not from what the AI claims.

## Who can open it

* The page itself holds no data. Every route under `/api/commander/` needs either the panel's own session or a **phone key**.
* A phone key is made by `POST /api/auth/phone/login` (+ `/login/2fa`): the same password, the same throttle, the same second step as the panel's sign-in. It is a signed cookie (`nikatel_phone`, HttpOnly, SameSite=Strict, `Path=/api/commander` - the browser sends it to nothing else), 30 days, forgotten after 14 days without use; its id is in `auth.json` (`phones`), so the page can list the phones and take one away (`DELETE /api/commander/phones/{id}`) or all (`POST /api/commander/phones/revoke-all`). A new password or a change of the two-step setup ends every key.
* `current_user` accepts the key for `/api/commander/` and for no other address: the rest of the panel answers 401 to a phone key. An API token of scope `read` or `act` is refused here (`/api/commander` is an admin path); the public demo refuses the whole prefix.
* Nothing is open without a sign-in. With a secret entry path the door applies as everywhere; `/<entry_path>/m` opens it and sets the door cookie (a home-screen app keeps its own cookies), and the web-app manifest's `start_url` is that address.

## Audit, masking, Stop

* Every order is a row of `commander_runs` (90 days): who (`admin` or `phone:<label>`), from which address, the words, every step (what the AI said, each tool with its arguments and its masked answer, each Confirm card and who tapped), how it ended, how many AI calls. The same events appear in the panel's Events (domain `commander`). The page shows it under *Settings and history*.
* Keys, tokens and passwords in what the owner types or a tool returns are replaced by `<hidden>` before they are stored or sent to the AI; IP addresses in tool answers become `<ip>` (the AI needs names, not addresses). The tools return only chosen fields: no SSH user, no key, no secret column ever reaches the AI.
* **Stop** ends the order at its next look (a Confirm card still waiting is cancelled). A repair the Doctor has already started is **not** cut: it goes on by itself to its own safe end - stopping it half way could leave a tunnel removed.
* An order that was working when the panel restarted is closed ("restarted"); one that waits for a tap stays (the card ends 15 minutes after it was made).

## API

`GET /api/commander/state` · `PUT /api/commander/settings {enabled, careful}` · `GET|POST /api/commander/runs` · `GET /api/commander/runs/{id}?after=N` · `POST /api/commander/runs/{id}/stop` · `POST /api/commander/runs/{id}/confirm/{card}` · `POST /api/commander/runs/{id}/cancel/{card}` · `POST /api/commander/logout` · `DELETE /api/commander/phones/{id}` · `POST /api/commander/phones/revoke-all`. Errors: `{"ok": false, "error", "code"}` with the codes `off`, `ai_not_ready`, `busy` (one order at a time), `answered`, `no_run`, `no_proposal`, `empty`.

## Not proved

Everything above is tested with a scripted AI and stand-ins for the Doctor's functions (`tests/test_commander.py`) and walked in a browser at phone size on a private panel with demo data. Not proved: a real AI model answering in the JSON shape (the parser drops what it cannot read and says so), a real repair of a real fault, the page on a real iPhone (the keyboard, dictation, add-to-home-screen, cookies of a home-screen app), the Telegram Mini App door (not built).
