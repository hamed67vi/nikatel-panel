"""Costs: the prices the owner types and what they add up to in a month (services/costs.py). Read only toward the servers and the panels."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import current_user
from ..services import costs
from ..services.panels import PanelError

router = APIRouter(tags=["costs"], dependencies=[Depends(current_user)])

MonthQ = Annotated[str, Query(pattern=r"^(\d{4}-\d{2})?$", description="1405-08 (Jalali) or 2026-10 (Gregorian); empty = this month")]


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except (PanelError, RuntimeError) as e:
        raise HTTPException(502, str(e)) from None


class ItemIn(BaseModel):
    name: str = Field(..., max_length=60)
    group: str = "other"
    kind: str
    amount: float = Field(..., ge=0)
    cur: str = "IRT"
    tax: float = Field(0, ge=0, le=100)
    server_id: int = 0
    node: str = Field("", max_length=60)
    included: float = Field(0, ge=0)
    dirs: str = "out"
    quota: float = Field(0, ge=0)
    starts: str = Field("", max_length=10)
    ends: str = Field("", max_length=10)
    months: int = Field(1, ge=1, le=120)
    note: str = Field("", max_length=200)


class SettingsIn(BaseModel):
    rates: dict[str, float] | None = None
    calendar: str | None = None
    bot_server_id: int | None = Field(None, ge=0)
    rates_auto: bool | None = None


@router.get("/costs")
def costs_get():
    """The typed prices, the rates, the calendar, the history of the finished months and what the form needs (servers, nodes, groups)."""
    return {"ok": True, **costs.get_state(), **costs.lists(), "history": costs.history()}


@router.put("/costs/settings")
def costs_settings(body: SettingsIn):
    """The rates (toman per dollar / euro / lira), whether the panel reads them from chand.online by itself, the calendar of a month (jalali or gregorian) and the inventory server that runs the shop bot."""
    return {"ok": True, **_wrap(costs.save_settings, body.rates, body.calendar, body.bot_server_id, body.rates_auto)}


@router.post("/costs/rates/fetch")
def costs_rates_fetch():
    """Today's dollar, euro and lira in toman from chand.online (a read of one public page), saved as the rates."""
    return {"ok": True, **_wrap(costs.fetch_rates)}


@router.post("/costs/items")
def costs_add(body: ItemIn):
    """Add one price: the rent of a server, a price per TB or GB of traffic, a domain per year, or a single invoice."""
    return {"ok": True, "item": _wrap(costs.add_item, body.model_dump())}


@router.put("/costs/items/{item_id}")
def costs_update(item_id: int, body: ItemIn):
    """Change one price; the months already saved keep the numbers they had."""
    return {"ok": True, "item": _wrap(costs.update_item, item_id, body.model_dump())}


@router.delete("/costs/items/{item_id}")
def costs_delete(item_id: int):
    """Remove one price; the months already saved keep the numbers they had."""
    _wrap(costs.delete_item, item_id)
    return {"ok": True}


@router.get("/costs/summary")
def costs_summary(month: MonthQ = "", recompute: bool = False):
    """What the month costs so far and in all, per group, per node and per GB. A finished month comes from the history (`recompute` makes it again from the prices as they are now)."""
    return {"ok": True, **_wrap(costs.summary, month, recompute)}
