"""Nikatel Bale relay - runs on an IRAN server, with the Python standard library only.

When Iran's international internet is cut, Telegram and every server abroad are out of reach, but the national
network stays up, and with it Bale (tapi.bale.ai) and this server. This program is a small Bale bot that
  * lets a customer start the bot and link a subscription (the customer pastes the subscription link; it is checked
    against the panel, and only against the hosts in `allowed_hosts`),
  * answers /status from the subscription's last known state (still works in a cut: the last answer is kept),
  * sends a notice to everybody who started the bot - by hand (an admin's /send, or the panel) or by itself when
    the server loses the whole outside world for `cut_after` seconds, and again when it comes back,
  * keeps working with no help from the panel: the panel only installs it and writes config.json / outbox files.

Files in $NIKATEL_RELAY_HOME (default /opt/nikatel-relay):
  config.json   written by the panel (token, admins, allowed_hosts, watch, texts)       0600
  data/relay.db users, links, state                                                    0600
  data/dns.json the last good addresses of the Bale API host (the .ai name may not resolve in a cut)
  data/status.json  what the panel reads
  outbox/*.msg  a broadcast to send: {"id", "text", "kind"}; the sender removes it when done
"""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse

VERSION = "1.0.0"
HOME = os.environ.get("NIKATEL_RELAY_HOME", "/opt/nikatel-relay")
DATA = os.path.join(HOME, "data")
OUTBOX = os.path.join(HOME, "outbox")

DEFAULT_TEXTS = {
    "welcome": "سلام 👋\nاین ربات فقط برای «اطلاع‌رسانی در زمان قطعی اینترنت» است. وقتی اینترنت بین‌الملل قطع باشد و تلگرام کار نکند، خبر سرویس را از همین‌جا می‌گیرید.\n\n"
               "برای دیدن وضعیت اشتراک‌تان، لینک اشتراک (Subscription) را همین‌جا بفرستید.\n\n/status وضعیت اشتراک\n/unlink جدا کردن اشتراک‌ها\n/stop قطع دریافت پیام‌ها",
    "linked": "✅ اشتراک «{username}» وصل شد. هر وقت /status بزنید، آخرین وضعیتش را می‌بینید.",
    "bad_link": "این لینک را نشناختم. لینک اشتراک باید از همان سایتی باشد که سرویس را از آن گرفته‌اید.",
    "cannot_check": "الان نمی‌توانم لینک را بررسی کنم. کمی بعد دوباره بفرستید.",
    "no_links": "هنوز اشتراکی وصل نکرده‌اید. لینک اشتراک‌تان را همین‌جا بفرستید.",
    "stopped": "پیام‌ها قطع شد. هر وقت خواستید /start بزنید.",
    "unlinked": "اشتراک‌ها جدا شد.",
    "cut": "⚠️ اینترنت بین‌الملل قطع شده است.\nاین قطعی از سمت شبکه است و به سرور ما ربطی ندارد؛ سرویس‌ها به‌محض وصل شدن اینترنت برمی‌گردند. لازم نیست تنظیمات را عوض کنید یا دوباره خرید کنید.\nخبر تازه را از همین ربات می‌گیرید.",
    "restored": "✅ اینترنت بین‌الملل دوباره وصل شد. اگر سرویس شما هنوز وصل نشد، برنامه را ببندید و دوباره باز کنید و اشتراک را به‌روزرسانی کنید.",
    "support_ack": "✅ پیام شما به پشتیبانی رسید. به‌محض اینکه ممکن شد، جواب را همین‌جا می‌فرستیم.",
    "support_fail": "الان نتوانستم پیام را به پشتیبانی برسانم. کمی بعد دوباره بفرستید.",
}
DEFAULTS = {
    "token": "", "admins": [], "allowed_hosts": [], "api_base": "https://tapi.bale.ai",
    "watch": [["1.1.1.1", 443], ["8.8.8.8", 443], ["9.9.9.9", 443]],
    "auto_notice": True, "support": True, "cut_after": 180, "restore_after": 120, "cooldown": 21600,
    "pace": 0.08, "texts": {},
}
ADMIN_HELP = ("دستورهای ادمین (همه از همین گفتگوی بله):\n"
              "• ریپلای روی پیام یک مشتری = جواب به او (متن، عکس، فایل)\n"
              "• /r شناسه متن = جواب بدون ریپلای (شناسه بعد از # در پیام مشتری است)\n"
              "• /send متن = پیام به همه‌ی مشتری‌ها\n"
              "• /cut و /back = فرستادن پیام «قطع شد» / «وصل شد» همین الان\n"
              "• /info = وضعیت اینترنتِ سرور و بله\n"
              "• /stats = تعداد مشتری‌ها\n"
              "• /mute شناسه و /unmute شناسه = بستن / باز کردن پیام‌های یک مشتری\n"
              "• /test = پیش‌نمایش پیام قطع، فقط برای شما")
MEDIA_KEYS = ("photo", "document", "voice", "video", "audio", "animation", "video_note")
STATUS_FA = {"active": "فعال", "expired": "منقضی‌شده", "limited": "حجم تمام شده", "disabled": "غیرفعال", "on_hold": "در انتظار شروع"}
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
LINK_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
KEYBOARD = {"keyboard": [[{"text": "وضعیت اشتراک"}], [{"text": "راهنما"}, {"text": "قطع پیام‌ها"}]], "resize_keyboard": True}
BUTTONS = {"وضعیت اشتراک": "/status", "راهنما": "/help", "قطع پیام‌ها": "/stop"}
STOP = threading.Event()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def now() -> int:
    return int(time.time())


# ------------------------------------------------------------------ config
class Config:
    """config.json, read again whenever the file changes."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(HOME, "config.json")
        self._mt = -1.0
        self._v = {**DEFAULTS, "texts": dict(DEFAULT_TEXTS)}
        self._warned = False
        self._lock = threading.Lock()

    def get(self) -> dict:
        with self._lock:
            try:
                mt = os.stat(self.path).st_mtime

                if mt != self._mt:
                    with open(self.path, encoding="utf-8") as f:
                        raw = json.load(f)

                    self._v, self._mt, self._warned = clean_config(raw), mt, False
            except (OSError, ValueError) as e:
                if not self._warned:
                    log("config not readable:", e)

                self._warned = True

            return self._v


def clean_config(raw) -> dict:
    """The file's values, each checked to be the shape the rest of the program uses; anything else falls back to the default."""
    raw = raw if isinstance(raw, dict) else {}
    v = {**DEFAULTS, "texts": dict(DEFAULT_TEXTS)}
    v["token"] = str(raw.get("token") or "")
    v["api_base"] = str(raw.get("api_base") or DEFAULTS["api_base"])
    v["admins"] = [int(x) for x in (raw.get("admins") or []) if str(x).lstrip("-").isdigit()]
    v["allowed_hosts"] = [str(x).strip().lower() for x in (raw.get("allowed_hosts") or []) if str(x).strip()]
    w = [[str(p[0]), int(p[1])] for p in (raw.get("watch") if isinstance(raw.get("watch"), list) else DEFAULTS["watch"]) if isinstance(p, (list, tuple)) and len(p) == 2 and str(p[1]).isdigit()]
    v["watch"] = w
    v["auto_notice"] = bool(raw.get("auto_notice", True))
    v["support"] = bool(raw.get("support", True))

    for k, lo, hi in (("cut_after", 30, 86400), ("restore_after", 10, 86400), ("cooldown", 0, 604800)):
        try:
            v[k] = min(hi, max(lo, int(raw.get(k, DEFAULTS[k]))))
        except (TypeError, ValueError):
            pass

    try:
        v["pace"] = min(5.0, max(0.02, float(raw.get("pace", DEFAULTS["pace"]))))
    except (TypeError, ValueError):
        pass

    v["texts"].update({k: t.strip() for k, t in (raw.get("texts") or {}).items() if k in DEFAULT_TEXTS and isinstance(t, str) and t.strip()})
    return v


# ------------------------------------------------------------------ the Bale API (addresses kept for a cut)
class ApiError(Exception):
    pass


def _resolve(host: str, wait: float = 4.0) -> list[str]:
    out: list[str] = []

    def work():
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                if sa[0] not in out and fam in (socket.AF_INET, socket.AF_INET6):
                    out.append(sa[0])
        except OSError:
            pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(wait)
    return list(out)


def _is_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        return True
    except OSError:
        return False


class _Conn(http.client.HTTPConnection):
    """HTTP(S) to a chosen address while the name (Host header, SNI) stays the real one."""

    def __init__(self, scheme: str, host: str, port: int, ip: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._scheme, self._ip, self._tmo = scheme, ip, timeout

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self._tmo)

        if self._scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)

        self.sock = sock


class DnsKeep:
    def __init__(self, path: str):
        self.path, self._lock = path, threading.Lock()
        self._mem: dict[str, tuple[float, list[str]]] = {}

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)

            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def addresses(self, host: str) -> list[str]:
        if _is_ip(host):
            return [host]

        t = time.time()
        at, fresh = self._mem.get(host, (0.0, []))

        if t - at > 60:
            fresh = _resolve(host)
            self._mem[host] = (t, fresh)

            if fresh:
                self.save(host, fresh)

        kept = [x for x in (self.load().get(host) or []) if isinstance(x, str)]
        return fresh + [x for x in kept if x not in fresh]

    def save(self, host: str, ips: list[str]) -> None:
        with self._lock:
            d = self.load()

            if d.get(host) == ips:
                return

            d[host] = ips[:6]
            tmp = self.path + ".tmp"

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)

            os.replace(tmp, self.path)


class Api:
    def __init__(self, cfg: Config, dns: DnsKeep):
        self.cfg, self.dns = cfg, dns
        self.state = {"ok": False, "last_ok": 0, "err": ""}

    def call(self, method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
        c = self.cfg.get()
        u = urllib.parse.urlsplit(c["api_base"])
        host, scheme = u.hostname or "", u.scheme or "https"
        port = u.port or (443 if scheme == "https" else 80)
        body = json.dumps(params or {}, ensure_ascii=False).encode()
        last: Exception | None = None

        for ip in self.dns.addresses(host):
            try:
                conn = _Conn(scheme, host, port, ip, timeout)
                conn.request("POST", f"/bot{c['token']}/{method}", body, {"Content-Type": "application/json"})
                raw = conn.getresponse().read(2_000_000)
                conn.close()
                d = json.loads(raw)

                if not isinstance(d, dict):
                    raise TypeError("not an object")

                self.state.update(ok=True, last_ok=now(), err="")
                return d
            except (OSError, ValueError, TypeError, http.client.HTTPException) as e:
                last = e

        self.state.update(ok=False, err=str(last or "no address")[:120].replace(c["token"] or "\0", "***"))
        raise ApiError(self.state["err"])

    def _try(self, method: str, p: dict) -> tuple[str, dict]:
        """('ok' | 'blocked' (the user left, the chat is gone) | 'fail' (Bale refused this one) | 'down' (Bale could not be reached at all), the answer's result)."""
        for _ in range(3):
            try:
                d = self.call(method, p, 20)
            except ApiError:
                return "down", {}

            if d.get("ok"):
                return "ok", d["result"] if isinstance(d.get("result"), dict) else {}

            code, why = int(d.get("error_code") or 0), str(d.get("description") or "").lower()

            if code == 429:
                time.sleep(min(30, int(((d.get("parameters") or {}).get("retry_after")) or 2)))
                continue

            if code == 403 or (code == 400 and ("chat not found" in why or "user not found" in why or "deactivated" in why)):
                return "blocked", {}

            return "fail", {}

        return "fail", {}

    @staticmethod
    def _mid(res: dict) -> int:
        try:
            return int(res.get("message_id") or 0)
        except (TypeError, ValueError):
            return 0

    def send_id(self, chat_id: int, text: str, markup: dict | None = None) -> tuple[str, int]:
        """The same as send, with the id of the message that was made (0 when Bale did not say)."""
        p: dict = {"chat_id": chat_id, "text": text[:4096]}

        if markup:
            p["reply_markup"] = markup

        st, res = self._try("sendMessage", p)
        return st, self._mid(res)

    def send(self, chat_id: int, text: str, markup: dict | None = None) -> str:
        return self.send_id(chat_id, text, markup)[0]

    def copy(self, chat_id: int, from_chat_id: int, message_id: int) -> tuple[str, int]:
        """A message (a photo, a file, a voice) copied from one chat to another without a mark of where it came from."""
        st, res = self._try("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})
        return st, self._mid(res)


# ------------------------------------------------------------------ the store
class Store:
    def __init__(self, path: str):
        self.path = path

    def conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def init(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(chat_id INTEGER PRIMARY KEY, first_name TEXT, started_at INTEGER, muted INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, last_seen INTEGER DEFAULT 0, nosupport INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS support(admin INTEGER, mid INTEGER, customer INTEGER, at INTEGER, PRIMARY KEY(admin, mid));
            CREATE TABLE IF NOT EXISTS links(chat_id INTEGER, username TEXT, host TEXT, linked_at INTEGER, info TEXT, info_at INTEGER DEFAULT 0, info_url TEXT DEFAULT '', PRIMARY KEY(chat_id, username));
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
            """)

        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def meta(self, k: str, default=None):
        with self.conn() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()

        if r is None:
            return default

        try:
            return json.loads(r["v"])
        except ValueError:
            return default

    def set_meta(self, k: str, v) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def touch(self, chat_id: int, first_name: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO users(chat_id, first_name, started_at, last_seen) VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET first_name=excluded.first_name, last_seen=excluded.last_seen, blocked=0", (chat_id, first_name[:60], now(), now()))

    def note_support(self, admin: int, mid: int, customer: int) -> None:
        """Which customer a message in an admin's chat is about, so that a reply to it (even to a photo) finds him. A fortnight is kept."""
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO support(admin, mid, customer, at) VALUES(?,?,?,?)", (admin, mid, customer, now()))
            c.execute("DELETE FROM support WHERE at<?", (now() - 14 * 86400,))

    def support_customer(self, admin: int, mid: int) -> int:
        with self.conn() as c:
            r = c.execute("SELECT customer FROM support WHERE admin=? AND mid=?", (admin, mid)).fetchone()

        return int(r[0]) if r else 0

    def is_nosupport(self, chat_id: int) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT nosupport FROM users WHERE chat_id=?", (chat_id,)).fetchone()

        return bool(r and r[0])

    def link_names(self, chat_id: int) -> list[str]:
        return [str(r["username"]) for r in self.links(chat_id)]

    def set_flag(self, chat_id: int, **kw) -> None:
        with self.conn() as c:
            for k, v in kw.items():
                if k in ("muted", "blocked", "nosupport"):
                    c.execute(f"UPDATE users SET {k}=? WHERE chat_id=?", (int(v), chat_id))

    def add_link(self, chat_id: int, username: str, host: str, info: dict, url: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO links(chat_id, username, host, linked_at, info, info_at, info_url) VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id, username) DO UPDATE SET info=excluded.info, info_at=excluded.info_at, host=excluded.host, info_url=excluded.info_url",
                      (chat_id, username[:80], host[:120], now(), json.dumps(info)[:4000], now(), url[:400]))

    def update_info(self, chat_id: int, username: str, info: dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE links SET info=?, info_at=? WHERE chat_id=? AND username=?", (json.dumps(info)[:4000], now(), chat_id, username))

    def links(self, chat_id: int) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM links WHERE chat_id=? ORDER BY linked_at", (chat_id,)).fetchall()

    def drop_links(self, chat_id: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM links WHERE chat_id=?", (chat_id,))

    def recipients(self, after: int = 0) -> list[int]:
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT chat_id FROM users WHERE muted=0 AND blocked=0 AND chat_id>? ORDER BY chat_id", (after,))]

    def counts(self) -> dict:
        with self.conn() as c:
            r = c.execute("SELECT COUNT(*) t, COALESCE(SUM(muted),0) m, COALESCE(SUM(blocked),0) b FROM users").fetchone()
            ln = c.execute("SELECT COUNT(DISTINCT chat_id) FROM links").fetchone()[0]

        return {"total": r["t"], "muted": r["m"], "blocked": r["b"], "linked": ln}


# ------------------------------------------------------------------ pure rules
def digits(s) -> str:
    return str(s).translate(_FA)


def extract_url(text: str) -> str:
    m = LINK_RE.search(text or "")
    return m.group(0).rstrip(".,;)،") if m else ""


def link_target(url: str, allowed) -> tuple[str, str] | None:
    """(host, info URL) for a subscription link on an allowed host, else None. Only the hosts the owner named are ever fetched."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return None

    host = (u.hostname or "").lower()

    if u.scheme not in ("http", "https") or not host or "@" in u.netloc or len(url) > 400 or u.query or u.fragment:
        return None

    ok = False

    for a in allowed or []:
        a = str(a).strip().lower()

        if not a:
            continue

        if ":" in a and not a.startswith("["):
            ah, _, ap = a.rpartition(":")
            ok = ok or (ah == host and ap.isdigit() and int(ap) == (port or (443 if u.scheme == "https" else 80)))
        else:
            ok = ok or a == host

    path = u.path.rstrip("/")

    if not ok or not re.fullmatch(r"/[A-Za-z0-9_\-./]{8,300}", path) or ".." in path:
        return None

    head, _, last = path.rpartition("/")

    if last in ("info", "v2ray", "v2ray-json", "sing-box", "clash", "clash-meta", "outline", "links", "usage") and head.count("/") >= 1:
        path = head

    netloc = host + (f":{port}" if port else "")
    return host, f"{u.scheme}://{netloc}{path}/info"


def fetch_info(info_url: str) -> dict | None:
    """GET <subscription>/info (PasarGuard and Marzban answer it with JSON) - no redirect, at most 64 KB. None when the panel does not know it; ApiError when it cannot be reached."""
    u = urllib.parse.urlsplit(info_url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    last: Exception | None = None

    for ip in _resolve(host) if not _is_ip(host) else [host]:
        try:
            conn = _Conn(u.scheme, host, port, ip, 6)
            conn.request("GET", u.path, headers={"Accept": "application/json", "User-Agent": "nikatel-relay"})
            r = conn.getresponse()
            raw = r.read(65536)
            conn.close()

            if r.status == 200:
                d = json.loads(raw)
                return d if isinstance(d, dict) and d.get("username") else None

            if r.status in (400, 401, 403, 404):
                return None

            last = ValueError(f"HTTP {r.status}")
        except (OSError, ValueError, http.client.HTTPException) as e:
            last = e

    raise ApiError(str(last or "no address")[:80])


def keep_info(info: dict) -> dict:
    return {k: info.get(k) for k in ("status", "expire", "data_limit", "used_traffic")}


def fmt_status(row, t: int) -> str:
    try:
        i = json.loads(row["info"] or "{}")
    except ValueError:
        i = {}

    st = STATUS_FA.get(str(i.get("status")), str(i.get("status") or "؟"))
    lim, used = int(i.get("data_limit") or 0), int(i.get("used_traffic") or 0)
    vol = "نامحدود" if not lim else f"{max(0, lim - used) / 1073741824:.1f} گیگ مانده از {lim / 1073741824:.0f}"
    exp = int(i.get("expire") or 0)
    days = "بدون تاریخ انقضا" if not exp else (f"{(exp - t) // 86400} روز مانده" if exp > t else "تاریخ انقضا گذشته")
    age = max(0, t - int(row["info_at"] or 0))
    ago = "هم‌اکنون" if age < 120 else (f"{age // 60} دقیقه پیش" if age < 7200 else f"{age // 3600} ساعت پیش" if age < 172800 else f"{age // 86400} روز پیش")
    return f"👤 {row['username']}\n" + digits(f"وضعیت: {st}\nحجم: {vol}\nزمان: {days}\n(آخرین بررسی: {ago})")


def watch_step(st: dict, up: bool, t: int, cfg: dict) -> list[str]:
    """One look at the outside world. `st` is kept between looks; returns 'cut' and/or 'restored' when a notice is due."""
    acts: list[str] = []

    if t - int(st.get("seen_at") or 0) > 300:          # the relay itself was off: the old timers mean nothing
        st["down_since"] = st["up_since"] = 0

    st["seen_at"] = t

    if up:
        st["up_since"] = st.get("up_since") or t
        st["down_since"] = 0

        if st.get("notified") and t - st["up_since"] >= int(cfg["restore_after"]):
            acts.append("restored")
            st["notified"] = False
    else:
        st["down_since"] = st.get("down_since") or t
        st["up_since"] = 0

        if not st.get("notified") and t - st["down_since"] >= int(cfg["cut_after"]) and t - int(st.get("last_cut_at") or 0) >= int(cfg["cooldown"]):
            acts.append("cut")
            st["notified"] = True
            st["last_cut_at"] = t

    return acts


# ------------------------------------------------------------------ the bot
class Relay:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.store = Store(os.path.join(DATA, "relay.db"))
        self.dns = DnsKeep(os.path.join(DATA, "dns.json"))
        self.api = Api(self.cfg, self.dns)
        self.bot: dict = {}
        self.tries: dict[int, list[int]] = {}
        self.global_tries: list[int] = []
        self.watch_state: dict = {}
        self.targets: dict[str, bool] = {}
        self.panel_skip_until = 0
        self.me_at = 0
        self.support_tries: dict[int, list[int]] = {}
        self.ack_at: dict[int, int] = {}

    # -- status file
    def write_status(self) -> None:
        bc = self.store.meta("broadcasts", [])
        d = {"v": 1, "version": VERSION, "at": now(), "pid": os.getpid(), "bot": self.bot, "bale": dict(self.api.state, ips=self.dns.load().get(urllib.parse.urlsplit(self.cfg.get()["api_base"]).hostname or "", [])),
             "users": self.store.counts(), "watch": {"up": not self.watch_state.get("down_since"), "down_since": self.watch_state.get("down_since", 0), "notified": bool(self.watch_state.get("notified")), "last_cut_at": self.watch_state.get("last_cut_at", 0), "targets": self.targets},
             "broadcasts": bc[-20:], "queue": len([f for f in os.listdir(OUTBOX) if f.endswith(".msg")]) if os.path.isdir(OUTBOX) else 0}
        tmp = os.path.join(DATA, "status.json.tmp")

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

        os.replace(tmp, os.path.join(DATA, "status.json"))

    # -- messages in
    def reply(self, chat_id: int, key: str, markup: dict | None = KEYBOARD, **kw) -> None:
        text = self.cfg.get()["texts"][key]

        for k, v in kw.items():      # (a plain replace, not str.format: a text the owner edited with a stray brace must still be sent)
            text = text.replace("{" + k + "}", v)

        self.api.send(chat_id, text, markup)

    def throttled(self, chat_id: int) -> bool:
        t = now()
        mine = [x for x in self.tries.get(chat_id, []) if t - x < 60]
        self.global_tries = [x for x in self.global_tries if t - x < 60]

        if len(mine) >= 4 or len(self.global_tries) >= 40:
            self.tries[chat_id] = mine
            return True

        mine.append(t)
        self.tries[chat_id] = mine
        self.global_tries.append(t)
        return False

    def on_link(self, chat_id: int, text: str) -> None:
        c = self.cfg.get()
        tgt = link_target(extract_url(text), c["allowed_hosts"])

        if tgt is None:
            return self.reply(chat_id, "bad_link")

        if self.throttled(chat_id):
            return self.reply(chat_id, "cannot_check")

        try:
            info = fetch_info(tgt[1])
        except ApiError:
            return self.reply(chat_id, "cannot_check")

        if info is None:
            return self.reply(chat_id, "bad_link")

        self.store.add_link(chat_id, str(info["username"]), tgt[0], keep_info(info), tgt[1])
        self.reply(chat_id, "linked", username=str(info["username"]))

    def on_status(self, chat_id: int) -> None:
        """The state of each linked subscription: asked from the panel again when it was last asked more than a minute ago and the panel answers; else the last answer is shown, with its age."""
        rows = self.store.links(chat_id)

        if not rows:
            return self.reply(chat_id, "no_links")

        out, allowed, t = [], self.cfg.get()["allowed_hosts"], now()

        for r in rows:
            row = dict(r)
            tgt = link_target(row.get("info_url") or "", allowed)

            if tgt and t - int(row["info_at"] or 0) > 60 and t >= self.panel_skip_until:
                try:
                    info = fetch_info(tgt[1])

                    if info:
                        self.store.update_info(chat_id, row["username"], keep_info(info))
                        row["info"], row["info_at"] = json.dumps(keep_info(info)), t
                except ApiError:
                    self.panel_skip_until = t + 120          # the panel is out of reach (a cut): do not make the next customers wait for it

            out.append(fmt_status(row, t))

        self.api.send(chat_id, "\n\n".join(out), KEYBOARD)

    def admins(self) -> list[int]:
        return [int(x) for x in self.cfg.get()["admins"]]

    def admin_info(self) -> str:
        """What the owner asks with /info when he cannot reach the server any other way: is the outside world there, is Bale answering, how many customers, what waits to be sent."""
        w, n, t = self.watch_state, self.store.counts(), now()
        down = int(w.get("down_since") or 0)
        outside = "وصل است" if not down else f"قطع است (از {max(1, (t - down) // 60)} دقیقه پیش)" + (" · به مشتری‌ها خبر داده شد" if w.get("notified") else "")
        queued = len([f for f in os.listdir(OUTBOX) if f.endswith(".msg")]) if os.path.isdir(OUTBOX) else 0
        bale = "جواب می‌دهد" if self.api.state.get("ok") else "جواب نمی‌دهد: " + str(self.api.state.get("err") or "؟")
        return digits(f"ℹ️ اینترنت بیرونِ سرور: {outside}\nبله: {bale}\nمشتری: {n['total']} نفر ({n['linked']} نفر اشتراک وصل کرده‌اند)\nپیام در صف: {queued}\nنسخه‌ی ربات: {VERSION}")

    def on_support(self, chat_id: int, name: str, m: dict, text: str) -> None:
        """A customer writes to the owner: the message (a text, or a photo / file / voice) goes to every admin's chat with `#<id> · <name>` on top, and a reply to it comes back to the customer."""
        if self.store.is_nosupport(chat_id):
            return

        t = now()
        mine = [x for x in self.support_tries.get(chat_id, []) if t - x < 600]
        self.support_tries[chat_id] = mine

        if len(mine) >= 15:          # a flood: dropped without an answer
            return

        mine.append(t)
        links = self.store.link_names(chat_id)
        head = f"💬 #{chat_id} · {name[:40] or '؟'}" + (" · " + "، ".join(links[:3]) if links else "")
        media = any(k in m for k in MEDIA_KEYS) and isinstance(m.get("message_id"), int)
        caption = str(m.get("caption") or "").strip()
        sent = 0

        for a in self.admins():
            if a == chat_id:
                continue

            st, mid = self.api.send_id(a, head + "\n\n" + ((caption or "(فایل زیر)") if media else text))

            if st != "ok":
                continue

            sent += 1
            self.store.note_support(a, mid, chat_id)

            if media:
                st, mid = self.api.copy(a, chat_id, m["message_id"])

                if st == "ok" and mid:
                    self.store.note_support(a, mid, chat_id)

        if not sent:
            return self.reply(chat_id, "support_fail")

        if t - self.ack_at.get(chat_id, 0) > 600:          # one "received" in ten minutes, not one for every line he types
            self.ack_at[chat_id] = t
            self.reply(chat_id, "support_ack")

    def deliver(self, admin: int, customer: int, m: dict, text: str) -> None:
        """An admin's reply to a customer: a text, or a photo / file / voice copied as it is; the admin is told whether it arrived."""
        if any(k in m for k in MEDIA_KEYS) and isinstance(m.get("message_id"), int):
            st = self.api.copy(customer, admin, m["message_id"])[0]
        elif text:
            st = self.api.send(customer, "💬 پشتیبانی:\n" + text)
        else:
            return

        self.api.send(admin, {"ok": "✓ رسید", "blocked": "✗ این مشتری ربات را مسدود کرده یا از بله رفته است", "down": "✗ بله جواب نداد؛ کمی بعد دوباره بفرستید"}.get(st, "✗ بله این پیام را نپذیرفت"))

    def on_admin(self, chat_id: int, m: dict, text: str, cmd: str) -> bool:
        """What only an admin can do - all of it from this one chat, so an owner who reaches nothing but Bale (not the panel, not the server) can still run the channel. True when the message was one of these."""
        c = self.cfg.get()
        reply = m.get("reply_to_message") if isinstance(m.get("reply_to_message"), dict) else None
        parts = text.split(None, 2)

        if cmd == "/stats":
            n = self.store.counts()
            self.api.send(chat_id, f"کاربران: {n['total']} · وصل‌شده به اشتراک: {n['linked']} · قطع‌کرده: {n['muted']} · مسدودکننده: {n['blocked']}")
        elif cmd == "/info":
            self.api.send(chat_id, self.admin_info())
        elif cmd == "/test":
            self.api.send(chat_id, c["texts"]["cut"])
        elif cmd in ("/cut", "/back"):
            queue(c["texts"]["cut" if cmd == "/cut" else "restored"], "manual")
            self.api.send(chat_id, "در صف ارسال گذاشته شد.")
        elif cmd == "/send":
            body = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""

            if not body:
                self.api.send(chat_id, "بعد از /send متن پیام را بنویسید.")
            else:
                queue(body, "manual")
                self.api.send(chat_id, "در صف ارسال گذاشته شد.")
        elif cmd == "/r":
            if len(parts) < 3 or not parts[1].lstrip("#").isdigit():
                self.api.send(chat_id, "شکل درست: /r شناسه متن  (شناسه همان عددِ بعد از # در پیام مشتری است)")
            else:
                self.deliver(chat_id, int(parts[1].lstrip("#")), m, parts[2].strip())
        elif cmd in ("/mute", "/unmute"):
            if len(parts) < 2 or not parts[1].lstrip("#").isdigit():
                self.api.send(chat_id, f"شکل درست: {cmd} شناسه")
            else:
                self.store.set_flag(int(parts[1].lstrip("#")), nosupport=(cmd == "/mute"))
                self.api.send(chat_id, "✓ پیام‌های این مشتری دیگر به شما نمی‌رسد." if cmd == "/mute" else "✓ دوباره پیام‌های این مشتری به شما می‌رسد.")
        elif cmd == "/admin":
            self.api.send(chat_id, ADMIN_HELP)
        elif reply is not None and not cmd:
            who = self.store.support_customer(chat_id, int(reply.get("message_id") or 0)) if isinstance(reply.get("message_id"), int) else 0
            found = re.match(r"\S+ #(\d{3,15})\b", str(reply.get("text") or ""))
            who = who or (int(found.group(1)) if found else 0)

            if who:
                self.deliver(chat_id, who, m, text)
            else:
                self.api.send(chat_id, "این پیام مربوط به یک مشتری نیست. روی پیامی ریپلای کنید که با 💬 و # شروع می‌شود، یا /r شناسه متن بنویسید.")
        else:
            return False

        return True

    def on_message(self, chat_id: int, first_name: str, m: dict) -> None:
        text = m.get("text") if isinstance(m.get("text"), str) else ""
        text = text.strip()
        text = BUTTONS.get(text, text)
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        c = self.cfg.get()
        admin = chat_id in self.admins()
        self.store.touch(chat_id, first_name)

        if admin and self.on_admin(chat_id, m, text, cmd):
            return

        if cmd == "/id":
            return self.api.send(chat_id, f"آیدی شما: {chat_id}")

        if cmd in ("/start", "/help"):
            self.store.set_flag(chat_id, muted=False)
            self.reply(chat_id, "welcome")

            if admin:
                self.api.send(chat_id, ADMIN_HELP)

            return

        if cmd == "/stop":
            self.store.set_flag(chat_id, muted=True)
            return self.reply(chat_id, "stopped")

        if cmd == "/status":
            return self.on_status(chat_id)

        if cmd == "/unlink":
            self.store.drop_links(chat_id)
            return self.reply(chat_id, "unlinked")

        if LINK_RE.search(text):
            return self.on_link(chat_id, text)

        if admin:
            return self.api.send(chat_id, ADMIN_HELP)          # an admin's own words are not a message to support

        if not cmd and (text or any(k in m for k in MEDIA_KEYS)) and c["support"] and self.admins():
            return self.on_support(chat_id, first_name, m, text)

        self.reply(chat_id, "welcome")

    def handle(self, upd: dict) -> None:
        m = upd.get("message")

        if not isinstance(m, dict):
            return

        chat = m.get("chat") or {}

        if chat.get("type", "private") != "private" or not isinstance(chat.get("id"), int):
            return

        self.on_message(chat["id"], str((m.get("from") or {}).get("first_name") or ""), m)

    # -- loops
    def poll_once(self, wait: int = 25) -> bool:
        """One round of getUpdates (long poll): the messages are handled and the place is kept. False when Bale could not be reached or refused (the caller waits a little)."""
        offset = int(self.store.meta("offset", 0))

        try:
            if now() - self.me_at > 3600:
                me = self.api.call("getMe", {}, 15)

                if me.get("ok"):
                    self.bot = {"id": (me.get("result") or {}).get("id"), "username": (me.get("result") or {}).get("username")}
                    self.me_at = now()
                    self.write_status()

            d = self.api.call("getUpdates", {"offset": offset, "timeout": wait, "limit": 50}, wait + 15)
        except ApiError:
            return False

        if not d.get("ok"):
            self.api.state["err"] = str(d.get("description") or "refused")[:100]
            return False

        for u in d.get("result") or []:
            if not isinstance(u, dict):
                continue

            try:
                offset = max(offset, int(u.get("update_id") or 0) + 1)
            except (TypeError, ValueError):
                continue

            try:
                self.handle(u)
            except Exception as e:      # noqa: BLE001 - one bad message must not stop the bot
                log("update failed:", type(e).__name__, str(e)[:100])

        self.store.set_meta("offset", offset)

        if d.get("result"):
            self.write_status()          # the counts on the panel follow the customers without waiting for the next look

        return True

    def poll_loop(self) -> None:
        while not STOP.is_set():
            began = time.time()

            try:
                good = bool(self.cfg.get()["token"]) and self.poll_once()
            except Exception as e:      # noqa: BLE001 - a loop must never end because of one round (a thread that dies leaves the program up and doing nothing)
                log("poll round failed:", type(e).__name__, str(e)[:100])
                good = False

            if not good:
                STOP.wait(5)
            else:
                STOP.wait(max(0.0, 1.0 - (time.time() - began)))          # a long poll that comes back at once (a proxy in the way) must not become a hammer

    def send_loop(self) -> None:
        while not STOP.is_set():
            try:
                files = sorted(f for f in (os.listdir(OUTBOX) if os.path.isdir(OUTBOX) else []) if f.endswith(".msg"))

                for f in files:
                    if STOP.is_set():
                        break

                    self.run_broadcast(os.path.join(OUTBOX, f))
            except Exception as e:      # noqa: BLE001 - see poll_loop
                log("send round failed:", type(e).__name__, str(e)[:100])
                STOP.wait(10)

            STOP.wait(3)

    def run_broadcast(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                msg = json.load(fh)

            text = str(msg["text"])
            rec = {"id": str(msg.get("id") or os.path.basename(path)), "kind": str(msg.get("kind") or "manual"), "at": int(msg.get("at") or now()), "sent": 0, "failed": 0, "blocked": 0}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):          # not a notice (a list, no text, a time that is no number): it is put aside, not retried for ever
            os.replace(path, path + ".bad")
            return

        pos_file = path[:-4] + ".pos"
        after = 0

        try:
            with open(pos_file, encoding="utf-8") as fh:
                p = json.load(fh)

            after, rec = int(p["after"]), {**rec, **p["rec"]}
        except (OSError, ValueError, KeyError):
            pass

        only = [int(x) for x in msg.get("to") or [] if str(x).lstrip("-").isdigit()]          # a test goes to the named chats only

        for cid in [x for x in only if x > after] if only else self.store.recipients(after):
            if STOP.is_set():
                return

            r = self.api.send(cid, text)

            if r == "down":          # Bale itself cannot be reached: keep the place and try again in a minute, do not burn through the list
                self.write_pos(pos_file, after, rec)
                STOP.wait(60)
                return

            rec["sent" if r == "ok" else "blocked" if r == "blocked" else "failed"] += 1

            if r == "blocked":
                self.store.set_flag(cid, blocked=True)

            after = cid
            self.write_pos(pos_file, after, rec)
            STOP.wait(float(self.cfg.get()["pace"]))

        rec["done_at"] = now()
        bc = [x for x in self.store.meta("broadcasts", []) if x.get("id") != rec["id"]] + [rec]
        self.store.set_meta("broadcasts", bc[-30:])

        for p in (path, pos_file):
            try:
                os.remove(p)
            except OSError:
                pass

        log("broadcast", rec["id"], rec["kind"], "sent", rec["sent"], "failed", rec["failed"], "blocked", rec["blocked"])

    @staticmethod
    def write_pos(pos_file: str, after: int, rec: dict) -> None:
        with open(pos_file, "w", encoding="utf-8") as fh:
            json.dump({"after": after, "rec": rec}, fh)

    def look(self) -> bool:
        """True while any of the outside addresses answers."""
        up = False
        self.targets = {}

        for h, p in self.cfg.get()["watch"]:
            try:
                socket.create_connection((str(h), int(p)), 3).close()
                ok = True
            except ConnectionRefusedError:
                ok = True          # something answered "no": the road to it is open
            except (OSError, ValueError):
                ok = False

            self.targets[f"{h}:{p}"] = ok
            up = up or ok

            if ok:
                break

        return up

    def watch_once(self) -> list[str]:
        """One look at the outside world; a notice that is due goes into the outbox. Returns the notices that were due (queued only while auto_notice is on)."""
        c = self.cfg.get()
        acts = watch_step(self.watch_state, self.look(), now(), c) if c["watch"] else []
        self.store.set_meta("watch", self.watch_state)

        for a in acts:
            if c["auto_notice"]:
                queue(c["texts"][a], a)
                log("notice queued:", a)

        try:
            self.write_status()
        except OSError as e:
            log("status not written:", e)

        return acts

    def watch_loop(self) -> None:
        self.watch_state = dict(self.store.meta("watch", {}))

        while not STOP.is_set():
            try:
                self.watch_once()
            except Exception as e:      # noqa: BLE001 - see poll_loop
                log("watch round failed:", type(e).__name__, str(e)[:100])

            STOP.wait(15)


def queue(text: str, kind: str = "manual") -> str:
    os.makedirs(OUTBOX, exist_ok=True)
    mid = f"{now()}-{os.urandom(3).hex()}"
    path = os.path.join(OUTBOX, mid + ".msg")

    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"id": mid, "text": text[:4096], "kind": kind, "at": now()}, f, ensure_ascii=False)

    os.replace(path + ".tmp", path)
    return mid


def run() -> int:
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUTBOX, exist_ok=True)
    r = Relay()
    r.store.init()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())

    log("nikatel-relay", VERSION, "started")
    threads = [threading.Thread(target=f, daemon=True) for f in (r.send_loop, r.watch_loop)]

    for t in threads:
        t.start()

    r.poll_loop()
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "run"

    if cmd == "run":
        return run()

    if cmd == "status":
        try:
            with open(os.path.join(DATA, "status.json"), encoding="utf-8") as f:
                print(f.read())
        except OSError:
            print("{}")

        return 0

    if cmd == "send" and len(argv) > 2:
        print("QUEUED", queue(" ".join(argv[2:]), "manual"))
        return 0

    print("usage: bale_relay.py run | status | send <text>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
