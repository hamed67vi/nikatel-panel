from fastapi import APIRouter

from . import (
    actions,
    ai,
    alerts,
    auth,
    autopilot,
    balance,
    configs,
    dns,
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
from . import analyst as analyst_api
from . import tokens as tokens_api

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, nodes, tunnels, events, jobs, alerts, actions, gateway, configs, panels, shopbot, ai, engines, autopilot, radar, failover, license, balance, dns, analyst_api, tokens_api):
    router.include_router(mod.router)
