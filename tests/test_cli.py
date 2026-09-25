import pytest

from molab_slurm import cli, config


class FakeCtx:
    """A box whose job goes through a scripted list of task states, one per status call."""

    def __init__(self, *states, exit_code=None):
        self.states, self.exit_code, self.calls = list(states), exit_code, 0
        self.box = {"root": "/r"}

    def jobs(self, ids=None, **_):
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        t = {"index": None, "state": state, "reason": "None"}
        if state in cli.FINAL:
            t["exit_code"] = self.exit_code or ("0:0" if state == "COMPLETED" else "1:0")
        return [{"id": ids[0], "is_array": False, "tasks": [t]}]


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(cli.time, "sleep", slept.append)
    return slept


@pytest.mark.parametrize(
    "state, code, rc",
    [("COMPLETED", "0:0", 0), ("FAILED", "2:0", 2), ("CANCELLED", "0:15", 143), ("TIMEOUT", "0:0", 1)],
)
def test_task_rc(state, code, rc):
    assert cli.task_rc({"state": state, "exit_code": code}) == rc


def test_wait_makes_one_status_call_per_interval(sleeps, capsys):
    ctx = FakeCtx("PENDING", "RUNNING", "RUNNING", "FAILED", exit_code="3:0")
    assert cli.wait(ctx, 12, None, 240) == 3
    assert ctx.calls == 4 and sleeps == [240, 240, 240]
    assert capsys.readouterr().out == "12 FAILED 3:0\n"


def test_wait_command_sleeps_first_and_refuses_short_intervals(sleeps):
    ctx = FakeCtx("RUNNING", "COMPLETED")
    assert cli.cmd_wait(ctx, ["12", "--after", "2h", "--every", "5m", "-q"]) == 0
    assert sleeps == [7200, 300]
    with pytest.raises(SystemExit):
        cli.cmd_wait(ctx, ["12", "--every", "10"])


def test_follow_backs_off(sleeps, monkeypatch):
    ctx = FakeCtx(*["RUNNING"] * 30, "COMPLETED")
    assert cli.follow(ctx, 1, None) == 0
    assert sleeps[0] == cli.POLL_MIN_S and max(sleeps) == cli.POLL_MAX_S
    assert sleeps == sorted(sleeps)


def test_config_reads_the_old_path_until_saved(tmp_path, monkeypatch):
    for var in ("MOLAB_URL", "MOLAB_BOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "CONFIG", tmp_path / "molab-slurm" / "config.json")
    monkeypatch.setattr(config, "OLD_CONFIG", tmp_path / "molab" / "config.json")
    config.OLD_CONFIG.parent.mkdir()
    config.OLD_CONFIG.write_text('{"default": "gpu", "boxes": {"gpu": {"url": "u", "token": "t"}}}')
    assert config.resolve()[0] == "gpu"
    config.add_box("cpu", "u2", "t2", make_default=False)
    assert sorted(config.load()["boxes"]) == ["cpu", "gpu"]
    assert config.CONFIG.exists()


class SubmitCtx(FakeCtx):
    """FakeCtx that also accepts a submission, as job 7."""

    name = "gpu"

    def __init__(self, *states, **kw):
        super().__init__(*states, **kw)
        self.box = {"root": "/r", "workdir": "/w", "cpus": 4}
        self.submitted = []

    def call(self, body, **params):
        assert body is cli.box.SUBMIT
        self.submitted.append(params["spec"])
        return {"id": 7, "dir": "/r/jobs/7"}


@pytest.mark.parametrize("flag", ["--wait", "-W"])
def test_sbatch_wait_blocks_and_returns_the_jobs_code(flag, sleeps, capsys):
    ctx = SubmitCtx("RUNNING", "FAILED", exit_code="4:0")
    assert cli.cmd_sbatch(ctx, [flag, "--wrap", "false"]) == 4
    assert ctx.calls == 2 and sleeps == [cli.WAIT_EVERY_S]
    assert "wait" not in ctx.submitted[0]["ignored"]  # handled, not reported as ignored
    assert capsys.readouterr().out == "Submitted batch job 7\n7 FAILED 4:0\n"


def test_sbatch_parsable_wait_prints_only_the_id(sleeps, capsys):
    ctx = SubmitCtx("COMPLETED")
    assert cli.cmd_sbatch(ctx, ["--parsable", "--wait", "--wrap", "true"]) == 0
    assert capsys.readouterr().out == "7\n"


def test_sbatch_without_wait_returns_at_once(sleeps):
    ctx = SubmitCtx("RUNNING")
    assert cli.cmd_sbatch(ctx, ["--wrap", "true"]) == 0
    assert ctx.calls == 0 and sleeps == []


def test_srun_rejects_wait():
    with pytest.raises(SystemExit) as e:
        cli.cmd_srun(SubmitCtx("RUNNING"), ["-W", "30", "python", "train.py"])
    assert e.value.code == 2
