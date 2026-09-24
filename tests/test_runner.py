"""The box-side runner, run for real on this machine: it is stdlib Python and
falls back to `ps` where there is no /proc. Each test submits a job the way
box.SUBMIT would (a job directory with job.json) and runs the runner on it."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "src" / "molab_slurm" / "runner.py"


def make_job(root: Path, jid: int, script: str, **spec) -> Path:
    d = root / "jobs" / str(jid)
    (d / "tasks").mkdir(parents=True)
    path = d / "job.sh"
    path.write_text("#!/bin/bash\n" + script + "\n")
    job = {
        "id": jid,
        "name": "t",
        "chdir": str(d),
        "script": str(path),
        "args": [],
        "rc": None,
        "env_inherit": True,
        "env_extra": {},
        "tasks": None,
        "throttle": None,
        "time_limit": None,
        "dependency": [],
        "cpus": 2,
        "output": "out-%a.log",
        "error": None,
    }
    job.update(spec)
    (d / "job.json").write_text(json.dumps(job))
    return d


def start(d: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(RUNNER), str(d)])


def task(d: Path, key="-") -> dict:
    return json.loads((d / "tasks" / f"{key}.json").read_text())


def wait_state(d: Path, key: str, states: set, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        try:
            t = task(d, key)
            if t.get("state") in states:
                return t
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"task {key} never reached {states}: {task(d, key)}")


def output(d: Path, idx=None) -> str:
    return (d / f"out-{4294967294 if idx is None else idx}.log").read_text()


def test_completed_job_gets_slurm_env_and_exit_code(tmp_path):
    d = make_job(tmp_path, 1, 'echo "$SLURM_JOB_ID $SLURM_CPUS_PER_TASK $SLURM_SUBMIT_DIR"')
    assert start(d).wait(20) == 0
    t = task(d)
    assert t["state"] == "COMPLETED" and t["exit_code"] == "0:0"
    assert output(d).split() == ["1", "2", str(d)]


def test_nonzero_exit_is_failed(tmp_path):
    d = make_job(tmp_path, 1, "echo boom >&2; exit 3")
    start(d).wait(20)
    t = task(d)
    assert (t["state"], t["exit_code"]) == ("FAILED", "3:0")
    assert "boom" in output(d)  # stderr merged into the output file by default


def test_array_throttle_1_runs_in_sequence_with_array_env(tmp_path):
    d = make_job(
        tmp_path, 1, 'echo "$SLURM_ARRAY_TASK_ID $SLURM_ARRAY_TASK_COUNT"; date +%s.%N; sleep 0.5; date +%s.%N',
        tasks=[5, 6, 7], throttle=1,
    )  # fmt: skip
    start(d).wait(30)
    spans = []
    for i in (5, 6, 7):
        assert task(d, str(i))["state"] == "COMPLETED"
        first, t0, t1 = output(d, i).split("\n")[:3]
        assert first == f"{i} 3"
        spans.append((float(t0), float(t1)))
    assert all(spans[k][1] <= spans[k + 1][0] + 0.05 for k in range(2)), spans  # no overlap


def test_cancel_stops_the_whole_tree_and_records_cancelled(tmp_path):
    # The case that broke smolab: a shell whose child is killed first carries on.
    d = make_job(tmp_path, 1, "echo started; sleep 120; echo never")
    proc = start(d)
    wait_state(d, "-", {"RUNNING"})
    time.sleep(0.5)
    (d / "cancel").touch()
    assert proc.wait(30) == 0
    t = task(d)
    assert t["state"] == "CANCELLED"
    assert "never" not in output(d)


def test_cancel_one_array_task_leaves_the_rest(tmp_path):
    d = make_job(tmp_path, 1, "sleep 1; echo ok", tasks=[0, 1, 2], throttle=1)
    (d / "cancel.1").touch()  # cancelled while still pending
    start(d).wait(30)
    assert [task(d, str(i))["state"] for i in (0, 1, 2)] == ["COMPLETED", "CANCELLED", "COMPLETED"]


def test_time_limit_is_timeout(tmp_path):
    d = make_job(tmp_path, 1, "sleep 60", time_limit=1)
    start(d).wait(30)
    assert task(d)["state"] == "TIMEOUT"


def test_afterok_waits_then_runs(tmp_path):
    a = make_job(tmp_path, 1, "sleep 1; echo a")
    b = make_job(tmp_path, 2, "echo b", dependency=[["afterok", "1"]])
    pb = start(b)
    wait_state(b, "-", {"PENDING"})
    assert task(b)["reason"] == "Dependency"
    start(a).wait(20)
    pb.wait(20)
    assert task(b)["state"] == "COMPLETED"
    assert task(b)["start"] >= task(a)["end"]


def test_afterok_on_a_failed_job_never_runs(tmp_path):
    a = make_job(tmp_path, 1, "exit 1")
    b = make_job(tmp_path, 2, "echo should-not-run", dependency=[["afterok", "1"]])
    start(a).wait(20)
    start(b).wait(20)
    t = task(b)
    assert t["state"] == "CANCELLED" and "DependencyNeverSatisfied" in t["reason"]
    assert not (b / "out-4294967294.log").exists()


def test_rc_file_is_sourced_first(tmp_path):
    rc = tmp_path / "env.sh"
    rc.write_text("export FROM_RC=yes\n")
    d = make_job(tmp_path, 1, 'echo "rc=$FROM_RC"', rc=str(rc))
    start(d).wait(20)
    assert output(d).strip() == "rc=yes"


def test_export_none_and_extra_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOULD_NOT_LEAK", "1")
    d = make_job(tmp_path, 1, 'echo "[${SHOULD_NOT_LEAK:-}] [$EXTRA]"', env_inherit=False, env_extra={"EXTRA": "x"})
    start(d).wait(20)
    assert output(d).strip() == "[] [x]"


@pytest.mark.skipif(sys.platform == "darwin", reason="SIGTERM to the runner is covered on Linux boxes")
def test_runner_terminated_cancels_running_task(tmp_path):
    d = make_job(tmp_path, 1, "sleep 120")
    proc = start(d)
    wait_state(d, "-", {"RUNNING"})
    proc.terminate()
    proc.wait(30)
    assert task(d)["state"] == "CANCELLED"
