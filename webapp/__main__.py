"""Start the AutoPlay web app:  python -m webapp   (then open http://127.0.0.1:8080)"""
from __future__ import annotations

import logging

from nicegui import app, ui

from webapp.api import install_api
from webapp.runner_api import router as runner_router
from webapp.auth import install_auth
from webapp.security import install_security
from webapp.state import services
from webapp.ui import shell


def _exit_with_parent() -> None:
    """Run by the control plane: stdin is a pipe from it, so end of input means it is gone; stop as well."""
    import os
    import sys
    import threading

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        logging.getLogger("webapp").warning("The control plane is gone; stopping.")
        os._exit(0)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    svc = services()
    s = svc.settings

    sso_admins = False
    if s.sso_enabled:
        from webapp.auth import sso_problem
        from webapp.services.oidc import RoleMap
        problem = sso_problem(s)
        if problem:
            raise SystemExit(f"Single sign-on is set up wrong: {problem}")
        sso_admins = "admin" in RoleMap.parse(s.oidc_role_map).roles.values()
    if s.login_required and not sso_admins and \
            not any(u.role == "admin" and not u.disabled for u in svc.users.list()):
        raise SystemExit(f"Login is required (AUTOPLAY_AUTH_ENABLED or host {s.host!r}) but there is no admin "
                         f"in {s.resolved_users_file}. Create one with:\n"
                         "  python -m webapp.users add <name> --role admin\n"
                         "or map an identity provider group to admin in AUTOPLAY_OIDC_ROLE_MAP.")
    # Order matters: install_auth's middleware must run after (inside) install_security's host/origin checks
    storage_secret = install_auth(app, s)
    install_security(app, s)
    install_api(app)
    app.include_router(runner_router)
    # Per-run folders: allure-report/index.html, console.log, raw allure-results
    app.add_static_files("/runs", s.resolved_runs_dir, max_cache_age=0)
    shell.register()
    if s.scheduler_enabled:
        svc.scheduler.start()
        svc.monitors.start()
    if s.exit_with_parent:
        _exit_with_parent()

    logging.getLogger("webapp").info(
        "workspace=%s dotenv=%s runs=%s allure=%s",
        s.workspace, s.dotenv_path, s.resolved_runs_dir,
        "found" if svc.runs.allure_available() else f"'{s.allure_cmd}' not found (reports disabled)",
    )
    ui.run(
        host=s.host, port=s.port, title="AutoPlay", reload=False, show=False,
        fastapi_docs=True, show_welcome_message=True, storage_secret=storage_secret,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
