"""Clear all: deletes finished runs and the shared files runs write, and keeps everything else."""
from __future__ import annotations

import shutil
from pathlib import Path

from webapp.services import cleanup
from webapp.services.run_manager import Run, RunConfig, RunManager


def make_run(manager: RunManager, run_id: str, status: str) -> Run:
    run = Run(id=run_id, config=RunConfig(system="S", env="qa", tags=["t"]), dir=manager.runs_dir / run_id,
              status=status)
    (run.dir / "allure-results").mkdir(parents=True)
    (run.dir / "console.log").write_text("line\n", encoding="utf-8")
    (run.dir / "allure-results" / "a.json").write_text("{}", encoding="utf-8")
    manager._runs[run.id] = run
    manager._save(run)
    return run


def setup(tmp_path: Path):
    repo, workspace = tmp_path / "repo", tmp_path / "ws"
    for p in (repo, workspace / "logs", workspace / "reports", workspace / "Data" / "Documents",
              repo / "logs", repo / "reports" / "videos" / "raw", repo / "reports" / "downloads",
              repo / "reports" / "screenshots", repo / "reports" / "traces"):
        p.mkdir(parents=True)
    (workspace / "logs" / "automation.log").write_text("log", encoding="utf-8")
    (workspace / "logs" / "automation.log.1").write_text("log", encoding="utf-8")
    (workspace / "logs" / "keep-me.txt").write_text("x", encoding="utf-8")
    (repo / "logs" / "context_log_TC-1_abc.json").write_text("{}", encoding="utf-8")
    (repo / "reports" / "videos" / "raw" / "v.webm").write_bytes(b"0" * 10)
    (repo / "reports" / "downloads" / "f.pdf").write_bytes(b"0" * 10)
    (repo / "reports" / "screenshots" / "s.png").write_bytes(b"0")
    (repo / "reports" / "other.html").write_text("keep", encoding="utf-8")
    keep = {workspace / "reports" / n: n for n in ("users.json", "audit.jsonl", "runners.json")}
    for path in keep:
        path.write_text("secret", encoding="utf-8")
    (workspace / "Data" / "Documents" / "input.pdf").write_text("input", encoding="utf-8")
    (workspace / ".env").write_text("PASSWORD=x", encoding="utf-8")
    manager = RunManager(repo_root=repo, workspace=workspace, runs_dir=workspace / "reports" / "runs",
                         python_exe="python")
    return repo, workspace, manager, list(keep) + [workspace / "Data" / "Documents" / "input.pdf",
                                                   workspace / ".env", repo / "reports" / "other.html",
                                                   workspace / "logs" / "keep-me.txt"]


def test_clear_all_deletes_previous_run_data_and_keeps_everything_else(tmp_path):
    repo, workspace, manager, keep = setup(tmp_path)
    a = make_run(manager, "20260101-100000-aaaa", "passed")
    b = make_run(manager, "20260101-110000-bbbb", "failed")
    orphan = manager.runs_dir / "20260101-090000-cccc"
    (orphan / "allure-results").mkdir(parents=True)
    stranger = manager.runs_dir / "my-notes"  # not one of ours: never touched
    stranger.mkdir()
    (stranger / "n.txt").write_text("mine", encoding="utf-8")

    found = cleanup.preview(manager, repo, workspace)
    assert (found.finished, found.orphans, found.active) == (2, 1, 0)
    assert {what for what, _p, _s in found.shared} == {"Suite log", "Test context log", "Recorded videos",
                                                      "Downloaded files", "Failure screenshots", "Failure traces"}
    assert a.dir.exists()  # a preview deletes nothing

    heard = []
    manager.bus.subscribe(heard.append, ["run"])
    report = cleanup.clear_all(manager, repo, workspace)

    assert (report.runs, report.orphans, report.failures) == (2, 1, [])
    assert not a.dir.exists() and not b.dir.exists() and not orphan.exists()
    assert manager.list() == [] and manager.get(a.id) is None
    assert [e["change"] for e in heard].count("deleted") == 2
    assert not list((workspace / "logs").glob("automation.log*"))
    assert not (repo / "logs" / "context_log_TC-1_abc.json").exists()
    for name in ("videos", "downloads", "screenshots", "traces"):
        assert not (repo / "reports" / name).exists(), name
    assert report.bytes_freed > 0
    assert stranger.exists()
    for path in keep:
        assert path.exists(), path
    assert manager.index.runs() == []  # the results index forgot them too
    again = RunManager(repo_root=repo, workspace=workspace, runs_dir=manager.runs_dir, python_exe="python")
    assert again.list() == []  # and a restart does not bring them back
    assert cleanup.preview(manager, repo, workspace).empty


def test_an_active_run_and_the_shared_files_it_writes_are_left_alone(tmp_path):
    repo, workspace, manager, keep = setup(tmp_path)
    done = make_run(manager, "20260101-100000-aaaa", "passed")
    live = make_run(manager, "20260101-120000-dddd", "running")
    queued = make_run(manager, "20260101-130000-eeee", "queued")

    found = cleanup.preview(manager, repo, workspace)
    assert (found.finished, found.active, found.shared_skipped) == (1, 2, True)
    report = cleanup.clear_all(manager, repo, workspace)

    assert report.runs == 1 and report.kept_active == 2 and report.shared_skipped
    assert not done.dir.exists() and live.dir.exists() and queued.dir.exists()
    assert {r.id for r in manager.list()} == {queued.id, live.id}
    assert (workspace / "logs" / "automation.log").exists()
    assert (repo / "reports" / "videos").exists()
    for path in keep:
        assert path.exists(), path


def test_what_cannot_be_deleted_is_reported_and_the_run_stays_listed(tmp_path, monkeypatch):
    repo, workspace, manager, _keep = setup(tmp_path)
    stuck = make_run(manager, "20260101-100000-aaaa", "passed")
    fine = make_run(manager, "20260101-110000-bbbb", "passed")
    real = cleanup.remove_tree

    def locked(path: Path):
        if path.name == stuck.id:  # as on Windows when something still has the file open
            return [(str(path / "console.log"), "Permission denied")]
        return real(path)

    monkeypatch.setattr("webapp.services.run_manager.remove_tree", locked)
    report = cleanup.clear_all(manager, repo, workspace)
    assert report.runs == 1 and not fine.dir.exists()
    assert stuck.dir.exists() and manager.get(stuck.id) is stuck  # still on disk, so still in the history
    assert report.failures == [(str(stuck.dir / "console.log"), "Permission denied")]


def test_a_folder_that_only_looks_like_a_run_is_not_deleted_and_links_are_not_followed(tmp_path):
    repo, workspace, manager, _keep = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("x", encoding="utf-8")
    shutil.rmtree(repo / "reports" / "videos")
    (repo / "reports" / "videos").symlink_to(outside, target_is_directory=True)
    empty_lookalike = manager.runs_dir / "20260101-090000-ffff"  # right name, none of a run's files
    empty_lookalike.mkdir()
    cleanup.clear_all(manager, repo, workspace)
    assert empty_lookalike.exists()
    assert (outside / "precious.txt").exists() and not (repo / "reports" / "videos").exists()
