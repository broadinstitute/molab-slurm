"""`molab` -- SLURM's commands for a molab box you cannot ssh into.

    molab init https://sb-....molab.run/ TOKEN --name gpu
    molab sbatch --array=5-9 -D /marimo/repo workflows/SLURM/03.0.train_bias_model.sh
    molab squeue | molab sacct -j 12 | molab scancel 12_7 | molab tail -f 12_5
    molab srun -D /marimo/repo nvidia-smi
    molab sinfo | molab put | molab get | molab open | molab keepalive

See README.md / docs/ for the model: every verb is a short request to the
notebook kernel; jobs run under a detached runner on the box.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import shlex
import signal
import stat
import sys
import time
from pathlib import Path

from molab_slurm import __version__, box, config, slurm
from molab_slurm.remote import Remote, RemoteError, snippet

FINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
ST = {"PENDING": "PD", "RUNNING": "R", "COMPLETED": "CD", "FAILED": "F", "CANCELLED": "CA",
      "TIMEOUT": "TO", "NODE_FAIL": "NF", "OUT_OF_MEMORY": "OOM"}  # fmt: skip
MAX_TRANSFER = 64 << 20  # put/get travel as base64 inside JSON: small files only
# Bytes per kernel reply. The kernel API cuts one stdout reply off at ~1 MB
# (measured: 700 KB of data = 933 KB of base64 arrives, 768 KB does not), so
# every transfer -- tail, get, put -- moves 512 KB at a time.
CHUNK = 512 << 10
POLL_S = 1.0
RUNNER_SRC = Path(__file__).with_name("runner.py").read_text()


def die(msg: str, code: int = 1):
    print(f"molab: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str):
    print(f"molab: {msg}", file=sys.stderr)


class Ctx:
    """The chosen box, connected lazily."""

    def __init__(self, box_name: str | None, session: str | None):
        self.name, self.box = config.resolve(box_name)
        self._session = session
        self._remote = None

    @property
    def remote(self) -> Remote:
        if self._remote is None:
            self._remote = Remote(self.box["url"], self.box.get("token", ""), self._session)
        return self._remote

    def call(self, body: str, **params):
        return self.remote.call(snippet(body, **params))

    def jobs(self, ids=None, active_only=False, limit=None):
        return self.call(box.STATUS, root=self.box["root"], ids=ids, active_only=active_only, limit=limit)["jobs"]


# ── job ids ───────────────────────────────────────────────────────────────────


def parse_jobid(text: str) -> tuple[int, int | None]:
    """`12` -> (12, None); `12_5` -> (12, 5)."""
    head, _, tail = text.partition("_")
    try:
        return int(head), (int(tail) if tail else None)
    except ValueError:
        die(f"not a job id: {text!r} (expected N or N_I)", 2)


def task_label(job: dict, t: dict) -> str:
    return f"{job['id']}_{t['index']}" if job["is_array"] else str(job["id"])


# ── sbatch / srun ─────────────────────────────────────────────────────────────

MOLAB_ONLY_VALUE = {"--rc", "--box", "--session"}
MOLAB_ONLY_FLAG = {"--notebook-env", "--follow", "--parsable"}


def split_command_line(argv: list[str]):
    """sbatch's own rule: options first, then the script and ITS arguments.

    Returns (slurm_tokens, molab_opts, rest). The first token that is neither
    an option nor an option's value starts `rest`.
    """
    slurm_tokens, molab, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            break
        if tok == "--":
            i += 1
            break
        name = tok.split("=", 1)[0]
        if name in MOLAB_ONLY_FLAG:
            molab[name[2:]] = True
            i += 1
            continue
        if name in MOLAB_ONLY_VALUE or name == "--wrap":
            if "=" in tok:
                molab[name[2:]] = tok.split("=", 1)[1]
                i += 1
            else:
                if i + 1 >= len(argv):
                    die(f"{name} needs a value", 2)
                molab[name[2:]] = argv[i + 1]
                i += 2
            continue
        try:
            opt, val = slurm._normalise(tok)
        except ValueError as e:
            die(str(e), 2)
        slurm_tokens.append(tok)
        if val is None and opt not in slurm.FLAGS:
            if i + 1 >= len(argv):
                die(f"option {tok} needs a value", 2)
            slurm_tokens.append(argv[i + 1])
            i += 2
        else:
            i += 1
    return slurm_tokens, molab, argv[i:]


def build_spec(ctx: Ctx, opts: slurm.JobOptions, *, script, wrap, args, molab: dict,
               default_name: str, output_default: str | None = None) -> dict:  # fmt: skip
    chdir = opts.chdir or ctx.box["workdir"]
    try:
        inherit, extra = slurm.export_env(opts.export)
    except ValueError as e:
        die(str(e), 2)
    cpus_box = int(ctx.box["cpus"])
    cpus = opts.cpus or cpus_box
    if opts.cpus and opts.cpus > cpus_box:
        warn(f"--cpus-per-task={opts.cpus} but box {ctx.name!r} has {cpus_box}; using {cpus_box}")
        cpus = cpus_box
    if opts.ignored:
        shown = ", ".join(f"--{k}" + (f"={v}" if v else "") for k, v in sorted(opts.ignored.items()))
        warn(f"not enforced on molab, ignored: {shown}")
    is_array = opts.array is not None
    return {
        "name": opts.name or default_name,
        "user": getpass.getuser(),
        "submitted": time.time(),
        "chdir": chdir,
        "script": script,
        "wrap": wrap,
        "args": args,
        "rc": molab.get("rc"),
        "env_inherit": inherit,
        "env_extra": extra,
        "notebook_env": bool(molab.get("notebook-env")),
        "tasks": opts.array.indices if is_array else None,
        "throttle": (opts.array.throttle or 1) if is_array else None,
        "time_limit": opts.time_limit,
        "dependency": [list(d) for d in opts.dependency],
        "cpus": cpus,
        "output": opts.output or output_default or slurm.default_output(is_array),
        "error": opts.error,
        "ignored": opts.ignored,
    }


def cmd_sbatch(ctx: Ctx, argv: list[str]) -> int:
    tokens, molab, rest = split_command_line(argv)
    try:
        cli = slurm.parse_options(tokens)
    except ValueError as e:
        die(str(e), 2)
    wrap = molab.get("wrap")
    if wrap is None and not rest:
        die("sbatch: give a script (a path on the box) or --wrap 'command'", 2)
    chdir = cli.get("chdir") or ctx.box["workdir"]
    directives = {}
    script = None
    if wrap is None:
        script, args = rest[0], rest[1:]
        r = ctx.call(box.READ_SCRIPT, script=script, chdir=chdir)
        if "error" in r:
            die(r["error"])
        script = r["path"]
        try:
            directives = slurm.parse_directives(r["text"])
        except ValueError as e:
            die(f"{script}: bad #SBATCH line: {e}", 2)
    else:
        args = []
    try:
        opts = slurm.merge(directives, cli)
    except ValueError as e:
        die(str(e), 2)
    name = os.path.basename(script) if script else "wrap"
    spec = build_spec(ctx, opts, script=script, wrap=wrap, args=args, molab=molab, default_name=name)
    r = ctx.call(box.SUBMIT, root=ctx.box["root"], runner_src=RUNNER_SRC, spec=spec)
    if "parsable" in cli or molab.get("parsable"):
        print(r["id"])
    else:
        print(f"Submitted batch job {r['id']}")
    if molab.get("follow"):
        return follow(ctx, r["id"], opts.array.indices[0] if opts.array else None)
    return 0


def cmd_srun(ctx: Ctx, argv: list[str]) -> int:
    tokens, molab, rest = split_command_line(argv)
    if not rest:
        die("srun: nothing to run", 2)
    try:
        opts = slurm.merge({}, slurm.parse_options(tokens))
    except ValueError as e:
        die(str(e), 2)
    if opts.array is not None:
        die("srun: --array is an sbatch option", 2)
    spec = build_spec(
        ctx, opts, script=None, wrap=shlex.join(rest), args=[], molab=molab,
        default_name=os.path.basename(rest[0]),
        output_default=f"{ctx.box['root']}/jobs/%A/srun.out",
    )  # fmt: skip
    jid = ctx.call(box.SUBMIT, root=ctx.box["root"], runner_src=RUNNER_SRC, spec=spec)["id"]
    return follow(ctx, jid, None, cancel_on_interrupt=True)


def follow(ctx: Ctx, jid: int, idx: int | None, cancel_on_interrupt: bool = False) -> int:
    """Stream one task's output until it ends; return its exit code (SLURM's rc)."""
    out = sys.stdout.buffer
    offset, path = 0, None
    try:
        while True:
            job = ctx.jobs(ids=[jid])[0]
            if job.get("missing"):
                die(f"no job {jid}")
            task = next((t for t in job["tasks"] if t["index"] == idx), None)
            if task is None:
                die(f"job {jid} has no task {idx}")
            path = task.get("output") or path
            if path:
                while True:  # drain everything written so far
                    r = ctx.call(box.READ, path=path, offset=offset, max_bytes=CHUNK)
                    data = base64.b64decode(r["data"])
                    if data:
                        out.write(data)
                        out.flush()
                    offset = r["offset"]
                    if len(data) < CHUNK:
                        break
            state = task.get("state", "PENDING")
            if state in FINAL:
                if state not in ("COMPLETED",):
                    warn(f"{task_label(job, task)} {state}" + (f" ({task['reason']})" if task.get("reason") not in (None, "None") else ""))
                code = str(task.get("exit_code") or "1:0")
                rc, sig = (int(x) for x in code.split(":"))
                return rc if rc else (128 + sig if sig else (0 if state == "COMPLETED" else 1))
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        if cancel_on_interrupt:
            ctx.call(box.CANCEL, root=ctx.box["root"], targets=[[jid, idx]])
            warn(f"cancelled {jid}")
        else:
            warn(f"stopped following; job {jid} keeps running")
        return 130
    except RemoteError as e:
        warn(f"{e}\nmolab: job {jid} is still on the box; resume with `molab tail -f {jid}`")
        return 255


# ── squeue / sacct / scancel / tail ───────────────────────────────────────────


def _elapsed(t: dict, now: float) -> float | None:
    if not t.get("start"):
        return None
    return (t.get("end") or now) - t["start"]


def cmd_squeue(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab squeue")
    p.add_argument("-j", "--jobs", help="comma-separated job ids")
    a = p.parse_args(argv)
    ids = [parse_jobid(x)[0] for x in a.jobs.split(",")] if a.jobs else None
    jobs = ctx.jobs(ids=ids, active_only=True)
    rows = [("JOBID", "PARTITION", "NAME", "USER", "ST", "TIME", "NODES", "NODELIST(REASON)")]
    for job in sorted(jobs, key=lambda j: j["id"]):
        if job.get("missing"):
            continue
        pending = [t for t in job["tasks"] if t.get("state") == "PENDING"]
        for t in job["tasks"]:
            if t.get("state") in ("RUNNING",) or (t.get("state") not in FINAL and t.get("state") != "PENDING"):
                rows.append((task_label(job, t), "molab", job["name"], job["user"], ST.get(t["state"], "?"),
                             slurm.format_elapsed(_elapsed(t, job["now"])), "1", ctx.name))  # fmt: skip
        if pending:
            if job["is_array"]:
                spec = slurm.ArraySpec([t["index"] for t in pending], job.get("throttle"))
                label = f"{job['id']}_[{spec.compact()}]"
            else:
                label = str(job["id"])
            reason = pending[0].get("reason") or "None"
            if reason == "None" and job["is_array"]:
                reason = "JobArrayTaskLimit"
            rows.append((label, "molab", job["name"], job["user"], "PD", "0:00", "1", f"({reason})"))
    _table(rows)
    return 0


def cmd_sacct(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab sacct")
    p.add_argument("-j", "--jobs", help="comma-separated job ids [the newest --last]")
    p.add_argument("--last", type=int, default=20, help="newest N jobs when -j is not given [20]")
    p.add_argument("-s", "--state", help="only these states, e.g. FAILED,TIMEOUT")
    a = p.parse_args(argv)
    ids = [parse_jobid(x)[0] for x in a.jobs.split(",")] if a.jobs else None
    want = set(a.state.upper().split(",")) if a.state else None
    jobs = ctx.jobs(ids=ids, limit=None if ids else a.last)
    rows = [("JobID", "JobName", "State", "ExitCode", "Elapsed", "Start", "End")]
    for job in sorted(jobs, key=lambda j: j["id"]):
        if job.get("missing"):
            continue
        for t in job["tasks"]:
            state = t.get("state", "PENDING")
            if want and state not in want:
                continue
            rows.append((task_label(job, t), job["name"][:24], state, t.get("exit_code") or "0:0",
                         slurm.format_elapsed(_elapsed(t, job["now"])), _ts(t.get("start")), _ts(t.get("end"))))  # fmt: skip
    _table(rows)
    return 0


def cmd_scancel(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab scancel")
    p.add_argument("jobs", nargs="*", help="N or N_I")
    p.add_argument("--all", action="store_true", help="every job that is still pending or running")
    a = p.parse_args(argv)
    if a.all:
        targets = [[j["id"], None] for j in ctx.jobs(active_only=True) if not j.get("missing")]
    else:
        if not a.jobs:
            die("scancel: give job ids, or --all", 2)
        targets = [list(parse_jobid(x)) for x in a.jobs]
    if not targets:
        return 0
    r = ctx.call(box.CANCEL, root=ctx.box["root"], targets=targets)
    for m in r["missing"]:
        warn(f"scancel: invalid job id {m}")
    return 1 if r["missing"] else 0


def cmd_tail(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab tail", description="Show a job's output file (slurm-%j.out or --output).")
    p.add_argument("job", help="N or N_I (for an array job, N means its first task)")
    p.add_argument("-f", "--follow", action="store_true", help="keep streaming until the task ends")
    p.add_argument("-n", "--lines", type=int, default=None, help="only the last N lines")
    a = p.parse_args(argv)
    jid, idx = parse_jobid(a.job)
    job = ctx.jobs(ids=[jid])[0]
    if job.get("missing"):
        die(f"no job {jid}")
    if job["is_array"] and idx is None:
        idx = job["tasks"][0]["index"]
    if a.follow:
        return follow(ctx, jid, idx)
    task = next((t for t in job["tasks"] if t["index"] == idx), None)
    if task is None or not task.get("output"):
        die(f"{a.job} has not started yet, so it has no output")
    # In 1 MB replies: one reply carrying a whole log (Keras progress bars make
    # them large) came back cut off mid-JSON. With -n, fetch only the end.
    size = ctx.call(box.READ, path=task["output"], offset=0, max_bytes=0).get("size", 0)
    offset = 0
    if a.lines is not None:
        offset = max(0, size - max(64 << 10, a.lines * 4096))
    chunks = []
    while offset < size:
        r = ctx.call(box.READ, path=task["output"], offset=offset, max_bytes=CHUNK)
        data = base64.b64decode(r["data"])
        if not data:
            break
        offset = r["offset"]
        if a.lines is None:
            sys.stdout.buffer.write(data)
        else:
            chunks.append(data)
    if a.lines is not None:
        tail = b"".join(chunks).replace(b"\r", b"\n").split(b"\n")
        sys.stdout.buffer.write(b"\n".join(tail[-a.lines - 1 :]))
    sys.stdout.buffer.flush()
    return 0


# ── sinfo / init / boxes ──────────────────────────────────────────────────────


def describe_box(ctx: Ctx) -> dict:
    info = ctx.call(box.INFO, root=ctx.box["root"])
    print(f"box       {ctx.name}  ({ctx.box['url']})")
    print(f"host      {info['host']}  python {info['python']}")
    print(f"cpus      {ctx.box['cpus']} (configured; the box reports the host's count)")
    if info["gpus"]:
        models = ", ".join(info["gpu_models"]) or "model unknown"
        print(f"gpus      {info['gpus']}  ({models})")
    else:
        why = "the NVIDIA driver entry is there but no GPU is attached" if info["driver_dir"] else "no NVIDIA driver"
        print(f"gpus      0  -- {why}; GPU jobs will run on the CPU")
    print(f"disk      {info['disk_used_gb']} GB used under {ctx.box['root']}'s filesystem")
    return info


def cmd_sinfo(ctx: Ctx, argv: list[str]) -> int:
    argparse.ArgumentParser(prog="molab sinfo").parse_args(argv)
    describe_box(ctx)
    active = [j for j in ctx.jobs(active_only=True) if not j.get("missing")]
    running = sum(1 for j in active for t in j["tasks"] if t.get("state") == "RUNNING")
    print(f"jobs      {len(active)} active, {running} task(s) running")
    return 0


def cmd_init(argv: list[str], session: str | None) -> int:
    p = argparse.ArgumentParser(prog="molab init", description="Save a box: its notebook URL and token.")
    p.add_argument("url", help="the notebook's URL, e.g. https://sb-....molab.run/")
    p.add_argument("token", help="the token from molab's 'connect' snippet (code execution on the box!)")
    p.add_argument("--name", default="default", help="what to call this box [default]")
    p.add_argument("--cpus", type=int, help="real CPU count of the box [4]")
    p.add_argument("--workdir", help="default working directory for jobs [/marimo]")
    p.add_argument("--no-default", action="store_true", help="save it without making it the default")
    a = p.parse_args(argv)
    # verify before saving: a typo'd token should not become the default
    try:
        Remote(a.url, a.token, session)
    except RemoteError as e:
        die(f"not saved: {e}")
    config.add_box(a.name, a.url, a.token, make_default=not a.no_default, cpus=a.cpus, workdir=a.workdir)
    print(f"saved box {a.name!r} to {config.CONFIG}" + ("" if a.no_default else " (default)"))
    describe_box(Ctx(a.name, session))
    return 0


def cmd_boxes(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="molab boxes").parse_args(argv)
    cfg = config.load()
    rows = [("", "NAME", "URL", "CPUS", "WORKDIR")]
    for name, b in sorted(cfg["boxes"].items()):
        rows.append(("*" if name == cfg["default"] else "", name, b["url"], str(b.get("cpus", "")), b.get("workdir", "")))
    _table(rows)
    return 0


# ── files ─────────────────────────────────────────────────────────────────────


def cmd_put(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab put", description="Copy a small local file to the box.")
    p.add_argument("local")
    p.add_argument("remote", help="path, or an existing directory, on the box")
    a = p.parse_args(argv)
    src = Path(a.local)
    if not src.is_file():
        die(f"not a file: {src}")
    if src.stat().st_size > MAX_TRANSFER:
        die(f"{src} is over {MAX_TRANSFER >> 20} MB; move big files through a bucket")
    mode, chunk, dest, first = stat.S_IMODE(src.stat().st_mode), CHUNK, a.remote, True
    with src.open("rb") as fh:
        while True:
            data = fh.read(chunk)
            if not data and not first:
                break
            r = ctx.call(box.PUT, path=dest, name=src.name, data=base64.b64encode(data).decode(),
                         append=not first, mode=mode)  # fmt: skip
            dest, first = r["path"], False  # later chunks append to the resolved path
            if len(data) < chunk:
                break
    print(dest)
    return 0


def _get(ctx: Ctx, remote: str, dst: Path) -> Path:
    if dst.is_dir():
        dst = dst / Path(remote).name
    offset, size, mode = 0, None, 0o644
    with dst.open("wb") as fh:
        while size is None or offset < size:
            r = ctx.call(box.GET, path=remote, offset=offset, chunk=CHUNK)
            if "error" in r:
                fh.close()
                dst.unlink()
                die(r["error"])
            size, mode = r["size"], r["mode"]
            if size > MAX_TRANSFER:
                fh.close()
                dst.unlink()
                die(f"{remote} is over {MAX_TRANSFER >> 20} MB; move big files through a bucket")
            data = base64.b64decode(r["data"])
            if not data:
                break
            fh.write(data)
            offset += len(data)
    dst.chmod(mode)
    return dst


def cmd_get(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab get", description="Copy a small file from the box.")
    p.add_argument("remote")
    p.add_argument("local", nargs="?", default=".")
    a = p.parse_args(argv)
    print(_get(ctx, a.remote, Path(a.local)))
    return 0


def cmd_open(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="molab open", description="Fetch files and open them locally (Preview for png/pdf).")
    p.add_argument("remote", nargs="+")
    p.add_argument("--no-open", action="store_true", help="fetch into the cache only")
    a = p.parse_args(argv)
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "molab" / "open" / ctx.name
    for remote in a.remote:
        dst = cache / remote.lstrip("/")  # mirror the path: two d0_profile.png never collide
        dst.parent.mkdir(parents=True, exist_ok=True)
        got = _get(ctx, remote, dst)
        print(got)
        if not a.no_open:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            os.spawnlp(os.P_NOWAIT, opener, opener, str(got))
    return 0


def cmd_keepalive(ctx: Ctx, argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="molab keepalive",
        description="Touch the kernel on a timer. Whether molab's idle timer counts "
        "kernel API traffic is not documented; this is the activity molab-slurm can generate.",
    )
    p.add_argument("--every", type=_duration, default=240, help="interval: 240, 4m [240s]")
    p.add_argument("--for", dest="duration", type=_duration, help="stop after, e.g. 8h [forever]")
    p.add_argument("-q", "--quiet", action="store_true")
    a = p.parse_args(argv)
    end = time.time() + a.duration if a.duration else None
    n = 0
    try:
        while end is None or time.time() < end:
            try:
                ctx.call(box.PING)
                n += 1
                if not a.quiet:
                    print(f"{time.strftime('%H:%M:%S')} keepalive #{n} ok", file=sys.stderr)
            except RemoteError as e:
                print(f"{time.strftime('%H:%M:%S')} keepalive failed: {e}", file=sys.stderr)
            time.sleep(a.every)
    except KeyboardInterrupt:
        pass
    return 0


# ── helpers ───────────────────────────────────────────────────────────────────


def _duration(text: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    return int(float(text[:-1]) * units[text[-1]]) if text[-1:] in units else int(text)


def _ts(t) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if t else "Unknown"


def _table(rows) -> None:
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())


COMMANDS = {
    "sbatch": ("submit a batch script (a path on the box)", cmd_sbatch),
    "srun": ("run a command and stream its output", cmd_srun),
    "squeue": ("pending and running jobs", cmd_squeue),
    "sacct": ("finished and running jobs, with exit codes", cmd_sacct),
    "scancel": ("cancel jobs or array tasks", cmd_scancel),
    "sinfo": ("the box: CPUs, GPUs, disk, jobs", cmd_sinfo),
    "tail": ("a job's output file", cmd_tail),
    "put": ("copy a small file to the box", cmd_put),
    "get": ("copy a small file from the box", cmd_get),
    "open": ("fetch a file and open it locally", cmd_open),
    "keepalive": ("ping the kernel on a timer", cmd_keepalive),
}


def usage() -> str:
    lines = [f"molab {__version__} -- SLURM's commands for a molab box", "",
             "usage: molab [--box NAME] [--session ID] <command> [args]", "",
             "  init       save a box (notebook URL + token)", "  boxes      list saved boxes"]  # fmt: skip
    lines += [f"  {k:<10} {v[0]}" for k, v in COMMANDS.items()]
    lines += ["", "`molab <command> -h` for each command's options."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    box_name = session = None
    while argv and argv[0].startswith("--") and argv[0].split("=")[0] in ("--box", "--session"):
        key, _, val = argv.pop(0).partition("=")
        if not val:
            if not argv:
                die(f"{key} needs a value", 2)
            val = argv.pop(0)
        if key == "--box":
            box_name = val
        else:
            session = val
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        print(__version__)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "init":
            return cmd_init(rest, session)
        if cmd == "boxes":
            return cmd_boxes(rest)
        if cmd not in COMMANDS:
            die(f"unknown command {cmd!r}\n\n{usage()}", 2)
        return COMMANDS[cmd][1](Ctx(box_name, session), rest)
    except (RemoteError, config.ConfigError) as e:
        die(str(e))


def entry() -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())


if __name__ == "__main__":
    entry()
