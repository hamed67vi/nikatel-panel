from fastapi import APIRouter

from . import accounts as accounts_api
from . import (
    actions,
    ai,
    alerts,
    auth,
    autopilot,
    balance,
    bale,
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
    pg_access,
    pg_admin,
    pg_client_templates,
    pg_configs,
    pg_cores,
    pg_hosts,
    pg_kinds,
    pg_nodes,
    pg_roles,
    pg_system,
    pg_update,
    pg_usage,
    pg_users,
    radar,
    servers,
    shopbot,
    system,
    tunnels,
)
from . import analyst as analyst_api
from . import best as best_api
from . import checkall as checkall_api
from . import doctor as doctor_api
from . import outbound as outbound_api
from . import profiles as profiles_api
from . import setup as setup_api
from . import tokens as tokens_api

router = APIRouter(prefix="/api")

for mod in (auth, system, servers, profiles_api, nodes, tunnels, events, jobs, alerts, actions, gateway, configs, panels, shopbot, ai, engines, autopilot, radar, failover, license, balance, bale, dns, analyst_api, tokens_api, checkall_api, best_api, doctor_api, accounts_api, outbound_api, setup_api, pg_access, pg_nodes, pg_cores, pg_admin, pg_roles, pg_client_templates, pg_system, pg_usage, pg_users, pg_hosts, pg_kinds, pg_update, pg_configs):
    router.include_router(mod.router)
