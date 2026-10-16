"""GET /api/setup: the first-run checklist of the Dashboard; POST /api/setup/dismiss and /api/setup/reopen hide it and bring it back."""
from fastapi import APIRouter, Depends

from ..auth import current_user
from ..services import setup

router = APIRouter(tags=["setup"], dependencies=[Depends(current_user)])


@router.get("/setup")
def setup_state():
    """What the panel still needs from its owner (panel, Telegram, AI key, DNS account, servers, probe, backup passphrase, two-step sign-in) and what is done."""
    return setup.state()


@router.post("/setup/dismiss")
def setup_dismiss():
    """Hide the checklist (nothing is switched off; it is only not shown)."""
    return setup.dismiss(True)


@router.post("/setup/reopen")
def setup_reopen():
    """Show the checklist again."""
    return setup.dismiss(False)