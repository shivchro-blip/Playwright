"""Actions on a run that more than one screen offers, so they mean the same thing everywhere."""
from __future__ import annotations

from nicegui import ui

from webapp.auth import audit, guard
from webapp.state import services


def cancel_run(run_id: str) -> bool:
    """Cancel a run (stops its browsers too) if this person may run tests there; refusals are shown and audited,
    a cancel is audited. Used by Control and by the Generator's run panel."""
    svc = services()
    run = svc.runs.get(run_id) if run_id else None
    if run is None:
        return False
    if not guard("run", "cancel run", run_id, system=run.config.system, env=run.config.env):
        return False
    if svc.runs.cancel(run_id):
        audit("run.cancel", run_id)
        ui.notify("Cancelling run...", type="info")
        return True
    return False
