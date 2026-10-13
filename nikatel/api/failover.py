import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..config import get_settings
from ..services import arvan, autopilot, failover
from ..services import settings as st

router = APIRouter(tags=["failover"], dependencies=[Depends(current_user)])


class ArvanIn(BaseModel):
    api_key: str = ""
    mode: str = "off"
    confirm_minutes: int = 5
    domains: list[str] = []


@router.get("/settings/arvan")
def arvan_get():
    """The ArvanCloud failover settings: mode, minutes before a plan, domains (the key is masked)."""
    return {"ok": True, **st.public("arvan")}


@router.put("/settings/arvan")
def arvan_put(body: ArvanIn):
    """Save the failover settings and the key (an empty key keeps the stored one)."""
    if body.mode not in ("off", "approve", "auto"):
        raise HTTPException(400, "mode must be off, approve or auto")

    if not 2 <= body.confirm_minutes <= 120:
        raise HTTPException(400, "minutes: between 2 and 120")

    doms = [d.strip().lower() for d in body.domains if d.strip()][:20]
    st.put("arvan", {"api_key": body.api_key.strip(), "mode": body.mode, "confirm_minutes": body.confirm_minutes, "domains": doms})

    if body.api_key.strip():                 # a new key: find out at once whether it works (read only)
        st.put("arvan", {"status": {}})

    if st.get("arvan").get("api_key") and (body.api_key.strip() or not st.get("arvan").get("status")):
        try:
            arvan.test()
        except RuntimeError as e:
            st.put("arvan", {"status": {"ok": False, "error": str(e)}})

    return arvan_get()


@router.post("/settings/arvan/test")
def arvan_test():
    """Read-only: lists the domains and counts the records the key can see."""
    try:
        return arvan.test()
    except RuntimeError as e:
        st.put("arvan", {"status": {"ok": False, "error": str(e)}})
        raise HTTPException(502, str(e))


@router.get("/failover")
def fo_state():
    """The failover state: plans pending, history, every DNS change with its Restore, whether automatic switching is on."""
    return {"ok": True, **failover.state()}


@router.get("/failover/plan/{server_id}")
def fo_plan(server_id: int):
    """What a failover of this Iran server would do right now (nothing is changed)."""
    try:
        return {"ok": True, **failover.plan_for(server_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _writable():
    if not failover.has_key():
        raise HTTPException(403, "no DNS account is saved (Tunnels → CDN → Add account)" if failover.ENABLED else "the DNS failover is switched off")

    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


@router.post("/failover/{plan_id}/approve")
def fo_approve(plan_id: str):
    """Run a pending failover plan (the tunnels are moved, then the records re-pointed)."""
    _writable()

    try:
        return {"ok": True, "action_id": failover.approve(plan_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/failover/{plan_id}/dismiss")
def fo_dismiss(plan_id: str):
    """Drop a pending plan."""
    try:
        failover.dismiss(plan_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True}


@router.post("/failover/{plan_id}/undo")
def fo_undo(plan_id: str):
    """Undo a failover that ran: records back, tunnels back."""
    _writable()

    try:
        return {"ok": True, "changed": failover.undo(plan_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


_RECORDS: dict = {"at": 0.0, "rows": []}


@router.get("/arvan/records")
def arvan_records(fresh: bool = False):
    """The A records of the account (read only, kept for one minute; `fresh` reads them again now): names to pick from in Create tunnel and on the DNS page.
    A read also updates the numbers of the connection card (domains and records), so a record removed in ArvanCloud shows here after the next read."""
    import time as _t

    if fresh or _t.time() - _RECORDS["at"] > 60:
        try:
            rows = []
            total = 0
            doms = arvan.domains()

            for d in doms:
                recs = arvan.records(d)
                total += len(recs)

                for r in recs:
                    if str(r.get("type", "")).lower() == "a":
                        name = str(r.get("name") or "@")
                        rows.append({"id": str(r.get("id") or ""), "name": name, "ttl": r.get("ttl"), "cloud": bool(r.get("cloud")), "fqdn": d if name == "@" else f"{name}.{d}", "domain": d, "ips": [str(i.get("ip")) for i in (r.get("value") or []) if isinstance(i, dict) and i.get("ip")]})

            _RECORDS.update(at=_t.time(), rows=sorted(rows, key=lambda x: x["fqdn"]))
            conf = st.get("arvan")
            st.put("arvan", {"status": {**(conf.get("status") or {}), "ok": True, "domains": len(doms), "records": total}})
        except RuntimeError as e:
            raise HTTPException(502, str(e))

    return {"ok": True, "records": _RECORDS["rows"]}


class PointIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=10)
    iran_id: int


@router.post("/failover/point")
def fo_point(body: PointIn):
    """Point these DNS names at an Iran server now (the operator asked): every change is listed with a Restore."""
    _writable()
    names = [n.strip().lower() for n in body.names if n.strip()]

    if not names or any(len(n) > 253 or not re.fullmatch(r"[a-z0-9.*-]+", n) for n in names):
        raise HTTPException(400, "a DNS name may only have letters, digits, dots and dashes")

    try:
        aid = autopilot._start("dns " + ", ".join(names)[:40], lambda aid: (failover.point_names(aid, names, body.iran_id), autopilot._say(aid, "Done.", done=True, ok=True)), action="create")
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "action_id": aid}


@router.post("/failover/dns/{log_id}/restore")
def fo_restore(log_id: str):
    """One re-pointed DNS record back to the address it had before the failover."""
    _writable()

    try:
        return {"ok": True, **failover.restore(log_id)}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
