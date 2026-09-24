---
title: Use cases
nav_order: 4
---

# Use cases
{: .no_toc }

Seven things people use molab-slurm for, with the commands and the pitfalls
that come with each. Paths are paths **on the box**; `/marimo/myproject`
stands for your project's checkout there. Where a number comes from a real
session it is what we saw on one box, not something molab promises.

1. TOC
{:toc}

## Quick checks with srun

You want to know what the box is doing before you commit hours to it: is the
GPU there, how full is the disk, is the last job still using the GPU.

```console
$ molab-slurm sinfo
$ molab-slurm srun nvidia-smi --query-gpu=name,utilization.gpu --format=csv,noheader
NVIDIA RTX PRO 6000 Blackwell Server Edition, 83 %
$ molab-slurm srun df -h /marimo
$ molab-slurm srun -D /marimo/myproject git status --short
$ molab-slurm srun bash -c 'nproc; free -g'
```

Pitfalls:

* **Your shell reads the line first.** `$VAR`, globs, `|` and `>` act on your
  laptop, not on the box. Put anything with them in `bash -c '...'`, in single
  quotes.
* **The box's own numbers overstate it.** `nproc` and `os.cpu_count()` report
  the host (20+), and `free` more memory than the session has. On one session
  the real slice was about 4 CPUs and 32 GB of RAM (the GPU was an RTX PRO 6000
  Blackwell, 96 GB). `molab-slurm sinfo` shows the CPU count you configured at `init`.
* **There is no terminal.** stdin is `/dev/null`; a REPL, `top` or anything
  that asks a question will not work.
* Every `srun` is a real job: it has an id, shows in `sacct`, and Ctrl-C
  cancels it with its whole process tree. Its output stays in
  `/marimo/.molab/jobs/<id>/srun.out`.
* `srun` does not wait for other jobs. It runs next to whatever is already
  running, which is what you want for `nvidia-smi` and not what you want for a
  second training run.

## Set up a fresh box in one job

A new session had nothing of ours in `/marimo` — not the code, not the data,
not even pixi. Setup is your project's script (see
[Sessions and recovery](sessions.md#setting-up-a-box-is-your-projects-job)).
What worked was one setup job that runs its independent steps in parallel
inside the job — the environment install, a large reference download, the
bucket fetches — and training jobs that wait for it.

```bash
#!/bin/bash
#SBATCH -J setup
#SBATCH -o logs/setup-%j.out
#SBATCH -t 1:00:00
set -uo pipefail
bash setup/env.sh       > logs/env.log 2>&1 & envjob=$!
bash setup/reference.sh > logs/reference.log 2>&1 & ref=$!
bash setup/inputs.sh    > logs/inputs.log 2>&1 & inputs=$!
rc=0
for p in $envjob $ref $inputs; do wait "$p" || rc=1; done
exit $rc
```

```bash
molab-slurm srun git clone https://github.com/my-org/myproject /marimo/myproject
setup=$(molab-slurm sbatch --parsable -D /marimo/myproject setup.sh)
molab-slurm sbatch -D /marimo/myproject --dependency=afterok:$setup train.sh
molab-slurm tail -f $setup
```

Pitfalls:

* **The script must already be on the box.** `molab-slurm sbatch setup.sh` reads
  `setup.sh` there and never uploads it; on an empty box it fails with
  `no such script on the box: /marimo/setup.sh`; with `-D /marimo/myproject`
  it fails earlier, with
  `working directory does not exist on the box: /marimo/myproject`. Clone the
  repository first, as above, or `molab-slurm put` a self-contained script: `put`
  moves small files (up to 64 MB) and creates missing directories; give it an
  absolute path.
* **A bare `wait` returns 0** even when a step failed. Wait on each PID, as
  above, or the job reports `COMPLETED` over a broken environment.
* Use `sbatch`, not `srun`, for a setup that takes more than a few minutes:
  Ctrl-C on `srun` cancels the job; Ctrl-C on `tail -f` only stops watching
  it.
* The box has none of your laptop's tools, credentials or environment
  variables. The bucket client and its login are part of the setup.
* Jobs do not inherit the notebook's Python (see
  [Batch scripts](batch-scripts.md#environment)). Point later jobs at the
  environment the setup built, with `--rc` or an explicit interpreter path.
* Make each step skip itself when its output is already there. Setup then
  costs little to rerun, and you will rerun it in every new session.

## Run an existing SLURM pipeline unchanged

Your pipeline is a set of `#SBATCH` scripts from a cluster, chained with
dependencies. With the repository on the box (cloned as in the setup above),
submit the same scripts:

```console
$ molab-slurm sbatch -D /marimo/repo workflows/SLURM/03.0.train_bias_model.sh
molab-slurm: not enforced on molab, ignored: --gres=gpu:1, --mem=128G, --partition=gpu
Submitted batch job 13
```

The driver that submitted the chain runs on your machine, with `sbatch`
replaced by `molab-slurm sbatch`:

```bash
train=$(molab-slurm sbatch --parsable -D /marimo/repo --rc workflows/molab/env.sh \
        workflows/SLURM/03.0.train_bias_model.sh)
molab-slurm sbatch -D /marimo/repo --rc workflows/molab/env.sh --dependency=afterok:$train \
        workflows/SLURM/03.1.select_bias.sh
```

`#SBATCH --array`, `--time`, `--output` patterns, `--chdir` and the `SLURM_*`
variables work as on the cluster, except that an array runs one task at a
time unless it says `%N`; [Batch scripts](batch-scripts.md) has the full list.

Pitfalls:

* **`module load` and `conda activate` have nothing to load.** Put the box's
  equivalent in a file and pass it with `--rc`; the job fails if the file
  fails.
* **There is no `sbatch` or `srun` on the box.** molab-slurm installs nothing
  there, so a script that submits its next step from inside a job, or runs
  `srun` job steps, needs changing: submit the chain from your machine.
* **Dependencies name whole jobs.** `--dependency=afterok:13_5` names no job
  here; the dependent job ends `CANCELLED` with
  `DependencyNeverSatisfied (no job 13_5)`. A dependency on an array waits for
  all of its tasks. `?` (OR) and `singleton` are refused at submit time.
* `--mem` and `--gres` reserve nothing. A process that runs out of memory is
  killed by the kernel. The task shows `OUT_OF_MEMORY` only when that process
  is the task's own; when it is a program the script started, the script
  usually exits 137 and the task ends `FAILED`, `137:0`. End the script with
  `exec python train.py ...` to make the training process the task's own
  (with `--wrap`, exec there too: `--wrap 'exec bash train.sh'`).
* Relative paths are relative to `-D`/`#SBATCH --chdir`, or the box's
  `--workdir`; your laptop's current directory means nothing on the box.

## A seed or hyperparameter sweep on one GPU

Five seeds of the same model, one GPU. An array with `%1` runs them one after
another, each task picking its seed from `SLURM_ARRAY_TASK_ID`:

```bash
#!/bin/bash
#SBATCH -J seed_sweep
#SBATCH --array=0-4%1
#SBATCH -o logs/%x-%A_%a.out
#SBATCH -t 3:00:00
set -euo pipefail
seeds=(1 2 3 4 5)
seed=${seeds[$SLURM_ARRAY_TASK_ID]}
out=runs/seed_$seed
if [ -e "$out/done" ]; then echo "seed $seed: done already"; exit 0; fi
python train.py --seed "$seed" --out "$out" --workers "$SLURM_CPUS_PER_TASK"
touch "$out/done"
```

```console
$ molab-slurm sbatch -D /marimo/myproject sweep.sh
Submitted batch job 12
$ molab-slurm squeue
JOBID       PARTITION  NAME        USER  ST  TIME     NODES  NODELIST(REASON)
12_0        molab      seed_sweep  me    R   1:02:13  1      gpu
12_[1-4%1]  molab      seed_sweep  me    PD  0:00     1      (JobArrayTaskLimit)
$ molab-slurm scancel 12_3            # drop one seed; the others still run
```

Pitfalls:

* **`%1` is the default; write it anyway**, so the script says what it
  relies on. `%2` does not give you a second GPU: it puts two runs on the same
  GPU and in the same memory. A session was killed after about 4.5 hours —
  most likely from memory — while two GPU training jobs ran at once. Heavy
  jobs were safer one at a time.
* **`%1` only limits one array.** Two separate `molab-slurm sbatch` submissions run
  at the same time; order them with `--dependency`.
* **Environment variables with commas cannot go through `--export`.**
  `--export=SEEDS=1,2` is split on the comma and refused
  (`--export: '2' must be ALL, NONE or VAR=value`). Put them in `--wrap`
  (`--wrap 'SEEDS=1,2 bash run.sh'`) or `export` them in an `--rc` file: a
  plain `SEEDS=1,2` line there does not reach the script.
* `--time` is per task, not for the whole array.
* The script is read when each task starts, the `#SBATCH` lines when you
  submit. Editing `sweep.sh` mid-sweep changes the tasks that have not
  started yet.
* Size data loaders from `SLURM_CPUS_PER_TASK`, not `os.cpu_count()`, which
  reports the host's cores. `OMP_NUM_THREADS` and `NUMBA_NUM_THREADS` are set
  to it for you unless already set; other thread pools are not.

## A long benchmark series while you are away

A series of runs, each an hour or more, that should keep going after you
close the laptop. Submit the whole chain at once, each job depending on the one
before; each job copies its results to a bucket as soon as it finishes.

```bash
prev=
for cfg in baseline fp16 bf16 large_batch; do
  prev=$(molab-slurm sbatch --parsable -D /marimo/myproject -J "bench_$cfg" -o 'logs/%x-%j.out' \
         ${prev:+--dependency=afterany:$prev} --wrap "CONFIG=$cfg bash bench.sh")
done
```

```bash
#!/bin/bash
# bench.sh: one configuration; skips itself if its results are already in the bucket
set -euo pipefail
dest=gs://my-bucket/bench/$CONFIG
if gcloud storage ls "$dest/metrics.json" > /dev/null 2>&1; then
  echo "$CONFIG: already in $dest"; exit 0
fi
python bench.py --config "$CONFIG" --out "results/$CONFIG"
gcloud storage cp -r "results/$CONFIG" gs://my-bucket/bench/
```

(`gcloud storage` stands for whatever bucket client your setup installs.)

```text
JOBID  PARTITION  NAME               USER  ST  TIME   NODES  NODELIST(REASON)
21     molab      bench_baseline     me    R   41:07  1      gpu
22     molab      bench_fp16         me    PD  0:00   1      (Dependency)
23     molab      bench_bf16         me    PD  0:00   1      (Dependency)
24     molab      bench_large_batch  me    PD  0:00   1      (Dependency)
```

Pitfalls:

* **`afterany`, not `afterok`, between independent runs.** With `afterok`,
  one failed or out-of-memory run cancels every job after it
  (`DependencyNeverSatisfied`). With `afterany` the chain moves on; a job you
  `scancel` is skipped the same way. Keep `afterok` for real prerequisites,
  such as the setup job.
* **Cancelling a pending job in the middle starts the next one at once.** A
  cancelled job is finished as far as `afterany` is concerned, so after
  `molab-slurm scancel 22` job 23 starts while 21 is still running: two heavy jobs
  at once. Cancel from the end of the chain, or cancel the rest and resubmit.
* **Know where each variable expands.** In double quotes (`"CONFIG=$cfg ..."`)
  it expands on your laptop, which is what the loop wants; `SLURM_*` variables
  exist only on the box and need single quotes.
* **Closing the laptop stops only what runs on the laptop.** The jobs and
  their dependency waits are on the box. `molab-slurm tail -f` loses its connection;
  run it again (it starts from the beginning of the file) or use
  `molab-slurm tail -n 50`.
* **The session is the limit, not the laptop.** molab's idle policy is not
  documented: keep the notebook open in a browser on a machine that stays
  awake, or run `molab-slurm keepalive` from one that stays online (see
  [Sessions and recovery](sessions.md#keeping-a-session-alive)). Copying each
  result as its job ends means a dead session costs one run, not the series.
* A job whose runner is gone (`NODE_FAIL`) counts as finished for
  `afterany`, so the chain does not hang on it.

## Recover from a session that died

`molab-slurm squeue` says `cannot reach https://sb-...`, or `HTTP 403 listing
sessions (wrong token?)`. The session has ended, and with it the jobs and
everything under `/marimo/.molab`.
[Sessions and recovery](sessions.md#when-a-session-dies) has the steps; the
short form:

```python
# in a cell of the new session's notebook, open in your browser
import molab_slurm as mos
mos.init_command(name="gpu")
```

```bash
molab-slurm init https://sb-0123456789abcdef.sb.molab.run/ <token> --name gpu   # the widget's line; "copy" includes the token
molab-slurm sinfo                                                               # is the GPU there?
molab-slurm srun bash -c '[ -d /marimo/myproject ] || git clone https://github.com/my-org/myproject /marimo/myproject'
setup=$(molab-slurm sbatch --parsable -D /marimo/myproject setup.sh)
# then resubmit the chain, with --dependency=afterok:$setup on its first job
```

Pitfalls:

* **Do not paste the address your browser shows.** molab opens the notebook
  at `https://sb-<id>-session.sb.molab.run/session/...`, a front end that
  rejects the token; the server is `https://sb-<id>.sb.molab.run/`.
  `init_command()` does that mapping for you.
* `init_command()` fills in the URL only once the notebook is open in a
  browser: the browser is the only place that knows it. It needs molab-slurm
  installed in the notebook's environment (for example
  `git+https://github.com/broadinstitute/molab-slurm` in marimo's package
  manager) and anywidget, which molab notebooks have. If it cannot find the
  token (it reads it from the marimo server's command line, on Linux only), it
  says so; copy the token from molab's connect snippet instead.
* **Old job ids mean nothing.** With `/marimo/.molab` gone, ids start again
  from 1: a `--dependency` on an id you wrote down either names no job or
  names an unrelated new one. Capture ids with `--parsable` in the script
  that submits.
* **Check the GPU before resubmitting.** A recreated session has come back
  without one, and GPU code then runs on the CPU without failing.
* Your code has to be back on the box before `sbatch` can read it: clone it
  again if it is gone, and rerun the setup first. Steps that skip work whose
  results are already in the bucket make the resubmitted chain redo only what
  was running when the session died; a check for a local file, such as the
  sweep's `runs/seed_N/done`, finds nothing if `/marimo` came back empty.

## Drive the box from a script or an AI coding agent

An agent or a CI-style script submits work, waits for it, and reads the
result. Nothing in molab-slurm prompts, so it works unattended.

This is also what makes it safe for an agent to keep checking on a long run.
An agent that runs code directly in the notebook kernel (the marimo-pair way)
shares that kernel with whatever else runs there, and marimo interrupts the
kernel when one of its requests times out or disconnects: the long run stops.
With molab-slurm the run is a job outside the kernel, and each check is a
short call. See
[How it works](how-it-works.md#why-work-never-runs-in-the-kernel).

```bash
export MOLAB_URL=https://sb-0123456789abcdef.sb.molab.run/
export MOLAB_TOKEN=...                        # rather than an argument to molab-slurm init, which ps can see
jid=$(molab-slurm sbatch --parsable -D /marimo/myproject --wrap 'bash run.sh')
molab-slurm tail -f "$jid" > run.log; rc=$?   # blocks until the task ends; rc is its exit code
molab-slurm sacct -j "$jid" -s FAILED,TIMEOUT,OUT_OF_MEMORY,NODE_FAIL,CANCELLED | tail -n +2
```

Any line from the last command is a task that did not complete.

| exit code | meaning |
|---|---|
| `0` | success |
| `1` | an error: cannot reach the box, no such script, no box configured, an unknown job id for `scancel` |
| `2` | a usage error: bad option, bad `--array`, unsupported dependency. An unknown `--option=value` to `sbatch` or `srun` is not one: it is listed as ignored and the job runs. An unknown `--option` without `=` takes the next word as its value |
| the task's | `srun`, `tail -f` and `sbatch --follow`; a task killed by signal N gives 128+N; one that never started (a dependency never satisfied, cancelled while pending) or lost its runner gives `1` |
| `130` | Ctrl-C while streaming (`srun` also cancels the job) |
| `255` | the connection dropped while streaming; the job is still on the box |

Pitfalls:

* **`squeue` and `sacct` print tables only.** There is no `--format`,
  `--noheader` or `--parsable` for them: skip the header line and split on
  runs of two or more spaces. `sacct` cuts `JobName` at 24 characters.
* **An empty `squeue -j ID` does not mean finished.** A job that does not
  exist prints the same header-only table; check `sacct -j ID`.
* `tail -f ID` on an array follows only its first task. For the whole array,
  poll `squeue -j ID` until only the header is left, then read `sacct -j ID`.
* `--parsable` prints only the id on stdout. Warnings, such as the
  `not enforced on molab, ignored: ...` line, go to stderr, prefixed `molab-slurm:`.
* **Prefer `sbatch` to `srun` for anything long.** `srun` holds the command
  open until the job ends, which can run into an agent's own command timeout;
  `sbatch` returns at once and `tail -n 50` reads the latest output without
  pulling a whole log into the agent's context.
* With `MOLAB_URL` and `MOLAB_TOKEN` the config file is not read: the box is
  named `env`, with the defaults of 4 CPUs and `/marimo`. `--box NAME` still
  wins over them.
* **The token is a shell on the box.** An agent holding it can run anything
  there, as root on molab; give it one only for a session you are willing to
  have it change.
