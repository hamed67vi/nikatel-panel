"""PasarGuard updates (Settings → PasarGuard): the newest versions, what is installed, and the updates - the nodes one at a time, and the panel itself on its own server (roadmap #103)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import pg_safe, pg_update
from ..services.panels import PanelError
from ..ssh import SSHError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


class NodeItem(BaseModel):
    panel_id: int = Field(ge=1)
    node_id: int = Field(ge=1)
    what: Literal["node", "core"] = "node"          # the software of the node, or the Xray core on it


class NodesIn(BaseModel):
    items: list[NodeItem] = Field(min_length=1, max_length=pg_update.MAX_NODES)


class PanelIn(BaseModel):
    backup: bool = True                              # the pasarguard backup command first (when it has one); a backup that fails stops the update before anything is changed
    typed: str = Field("", max_length=80)            # the name of the panel, typed back


@router.get("/pg-updates")
def pg_updates(force: bool = False):
    """The newest releases of PasarGuard (panel and node) and Xray on GitHub (asked once an hour, `force` asks now), every PasarGuard panel with its installed version and the server of ours that runs it, and
    every node of them with its software and Xray version - each marked behind or not (null when a version is not known). One read of each panel."""
    return {"ok": True, **_wrap(pg_update.overview, force)}


@router.get("/pg-updates/panels/{panel_id}/plan")
def pg_update_panel_plan(panel_id: int):
    """What updating this panel needs and what stands in the way (the server that runs it must be in the Servers list, the panel must not be at the newest, not read-only); nothing is changed."""
    return {"ok": True, **_wrap(pg_update.panel_plan, panel_id)}


@router.post("/pg-updates/panels/{panel_id}", status_code=202)
def pg_update_panel(panel_id: int, body: PanelIn):
    """Update a PasarGuard panel on its own server with the official command, as an action (the story is read with /api/actions/{id}): look at the server, a backup, `pasarguard update`, wait until the panel
    answers with the new version. The name of the panel must be typed back; refused while the panel is read-only and when the server that runs the panel is not one of ours."""
    return {"ok": True, "action_id": _wrap(pg_update.panel_start, panel_id, body.backup, body.typed)}


class SafeIn(BaseModel):
    typed: str = Field("", max_length=80)            # the name of the panel, typed back (only when the panel is part of it)
    scope: Literal["all", "panel", "nodes"] = "all"   # the nodes and then the panel, only the panel, only the nodes


@router.get("/pg-updates/safe/plan")
def pg_safe_plan(scope: Literal["all", "panel", "nodes"] = "all"):
    """What a Safe update would do now, in order (the quietest node first), what it would skip and why, and what stands in the way; nothing is changed (the traffic of the nodes is read for a few seconds)."""
    return {"ok": True, **_wrap(pg_safe.plan, None, False, scope)}


@router.post("/pg-updates/safe", status_code=202)
def pg_safe_start(body: SafeIn):
    """The Safe update as an action (the story is read with /api/actions/{id}): the nodes one at a time, the quietest first, each looked at before and after with its old version kept to go back to, then the panel
    (a backup first). Refused when there is nothing to update, while another PasarGuard update runs, and unless the name of the panel is typed back when the panel is part of it."""
    return {"ok": True, "action_id": _wrap(pg_safe.start, body.typed, None, body.scope)}


@router.post("/pg-updates/nodes", status_code=202)
def pg_update_nodes(body: NodesIn):
    """Update nodes ONE AT A TIME as an action: each (its software, or its Xray) must be connected again with the new version before the next starts; one that does not come back stops the run and the
    nodes after it are left as they were. A node that is not connected, or is the newest already, is not touched."""
    return {"ok": True, "action_id": _wrap(pg_update.nodes_start, [i.model_dump() for i in body.items])}
