"""PasarGuard: the CLIENT templates of a PasarGuard 5.4 panel - the templates its subscription answers are built from (a Clash YAML, an Xray or sing-box JSON, the lists of
User-Agent strings) - list, read, make, change, set the default, delete, and the files that make a change or a delete undoable (the owner asked for every section of the panel,
2026-10-14). Written against the REST API read on a live 5.4.1 panel and its own sources (app/models/client_template.py, app/operation/client_template.py).

What the panel enforces, and so what is checked before it is asked:
  * a name is 1 to 64 characters and unique PER TYPE (409); the type never changes after a template is made; the content is not empty;
  * the content is checked by the panel itself (it renders the template with Jinja and parses the result: YAML for Clash, JSON for the others, a JSON object with a non-empty list
    `list` for the User-Agent kinds and with non-empty `inbounds` and `outbounds` for Xray and sing-box). A refusal comes back as the panel's own sentence;
  * a template that is the DEFAULT of its type cannot be switched off directly: another one is made the default instead; a SYSTEM template and the last template of a type can
    never be deleted (403); deleting the default makes another template of the type the default, and a host that used the deleted template as its own loses that choice.
The content of a template is not a secret (it has the shape of a client config, with placeholders), so the files kept here hold it as it is."""
from __future__ import annotations

import json
import re
import time

from .panels import PanelError
from .pg_admin import _backup_dir, _call, _dict, _list, _write_backup, audit

TYPES = ("clash_subscription", "xray_subscription", "singbox_subscription", "user_agent", "grpc_user_agent")
LABELS = {"clash_subscription": "Clash", "xray_subscription": "Xray (v2ray) JSON", "singbox_subscription": "sing-box JSON", "user_agent": "User-Agent list", "grpc_user_agent": "gRPC User-Agent list"}
MAX_NAME = 64
MAX_CONTENT = 400_000
KEEP_LISTED = 40
_BK = re.compile(r"panel(\d+)-ctpl-(\d+)-(\d{8}-\d{6})(?:-(\d+))?\.json")


# ------------------------------------------------------------------ pure
def clean_name(name) -> str:
    n = str(name or "").strip()

    if not n or len(n) > MAX_NAME:
        raise ValueError(f"a template needs a name of 1 to {MAX_NAME} characters")

    return n


def clean_content(content) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("the content of a template cannot be empty")

    if len(content) > MAX_CONTENT:
        raise ValueError(f"the content is too long ({MAX_CONTENT} characters at most)")

    return content


def clean_type(t) -> str:
    t = str(t or "").strip()

    if t not in TYPES:
        raise ValueError(f"the type of a template is one of {', '.join(TYPES)}")

    return t


def row(t, with_content: bool = False) -> dict:
    """One template as the page sees it; the list leaves the content out (its size says how big it is), `with_content` has it."""
    t = _dict(t)
    content = str(t.get("content") or "")
    out = {"id": t.get("id"), "name": str(t.get("name") or ""), "type": str(t.get("template_type") or ""), "label": LABELS.get(str(t.get("template_type") or ""), ""),
           "default": bool(t.get("is_default")), "system": bool(t.get("is_system")), "size": len(content), "lines": content.count("\n") + (1 if content else 0)}

    if with_content:
        out["content"] = content

    return out


# ------------------------------------------------------------------ reading
def _all(c: dict) -> list[dict]:
    r = _dict(_call(c, "GET", "/api/client_templates"))
    return [x for x in _list(r.get("templates")) if isinstance(x, dict) and x.get("id") is not None]


def templates_list(c: dict) -> dict:
    """Every client template (without its content) in the order of the panel, how many there are per type, and the types and their names."""
    rows = [row(x) for x in _all(c)]
    return {"templates": rows, "total": len(rows), "types": [{"key": k, "label": v} for k, v in LABELS.items()], "per_type": {t: sum(1 for r in rows if r["type"] == t) for t in TYPES}}


def template_get(c: dict, tid: int) -> dict:
    t = _dict(_call(c, "GET", f"/api/client_template/{int(tid)}"))

    if t.get("id") is None:
        raise ValueError("this template does not exist (any more)")

    return row(t, True)


# ------------------------------------------------------------------ backups
def _backup(c: dict, t: dict, why: str) -> str:
    return _write_backup(f"panel{int(c['id'])}-ctpl-{int(t['id'])}", {"panel": int(c["id"]), "template": {k: t.get(k) for k in ("id", "name", "template_type", "content", "is_default", "is_system")}, "why": why, "at": int(time.time())})


def template_backups(c: dict) -> list[dict]:
    """The files kept before a change or a delete of a client template of this panel, newest first: {file, id, name, type, why, at}."""
    found = []

    for p in _backup_dir().iterdir():
        m = _BK.fullmatch(p.name)

        if not m or int(m.group(1)) != int(c["id"]):
            continue

        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            t = _dict(doc.get("template"))
            found.append((m.group(3), int(m.group(4) or 1), {"file": p.name, "id": int(m.group(2)), "name": str(t.get("name") or ""), "type": str(t.get("template_type") or ""), "why": str(doc.get("why") or ""), "at": doc.get("at")}))
        except (OSError, ValueError, AttributeError):
            continue

    return [x[2] for x in sorted(found, key=lambda x: (x[0], x[1]), reverse=True)[:KEEP_LISTED]]


def template_restore(c: dict, name: str) -> dict:
    """Put a template back from a file kept here: its name and content are written over the template that has its id now (saved first), or the template is made again (a new id) when it was deleted."""
    m = _BK.fullmatch(str(name))

    if not m or int(m.group(1)) != int(c["id"]):
        raise ValueError("unknown backup")

    try:
        doc = json.loads((_backup_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("unknown backup") from None

    t = _dict(doc.get("template"))

    if t.get("id") is None or t.get("template_type") not in TYPES:
        raise ValueError("this backup cannot be put back")

    body = {"name": clean_name(t.get("name")), "content": clean_content(t.get("content"))}

    try:
        cur = _dict(_call(c, "GET", f"/api/client_template/{int(t['id'])}"))
    except PanelError:
        cur = {}

    if cur.get("id") is None:
        made = _dict(_call(c, "POST", "/api/client_template", body={**body, "template_type": t["template_type"], "is_default": False}))
        audit(c, f"client template “{body['name']}” made again from a backup")
        return {"template": row(made), "made_again": True}

    bk = _backup(c, cur, "before a restore")
    made = _dict(_call(c, "PUT", f"/api/client_template/{int(t['id'])}", body=body))
    audit(c, f"client template “{body['name']}” put back from a backup")
    return {"template": row(made), "made_again": False, "backup": bk}


# ------------------------------------------------------------------ writing
def template_create(c: dict, name, template_type, content, is_default: bool = False) -> dict:
    """Make a client template. The panel checks the content (Jinja, then YAML or JSON) and answers 400 with its own sentence; a name that exists for the type is a 409."""
    body = {"name": clean_name(name), "template_type": clean_type(template_type), "content": clean_content(content), "is_default": bool(is_default)}
    made = _dict(_call(c, "POST", "/api/client_template", body=body))
    audit(c, f"client template “{body['name']}” ({LABELS[body['template_type']]}) made")
    return row(made)


def template_update(c: dict, tid: int, name=None, content=None, is_default=None) -> dict:
    """Change the name, the content and/or make it the default of its type; only what differs is sent, after the template is saved to a file. Switching the default OFF is refused with
    the way to do it (make another template the default), as the panel does."""
    cur = _dict(_call(c, "GET", f"/api/client_template/{int(tid)}"))

    if cur.get("id") is None:
        raise ValueError("this template does not exist (any more)")

    body: dict = {}

    if name is not None and clean_name(name) != cur.get("name"):
        body["name"] = clean_name(name)

    if content is not None and clean_content(content) != cur.get("content"):
        body["content"] = content

    if is_default is not None and bool(is_default) != bool(cur.get("is_default")):
        if not is_default:
            raise ValueError("the default of a type cannot be switched off: make another template of this type the default instead")

        body["is_default"] = True

    if not body:
        raise ValueError("nothing to change: the template already has these values")

    bk = _backup(c, cur, "before a change")
    made = _dict(_call(c, "PUT", f"/api/client_template/{int(tid)}", body=body))
    audit(c, f"client template “{made.get('name') or cur.get('name')}” changed ({', '.join(sorted(body))})")
    return {"template": row(made), "backup": bk, "changed": sorted(body)}


def template_delete(c: dict, tid: int, confirm: bool = False) -> dict:
    """Delete a template (needs `confirm`): never a system template, never the last one of its type (the panel refuses both); deleting the default of a type makes another one the default.
    The template is saved to a file first."""
    cur = _dict(_call(c, "GET", f"/api/client_template/{int(tid)}"))

    if cur.get("id") is None:
        raise ValueError("this template does not exist (any more)")

    name, typ = str(cur.get("name") or ""), str(cur.get("template_type") or "")

    if cur.get("is_system"):
        raise ValueError(f"“{name}” is a system template: the panel does not delete it")

    same = [x for x in _all(c) if x.get("template_type") == typ]

    if len(same) <= 1:
        raise ValueError(f"“{name}” is the last {LABELS.get(typ, typ)} template: a type always keeps one")

    if not confirm:
        raise ValueError("deleting a template cannot be undone on the panel: send confirm: true (the template is saved here first)")

    bk = _backup(c, cur, "before a delete")
    _call(c, "DELETE", f"/api/client_template/{int(tid)}")
    audit(c, f"client template “{name}” deleted (saved as {bk})", "warn")
    return {"deleted": True, "backup": bk, "was_default": bool(cur.get("is_default"))}


MAX_BULK = 50


def templates_delete(c: dict, ids, confirm: bool = False) -> dict:
    """Delete several client templates in ONE call (the panel's `POST /api/client_templates/bulk/delete`, all or none), with the rules of one: never a system template, and every type keeps at
    least one template (what is left of a type after the delete is counted here, the panel refuses otherwise); each is saved to a file first. Needs `confirm`."""
    want = sorted({int(i) for i in ids})

    if not 1 <= len(want) <= MAX_BULK:
        raise ValueError(f"pick 1 to {MAX_BULK} templates")

    everything = _all(c)
    by_id = {x.get("id"): x for x in everything if isinstance(x, dict)}
    missing = [i for i in want if i not in by_id]

    if missing:
        raise ValueError("there is no template " + ", ".join(str(i) for i in missing))

    for i in want:
        if by_id[i].get("is_system"):
            raise ValueError(f"“{by_id[i].get('name')}” is a system template: the panel does not delete it")

    for typ in {str(by_id[i].get("template_type") or "") for i in want}:
        left = [x for x in everything if x.get("template_type") == typ and x.get("id") not in want]

        if not left:
            raise ValueError(f"that would leave no {LABELS.get(typ, typ)} template: a type always keeps one")

    if not confirm:
        raise ValueError("deleting templates cannot be undone on the panel: send confirm: true (each is saved here first)")

    backups = [_backup(c, _dict(_call(c, "GET", f"/api/client_template/{i}")), "before a delete (several at once)") for i in want]      # the whole template (the list may cut the content)
    r = _dict(_call(c, "POST", "/api/client_templates/bulk/delete", body={"ids": want}))
    gone = [str(x) for x in _list(r.get("templates"))] or [str(by_id[i].get("name") or "") for i in want]
    audit(c, f"{len(gone)} client templates deleted: {', '.join('“' + n + '”' for n in gone[:5])}{', …' if len(gone) > 5 else ''}", "warn")
    return {"deleted": gone, "count": r.get("count", len(gone)) if isinstance(r.get("count"), int) else len(gone), "backups": backups}
