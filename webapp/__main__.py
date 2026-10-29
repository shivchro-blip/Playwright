"""Start the AutomateNY web app:  python -m webapp   (then open http://127.0.0.1:8080)"""
from __future__ import annotations

import logging

from nicegui import app, ui

from webapp.api import router
from webapp.auth import install_auth
from webapp.security import install_security
from webapp.state import services
from webapp.ui import shell


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    svc = services()
    s = svc.settings

    if s.login_required and not any(u.role == "admin" and not u.disabled for u in svc.users.list()):
        raise SystemExit(f"Login is required (AUTOMATENY_AUTH_ENABLED or host {s.host!r}) but there is no admin "
                         f"in {s.resolved_users_file}. Create one with:\n"
                         "  python -m webapp.users add <name> --role admin")
    # Order matters: install_auth's middleware must run after (inside) install_security's host/origin checks
    storage_secret = install_auth(app, s)
    install_security(app, s)
    app.include_router(router)
    # Per-run folders: allure-report/index.html, console.log, raw allure-results
    app.add_static_files("/runs", s.resolved_runs_dir, max_cache_age=0)
    shell.register()
    if s.scheduler_enabled:
        svc.scheduler.start()

    logging.getLogger("webapp").info(
        "workspace=%s dotenv=%s runs=%s allure=%s",
        s.workspace, s.dotenv_path, s.resolved_runs_dir,
        "found" if svc.runs.allure_available() else f"'{s.allure_cmd}' not found (reports disabled)",
    )
    ui.run(
        host=s.host, port=s.port, title="AutomateNY", reload=False, show=False,
        fastapi_docs=True, show_welcome_message=True, storage_secret=storage_secret,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
