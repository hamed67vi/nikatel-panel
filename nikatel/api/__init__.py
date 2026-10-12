from fastapi import APIRouter

from . import (
    actions,
    ai,
    alerts,
    auth,
    autopilot,
    balance,
    configs,
    engines,
    events,
    failover,
    gateway,
    jobs,
    license,
    nodes,
    panels,
    radar,
    servers,
    shopbot,
    system,
    tunnels,
)

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, nodes, tunnels, events, jobs, alerts, actions, gateway, configs, panels, shopbot, ai, engines, autopilot, radar, failover, license, balance):
    router.include_router(mod.router)
