"""The workspace model through the API: search, what reaches a page or field, and edits picked up."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.e2e.test_ui import running_app


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_search_reach_and_an_outside_edit(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url:
        status, hits = get(f"{url}api/model/search?q=search")
        assert status == 200
        assert {"kind": "test", "name": "TC-SMOKE-001-Search", "system": "SMOKE",
                "file": "SMOKE/SmokeTests.json"} in hits
        assert {"kind": "field", "name": "searchBox", "system": "SMOKE", "page": "SmokeHome"} in hits
        status, reach = get(f"{url}api/model/reach?system=SMOKE&page=SmokeHome&field=searchBox")
        assert status == 200 and reach["tests"] == ["TC-SMOKE-001-Search"]
        assert reach["steps"] == [{"file": "SMOKE/SmokeTests.json", "owner": "TC-SMOKE-001-Search", "step": 1,
                                   "source": "test"}]
        assert get(f"{url}api/model/reach?system=SMOKE&field=searchBox")[0] == 400

        # an edit made outside the app (a git pull, an editor) shows on the next question
        tests_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "SmokeTests.json"
        data = json.loads(tests_file.read_text(encoding="utf-8"))
        data["testCases"][1]["steps"][0]["formData"]["searchBox"] = "also here"
        time.sleep(0.05)
        tests_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        status, reach = get(f"{url}api/model/reach?system=SMOKE&page=SmokeHome&field=searchBox")
        assert reach["tests"] == ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]
