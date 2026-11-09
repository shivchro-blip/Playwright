"""Deploy-aware releases: run on deploy, a release check from that run, GO / NO-GO reported, monitors on GO."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from webapp.services.deploys import DeployError, DeployFlow, DeployStore, check_request


class Fakes:
    def __init__(self, decision="go", token="ghp_x", fail_start=None):
        self.decision, self.token, self.fail_start = decision, token, fail_start
        self.started, self.posts, self.published, self.monitors = [], [], [], []

    def start_journeys(self, system, ids, env, trigger):
        if self.fail_start:
            raise ValueError(self.fail_start)
        self.started.append((system, ids, env, trigger))
        return SimpleNamespace(id="run-1")

    def make_release(self, deploy):
        return {"id": "rel-1", "decision": self.decision,
                "reasons": [] if self.decision == "go" else ["Payment failed on uat."]}

    def post(self, url, payload, headers):
        self.posts.append((url, payload, headers))
        return 201, "ok"

    def lookup(self, key):
        return {"RELEASE_GITHUB_TOKEN": self.token}.get(key) if self.token else None

    def check_monitors(self, system, env):
        self.monitors.append((system, env))
        return ["mon-1"]


def flow(tmp_path, fakes):
    return DeployFlow(DeployStore(tmp_path / "deploys.json"), start_journeys=fakes.start_journeys,
                      make_release=fakes.make_release, report_url=lambda pack: f"https://app/r/{pack['id']}",
                      post=fakes.post, lookup=fakes.lookup, check_monitors=fakes.check_monitors,
                      publish=lambda *a, **k: fakes.published.append((a, k)))


def test_a_deploy_runs_decides_reports_and_starts_monitors(tmp_path):
    f = Fakes()
    df = flow(tmp_path, f)
    d = df.start("PORTAL", "uat", build="482", by="ci", repo="acme/portal", sha="abc1234",
                 callback_url="https://ci.example/hook")
    assert d.status == "running" and d.run_id == "run-1"
    assert f.started == [("PORTAL", [], "uat", f"deploy:{d.id}")]
    assert f.published[0][1]["build"] == "482"  # automation rules waiting for a deploy still fire
    url, body, headers = f.posts[0]
    assert url == "https://api.github.com/repos/acme/portal/statuses/abc1234" and body["state"] == "pending"
    assert headers["Authorization"] == "Bearer ghp_x"

    df.on_event({"topic": "run", "change": "finished", "trigger": "manual"})  # not a deploy's run: ignored
    done = df.finish(d.id)
    assert done.status == "decided" and done.decision == "go" and done.release_id == "rel-1"
    status = next(p for p in f.posts if p[1].get("state") == "success")[1]
    assert status["description"] == "GO" and status["target_url"] == "https://app/r/rel-1"
    callback = next(p for p in f.posts if p[0] == "https://ci.example/hook")[1]
    assert callback["decision"] == "go" and callback["report"] == "https://app/r/rel-1" and callback["run"] == "run-1"
    assert done.monitors == ["mon-1"] and f.monitors == [("PORTAL", "uat")]
    assert all(r["ok"] for r in done.reported) and len(done.reported) == 3
    assert df.finish(d.id).decision == "go" and len(f.posts) == 3  # decided once
    assert df.store.list()[0].id == d.id


def test_no_go_reports_failure_and_leaves_monitors_alone(tmp_path):
    f = Fakes(decision="no-go")
    df = flow(tmp_path, f)
    d = df.finish(df.start("PORTAL", "uat", repo="acme/portal", sha="abc1234").id)
    failure = next(p for p in f.posts if p[1].get("state") == "failure")[1]
    assert failure["description"] == "NO-GO: Payment failed on uat." and d.monitors == [] and f.monitors == []


def test_a_deploy_that_cannot_start_says_why_everywhere(tmp_path):
    f = Fakes(fail_start="PORTAL has no journeys.")
    df = flow(tmp_path, f)
    with pytest.raises(DeployError, match="no journeys"):
        df.start("PORTAL", "uat", repo="acme/portal", sha="abc1234", callback_url="https://ci.example/hook")
    (d,) = df.store.list()
    assert d.status == "failed" and "no journeys" in d.error
    assert [p[1].get("state") for p in f.posts if "statuses" in p[0]] == ["pending", "error"]
    assert next(p for p in f.posts if p[0] == "https://ci.example/hook")[1]["status"] == "failed"


def test_without_a_token_the_status_is_skipped_and_said(tmp_path):
    f = Fakes(token="")
    df = flow(tmp_path, f)
    d = df.finish(df.start("PORTAL", "uat", repo="acme/portal", sha="abc1234").id)
    assert f.posts == [] and d.reported and not d.reported[0]["ok"]
    assert "RELEASE_GITHUB_TOKEN" in d.reported[0]["detail"]


@pytest.mark.parametrize("kwargs, message", [
    ({"callback_url": "ftp://x"}, "callback_url"), ({"repo": "not a repo", "sha": "abc1234"}, "owner/repo"),
    ({"repo": "a/b", "sha": "zzz"}, "commit hash"), ({"repo": "a/b"}, "both repo and sha"),
])
def test_requests_are_checked(kwargs, message):
    with pytest.raises(DeployError, match=message):
        check_request("S", "uat", **kwargs)
