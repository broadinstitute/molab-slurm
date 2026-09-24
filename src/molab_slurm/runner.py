"""Box-side job runner: one process per `molab-slurm sbatch` job. Stdlib only.

This file is copied verbatim to the box and run with the box's own python3
(`python3 runner.py <job_dir>`), detached from the notebook kernel that
started it. It is the only thing on the box that starts, watches or stops a
job's processes, which is the point of the design:

* PIDs are only trusted *inside* this process. Under gVisor the notebook
  kernel sees a different PID namespace from the processes it spawns, so a
  PID handed back to the kernel -- or process groups, which showed up as
  pgid 1 -- cannot be used to signal anything. The runner and its children
  do share one, so it walks and signals its own process tree.
* `scancel` never signals anything. It drops a `cancel` file in the job
  directory; the runner sees it within a second and stops the task's tree
  itself, parents first then SIGKILL after a grace period (SLURM's
  KillWait), and records CANCELLED.
* The runner heartbeats into `runner.json`. A job whose heartbeat has gone
  stale without reaching a final state lost its runner (box restart, OOM
  kill) and is reported as NODE_FAIL by the CLI.

Layout of a job directory (written by the CLI, then by this runner):

    job.json              the spec (see molab_slurm.box.submit)
    runner.json           {"pid": ..., "heartbeat": epoch}
    tasks/<key>.json      one per task: state, reason, start, end, exit_code, output
    cancel | cancel.<i>   cancel requests (whole job | one array task)
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

FINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
KILL_WAIT_S = 10
HEARTBEAT_S = 5
POLL_S = 0.5


def _write_json(path: str, obj) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)  # atomic: readers never see half a file


def _read_json(path: str):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def task_key(idx) -> str:
    return "-" if idx is None else str(idx)


_PATTERN = re.compile(r"%(%|[AajxNu])")


def fill_pattern(pattern: str, *, job_id: str, array_job_id: str, task, name: str, node: str,
                 user: str) -> str:  # fmt: skip
    """Expand SLURM filename patterns. For array tasks %j is `A_a` (molab-slurm has no
    separate per-task numeric id); %a is 4294967294, as in SLURM, outside an array."""
    subs = {
        "%": "%",
        "A": array_job_id,
        "a": str(task) if task is not None else "4294967294",
        "j": job_id,
        "x": name,
        "N": node,
        "u": user,
    }
    return _PATTERN.sub(lambda m: subs[m.group(1)], pattern)


# ── process trees ─────────────────────────────────────────────────────────────


def _parent_map() -> dict[int, int]:
    """pid -> ppid for every process we can see."""
    out = {}
    if os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat") as fh:
                    stat = fh.read()
                # the command field is in parentheses and may contain spaces
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
                out[int(name)] = ppid
            except (OSError, ValueError, IndexError):
                continue
        return out
    # no /proc (macOS, for the test suite): ask ps
    r = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, check=False)
    for line in r.stdout.splitlines():
        try:
            pid, ppid = (int(x) for x in line.split())
            out[pid] = ppid
        except ValueError:
            continue
    return out


def tree(root: int) -> list[int]:
    """root and all its descendants, PARENTS FIRST.

    Parents first matters: signalled deepest-first, a shell sees its child die
    and simply runs its next command -- `sleep 120; echo never` printed
    `never` and exited 0 when it was cancelled that way.
    """
    parents = _parent_map()
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    order, stack = [], [root]
    while stack:
        pid = stack.pop(0)
        order.append(pid)
        stack.extend(sorted(children.get(pid, [])))
    return order


def signal_tree(root: int, sig: int) -> None:
    for pid in tree(root):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


# ── the job ───────────────────────────────────────────────────────────────────


class Runner:
    def __init__(self, job_dir: str):
        self.dir = job_dir
        self.spec = _read_json(os.path.join(job_dir, "job.json"))
        if self.spec is None:
            sys.exit(f"runner: no job.json in {job_dir}")
        self.jobs_root = os.path.dirname(job_dir)
        self.tasks_dir = os.path.join(job_dir, "tasks")
        os.makedirs(self.tasks_dir, exist_ok=True)
        self.indices = self.spec["tasks"] if self.spec["tasks"] is not None else [None]
        self.node = socket.gethostname()
        self.last_beat = 0.0
        self.stop_requested = False
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

    def _on_term(self, *_):
        self.stop_requested = True

    # state files
    def task_path(self, idx) -> str:
        return os.path.join(self.tasks_dir, task_key(idx) + ".json")

    def set_task(self, idx, **fields) -> None:
        cur = _read_json(self.task_path(idx)) or {"index": idx}
        cur.update(fields)
        _write_json(self.task_path(idx), cur)

    def beat(self, force: bool = False) -> None:
        now = time.time()
        if force or now - self.last_beat >= HEARTBEAT_S:
            _write_json(
                os.path.join(self.dir, "runner.json"), {"pid": os.getpid(), "heartbeat": now}
            )
            self.last_beat = now

    def cancelled(self, idx=None) -> bool:
        if self.stop_requested or os.path.exists(os.path.join(self.dir, "cancel")):
            return True
        return idx is not None and os.path.exists(os.path.join(self.dir, f"cancel.{idx}"))

    # dependencies
    def _job_states(self, job_id: str) -> list[str] | None:
        d = os.path.join(self.jobs_root, str(job_id))
        if not os.path.isdir(d):
            return None
        states = []
        for name in os.listdir(os.path.join(d, "tasks")) if os.path.isdir(os.path.join(d, "tasks")) else []:
            t = _read_json(os.path.join(d, "tasks", name))
            if t:
                states.append(t.get("state", "PENDING"))
        beat = _read_json(os.path.join(d, "runner.json")) or {}
        stale = time.time() - beat.get("heartbeat", 0) > 60
        if stale:  # its runner is gone: whatever did not finish never will
            states = [s if s in FINAL else "NODE_FAIL" for s in states]
        return states or ["PENDING"]

    def dependency_verdict(self) -> str | None:
        """None while waiting; "ok" once satisfied; a reason string if it never can be."""
        for kind, dep in self.spec.get("dependency") or []:
            states = self._job_states(dep)
            if states is None:
                return f"DependencyNeverSatisfied (no job {dep})"
            final = all(s in FINAL for s in states)
            if kind == "after":
                if all(s == "PENDING" for s in states):
                    return None
            elif not final:
                return None
            elif kind == "afterok" and any(s != "COMPLETED" for s in states):
                return f"DependencyNeverSatisfied (job {dep} did not complete)"
            elif kind == "afternotok" and all(s == "COMPLETED" for s in states):
                return f"DependencyNeverSatisfied (job {dep} completed)"
        return "ok"

    # one task
    def command(self) -> list[str]:
        s = self.spec
        script = s["script"]
        path = script if os.path.isabs(script) else os.path.join(s["chdir"], script)
        try:
            with open(path) as fh:
                first = fh.readline()
        except OSError:
            first = ""
        interp = shlex.split(first[2:]) if first.startswith("#!") else ["bash"]
        cmd = interp + [path] + list(s.get("args") or [])
        if s.get("rc"):
            cmd = ["bash", "-c", 'source "$1" || exit $?; shift; exec "$@"', "molab-slurm-rc", s["rc"]] + cmd
        return cmd

    def environment(self, idx) -> dict[str, str]:
        s = self.spec
        if s.get("env_inherit", True):
            env = dict(os.environ)
        else:
            env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "USER", "TERM") if k in os.environ}
        env.update(s.get("env_extra") or {})
        jid = str(s["id"])
        env.update(
            {
                "SLURM_JOB_ID": jid,
                "SLURM_JOBID": jid,
                "SLURM_JOB_NAME": s["name"],
                "SLURM_SUBMIT_DIR": s["chdir"],
                "SLURM_SUBMIT_HOST": self.node,
                "SLURM_JOB_NODELIST": self.node,
                "SLURMD_NODENAME": self.node,
                "SLURM_JOB_PARTITION": "molab",
                "SLURM_NTASKS": "1",
                "SLURM_NNODES": "1",
                "SLURM_CPUS_PER_TASK": str(s["cpus"]),
                "SLURM_CPUS_ON_NODE": str(s["cpus"]),
                # Thread pools size themselves from os.cpu_count(), which on a
                # molab box is the HOST's count (20 on a 4-CPU slice); numba is
                # the one easiest to miss (a numba job ran 24 threads on 4 CPUs).
                "OMP_NUM_THREADS": env.get("OMP_NUM_THREADS", str(s["cpus"])),
                "NUMBA_NUM_THREADS": env.get("NUMBA_NUM_THREADS", str(s["cpus"])),
            }
        )
        if idx is not None:
            tasks = s["tasks"]
            env.update(
                {
                    "SLURM_ARRAY_JOB_ID": jid,
                    "SLURM_ARRAY_TASK_ID": str(idx),
                    "SLURM_ARRAY_TASK_COUNT": str(len(tasks)),
                    "SLURM_ARRAY_TASK_MIN": str(min(tasks)),
                    "SLURM_ARRAY_TASK_MAX": str(max(tasks)),
                }
            )
        return env

    def paths(self, idx) -> tuple[str, str | None]:
        """This task's stdout and (if separate) stderr file, from the job's patterns."""
        s = self.spec
        jid = str(s["id"])
        fields = {
            "job_id": f"{jid}_{idx}" if idx is not None else jid,
            "array_job_id": jid,
            "task": idx,
            "name": s["name"],
            "node": self.node,
            "user": pwd.getpwuid(os.getuid()).pw_name,
        }

        def resolve(pattern):
            p = fill_pattern(pattern, **fields)
            return p if os.path.isabs(p) else os.path.join(s["chdir"], p)

        out = resolve(s["output"])
        err = resolve(s["error"]) if s.get("error") else None
        return out, (err if err != out else None)

    def start(self, idx):
        s = self.spec
        out, err = self.paths(idx)
        for p in filter(None, (out, err)):
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        # the child gets its own copies of the descriptors, so ours close as soon as it has started
        with open(out, "ab") as out_fh, (open(err, "ab") if err else contextlib.nullcontext()) as err_fh:
            try:
                proc = subprocess.Popen(
                    self.command(),
                    cwd=s["chdir"],
                    env=self.environment(idx),
                    stdin=subprocess.DEVNULL,
                    stdout=out_fh,
                    stderr=err_fh or subprocess.STDOUT,
                )
            except OSError as exc:
                out_fh.write(f"molab-slurm: cannot start job: {exc}\n".encode())
                failed = exc
            else:
                failed = None
        if failed is not None:
            self.set_task(idx, state="FAILED", reason=str(failed), exit_code="127:0", end=time.time())
            return None
        self.set_task(idx, state="RUNNING", reason="None", start=time.time(), pid=proc.pid, output=out, error=err or out)
        return proc

    def stop(self, proc, idx, state: str, reason: str) -> None:
        signal_tree(proc.pid, signal.SIGTERM)
        deadline = time.time() + KILL_WAIT_S
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            signal_tree(proc.pid, signal.SIGKILL)
            proc.wait()
        # No sweep after the root is reaped: its PID can be reused by then, and
        # orphans have been re-parented away from it anyway.
        rc = proc.returncode
        code = f"0:{-rc}" if rc is not None and rc < 0 else f"{rc}:0"
        self.set_task(idx, state=state, reason=reason, exit_code=code, end=time.time())

    # the whole job
    def run(self) -> None:
        self.beat(force=True)
        for idx in self.indices:
            if not os.path.exists(self.task_path(idx)):
                self.set_task(idx, state="PENDING", reason="Dependency" if self.spec.get("dependency") else "None")

        verdict = self.dependency_verdict()
        while verdict is None:
            if self.cancelled():
                self._cancel_pending("CANCELLED", "cancelled while pending")
                return
            self.beat()
            time.sleep(1)
            verdict = self.dependency_verdict()
        if verdict != "ok":
            self._cancel_pending("CANCELLED", verdict)
            return

        limit = self.spec.get("time_limit")
        throttle = self.spec.get("throttle") or (1 if self.spec["tasks"] is not None else None)
        queue = list(self.indices)
        running: dict = {}
        while queue or running:
            self.beat()
            # start what the throttle allows
            while queue and (throttle is None or len(running) < throttle):
                idx = queue.pop(0)
                if self.cancelled(idx):
                    self.set_task(idx, state="CANCELLED", reason="cancelled while pending", end=time.time())
                    continue
                proc = self.start(idx)
                if proc is not None:
                    running[idx] = (proc, time.time())
            # watch what runs
            for idx, (proc, t0) in list(running.items()):
                rc = proc.poll()
                if rc is not None:
                    reason = "None"
                    if rc < 0:
                        state, code = "FAILED", f"0:{-rc}"
                        if -rc == signal.SIGKILL:
                            # Not us (scancel/timeout go through stop()), so most
                            # likely the kernel's OOM killer -- but that is an
                            # inference, and the reason says so.
                            state, reason = "OUT_OF_MEMORY", "SIGKILL, likely the OOM killer"
                    else:
                        state, code = ("COMPLETED" if rc == 0 else "FAILED"), f"{rc}:0"
                    self.set_task(idx, state=state, reason=reason, exit_code=code, end=time.time())
                    del running[idx]
                elif self.cancelled(idx):
                    self.stop(proc, idx, "CANCELLED", "cancelled")
                    del running[idx]
                elif limit is not None and time.time() - t0 > limit:
                    self.stop(proc, idx, "TIMEOUT", "time limit")
                    del running[idx]
            if self.stop_requested:  # runner itself told to go: take everything down
                for idx, (proc, _) in list(running.items()):
                    self.stop(proc, idx, "CANCELLED", "runner terminated")
                running.clear()
                for idx in queue:
                    self.set_task(idx, state="CANCELLED", reason="runner terminated", end=time.time())
                queue.clear()
            time.sleep(POLL_S)
        self.beat(force=True)

    def _cancel_pending(self, state: str, reason: str) -> None:
        for idx in self.indices:
            cur = _read_json(self.task_path(idx)) or {}
            if cur.get("state") not in FINAL:
                self.set_task(idx, state=state, reason=reason, end=time.time())
        self.beat(force=True)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: runner.py <job_dir>", file=sys.stderr)
        return 2
    Runner(argv[0]).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
