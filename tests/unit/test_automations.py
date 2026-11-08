"""Automations: rules, what sets them off (and what never does), and what the engine does when one fires."""
import pytest

from webapp.services.automations import AutomationEngine, AutomationStore, Rule, matches, validate


def rule(on="deploy", do=None, **when):
    return validate({"id": "r1", "name": "Rule one", "when": {"on": on, **when},
                     "do": do or [{"type": "run_tests", "tags": ["smoke"]}]})


def test_validation_says_what_is_wrong():
    with pytest.raises(ValueError, match="id"):
        validate({"id": "Bad Id", "name": "x", "when": {"on": "deploy"}, "do": [{"type": "run_tests", "tags": ["a"]}]})
    with pytest.raises(ValueError, match="When"):
        validate({"id": "a", "name": "x", "when": {"on": "sometimes"}, "do": [{"type": "run_tests", "tags": ["a"]}]})
    with pytest.raises(ValueError, match="tag"):
        validate({"id": "a", "name": "x", "when": {"on": "deploy"}, "do": [{"type": "run_tests", "tags": []}]})
    with pytest.raises(ValueError, match="channel"):
        validate({"id": "a", "name": "x", "when": {"on": "deploy"}, "do": [{"type": "notify", "channels": []}]})
    with pytest.raises(ValueError, match="at least one action"):
        validate({"id": "a", "name": "x", "when": {"on": "deploy"}, "do": []})
    r = rule("run_finished", status="failed", system="S")
    assert r.when["status"] == "failed"
    assert r.summary == "When a run finishes (failed) on S/any environment: run tests tagged smoke"


def test_what_sets_a_rule_off():
    deploy = {"topic": "deploy", "system": "S", "env": "uat", "build": "482"}
    assert matches(rule("deploy"), deploy)
    assert matches(rule("deploy", system="S", env="uat"), deploy)
    assert not matches(rule("deploy", env="prod"), deploy)
    assert not matches(rule("deploy", system="OTHER"), deploy)

    finished = {"topic": "run", "change": "finished", "status": "failed", "system": "S", "env": "qa", "trigger": ""}
    assert matches(rule("run_finished", status="failed"), finished)
    assert matches(rule("run_finished", status="failed"), {**finished, "status": "error"})
    assert not matches(rule("run_finished", status="passed"), finished)
    assert matches(rule("run_finished"), {**finished, "status": "passed"})
    assert not matches(rule("run_finished"), {**finished, "change": "saved"})
    # a run an automation started never sets a rule off: no loops
    assert not matches(rule("run_finished"), {**finished, "trigger": "automation:r1"})

    checked = {"topic": "monitor", "change": "checked", "system": "S", "env": "prod",
               "changes": [{"journey": "renew", "state": "down", "detail": "Payment > pay"}]}
    assert matches(rule("journey_down"), checked)
    assert not matches(rule("journey_recovered"), checked)
    assert matches(rule("journey_recovered"), {**checked, "changes": [{"journey": "renew", "state": "recovered"}]})

    off = rule("deploy")
    off.enabled = False
    assert not matches(off, deploy)


def test_store_round_trip(tmp_path):
    store = AutomationStore(tmp_path)
    assert store.list() == []
    store.save(rule("deploy"), new=True)
    with pytest.raises(Exception, match="exists"):
        store.save(rule("deploy"), new=True)
    changed = store.get("r1")
    changed.name = "Smoke after deploys"
    store.save(changed)
    assert [r.name for r in store.list()] == ["Smoke after deploys"]
    assert store.delete("r1") and not store.delete("r1") and store.list() == []


def test_the_engine_does_each_action_with_the_events_system_and_env(tmp_path):
    started, notified = [], []
    store = AutomationStore(tmp_path)
    store.save(validate({"id": "after-deploy", "name": "After deploy", "when": {"on": "deploy", "system": "S"},
                         "do": [{"type": "run_tests", "tags": ["smoke"]},
                                {"type": "run_journeys", "journeys": ["renew"], "env": "prod"},
                                {"type": "notify", "channels": [{"type": "webhook"}]}]}))
    store.save(validate({"id": "other", "name": "Other system", "when": {"on": "deploy", "system": "T"},
                         "do": [{"type": "run_tests", "tags": ["x"]}]}))

    def start_tests(system, env, tags, trigger):
        started.append(("tests", system, env, tags, trigger))
        return type("Run", (), {"id": "run-1"})()

    def start_journeys(system, ids, env, trigger):
        raise RuntimeError("the queue is full")

    def notify(r, event, channels):
        notified.append((r.id, event["build"], [c["type"] for c in channels]))
        return [{"channel": "webhook", "ok": True, "error": ""}]

    engine = AutomationEngine(store, tmp_path / "automations.jsonl", start_tests=start_tests,
                              start_journeys=start_journeys, notify=notify)
    engine.on_event({"topic": "deploy", "system": "S", "env": "uat", "build": "482", "by": "ci"})
    engine.on_event({"topic": "run", "change": "saved"})  # not a finished run: ignored
    assert engine.wait_idle()
    assert started == [("tests", "S", "uat", ["smoke"], "automation:after-deploy")]
    assert notified == [("after-deploy", "482", ["webhook"])]
    [entry] = engine.recent()
    assert entry["rule"] == "after-deploy" and entry["event"]["build"] == "482"
    assert [(r["action"], r["ok"]) for r in entry["results"]] == [("run_tests", True), ("run_journeys", False),
                                                                  ("notify", True)]
    assert entry["results"][1]["error"] == "the queue is full"
    assert engine.recent(rule="other") == []


def test_rules_start_off_enabled_and_keep_their_order():
    r = Rule("a", "A", {"on": "deploy"}, [{"type": "run_tests", "tags": ["t"]}])
    assert validate(r.to_dict()).enabled
