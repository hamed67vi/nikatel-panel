from fastapi import APIRouter

from . import actions, alerts, auth, events, jobs, servers, system, tunnels

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, tunnels, events, jobs, alerts, actions):
    router.include_router(mod.router)
