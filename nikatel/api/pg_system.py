"""PasarGuard: the system view (machine, users, workers, WireGuard pools, inbounds) of a PasarGuard panel. Route: /api/panels/{panel_id}/pg/system."""
from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..services import panels, pg_system
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])


@router.get("/panels/{panel_id}/pg/system")
def system(panel_id: int):
    """The machine the panel runs on (CPU, memory, disk, uptime, version), the users by state and the traffic, the scheduler and node workers, the WireGuard pools and the inbounds the panel knows; read only, a read the panel refuses is named in warnings."""
    try:
        c = panels.cred_for(panel_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if c.get("type") != "pasarguard":
        raise HTTPException(400, "this is only for PasarGuard panels")

    try:
        return {"ok": True, **pg_system.system(c)}
    except PanelError as e:
        raise HTTPException(502, str(e))
