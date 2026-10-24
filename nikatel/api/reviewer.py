from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..config import get_settings
from ..services import reviewer

router = APIRouter(tags=["reviewer"], dependencies=[Depends(current_user)])


def _writable() -> None:
    if get_settings().read_only:
        raise HTTPException(403, "the panel is read-only")


class ReviewerIn(BaseModel):
    ai: bool | None = None


@router.get("/reviewer")
def reviewer_state():
    """The Reviewer, the teacher of the Doctor: what it found about the repairs that went badly (the panel's own findings, the AI's review, a report to copy), the lessons it proposes and the ones in force, and whether the AI writes the reviews."""
    return {"ok": True, "read_only": get_settings().read_only, **reviewer.state()}


@router.put("/reviewer")
def reviewer_set(body: ReviewerIn):
    """Let the AI write the reviews (when a key is saved in Settings -> Api & Key) or not; the findings of the panel's own rules are written either way."""
    _writable()
    return {"ok": True, **reviewer.configure(body.ai)}


@router.get("/reviewer/reviews/{review_id}/report")
def reviewer_report(review_id: int):
    """The report of one review - what the Doctor did, what it should have done, the findings and the evidence - as plain text to copy for the developer."""
    try:
        return {"ok": True, "report": reviewer.report_of(review_id)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/reviewer/run", status_code=202)
def reviewer_run():
    """Review the newest finished smart repair that has no review yet, now (in the background: the AI may take a minute)."""
    _writable()

    try:
        return {"ok": True, "id": reviewer.review_latest()}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/reviewer/lessons/{lesson_id}/{status}")
def reviewer_lesson(lesson_id: int, status: str):
    """The owner's word on a lesson the Reviewer proposed: `approved` (the Doctor follows it for its days) or `dismissed`. Nothing a lesson says is done before it is approved."""
    _writable()

    try:
        return {"ok": True, "lesson": reviewer.set_lesson(lesson_id, status)}
    except ValueError as e:
        raise HTTPException(400, str(e))
