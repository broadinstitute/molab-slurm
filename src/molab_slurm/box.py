"""The Python that runs in the notebook kernel on the box, one snippet per verb.

Each is a string sent through `remote.snippet()`: parameters arrive as `_p`
(decoded JSON), the answer goes back through `_reply(...)`. They only read
and write files under the box's molab root and, for `SUBMIT`, start one
detached runner (runner.py) per job. None of them waits for a job: long work
never happens inside a kernel request.
"""

# Environment for anything started on the box: the box's, minus the notebook's
# own Python. The kernel exports PYTHONPATH/VIRTUAL_ENV pointing at its venv
# (/tmp/uv-venv) and puts that venv first on PATH; a job inheriting them
# imports the notebook's packages -- it broke a python 3.8 container outright.
CLEAN_ENV = r'''
def _clean_env(keep_notebook=False):
    import os
    env = dict(os.environ)
    if keep_notebook:
        return env
    venv = env.get("VIRTUAL_ENV")
    venv_bin = os.path.realpath(os.path.join(venv, "bin")) if venv else None
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"):
        env.pop(k, None)
    env["PATH"] = os.pathsep.join(
        x for x in env.get("PATH", "").split(os.pathsep) if x and os.path.realpath(x) != venv_bin
    )
    return env
'''

READ_SCRIPT = r'''
import os
chdir = _p["chdir"]
path = _p["script"] if os.path.isabs(_p["script"]) else os.path.join(chdir, _p["script"])
if not os.path.isdir(chdir):
    _reply({"error": f"working directory does not exist on the box: {chdir}"})
elif not os.path.isfile(path):
    _reply({"error": f"no such script on the box: {path}"})
else:
    with open(path, errors="replace") as fh:
        _reply({"path": path, "text": fh.read(256 << 10)})  # directives live at the top; stay under the ~1 MB reply cap
'''

SUBMIT = (
    CLEAN_ENV
    + r'''
import fcntl, os, shutil, subprocess
root = _p["root"]
jobs = os.path.join(root, "jobs")
os.makedirs(jobs, exist_ok=True)

# The runner is (re)written on every submit so an upgrade reaches the box;
# jobs already running keep the code they started with.
runner = os.path.join(root, "runner.py")
cur = open(runner).read() if os.path.exists(runner) else None
if cur != _p["runner_src"]:
    tmp = runner + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(_p["runner_src"])
    os.replace(tmp, runner)

# Integer job ids, SLURM-style, from a locked counter.
with open(os.path.join(root, "next_id"), "a+") as fh:
    fcntl.flock(fh, fcntl.LOCK_EX)
    fh.seek(0)
    jid = int(fh.read().strip() or 1)
    fh.seek(0)
    fh.truncate()
    fh.write(str(jid + 1))

d = os.path.join(jobs, str(jid))
os.makedirs(os.path.join(d, "tasks"))
spec = dict(_p["spec"], id=jid)
if spec.get("wrap") is not None:
    spec["script"] = os.path.join(d, "wrap.sh")
    with open(spec["script"], "w") as fh:
        fh.write("#!/bin/bash\n" + spec["wrap"] + "\n")
with open(os.path.join(d, "job.json"), "w") as fh:
    _j.dump(spec, fh, indent=2)

env = _clean_env(spec.get("notebook_env", False))
python = shutil.which("python3", path=env["PATH"]) or "/usr/bin/python3"
log = open(os.path.join(d, "runner.log"), "ab")
subprocess.Popen([python, runner, d], cwd=d, env=env, stdin=subprocess.DEVNULL,
                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
log.close()
_reply({"id": jid, "dir": d})
'''
)

# Everything squeue/sacct/scancel/tail need, in one read.
STATUS = r'''
import os, time
root = os.path.join(_p["root"], "jobs")
FINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
def rj(p):
    try:
        return _j.load(open(p))
    except (OSError, ValueError):
        return None
ids = _p["ids"]
if ids is None:
    ids = sorted((int(x) for x in os.listdir(root) if x.isdigit()), reverse=True) if os.path.isdir(root) else []
    ids = ids[: _p["limit"]] if _p["limit"] else ids
out, now = [], time.time()
for jid in ids:
    d = os.path.join(root, str(jid))
    spec = rj(os.path.join(d, "job.json"))
    if spec is None:
        out.append({"id": jid, "missing": True})
        continue
    beat = (rj(os.path.join(d, "runner.json")) or {}).get("heartbeat")
    stale = beat is None and now - spec.get("submitted", now) > 60 or beat is not None and now - beat > 60
    tasks = []
    idx_list = spec["tasks"] if spec["tasks"] is not None else [None]
    for idx in idx_list:
        t = rj(os.path.join(d, "tasks", ("-" if idx is None else str(idx)) + ".json")) or {"index": idx, "state": "PENDING", "reason": "None"}
        if t.get("state") not in FINAL and stale:
            t = dict(t, state="NODE_FAIL", reason="runner gone (box restarted or runner killed)")
        tasks.append(t)
    active = any(t.get("state") not in FINAL for t in tasks)
    if _p["active_only"] and not active:
        continue
    out.append({"id": jid, "name": spec["name"], "user": spec.get("user", ""), "submitted": spec.get("submitted"),
                "chdir": spec["chdir"], "throttle": spec.get("throttle"), "is_array": spec["tasks"] is not None,
                "time_limit": spec.get("time_limit"), "tasks": tasks, "now": now})
_reply({"jobs": out})
'''

CANCEL = r'''
import os
root = os.path.join(_p["root"], "jobs")
done, missing = [], []
for jid, idx in _p["targets"]:
    d = os.path.join(root, str(jid))
    if not os.path.isdir(d):
        missing.append(f"{jid}" if idx is None else f"{jid}_{idx}")
        continue
    open(os.path.join(d, "cancel" if idx is None else f"cancel.{idx}"), "w").close()
    done.append(f"{jid}" if idx is None else f"{jid}_{idx}")
_reply({"done": done, "missing": missing})
'''

READ = r'''
import base64, os
p = _p["path"]
if not os.path.isfile(p):
    _reply({"exists": False, "data": "", "offset": _p["offset"]})
else:
    with open(p, "rb") as fh:
        fh.seek(_p["offset"])
        data = fh.read(_p["max_bytes"])
    _reply({"exists": True, "size": os.path.getsize(p), "data": base64.b64encode(data).decode(),
            "offset": _p["offset"] + len(data)})
'''

PUT = r'''
import base64, os
dst = _p["path"]
if os.path.isdir(dst):
    dst = os.path.join(dst, _p["name"])
os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
with open(dst, "ab" if _p["append"] else "wb") as fh:
    fh.write(base64.b64decode(_p["data"]))
if _p["mode"] is not None:
    os.chmod(dst, _p["mode"])
_reply({"path": os.path.abspath(dst)})
'''

GET = r'''
import base64, os
p = _p["path"]
if not os.path.isfile(p):
    _reply({"error": "not a file on the box: " + p})
else:
    with open(p, "rb") as fh:
        fh.seek(_p["offset"])
        data = fh.read(_p["chunk"])
    _reply({"size": os.path.getsize(p), "data": base64.b64encode(data).decode(),
            "mode": os.stat(p).st_mode & 0o777})
'''

# sinfo: what the box really has. Checks the GPU the way the box exposes it
# (device nodes and the driver's /proc entry), because a session can come back
# WITHOUT the GPU it had: /proc/driver/nvidia exists but is empty.
INFO = r'''
import glob, os, shutil, socket, subprocess, sys
gpus = sorted(glob.glob("/dev/nvidia[0-9]*"))
models = []
# nvidia-smi first: under gVisor /proc/driver/nvidia/gpus is empty even when a
# GPU is attached, so the /proc entry can only confirm, never name, a card.
smi = shutil.which("nvidia-smi")
if smi:
    r = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                       capture_output=True, text=True, timeout=20)
    models = [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
if not models and os.path.isdir("/proc/driver/nvidia/gpus"):
    for g in sorted(os.listdir("/proc/driver/nvidia/gpus")):
        try:
            for line in open(f"/proc/driver/nvidia/gpus/{g}/information"):
                if line.startswith("Model:"):
                    models.append(line.split(":", 1)[1].strip())
        except OSError:
            pass
du = shutil.disk_usage(_p["root"] if os.path.exists(_p["root"]) else "/")
_reply({"host": socket.gethostname(), "gpus": len(gpus), "gpu_models": models,
        "driver_dir": os.path.isdir("/proc/driver/nvidia"), "disk_used_gb": round((du.total - du.free) / 2**30, 1),
        "python": sys.version.split()[0]})
'''

PING = r'''
import time
_reply({"t": time.time()})
'''
