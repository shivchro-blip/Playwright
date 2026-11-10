"""Which edition of AutoPlay this is.

The full app has every section. AutoPlay Lite (built by tools/build_lite.py into the AutomationLite folder) is for
people who only run tests that someone else wrote: Control (start a run, watch it, open its report and Allure),
History, and the Generator without the Recorder and the Studio.

The build script writes `LITE = True` here in its copy; in the source tree, set AUTOPLAY_EDITION=lite to try the
Lite screens without building.
"""
from __future__ import annotations

import os

# tools/build_lite.py replaces the next line with LITE = True
LITE = os.environ.get("AUTOPLAY_EDITION", "").strip().lower() == "lite"
NAME = "AutoPlay Lite" if LITE else "AutoPlay"
LITE_TABS = ("Control", "Generator", "History")
