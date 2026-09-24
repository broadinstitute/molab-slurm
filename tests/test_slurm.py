import pytest

from molab_slurm import slurm


@pytest.mark.parametrize(
    "spec, indices, throttle",
    [
        ("0-3", [0, 1, 2, 3], None),
        ("5-9%1", [5, 6, 7, 8, 9], 1),
        ("0,2,4", [0, 2, 4], None),
        ("1-9:2", [1, 3, 5, 7, 9], None),
        ("7", [7], None),
        ("0-3,10-11%2", [0, 1, 2, 3, 10, 11], 2),
    ],
)
def test_parse_array(spec, indices, throttle):
    a = slurm.parse_array(spec)
    assert a.indices == indices and a.throttle == throttle


@pytest.mark.parametrize("bad", ["3-1", "0-3%0", "", "a-b", "-1"])
def test_parse_array_rejects(bad):
    with pytest.raises(ValueError):
        slurm.parse_array(bad)


def test_compact_is_squeue_notation():
    assert slurm.ArraySpec([5, 6, 7, 9], 1).compact() == "5-7,9%1"
    assert slurm.ArraySpec([0, 1, 2, 3]).compact([2, 3]) == "2-3"


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("30", 1800),  # bare number is minutes
        ("30:15", 1815),
        ("2:00:00", 7200),
        ("1-0", 86400),
        ("1-2:30", 86400 + 2 * 3600 + 1800),
        ("7-00:00:00", 7 * 86400),
        ("UNLIMITED", None),
    ],
)
def test_parse_time(text, seconds):
    assert slurm.parse_time(text) == seconds


def test_format_elapsed():
    assert slurm.format_elapsed(65) == "1:05"
    assert slurm.format_elapsed(3725) == "1:02:05"
    assert slurm.format_elapsed(90061) == "1-01:01:01"


def test_directives_stop_at_first_command_and_later_lines_win():
    lines = [
        "#!/bin/bash",
        "#SBATCH --job-name=first",
        "# an ordinary comment keeps the block open",
        "",
        "#SBATCH -J second --array=0-19  # trailing comment",
        "#SBATCH --mem=100G",
        "echo hi",
        "#SBATCH --time=5:00  (after a command: ignored, as sbatch does)",
    ]
    script = "\n".join(lines)
    d = slurm.parse_directives(script)
    assert d == {"job-name": "second", "array": "0-19", "mem": "100G"}


def test_cli_overrides_directives_and_unknowns_are_ignored_not_errors():
    o = slurm.merge(
        {"array": "0-19", "mem": "128G", "gres": "gpu:1", "time": "1-0"},
        {"array": "5-9", "partition": "gpu"},
    )
    assert o.array.indices == [5, 6, 7, 8, 9]
    assert o.time_limit == 86400
    assert o.ignored == {"mem": "128G", "gres": "gpu:1", "partition": "gpu"}


def test_parse_options_short_long_and_flags():
    o = slurm.parse_options(["-J", "x", "--array=1-2", "-o", "log.%j", "--exclusive", "-c4"])
    assert o == {"job-name": "x", "array": "1-2", "output": "log.%j", "exclusive": "", "cpus-per-task": "4"}


def test_dependency():
    assert slurm.parse_dependency("afterok:12:13,afterany:9") == [
        ("afterok", "12"),
        ("afterok", "13"),
        ("afterany", "9"),
    ]
    with pytest.raises(ValueError):
        slurm.parse_dependency("afterok:1?afterok:2")
    with pytest.raises(ValueError):
        slurm.parse_dependency("singleton")


def test_fill_pattern():
    kw = {"job_id": "12_5", "array_job_id": "12", "task": 5, "name": "bias", "node": "box", "user": "me"}
    assert slurm.fill_pattern("%x_%A_%a.log", **kw) == "bias_12_5.log"
    assert slurm.fill_pattern("%j-%N-%u-100%%", **kw) == "12_5-box-me-100%"
    kw["task"] = None
    assert slurm.fill_pattern("%a", **kw) == "4294967294"


def test_export_env():
    assert slurm.export_env("ALL") == (True, {})
    assert slurm.export_env("NONE,A=1") == (False, {"A": "1"})
    assert slurm.export_env("A=1,B=x=y") == (True, {"A": "1", "B": "x=y"})
    with pytest.raises(ValueError):
        slurm.export_env("PATH")
