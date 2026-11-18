"""The Servers folders as WALLS (1.69.286).

A Servers folder is the tag of a server's profile (`Profile.tag`, "" = Any). Two rules make a folder a wall; while every server is in no folder, or the Iran servers are in one folder and the foreign servers in others
(the fleet: Iran / PasarGuard / main), nothing here changes anything - every function answers "yes" and every list stays as it was.

  pool     the Iran servers of ONE folder are one pool: a tunnel is moved only inside its pool (1.69.211, the owner, 2026-11-04: "the Iran servers used for these must be separate from the ones used for the other tunnels").
  private  a folder that holds an Iran server AND a foreign server is a PRIVATE folder - a pair of servers that belong together (the owner, 2026-11-18, two servers of a friend's panel, folder "ali": "keep their folder
           separate from our panel, nothing of our servers may be tunneled onto them"). Its servers are chosen only for tunnels between servers of that folder, and its Iran servers do not run the fleet's probes
           (vantage, "filtered in Iran?", config reach, SSH jumps ...). The fleet can still LOOK at them: the Doctor watches them like every other server.

Every automatic choice of an Iran server (new tunnel, resource move, quality move, return, failover target, temporary test tunnel) goes through `compatible` / `same_pool`; every probe that runs a script of ours from an Iran
server goes through `may_probe`. A hand choice of the owner (the Move button of one tunnel, Create tunnel with an Iran server picked) is his own decision and stays his.

The pure functions work on a `Folders` value so that they are tested without a database; `load()` reads the profiles once."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from ..db import session
from ..models import Profile, Server


def norm(tag) -> str:
    """Pure: a tag compared the way the Servers page treats it - without capitals and edge spaces ("Ali" and "ali" are one folder)."""
    return str(tag or "").strip().casefold()


@dataclass(frozen=True)
class Folders:
    tag: dict = field(default_factory=dict)             # server id -> its folder as written on the profile ("" = Any); a server without a profile is not in the map
    private: frozenset = frozenset()                    # the folders (normalised) that hold an Iran server and a foreign server

    def folder(self, server_id: int) -> str:
        return str(self.tag.get(int(server_id), "") or "")

    def is_private(self, server_id: int) -> bool:
        return norm(self.folder(server_id)) in self.private

    def same_pool(self, a: int, b: int) -> bool:
        """The two servers are in the same folder (an untagged server and an untagged server are in the same one: "Any")."""
        return norm(self.folder(a)) == norm(self.folder(b))

    def compatible(self, a: int, b: int) -> bool:
        """May a tunnel be built between these two servers by a choice the panel makes? Yes, unless one of them is in a private folder and the other is not in that same folder."""
        return not (self.is_private(a) or self.is_private(b)) or self.same_pool(a, b)

    def may_probe(self, prober: int, target: int | None = None) -> bool:
        """May the panel run a probe of its own, FROM this Iran server? A fleet server looks at anything; a server of a private folder looks only at servers of its own folder (a probe with no server as the target is the fleet's)."""
        return not self.is_private(prober) or (target is not None and self.same_pool(prober, target))


def build(rows) -> Folders:
    """Pure: `rows` = (profile kind, server id, tag) of the Iran and foreign profiles. The first profile of a server wins (a server has one profile of its kind)."""
    tag: dict[int, str] = {}
    by_kind: dict[str, set[str]] = {"iran": set(), "foreign": set()}

    for kind, sid, t in rows:
        if sid is None or kind not in by_kind:
            continue

        tag.setdefault(int(sid), str(t or ""))

        if norm(t):
            by_kind[kind].add(norm(t))

    return Folders(tag=tag, private=frozenset(by_kind["iran"] & by_kind["foreign"]))


def load() -> Folders:
    with session() as db:
        return build(db.execute(select(Profile.kind, Profile.server_id, Profile.tag).where(Profile.kind.in_(("iran", "foreign")), Profile.server_id.is_not(None)).order_by(Profile.id)).all())


def pool_of(server_id: int) -> str:
    """The folder of an Iran server on the Servers page (the tag of its profile; "" = Any)."""
    with session() as db:
        return str(db.scalar(select(Profile.tag).where(Profile.kind == "iran", Profile.server_id == server_id).limit(1)) or "")


def same_pool(a: int, b: int) -> bool:
    return load().same_pool(a, b)


def compatible(a: int, b: int) -> bool:
    return load().compatible(a, b)


def in_pool(rows: list[dict], iran_id: int) -> list[dict]:
    """The rows of a ranking (each has the server's "id") that are Iran servers of the same folder as `iran_id`."""
    f = load()
    return [r for r in rows if f.same_pool(r["id"], iran_id)]


def server_id_of_host(host: str) -> int | None:
    """The id of the server that has this address, or None (the address of a config, a site ...)."""
    h = str(host or "").strip().lower().rstrip(".")

    if not h:
        return None

    with session() as db:
        for sid, sh in db.execute(select(Server.id, Server.host)):
            if str(sh or "").strip().lower().rstrip(".") == h:
                return int(sid)

    return None


def probers(ids: list[int], target_host: str = "") -> set[int]:
    """Of these Iran server ids, the ones whose probe of `target_host` (an address; "" = no server in particular) the panel may run: see `Folders.may_probe`."""
    f = load()
    target = server_id_of_host(target_host) if target_host else None
    return {i for i in ids if f.may_probe(i, target)}
