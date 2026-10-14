"""PasarGuard: the nodes of a PasarGuard panel, joined with the servers of this panel (by address). Routes live under /api/panels/{panel_id}/pg/nodes..."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import panels, pg_nodes
from ..services.panels import PanelError
from ..ssh import SSHError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])
P = "/panels/{panel_id}/pg/nodes"


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, SSHError, RuntimeError) as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    """The login of a PasarGuard panel: 404 when there is no such panel, 400 when it is a Marzban panel (it has no PasarGuard nodes)."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this tab is for PasarGuard panels")

    return c


class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(62050, ge=1, le=65535)
    api_port: int = Field(62051, ge=1, le=65535)
    usage_coefficient: float = Field(1.0, ge=0, le=1000)
    connection_type: Literal["grpc", "rest"] = "grpc"
    keep_alive: int = Field(60, ge=0, le=86400)
    core_config_id: int = Field(ge=1)
    api_key: str = Field(min_length=32, max_length=40)
    server_ca: str = Field(min_length=1, max_length=4000)
    data_limit_gb: float = Field(0, ge=0, le=1000000)
    data_limit_reset_strategy: Literal["no_reset", "day", "week", "month", "year"] = "no_reset"


class NodePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    address: str | None = Field(None, min_length=1, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    api_port: int | None = Field(None, ge=1, le=65535)
    usage_coefficient: float | None = Field(None, ge=0, le=1000)
    connection_type: Literal["grpc", "rest"] | None = None
    keep_alive: int | None = Field(None, ge=0, le=86400)
    core_config_id: int | None = Field(None, ge=1)
    api_key: str | None = Field(None, min_length=32, max_length=40)
    server_ca: str | None = Field(None, min_length=1, max_length=4000)
    data_limit_gb: float | None = Field(None, ge=0, le=1000000)
    data_limit_reset_strategy: Literal["no_reset", "day", "week", "month", "year"] | None = None


def _fields(body: BaseModel) -> dict:
    """The fields the caller gave, with the data limit turned from GB into the bytes the panel wants."""
    d = body.model_dump(exclude_none=True)

    if "data_limit_gb" in d:
        d["data_limit"] = int(d.pop("data_limit_gb") * 1024 ** 3)

    return d


class StateIn(BaseModel):
    enabled: bool


class SyncIn(BaseModel):
    flush_users: bool = True


class MaintenanceIn(BaseModel):
    what: Literal["node", "core", "geofiles"]
    core_version: str = Field("latest", max_length=20, pattern=r"^(latest|v?\d+\.\d+\.\d+)$")
    region: Literal["iran", "china", "russia"] = "iran"


class MakeIn(BaseModel):
    server_id: int = Field(ge=1)
    name: str | None = Field(None, max_length=40)
    usage_coefficient: float = Field(1.0, ge=0, le=1000)
    core_config_id: int | None = Field(None, ge=1)


class RegisterIn(MakeIn):
    api_key: str = Field("", max_length=40)
    port: int = Field(62050, ge=1, le=65535)
    api_port: int = Field(62051, ge=1, le=65535)
    connection_type: Literal["grpc", "rest"] = "grpc"


# ---- the list and the one-time reads (static paths first: {node_id} would swallow them)
@router.get(P)
def nodes_list(panel_id: int):
    """Every node of the panel with the server of ours it runs on (matched by address), the foreign servers that are not a node yet, the minimum node version and the core configs (a few reads of the panel)."""
    return {"ok": True, **_wrap(pg_nodes.overview, _cred(panel_id))}


@router.post(P)
def node_add(panel_id: int, body: NodeIn):
    """Add a node to the panel from its address, API key and certificate (the panel connects to it in the background); a node with that name must not exist."""
    return {"ok": True, "node": _wrap(pg_nodes.node_add, _cred(panel_id), _fields(body))}


@router.get(P + "/backups")
def nodes_backups(panel_id: int):
    """The nodes saved to files before an edit, a delete or a certificate change (the last 20), newest first; the files hold the key and the certificate and are never returned."""
    _cred(panel_id)
    return {"ok": True, "backups": _wrap(pg_nodes.backups, {"id": panel_id})}


@router.post(P + "/restore/{name}")
def nodes_restore(panel_id: int, name: str):
    """Put a node back from one of the kept files: a deleted node is made again (new id, no history), an edited one gets its old values (the state before is saved first)."""
    return {"ok": True, **_wrap(pg_nodes.restore, _cred(panel_id), name)}


@router.post(P + "/make/plan")
def make_plan(panel_id: int, body: MakeIn):
    """For one of our servers: what making it a node would do step by step, whether the node is installed there already (read-only over SSH), and what stands in the way; nothing is changed."""
    return {"ok": True, **_wrap(pg_nodes.make_plan, _cred(panel_id), body.server_id, body.model_dump(exclude_none=True))}


@router.post(P + "/make", status_code=202)
def make(panel_id: int, body: MakeIn):
    """Make one of our servers a node as an action: install the PasarGuard node on it, read its certificate, add it to the panel, wait until it connects; answers at once with the action id."""
    return {"ok": True, "action_id": _wrap(pg_nodes.make_start, _cred(panel_id), body.server_id, body.model_dump(exclude_none=True))}


@router.post(P + "/register")
def register(panel_id: int, body: RegisterIn):
    """Add the node that is installed on one of our servers already: its certificate is read from the server, the API key is the one given (empty: the one this panel makes)."""
    opts = body.model_dump(exclude={"server_id", "api_key"}, exclude_none=True)
    return {"ok": True, **_wrap(pg_nodes.register, _cred(panel_id), body.server_id, opts, body.api_key)}


@router.get(P + "/servers/{server_id}/cert")
def server_cert(panel_id: int, server_id: int):
    """The public certificate of the PasarGuard node installed on one of our servers (to copy into the panel by hand) with its subject, end date and fingerprint; read-only over SSH."""
    _cred(panel_id)
    return {"ok": True, **_wrap(pg_nodes.server_cert, server_id)}


# ---- one node
@router.get(P + "/{node_id}")
def node_get(panel_id: int, node_id: int):
    """One node in full (the key and the certificate only as set / not set), the server of ours it runs on, and the numbers it reports about itself."""
    return {"ok": True, "node": _wrap(pg_nodes.node_get, _cred(panel_id), node_id)}


@router.put(P + "/{node_id}")
def node_edit(panel_id: int, node_id: int, body: NodePatch):
    """Change a node; every field is sent back with the new ones, the node is saved to a file first, and the panel then starts the core on it again, so its users drop for a moment."""
    return {"ok": True, **_wrap(pg_nodes.node_edit, _cred(panel_id), node_id, _fields(body))}


@router.delete(P + "/{node_id}")
def node_delete(panel_id: int, node_id: int, confirm: bool = False):
    """Delete a node (needs confirm=true): its traffic history goes with it and whoever is connected through it is cut; the users stay and the node is saved to a file first."""
    return {"ok": True, **_wrap(pg_nodes.node_delete, _cred(panel_id), node_id, confirm)}


@router.get(P + "/{node_id}/logs")
def node_logs(panel_id: int, node_id: int, lines: int = 150):
    """The last lines (10 to 500) of the node's log as the panel streams them; reads until the stream is quiet, a few seconds."""
    return {"ok": True, **_wrap(pg_nodes.node_logs, _cred(panel_id), node_id, lines)}


@router.get(P + "/{node_id}/usage")
def node_usage(panel_id: int, node_id: int, period: Literal["hour", "day", "month"] = "day", days: int = 7):
    """The traffic of one node per hour, day or month over the last days (hours: at most 14 days), with the totals."""
    return {"ok": True, **_wrap(pg_nodes.node_usage, _cred(panel_id), node_id, period, days)}


@router.post(P + "/{node_id}/reconnect")
def node_reconnect(panel_id: int, node_id: int):
    """Restart the core on the node and connect again; connections through the node drop for a moment and come back by themselves."""
    return {"ok": True, **_wrap(pg_nodes.node_reconnect, _cred(panel_id), node_id)}


@router.post(P + "/{node_id}/sync")
def node_sync(panel_id: int, node_id: int, body: SyncIn | None = None):
    """Send the user list to the node again without a restart (the panel refuses it while the node is not connected)."""
    return {"ok": True, **_wrap(pg_nodes.node_sync, _cred(panel_id), node_id, body.flush_users if body else True)}


@router.post(P + "/{node_id}/state")
def node_state(panel_id: int, node_id: int, body: StateIn):
    """Enable or disable a node; a disabled node is disconnected, so whoever is connected through it is cut until it is enabled again."""
    return {"ok": True, **_wrap(pg_nodes.node_state, _cred(panel_id), node_id, body.enabled)}


@router.post(P + "/{node_id}/reset-usage")
def node_reset_usage(panel_id: int, node_id: int):
    """Set the traffic counters of the node to zero (the panel keeps a log row with the old numbers); a node stopped by its data limit connects again."""
    return {"ok": True, **_wrap(pg_nodes.node_reset_usage, _cred(panel_id), node_id)}


@router.post(P + "/{node_id}/maintenance")
def node_maintenance(panel_id: int, node_id: int, body: MaintenanceIn):
    """Ask the node to update its own software (it restarts), its Xray core (latest or a version) or its geo files (iran, china, russia)."""
    return {"ok": True, **_wrap(pg_nodes.node_maintenance, _cred(panel_id), node_id, body.what, body.core_version, body.region)}


@router.get(P + "/{node_id}/cert")
def node_cert(panel_id: int, node_id: int):
    """Is the certificate the panel holds for this node the one the node on its server made? Read-only; answers with the two summaries (subject, end date, fingerprint), never the text."""
    return {"ok": True, **_wrap(pg_nodes.cert_compare, _cred(panel_id), node_id)}


@router.post(P + "/{node_id}/cert")
def node_cert_apply(panel_id: int, node_id: int):
    """Copy the certificate the node made (read from its server) into the panel when the two differ; the node is saved to a file first and the panel starts the core on it again."""
    return {"ok": True, **_wrap(pg_nodes.cert_apply, _cred(panel_id), node_id)}
