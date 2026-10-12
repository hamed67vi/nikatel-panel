from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..config import get_settings
from ..services import gateway
from ..ssh import SSHError

router = APIRouter(tags=["gateway"], dependencies=[Depends(current_user)])


class StateIn(BaseModel):
    mode: str = "auto"
    ports: list[dict] = []
    borrowed: list[dict] = []


def _wrap(fn, *a):
    try:
        return fn(*a)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, SSHError) as e:
        raise HTTPException(502, str(e))


@router.get("/gateway/{server_id}")
def get_state(server_id: int):
    """The Iran gateway of a server: mode, ports, borrowed configs, whether Xray is installed and applied."""
    return {"ok": True, "read_only": get_settings().read_only, "binary": (get_settings().data_dir.parent / "engines" / "xray").exists(), **_wrap(gateway.get_state, server_id)}


@router.put("/gateway/{server_id}")
def put_state(server_id: int, body: StateIn):
    """Save the gateway's mode, ports and borrowed configs (nothing is written to the server until Apply)."""
    return {"ok": True, **_wrap(gateway.set_state, server_id, body.model_dump())}


@router.post("/gateway/{server_id}/install")
def install(server_id: int):
    """Install Xray on the server for the gateway (the cached, verified binary)."""
    return {"ok": True, **_wrap(gateway.install, server_id)}


@router.post("/gateway/{server_id}/apply")
def apply(server_id: int):
    """Write the gateway config to the server and restart it."""
    return {"ok": True, **_wrap(gateway.apply, server_id)}


@router.post("/gateway/{server_id}/status")
def status(server_id: int):
    """Ask the server how the gateway runs now."""
    return {"ok": True, **_wrap(gateway.status, server_id)}


@router.post("/gateway/{server_id}/test")
def test(server_id: int):
    """Test each exit of the gateway from the server (does traffic leave through it)."""
    return {"ok": True, "results": _wrap(gateway.test, server_id)}


@router.post("/gateway/{server_id}/remove")
def remove(server_id: int):
    """Remove the gateway from the server."""
    _wrap(gateway.remove, server_id)
    return {"ok": True}
