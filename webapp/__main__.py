"""Start the AutomateNY web app:  python -m webapp   (then open http://127.0.0.1:8080)"""
from __future__ import annotations

import logging

from nicegui import app, ui

from webapp.api import router
from webapp.state import services
from webapp.ui import shell


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    svc = services()
    s = svc.settings

    app.include_router(router)
    # Per-run folders: allure-report/index.html, console.log, raw allure-results
    app.add_static_files("/runs", s.resolved_runs_dir, max_cache_age=0)
    shell.register()

    logging.getLogger("webapp").info(
        "workspace=%s dotenv=%s runs=%s allure=%s",
        s.workspace, s.dotenv_path, s.resolved_runs_dir,
        "found" if svc.runs.allure_available() else f"'{s.allure_cmd}' not found (reports disabled)",
    )
    ui.run(
        host=s.host, port=s.port, title="AutomateNY", reload=False, show=False,
        fastapi_docs=True, show_welcome_message=True,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
