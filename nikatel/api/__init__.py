from fastapi import APIRouter

from . import auth, events, jobs, servers, system, tunnels

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, tunnels, events, jobs):
    router.include_router(mod.router)
