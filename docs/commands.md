---
title: Commands
nav_order: 5
---

# Commands
{: .no_toc }

Every command takes `--box NAME` before the verb to pick a saved box
(`molab-slurm --box gpu squeue`). Exit codes follow SLURM: `0` on success,
`srun` returns the command's own.

1. TOC
{:toc}

## sbatch

```text
molab-slurm sbatch [options] SCRIPT [ARGS...]
molab-slurm sbatch [options] --wrap 'COMMAND'
```

`SCRIPT` is a path **on the box**, relative to the working directory.
molab-slurm reads its `#SBATCH` lines the way sbatch does — only in the leading comment
block, later lines winning — and command-line options override them.

```console
$ molab-slurm sbatch --array=5-9%1 -D /marimo/repo slurm/train.sh
Submitted batch job 13
$ molab-slurm sbatch --parsable --dependency=afterok:13 --wrap 'bash summarize.sh'
14
```

| option | meaning |
|---|---|
| `-a, --array=SPEC` | `0-3`, `0,2,4`, `1-9:2`, with `%N` to run at most N at once. **The default throttle is `%1`**: one box, usually one GPU |
| `-J, --job-name=NAME` | default: the script's file name, or `wrap` |
| `-D, --chdir=DIR` | working directory on the box (default: the box's `--workdir`) |
| `-o, --output=PATTERN` | default `slurm-%j.out`, or `slurm-%A_%a.out` for arrays; relative to the working directory |
| `-e, --error=PATTERN` | separate stderr file; by default stderr goes to the output file |
| `-c, --cpus-per-task=N` | sets `SLURM_CPUS_PER_TASK`, and `OMP_NUM_THREADS` / `NUMBA_NUM_THREADS` unless already set; capped at the box's CPU count, default all of them |
| `-t, --time=LIMIT` | walltime, any SLURM format (`30`, `2:00:00`, `1-12`); the task ends `TIMEOUT` |
| `-d, --dependency=LIST` | `afterok:ID[:ID...]`, `afterany`, `afternotok`, `after`, comma-separated (AND) |
| `--export=SPEC` | `ALL` (default), `NONE`, plus `VAR=value` pairs |
| `--wrap 'CMD'` | run a command instead of a script |
| `--parsable` | print only the job id |
| `-W, --wait` | don't return until the job ends, then exit with its code, checking every 4 minutes, like [`wait`](#wait) |
| `--rc FILE` | *molab-slurm only.* source FILE on the box before the script (e.g. an `env.sh`) |
| `--follow` | *molab-slurm only.* stream the (first) task's output after submitting; for short jobs only, like `srun` |
| `--notebook-env` | *molab-slurm only.* keep the notebook kernel's Python on PATH — see [Batch scripts](batch-scripts.md#environment) |

Anything else a script asks for — `--mem`, `--gres`, `--partition`,
`--constraint`, `--qos`, `--account`, `--mail-*` — is accepted and reported:

```text
molab-slurm: not enforced on molab, ignored: --gres=gpu:1, --mem=128G, --partition=gpu
```

Pattern substitutions in `--output`/`--error`: `%A` array job id, `%a` task
index, `%j` job id (`A_a` for an array task), `%x` job name, `%N` host,
`%u` user, `%%` a literal `%`.

## srun

```text
molab-slurm srun [options] COMMAND [ARGS...]
```

Runs a command as a job and streams its output until it ends; molab-slurm's
exit code is the command's. **Ctrl-C cancels it**, like srun. Takes the sbatch
options that make sense for one task: `-D`, `-t`, `-J`, `-c`, `--export`,
`--rc`, `--notebook-env`.

```console
$ molab-slurm srun -D /marimo/repo git log --oneline -1
fbea595 Give container steps their own bootstrap Python, and check for the GPU
$ molab-slurm srun nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader
NVIDIA RTX PRO 6000 Blackwell Server Edition, 83 %
```

Every `srun` is a real job with an id, so `sacct` shows it afterwards.

While it runs, `srun` checks the job and reads its output, two or more calls
to the notebook kernel each time: every second at first, backing off to every
10 seconds after about a minute. Keep it for commands that finish within a
minute or so. Submit anything longer with `sbatch` and block on it with
[`wait`](#wait) ([For AI agents](ai-agents.md)).

## squeue

```text
molab-slurm squeue [-j ID[,ID...]]
```

Pending and running jobs. Running array tasks get a row each; pending tasks
of an array collapse into one, with SLURM's reason:

```text
JOBID       PARTITION  NAME       USER  ST  TIME  NODES  NODELIST(REASON)
13_5        molab      sweep      me    R   8:23  1      gpu
13_[6-9%1]  molab      sweep      me    PD  0:00  1      (JobArrayTaskLimit)
14          molab      summarize  me    PD  0:00  1      (Dependency)
```

`ST` is SLURM's code: `PD` pending, `R` running, `CD` completed, `F` failed,
`CA` cancelled, `TO` timeout, `NF` node fail, `OOM` out of memory.

## sacct

```text
molab-slurm sacct [-j ID[,ID...]] [--last N] [-s STATE[,STATE...]]
```

One row per task, finished or not; the newest 20 jobs without `-j`.
`ExitCode` is SLURM's `exit:signal`: `3:0` exited 3, `0:15` was killed by
SIGTERM (a cancel).

```text
JobID  JobName  State      ExitCode  Elapsed  Start                End
2_0    demo     COMPLETED  0:0       0:08     2026-09-23T15:34:29  2026-09-23T15:34:37
4      longone  CANCELLED  0:15      0:01     2026-09-23T15:34:29  2026-09-23T15:34:31
```

Times are shown in your machine's local time zone.

## scancel

```text
molab-slurm scancel ID | ID_INDEX ...
molab-slurm scancel --all
```

Cancels a whole job or one array task — pending ones never start, running
ones have their **whole process tree** stopped (SIGTERM, then SIGKILL after 10
seconds) and end `CANCELLED`. A job waiting on `afterok` of a cancelled job
ends `CANCELLED` with reason `DependencyNeverSatisfied`.

## tail

```text
molab-slurm tail [-f] [-n N] ID | ID_INDEX
```

Shows a task's output file (wherever `--output` put it). `-f` streams until
the task ends and exits with its code. The job does not depend on it: if your
laptop sleeps or the connection drops, the task keeps running, and `tail -f`
run again streams the file from the beginning. Like `srun`, `-f` calls the
box every second at first and every 10 seconds after about a minute: keep it
for short jobs, and for a long one use [`wait`](#wait), then `-n`. For an array job, `ID` alone means its first
task. `-n` treats carriage returns as line breaks, so progress bars show
their latest state instead of one enormous line.

## wait

```text
molab-slurm wait [--after DURATION] [--every DURATION] [-q] ID | ID_INDEX
```

Waits until a job ends, then prints one line per task (`12 FAILED 3:0`) and
exits with the first failed task's exit code, or 0. For an array job, `ID`
means every task; `ID_INDEX` only that one.

The waiting happens on your machine. Each check is **one** status call to the
notebook kernel, made every `--every` (4 minutes by default, as often as
`keepalive`; 60 seconds at least). `--after` holds off the first check, for
example for the job's expected run time, and makes no calls meanwhile:

```console
$ molab-slurm sbatch --parsable train.sh
12
$ molab-slurm wait --after 2h 12 && molab-slurm tail -n 30 12
12 COMPLETED 0:0
...
```

Ctrl-C stops waiting and leaves the job running. `sbatch --wait` submits and
then does the same.

## sinfo

```text
molab-slurm sinfo
```

What the box has, checked on the box:

```text
box       gpu  (https://sb-0123456789abcdef.sb.molab.run/)
host      a1b2c3d4-...-xyz12  python 3.13.11
cpus      4 (configured; the box reports the host's count)
gpus      1  (NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB)
disk      22.2 GB used under /marimo/.molab's filesystem
jobs      2 active, 1 task(s) running
```

**Run it after every new session.** A molab session recreated after a crash
can come back without its GPU; nothing fails, GPU code just runs on the CPU.
`sinfo` says `gpus 0 -- the NVIDIA driver entry is there but no GPU is
attached` in that case.

## put, get, open

```text
molab-slurm put LOCAL REMOTE          # REMOTE may be an existing directory
molab-slurm get REMOTE [LOCAL]
molab-slurm open REMOTE [REMOTE...]   # fetch, then open with the OS viewer
```

For small files (up to 64 MB), moved through the kernel API in 512 KB pieces.
`open` caches under `~/.cache/molab-slurm/open/<box>/<remote path>`, so two
`plot.png` from different directories never collide, and opens PNGs and
PDFs in Preview on macOS (`xdg-open` elsewhere). Move large files through a
bucket instead.

## keepalive

```text
molab-slurm keepalive [--every 4m] [--for 8h] [-q]
```

Pings the kernel on a timer, every 4 minutes by default. **Whether molab's
idle timer counts kernel API traffic is not documented** — this is the
activity molab-slurm can generate, not a guarantee. Keeping the notebook open
in a browser tab is the other half. Do not make `--every` shorter than the
default: each ping is a call like any other ([For AI agents](ai-agents.md)).

## init, boxes

See [Install and connect](install.md).
