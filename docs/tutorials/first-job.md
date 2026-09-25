---
title: Your first job
parent: Tutorials
nav_order: 1
---

# Your first job

**Goal:** connect a molab GPU session to your laptop, run a command on it, run
a one-minute batch job, watch it, and copy its result back.

You need a molab account that can start GPU sessions, and Python ≥ 3.9 with
[uv](https://docs.astral.sh/uv/) on your laptop.

## 1. Install the CLI

The package gives you one command, `molab-slurm`:

```bash
uv tool install git+https://github.com/broadinstitute/molab-slurm
molab-slurm --version
```

You should see the version number (`0.2.0` as of this writing). molab-slurm
is not on PyPI yet; the git URL is the install (`pipx install
git+https://github.com/broadinstitute/molab-slurm` works too). Nothing gets
installed on the box.

## 2. Start a session

In molab, open a notebook and pick a GPU machine. This is always your step:
molab-slurm cannot start a session, only connect to one that is running
([Sessions and recovery](../sessions.md)).

Leave the notebook open in a browser tab. molab-slurm reaches the box through
that notebook's server, and answers `no active notebook session` when no
notebook is open on it.

## 3. Get the connect line from the notebook

The session's URL and token are both known inside the session, just not in
one place; `init_command` puts them together. First make the package importable in the
notebook: add `git+https://github.com/broadinstitute/molab-slurm` with
marimo's package manager. Then, in a cell:

```python
import molab_slurm as mos
mos.init_command(name="gpu")
```

You should see the line, with a **copy** and a **show token** button, and a
note under it:

```text
molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ <token hidden> --name gpu
the token is a shell on this box: treat it like an ssh key
```

Press **copy**. What is going on:

* The URL is not the one in your address bar. molab shows the notebook at
  `https://sb-0123456789abcdef-session.sb.molab.run/session/...`, a front end
  that rejects the token; the line uses the notebook server behind it,
  `https://sb-0123456789abcdef.sb.molab.run/`.
* Only the browser knows that address — the kernel sees molab's internal proxy
  host — so the widget sends it back from the page, and reads `waiting for the
  browser's address...` until the kernel has it. From then on the widget's
  `.url` and `.command` hold the line for Python too.
* The token is read from the marimo server's command line (Linux only). If
  none is found the line shows `<token>` and the note says so; fill it in from
  molab's connect snippet.
* If the browser refuses clipboard access, **copy** shows the token and
  selects the line for you to copy by hand.
* `cpus=` and `workdir=` add `--cpus` and `--workdir` to the line:
  `mos.init_command(name="gpu", cpus=4)`.
* `init_command()` needs anywidget, which molab notebooks have. Outside
  molab, install it next to molab-slurm (the package's `notebook` extra).

Without the widget: ask the notebook to connect an agent, and molab shows a
snippet with the notebook's URL and a token. Put them in the same line:
`molab-slurm init <url> <token> --name gpu`.

## 4. Save the box

Paste the line into a terminal on your laptop:

```bash
molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ <token> --name gpu
```

You should see:

```text
saved box 'gpu' to /Users/me/.config/molab-slurm/config.json (default)
box       gpu  (https://sb-0123456789abcdef.sb.molab.run/)
host      a1b2c3d4-...-xyz12  python 3.13.11
cpus      4 (configured; the box reports the host's count)
gpus      1  (NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB)
disk      22.2 GB used under /marimo/.molab's filesystem
```

`init` checks the token before it saves anything. A wrong or stale token
gives:

```text
molab-slurm: not saved: https://sb-0123456789abcdef.sb.molab.run: HTTP 403 listing sessions (wrong token?)
```

`gpu` is now the default box, so the commands below need no `--box`. The name
is what you and your scripts refer to; the URL changes with every session.
The token is code execution on the box: the config file is mode 600, and it
does not belong in a repository ([The token is a shell on the
box](../install.md#the-token-is-a-shell-on-the-box)).

## 5. Check the box

```bash
molab-slurm sinfo
```

You should see the box description `init` printed, then a `jobs` line:

```text
gpus      1  (NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB)
disk      22.2 GB used under /marimo/.molab's filesystem
jobs      0 active, 0 task(s) running
```

**Check the `gpus` line every time you connect a session.** A session
without its GPU says:

```text
gpus      0  -- the NVIDIA driver entry is there but no GPU is attached; GPU jobs will run on the CPU
```

Nothing else fails in that case; GPU code just runs on the CPU, very slowly.
Start the session again on a GPU machine.

The `cpus` line is what you told `init` (default 4), not what the box
reports. molab boxes report the host's CPU count (20+) and more memory than a
session gets; on one session we measured about 4 CPUs and 32 GB. Jobs get
`SLURM_CPUS_PER_TASK` set to the configured count, and `OMP_NUM_THREADS` and
`NUMBA_NUM_THREADS` too unless already set.

## 6. Run a command

```bash
molab-slurm srun pwd
molab-slurm srun nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
```

You should see:

```text
/marimo
NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB
```

`srun` runs the command on the box as a job and streams its output until it
ends. It runs in `/marimo`, the box's `--workdir`: your laptop's current
directory means nothing on the box, and `-D DIR` picks another one.

* molab-slurm's exit code is the command's, so `srun` works in `&&` chains and
  scripts on your laptop.
* **Ctrl-C cancels the job**, as with SLURM's `srun`.
* If the connection drops instead, the job keeps running; molab-slurm says which
  job is still on the box, and `molab-slurm tail -f ID` picks its output up again.

Every `srun` is a real job with an id. These two were jobs 1 and 2.

## 7. Submit a batch job

A job you do not want to sit and watch goes through `sbatch`. `--wrap` runs a
command line without a script:

```bash
molab-slurm sbatch -J count --wrap 'for i in $(seq 1 12); do echo "step $i of 12"; sleep 5; done; nvidia-smi > gpu.txt'
```

You should see:

```text
Submitted batch job 3
```

* The single quotes send the command to the box as written; bash on the box
  expands `$i`.
* The job runs detached on the box. You can close your laptop.
* Its output goes to `slurm-%j.out` in the working directory:
  `/marimo/slurm-3.out`. `gpu.txt` lands next to it.
* Without `-J` the job would be called `wrap`.

## 8. Watch it

```bash
molab-slurm squeue
```

You should see:

```text
JOBID  PARTITION  NAME   USER  ST  TIME  NODES  NODELIST(REASON)
3      molab      count  me    R   0:15  1      gpu
```

`NODELIST` is the name you saved the box under, not its host name. With nothing pending or running, `squeue`
prints only the header.

Stream the job's output:

```bash
molab-slurm tail -f 3
```

```text
step 1 of 12
step 2 of 12
step 3 of 12
```

`tail -f` returns when the job ends, with the job's exit code; if the job has
already ended, it prints the whole output and returns at once. Ctrl-C only
stops following — `molab-slurm: stopped following; job 3 keeps running` — and
running it again streams the file from the beginning. `molab-slurm tail -n 5 3`
prints the last five lines and returns.

`tail -f`, like `srun`, asks the box for news every few seconds while the
job runs. That is fine for this one-minute job. For a job that runs for hours,
use `molab-slurm wait --after 3h ID`, which makes no calls for the first three
hours and then one every 4 minutes until the job ends, then
`molab-slurm tail -n 20 ID`, and do not leave `tail -f` or
`watch molab-slurm squeue` running: sessions have ended while the box was
polled like that ([For AI agents](../ai-agents.md)).

When it has finished:

```bash
molab-slurm sacct
```

You should see all three jobs:

```text
JobID  JobName     State      ExitCode  Elapsed  Start                End
1      pwd         COMPLETED  0:0       0:00     2026-09-24T10:02:11  2026-09-24T10:02:11
2      nvidia-smi  COMPLETED  0:0       0:01     2026-09-24T10:02:40  2026-09-24T10:02:41
3      count       COMPLETED  0:0       1:01     2026-09-24T10:03:05  2026-09-24T10:04:06
```

`sacct` shows the newest 20 jobs, finished or not; `-j 3` picks one.
`ExitCode` is SLURM's `exit:signal`, and times are in your laptop's time zone.

## 9. Copy the result back

```bash
molab-slurm get /marimo/gpu.txt
molab-slurm get /marimo/slurm-3.out
```

You should see where each file was written — your current directory, unless
you give a local path after the remote one:

```text
gpu.txt
slurm-3.out
```

Give the path on the box in full. `molab-slurm open /marimo/plot.png` fetches a
file and opens it (Preview on macOS), and `molab-slurm put LOCAL REMOTE` goes the
other way. All three are for small files, up to 64 MB; larger results should
leave the box through a bucket, copied by the job itself. The next tutorial
does that.

## Next

[A small pipeline](pipeline.md): a setup job for a fresh box, an `#SBATCH`
array, dependencies, and results copied to a bucket as each job finishes.
