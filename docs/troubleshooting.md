---
title: Troubleshooting
nav_order: 11
---

# Troubleshooting

| symptom | cause and fix |
|---|---|
| `HTTP 403 listing sessions (wrong token?)` | The token is wrong or from an older session — every new session has a new URL **and** token. Copy both from molab's connect snippet and `molab init` again. |
| `cannot reach https://sb-...: ... CERTIFICATE_VERIFY_FAILED` | Your Python has no CA bundle (python.org's macOS builds, until "Install Certificates.command" is run). molab-slurm falls back to `certifi` and `/etc/ssl/cert.pem`; install `certifi` if neither exists. |
| `cannot reach ...` / connection refused | The session is gone. [Start a new one yourself](sessions.md) and `molab init` its URL. |
| `no active notebook session` | The notebook server is up but no notebook is open. Open it in a browser. |
| `several sessions on this server` | More than one notebook is open on that server; pass `--session ID`. |
| `reply truncated ... (the kernel caps one reply at ~1 MB)` | A reply exceeded the kernel API's limit. molab-slurm moves data in 512 KB pieces; if you see this, it is a bug — please report it. |
| GPU code is extremely slow; `sinfo` says `gpus 0` | The session has no GPU attached — this happens when molab recreates a session. Restart it on a GPU machine. |
| A job's Python imports the wrong packages | It inherited the notebook's venv. molab-slurm strips it by default; check you did not pass `--notebook-env`, and set the interpreter explicitly in `--rc`. |
| A task shows `NODE_FAIL` | Its runner stopped heartbeating: the box restarted or the runner was killed. Resubmit. |
| A task shows `OUT_OF_MEMORY` | It was SIGKILLed by something other than molab-slurm — on molab, almost always the kernel's OOM killer. The box's real memory is smaller than what `free` reports. |
| `molab tail` shows one enormous line | Progress bars use carriage returns. `tail -n N` splits on them; `tail -f` passes them through for a terminal to render. |
