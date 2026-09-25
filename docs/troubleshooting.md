---
title: Troubleshooting
nav_order: 11
---

# Troubleshooting

| symptom | cause and fix |
|---|---|
| `HTTP 403 listing sessions (wrong token?)` | The token is wrong or from an older session — every new session has a new URL **and** token. Copy both from molab's connect snippet and `molab-slurm init` again. |
| `HTTP 410 listing sessions (wrong token?)` | The session has ended (410: gone) and cannot be restored. [Start a new one yourself](sessions.md), `molab-slurm init` its URL and token, and check `sinfo`. |
| Sessions end within minutes of starting jobs, no reason shown | Seen while the box was polled frequently (`watch -n 10 molab-slurm squeue` plus an agent checking `squeue` and `tail` every few minutes); the cause is not confirmed. Stop polling: check each job once, when it should be done, or block on it with `molab-slurm wait`, and keep `srun`, `--follow` and `tail -f`, which call the box every 1 to 10 seconds, to short commands. See [For AI agents](ai-agents.md). |
| `cannot reach https://sb-...: ... CERTIFICATE_VERIFY_FAILED` | Your Python has no CA bundle (python.org's macOS builds, until "Install Certificates.command" is run). molab-slurm falls back to `certifi` and `/etc/ssl/cert.pem`; install `certifi` if neither exists. |
| `cannot reach ...` / connection refused | The session is gone. [Start a new one yourself](sessions.md) and `molab-slurm init` its URL. |
| `no active notebook session` | The notebook server is up but no notebook is open. Open it in a browser. |
| `several sessions on this server` | More than one notebook is open on that server; pass `--session ID`. |
| `reply truncated ... (the kernel caps one reply at ~1 MB)` | A reply exceeded the kernel API's limit. molab-slurm moves data in 512 KB pieces; if you see this, it is a bug — please report it. |
| GPU code is extremely slow; `sinfo` says `gpus 0` | The session has no GPU attached — this happens when molab recreates a session. Restart it on a GPU machine. |
| A job's Python imports the wrong packages | It inherited the notebook's venv. molab-slurm strips it by default; check you did not pass `--notebook-env`, and set the interpreter explicitly in `--rc`. |
| A task shows `NODE_FAIL` | Its runner stopped heartbeating: the box restarted or the runner was killed. Resubmit. |
| A task shows `OUT_OF_MEMORY` | It was SIGKILLed by something other than molab-slurm — on molab, almost always the kernel's OOM killer. The box's real memory is smaller than what `free` reports. |
| Jobs run slower than expected, or the notebook stops answering while they run | gVisor reports the host's CPU count (`os.cpu_count()`), so libraries that size thread pools from it start far more threads than the box has CPUs. molab-slurm sets `OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` to the box's `--cpus` (or `-c N`) unless already set; check that `--cpus` is the real count, set other libraries' thread counts yourself, or pin the job with `taskset -c 0-<N-1>`. Leave one CPU for the notebook kernel (`-c 3` on a 4-CPU box). |
| `molab-slurm tail` shows one enormous line | Progress bars use carriage returns. `tail -n N` splits on them; `tail -f` passes them through for a terminal to render. |
