"""PasarGuard / Marzban: usage and live statistics of a panel (read only). Routes live under /api/panels/{panel_id}/pg/usage..."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import current_user
from ..services import panels, pg_usage
from ..services.panels import PanelError

router = APIRouter(tags=["pasarguard"], dependencies=[Depends(current_user)])

Preset = Annotated[str, Query(pattern=r"^(today|last7|last30|jmonth|last_jmonth|custom)$", description="today, last7, last30, jmonth, last_jmonth or custom")]
FromDate = Annotated[str, Query(alias="from", max_length=10, description="custom range start: 2026-10-14 or 1405/07/22")]
ToDate = Annotated[str, Query(max_length=10, description="custom range end: 2026-10-14 or 1405/07/22")]


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (PanelError, RuntimeError) as e:
        raise HTTPException(502, str(e))


def _cred(panel_id: int) -> dict:
    return _wrap(panels.cred_for, panel_id)


@router.get("/panels/{panel_id}/pg/usage/overview")
def usage_overview(panel_id: int):
    """The counters of the panel: users by status, online now, CPU, memory, disk and total traffic (asked from the panel at most once in 15 seconds)."""
    return {"ok": True, **_wrap(pg_usage.overview, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/usage/online")
def usage_online(panel_id: int):
    """How many users are online right now, from the same cached counters as the overview."""
    return {"ok": True, **_wrap(pg_usage.online, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/usage/ranges")
def usage_ranges(panel_id: int):
    """The ready-made ranges (today, last 7 and 30 days, this and last Jalali month) with their Tehran-day boundaries in Gregorian and Jalali dates; the panel is not asked."""
    _cred(panel_id)
    return {"ok": True, **pg_usage.presets()}


@router.get("/panels/{panel_id}/pg/usage/range")
def usage_range(panel_id: int, preset: Preset = "last7", frm: FromDate = "", to: ToDate = ""):
    """The usage of all users in a range of Tehran days: total, by node and the day-by-day series (PasarGuard only; Marzban needs an admin lookup)."""
    return {"ok": True, **_wrap(pg_usage.usage, _cred(panel_id), preset, frm, to)}


@router.get("/panels/{panel_id}/pg/usage/admins")
def usage_admins(panel_id: int):
    """The admin names of the panel, for the lookup box."""
    return {"ok": True, **_wrap(pg_usage.admins, _cred(panel_id))}


@router.get("/panels/{panel_id}/pg/usage/admin/{name}")
def usage_admin(panel_id: int, name: str, preset: Preset = "last7", frm: FromDate = "", to: ToDate = ""):
    """The usage of the users of one admin in a range, in GB and by node (Marzban adds the admin's lifetime total)."""
    return {"ok": True, **_wrap(pg_usage.admin_usage, _cred(panel_id), name, preset, frm, to)}


@router.get("/panels/{panel_id}/pg/usage/user/{username}")
def usage_user(panel_id: int, username: str, preset: Preset = "last7", frm: FromDate = "", to: ToDate = ""):
    """The usage of one user in a range, in GB and by node."""
    return {"ok": True, **_wrap(pg_usage.user_usage, _cred(panel_id), username, preset, frm, to)}


@router.get("/panels/{panel_id}/pg/usage/counts")
def usage_counts(panel_id: int, metric: str = Query("online", pattern=r"^(online|expired|limited)$"), preset: Preset = "last7", frm: FromDate = "", to: ToDate = "", admin: str = Query("", max_length=64)):
    """How many users were online, expired or limited over time in a range of Tehran days (a point per hour up to two days, else per day), for everybody or one admin (PasarGuard only)."""
    return {"ok": True, **_wrap(pg_usage.counts, _cred(panel_id), metric, preset, frm, to, admin)}


@router.get("/panels/{panel_id}/pg/usage/sub-apps")
def usage_sub_apps(panel_id: int, preset: Preset = "last7", frm: FromDate = "", to: ToDate = "", user: str = Query("", max_length=128), admin: str = Query("", max_length=64)):
    """Which apps fetch the subscription: the share of each client over a range, for everybody, one user or one admin (PasarGuard only)."""
    return {"ok": True, **_wrap(pg_usage.sub_apps, _cred(panel_id), preset, frm, to, user, admin)}


@router.get("/panels/{panel_id}/pg/usage/top-users")
def usage_top_users(panel_id: int, sort: str = Query("used_traffic", max_length=20), limit: int = Query(20, ge=1, le=20), admin: str = Query("", max_length=64)):
    """The top users by a sort the panel itself does (used traffic, last online, data limit, creation date): at most 20 rows, never the whole list, never secrets."""
    return {"ok": True, **_wrap(pg_usage.top_users, _cred(panel_id), sort, limit, admin)}
