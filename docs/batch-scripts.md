---
title: Batch scripts
nav_order: 4
---

# Batch scripts

A script written for SLURM runs under `molab sbatch` as it is: same
`#SBATCH` header, same `SLURM_*` variables, same array arithmetic. What it
cannot get is software the box does not have — a `module load` or a conda
environment from your cluster — so that part is up to the box
(see [Environment](#environment)).

## How the script is run

* The script is a path **on the box**. molab reads it there, never uploads it.
* It runs with its own shebang interpreter (`#!/bin/bash`, `#!/usr/bin/env python3`, ...), or `bash` if it has none.
* The working directory is `-D`/`--chdir`, else `#SBATCH --chdir`, else the
  box's `--workdir` (set at `molab init`, default `/marimo`). **Your laptop's
  current directory means nothing on the box.**
* Arguments after the script name are passed to it.
* stdin is `/dev/null`; there is no terminal.

## `#SBATCH` directives

Read only from the leading comment block — blank lines and ordinary comments
may appear in it, the first line of code ends it — exactly as sbatch does.
Later lines override earlier ones; command-line options override all of them.

| directive | honoured | notes |
|---|---|---|
| `--array` | ✅ | with `%N`; **default throttle `%1`** |
| `--job-name` / `-J` | ✅ | |
| `--output` / `-o`, `--error` / `-e` | ✅ | all of `%A %a %j %x %N %u %%` |
| `--chdir` / `-D` | ✅ | |
| `--cpus-per-task` / `-c` | ✅ | capped at the box's configured CPUs |
| `--time` / `-t` | ✅ | enforced: SIGTERM, then SIGKILL, state `TIMEOUT` |
| `--dependency` / `-d` | ✅ | `after`, `afterany`, `afterok`, `afternotok`; AND only (`,`), no `?` |
| `--export` | ✅ | `ALL`, `NONE`, `VAR=value` |
| `--mem`, `--gres`, `--partition`, `--constraint`, `--qos`, `--account`, `--nodes`, `--ntasks`, `--mail-*`, ... | ➖ | accepted and listed as ignored at submit time |

## Environment variables a job sees

| variable | value |
|---|---|
| `SLURM_JOB_ID`, `SLURM_JOBID` | the job id (an array's tasks share it) |
| `SLURM_ARRAY_JOB_ID` | the job id — arrays only |
| `SLURM_ARRAY_TASK_ID` | this task's index — arrays only |
| `SLURM_ARRAY_TASK_COUNT`, `_MIN`, `_MAX` | over the requested indices — arrays only |
| `SLURM_JOB_NAME` | `--job-name` |
| `SLURM_SUBMIT_DIR` | the working directory (there is no separate submit host) |
| `SLURM_CPUS_PER_TASK`, `SLURM_CPUS_ON_NODE` | `--cpus-per-task`, else the box's configured CPUs |
| `OMP_NUM_THREADS`, `NUMBA_NUM_THREADS` | the same, unless already set — thread pools otherwise size themselves to the host's cores |
| `SLURM_JOB_PARTITION` | `molab` |
| `SLURM_JOB_NODELIST`, `SLURMD_NODENAME`, `SLURM_SUBMIT_HOST` | the box's host name |
| `SLURM_NTASKS`, `SLURM_NNODES` | `1` |

On a real cluster each array task has its own numeric job id; here it does
not, so `%j` in an output pattern expands to `A_a` for array tasks.

## Environment

Jobs get the box's environment — **minus the notebook's own Python**. The
marimo kernel runs from a virtualenv (`/tmp/uv-venv`) and exports
`PYTHONPATH`/`VIRTUAL_ENV` for it; anything started from the kernel would
inherit them and import the notebook's packages instead of its own. molab
removes those variables and takes that venv off `PATH` before starting a job.
Pass `--notebook-env` to keep them.

This matters more than it looks: a pipeline that had been *working* on molab
turned out to depend on the notebook's Python by accident (a container found
the venv through the bound `/tmp`), and broke only when jobs stopped
inheriting it. If a script needs a particular Python, point at it explicitly.

To set up the environment a script expects — the equivalent of your cluster's
`module load`/`conda activate` lines — put it in a file on the box and pass
`--rc`:

```bash
molab sbatch --rc workflows/molab/env.sh --array=5-9 step.sh
```

`--rc` is sourced by bash before the script starts; if it fails, the job fails.

## Exit codes and states

| the task... | state | `ExitCode` |
|---|---|---|
| exits 0 | `COMPLETED` | `0:0` |
| exits non-zero | `FAILED` | `N:0` |
| is killed by a signal | `FAILED` | `0:SIG` |
| is SIGKILLed by something other than molab | `OUT_OF_MEMORY` | `0:9` — an inference: on molab that is almost always the kernel's OOM killer, and the reason field says so |
| is cancelled | `CANCELLED` | usually `0:15` |
| exceeds `--time` | `TIMEOUT` | |
| loses its runner (box restarted) | `NODE_FAIL` | |
