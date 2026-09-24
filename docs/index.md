---
title: Home
nav_order: 1
---

# molab-slurm

SLURM's commands for a molab box you cannot ssh into.
{: .fs-6 .fw-300 }

A molab session is a gVisor sandbox, usually with a GPU, that you reach only
through its marimo notebook server. There is no ssh, no scheduler, and the
session can be recreated from scratch at any time. `molab-slurm` gives you
the interface you already know from a cluster:

```console
$ molab sbatch --array=0-3%1 train.sh
Submitted batch job 7
$ molab squeue
JOBID     PARTITION  NAME      USER  ST  TIME  NODES  NODELIST(REASON)
7_0       molab      train.sh  me    R   1:12  1      gpu
7_[1-3%1] molab      train.sh  me    PD  0:00  1      (JobArrayTaskLimit)
$ molab scancel 7_2
$ molab sacct -j 7
JobID  JobName   State      ExitCode  Elapsed  Start                End
7_0    train.sh  COMPLETED  0:0       4:03     2026-09-23T15:34:29  2026-09-23T15:38:32
7_1    train.sh  RUNNING    0:0       0:41     2026-09-23T15:38:33  Unknown
7_2    train.sh  CANCELLED  0:0       0:00     Unknown              2026-09-23T15:39:02
7_3    train.sh  PENDING    0:0       0:00     Unknown              Unknown
```

## What you get

| | |
|---|---|
| `molab sbatch` | reads `#SBATCH` lines, runs array tasks with `%N` throttling, `--time`, `--dependency`, `--output` patterns, and sets the `SLURM_*` variables scripts rely on |
| `molab srun` | runs a command and streams its output; Ctrl-C cancels it; the exit code is the command's |
| `molab squeue` / `sacct` | SLURM-shaped tables, including collapsed pending arrays |
| `molab scancel` | cancels a job or one array task, and the whole process tree with it |
| `molab tail -f` | streams a job's output file, resumable after your laptop sleeps |
| `molab sinfo` | what the box really has — including whether the GPU is attached |
| `molab put` / `get` / `open` | small files both ways; `open` shows a PNG or PDF in Preview |
| `molab keepalive` | touches the kernel on a timer |

Jobs run outside the notebook kernel. marimo interrupts the kernel when an agent's request times out or
disconnects, and that would stop long work running *in* it, such as a training loop in a cell. molab's calls are
short, so agents can check on jobs as often as they like. See
[How it works](how-it-works.md#why-work-never-runs-in-the-kernel).

## Where to start

1. [Install and connect](install.md) — one command, then `molab init`.
2. [Tutorials](tutorials/index.md) — a first job, a small pipeline, and getting back to work when the session ends.
3. [Use cases](use-cases.md) — quick checks, a fresh box, an existing SLURM pipeline, sweeps, long series, recovery, scripts and agents, each with its pitfalls.
4. [Commands](commands.md) — every verb and its options.
5. [Python API](api.md) — `init_command()` for the session's notebook, the config file, environment variables and exit codes.
6. [Batch scripts](batch-scripts.md) — which `#SBATCH` options are honoured and which environment variables are set.
7. [Sessions and recovery](sessions.md) — **you start molab sessions yourself**; what to do when one ends.
8. [How it works](how-it-works.md) — the kernel API, the runner, and why jobs survive your laptop.
9. [Differences from SLURM](differences.md) — read this before relying on anything.
10. [Troubleshooting](troubleshooting.md) and [related projects](related.md).

molab-slurm connects to a session that is already running; it never starts,
stops or sets one up. Starting a session is done in molab, by you; setting up
the box for your project is your project's own script, run with `molab srun`.
