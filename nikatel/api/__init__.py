from fastapi import APIRouter

from . import (
    actions,
    alerts,
    auth,
    configs,
    events,
    gateway,
    jobs,
    panels,
    servers,
    system,
    tunnels,
)

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, tunnels, events, jobs, alerts, actions, gateway, configs, panels):
    router.include_router(mod.router)
