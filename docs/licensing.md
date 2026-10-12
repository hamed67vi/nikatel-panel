# Licensing

How paid features are sold without pretending that code inside an open-source panel can protect itself.

## The honest model

The panel's source reaches every customer. Any check that lives only inside it - a signature, an expiry, an
install binding - can be removed by editing a file. So the design has two layers and is honest about which
one carries weight:

1. **In the panel** (`nikatel/services/license.py`): a signed token switches features on. This keeps honest
   customers honest, gives a clear message ("needs a license") instead of a broken page, and makes the
   free tier a configuration, not a fork of the code.
2. **On the vendor's side** (the license server, not in this repository): the things that are *worth*
   paying for run there or depend on it - the AI (the vendor's Anthropic key behind a proxy that meters
   credits per license), updates (the installer and the update button fetch releases with the license),
   support. A cracked check in the panel gets a customer a BackPack tunnel; it never gets them the AI or
   the next release.

## Tiers

| tier | how it is set | what is on |
|---|---|---|
| **free** | nothing (no token) | Backhaul · TCP Mux over GRE (one engine, the one that works on most routes), every read-only part of the panel |
| **licensed** | a token under Settings → About → License | the features named in the token: `tunnels` (every engine and transport), `ai`, `failover` (ArvanCloud DNS), `shopbot` |
| **owner** | `NIKATEL_LICENSE=owner` in `/etc/nikatel/nikatel.env` | everything, no token - the vendor's own installs |

Where the gate sits: `builder.start_build` (a transport other than tcpmux), `build_other.start_build`
(BackPack, Paqet, Chisel), `tunnel_test.compare_start` (the kinds that may be tried), `autopilot._recommend_now`
(the smart repair only tries licensed kinds), `ai.ask`, `failover.has_key`, the whole `/api/shopbot` router (402).
Every refusal is one sentence that names Settings → About → License.

## The token

`NK1.<base64url(payload)>.<base64url(Ed25519 signature)>`

```json
{"lic": "L-2026-0042", "to": "Customer name", "iat": 1760000000, "exp": 1791536000,
 "feat": ["tunnels", "ai"], "inst": "a1b2c3d4e5f60718"}
```

* signed with the vendor's **Ed25519** private key; the public key is `VENDOR_PUBKEY_HEX` in `license.py`
  (empty in the public repository until the vendor fills it: then no token is valid, only the free tier and
  the owner mode exist);
* `exp` = 0 means no end; `inst` binds the token to one install (the id is derived from the install's own
  `secret.key` and shown under Settings → About → License); an empty `inst` is a floating token;
* checked with the `cryptography` package; the parsed result is cached for a minute.

Making a token (vendor side, never in the panel):

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import base64, json, time
priv = Ed25519PrivateKey.generate()                     # keep this; print(priv.public_key().public_bytes_raw().hex()) goes into VENDOR_PUBKEY_HEX
body = json.dumps({"lic": "L-1", "to": "Customer", "iat": int(time.time()), "exp": 0, "feat": ["tunnels"], "inst": ""}).encode()
b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
print("NK1." + b64(body) + "." + b64(priv.sign(body)))
```

## The license server (to build when sales start)

A small service on the vendor's own server:

* `POST /v1/token` - issues a token for a paid license (the private key lives only here);
* `POST /v1/ai/messages` - the AI proxy: the panel sends its token + the request, the server checks the
  signature, the expiry and the **credit balance of the license**, forwards to Anthropic with the vendor's key,
  deducts the tokens used, answers. Monthly cap per plan; a customer can buy more credits. The panel's
  `ai.ask` gets a second backend ("vendor") beside the current "own key";
* `GET /v1/releases/latest` - the update endpoint the panel's Update button asks with its token (the public
  repository keeps the source; releases of the built package come from here);
* `POST /v1/revoke` - a stolen or refunded license: the panel asks the server once a day and keeps the last
  answer, so a revoked token stops working within a day even offline (planned: a `rev` list in `license.py`).

Decisions already taken (2026-10-11): the free tunnel method is Backhaul TCP Mux; the AI is billed monthly
with a credit cap; the AI key stays on the vendor's server.

## What this does NOT protect against

A customer who edits `license.py` gets every *local* feature. That is accepted: the local features are the
open-source part. What they do not get is the AI (metered on the vendor's server), updates and support.
Do not spend effort on obfuscation; spend it on the server side.
