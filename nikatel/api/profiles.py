from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..auth import current_user
from ..db import session
from ..models import Profile
from ..services import profiles as svc
from ..services import uptime

router = APIRouter(tags=["profiles"], dependencies=[Depends(current_user)])


class ProfilesIn(BaseModel):
    kind: str = Field(max_length=8)                                    # iran | foreign | node
    server_ids: list[int] = Field(default_factory=list, max_length=100)        # iran / foreign: servers of ours that exist
    panel_id: int | None = None                                       # node: the PasarGuard panel ...
    node_ids: list[int] = Field(default_factory=list, max_length=100)          # ... and the nodes of it
    tag: str = Field("", max_length=40)                                # the category it is listed under ("" / "any" = Any)


class TagIn(BaseModel):
    tag: str = Field("", max_length=40)


def _code(e: Exception) -> int:
    return 404 if isinstance(e, LookupError) else 409 if isinstance(e, FileExistsError) else 400


@router.get("/profiles")
def list_profiles():
    """Every profile on the Lab page - an Iran server, a foreign server or a PasarGuard node - with the name and address of what it points at."""
    with session() as db:
        return {"ok": True, "profiles": svc.list_all(db), "tags": svc.all_tags(db), "kinds": [{"key": k, "label": svc.LABEL[k]} for k in svc.KINDS]}


@router.post("/profiles", status_code=201)
def add_profiles(body: ProfilesIn):
    """Add profiles by saying what they are: servers of ours as Iran or foreign, or nodes of a connected PasarGuard panel; one that is on the page already or does not fit the kind is skipped with the reason."""
    try:
        kind = svc.check_kind(body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    ids = body.node_ids if kind == "node" else body.server_ids

    if not ids:
        raise HTTPException(400, "choose at least one " + ("node" if kind == "node" else "server"))

    added, skipped = [], []

    with session() as db:
        for i in dict.fromkeys(ids):
            try:
                p = svc.create(db, kind, server_id=None if kind == "node" else i, panel_id=body.panel_id, node_id=i if kind == "node" else None, tag=body.tag)
                added.append(svc.get_row(db, p.id))
            except (ValueError, LookupError, FileExistsError) as e:
                skipped.append({"id": i, "why": str(e.args[0]) if e.args else str(e), "code": _code(e)})

    if not added:
        raise HTTPException(skipped[0]["code"], skipped[0]["why"])

    return {"ok": True, "added": added, "skipped": skipped}


@router.post("/profiles/tags", status_code=201)
def add_tag(body: TagIn):
    """Make a category (a tag) by hand - the "+" beside the tabs of the Lab; it stays on the page while no profile carries it."""
    try:
        return {"ok": True, "tag": svc.add_tag(body.tag)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.put("/profiles/tags/{tag}")
def rename_tag(tag: str, body: TagIn):
    """Rename a category of the Lab (a profile in the owner's words): the servers that carry it follow."""
    with session() as db:
        try:
            return {"ok": True, "tag": svc.rename_tag(db, tag, body.tag)}
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except FileExistsError as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


@router.delete("/profiles/tags/{tag}")
def delete_tag(tag: str):
    """Delete a category of the Lab: the servers that carried it go to Any; nothing else changes on them."""
    with session() as db:
        try:
            return {"ok": True, "moved": svc.delete_tag(db, tag)}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


@router.put("/profiles/{profile_id}/tag")
def tag_profile(profile_id: int, body: TagIn):
    """Give a profile another tag - the category it is listed under on the Lab page (empty or "any" = Any); nothing changes on what it points at."""
    with session() as db:
        try:
            return {"ok": True, "tag": svc.set_tag(db, profile_id, body.tag)}
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int):
    """Take a profile off the Lab page; the server or node it points at is not touched."""
    with session() as db:
        try:
            name = svc.delete(db, profile_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None

        return {"ok": True, "removed": name}


@router.get("/profiles/strips")
def profile_strips(hours: int = Query(24, ge=1, le=uptime.MAX_HOURS), buckets: int = Query(48, ge=6, le=168)):
    """For the server of every Iran / foreign profile a strip of cells over the last hours (2 answered, 1 now and then, 0 silent, - not added yet) and the share of the time it was heard; read from the samples."""
    with session() as db:
        ids = [i for i in db.scalars(select(Profile.server_id).where(Profile.server_id.is_not(None)))]
        return {"ok": True, "hours": hours, "buckets": buckets, "servers": {str(k): v for k, v in uptime.strips(db, ids, hours, buckets).items()}}
