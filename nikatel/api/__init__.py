from fastapi import APIRouter

from . import (
    actions,
    alerts,
    auth,
    configs,
    events,
    gateway,
    jobs,
    nodes,
    panels,
    servers,
    shopbot,
    system,
    tunnels,
)

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, nodes, tunnels, events, jobs, alerts, actions, gateway, configs, panels, shopbot):
    router.include_router(mod.router)
