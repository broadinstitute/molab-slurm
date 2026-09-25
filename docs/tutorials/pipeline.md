---
title: A small pipeline
parent: Tutorials
nav_order: 2
---

# A small pipeline

**Goal:** run a project on a fresh session the way you would on a cluster: a
setup job that installs what the project needs, a training array throttled
with `%N`, a last step that waits for it with `--dependency`, logs where you
want them, and every result copied to a bucket by the job that made it.

It starts from a connected box named `gpu` ([Your first job](first-job.md)).
The project is an example: `myproject`, `pixi`, `gcloud`, `train.py` and
`summarize.py` stand for whatever yours uses. Only the `molab-slurm` commands
are the tool's; the scripts are yours, and molab-slurm reads nothing in them but
the shebang and the `#SBATCH` lines. The bucket commands assume `gcloud` on
the box with access to `gs://my-bucket/` — on a fresh box, getting it there
is part of your setup too.

```text
myproject/
├── env.sh          environment for every job (--rc)
├── setup.sh        everything a fresh box needs
├── train.sh        one fold per array task
├── submit.sh       runs on your laptop; submits the whole pipeline
├── pixi.toml
├── train.py
└── summarize.py
```

## 1. Put the project on the box

A new session has nothing of yours on it. Clone the project with a short
`srun`, which runs in `/marimo`, the default working directory:

```bash
molab-slurm srun git clone https://github.com/my-org/myproject /marimo/myproject
```

You should see git's own output. On a new session this is job 1, and the ids
below follow from that. (A private repository needs credentials on the box
first.)

This comes first because `sbatch` reads a script **on the box** when you
submit it. Before the clone, it stops with:

```text
molab-slurm: working directory does not exist on the box: /marimo/myproject
```

To update the code later, `molab-slurm srun -D /marimo/myproject git pull`. A task
reads its script when it starts, so a pull also changes what pending tasks
will run; `#SBATCH` lines, read at submit time, do not change.

## 2. Give jobs their environment

On a cluster, `module load` and `conda activate` lines set up a job. Here they
go in a file in the project, passed with `--rc`:

```bash
# env.sh -- sourced by bash before every job: molab-slurm sbatch --rc ./env.sh
export PATH="$HOME/.pixi/bin:$PATH"
export BUCKET=gs://my-bucket/myproject
export SEEDS=1,2,3
```

The file is sourced in the job's working directory, so `./env.sh` next to
`-D /marimo/myproject` means `/marimo/myproject/env.sh`. If it fails, the job
fails. Your laptop's environment never reaches the box.

Why a file rather than `--export`: `--export` values are split on commas, so a
value with a comma in it cannot go through it.

```console
$ molab-slurm sbatch -D /marimo/myproject --export=SEEDS=1,2 train.sh
molab-slurm: --export: '2' must be ALL, NONE or VAR=value
```

`--export=ALL,SEED=1` works. For values with commas, use `--rc`, put the
variable inside `--wrap` (`--wrap 'SEEDS=1,2 bash run.sh'`), or pass an
argument after the script name (`train.sh --seeds 1,2`).

## 3. Write the setup job

A fresh box needs the project's software, a large reference file, and the
inputs from the bucket. The three do not depend on each other, so the setup
job runs them in parallel, each with its own log, and fails if any of them
fails:

```bash
#!/bin/bash
#SBATCH --job-name=setup
#SBATCH --output=logs/%x-%j.out
# Everything this project needs on a fresh box. Safe to run again.
set -euo pipefail
mkdir -p data logs

install_env() {
  command -v pixi >/dev/null || curl -fsSL https://pixi.sh/install.sh | sh
  pixi install
}
fetch_reference() {  # large: skipped if it is already on the box
  [ -s data/reference.dat ] && return 0
  curl -fsSL https://example.org/reference.dat.gz | gunzip > data/reference.dat.part
  mv data/reference.dat.part data/reference.dat
}
fetch_inputs() {
  gcloud storage cp -r "$BUCKET/inputs" data/
}

# Independent steps, in parallel inside this one job.
steps=(install_env fetch_reference fetch_inputs)
pids=()
for s in "${steps[@]}"; do
  "$s" > "logs/$s.log" 2>&1 &
  pids+=($!)
done
status=0
for i in "${!steps[@]}"; do
  if wait "${pids[$i]}"; then echo "${steps[$i]}: ok"
  else echo "${steps[$i]}: failed, see logs/${steps[$i]}.log"; status=1; fi
done
exit "$status"
```

It uses `$BUCKET` and the pixi `PATH`, so it runs with `--rc ./env.sh` like
every other job. To try it on its own, `molab-slurm srun -D /marimo/myproject --rc
./env.sh bash setup.sh` streams it to your terminal — but Ctrl-C there
cancels it, and `srun` calls the box every few seconds until it ends. A setup
that takes more than a minute or so belongs in `sbatch`, which step 6 does.

## 4. Write the training array

One array task per fold. The `#SBATCH` header is the one you would use on a
cluster:

```bash
#!/bin/bash
#SBATCH --job-name=train
#SBATCH --array=0-3%1
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --time=6:00:00
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
set -euo pipefail
fold=$SLURM_ARRAY_TASK_ID
dest="$BUCKET/results/fold_$fold"

# Finished in an earlier session? The bucket is what outlives the box.
if gcloud storage ls "$dest/done" >/dev/null 2>&1; then
  echo "fold $fold: already in $dest"
  exit 0
fi

out="results/fold_$fold"
mkdir -p "$out"
pixi run python train.py --fold "$fold" --reference data/reference.dat --inputs data/inputs --out "$out"

# Off the box as soon as it exists. The marker goes last, so a fold whose
# upload was cut short does not count as done.
gcloud storage cp -r "$out" "$BUCKET/results/"
touch "$out/done"
gcloud storage cp "$out/done" "$dest/done"
```

What molab-slurm does with the header:

* `--array=0-3%1`: tasks 0 to 3, at most one at a time. `%1` is also
  molab-slurm's default. On the command line, `--array=2,3` overrides it and runs only
  those folds, still one at a time.
* `--output=logs/%x_%A_%a.out`: `logs/train_3_0.out` for task 0 of job 3.
  molab-slurm creates `logs/` when the task starts.
* `--time=6:00:00` is enforced: SIGTERM, then SIGKILL, and the task ends
  `TIMEOUT`.
* `--gres` and `--mem` are not enforced; molab-slurm accepts them and says so when
  you submit.

## 5. Add a step that waits

The summary needs every fold, so it waits with `--dependency=afterok:<id>`:
it starts once every task of that job has `COMPLETED`, and is cancelled if
any of them ends otherwise. It is one command line, so it goes in `--wrap`
rather than a script of its own, and it reads the folds from the bucket, so
folds trained in an earlier session count too. These are its options:

```text
-J summarize --dependency=afterok:<id of the training job>
--wrap 'gcloud storage cp -r "$BUCKET/results" . && pixi run python summarize.py results > summary.tsv && gcloud storage cp summary.tsv "$BUCKET/"'
```

It needs the training job's id, which does not exist yet, so it is submitted
together with the others in the next step rather than by hand.

## 6. Submit the whole pipeline at once

`--parsable` makes `sbatch` print only the job id, as on a cluster, so a
script on your laptop can submit everything in one go. Options go before the
script name; anything after it is passed to the script:

```bash
#!/bin/bash
# submit.sh -- runs on your laptop and submits the whole pipeline
set -euo pipefail
on_box=(-D /marimo/myproject --rc ./env.sh)
setup=$(molab-slurm sbatch --parsable "${on_box[@]}" setup.sh)
train=$(molab-slurm sbatch --parsable "${on_box[@]}" --dependency=afterok:"$setup" train.sh)
summary=$(molab-slurm sbatch --parsable "${on_box[@]}" --dependency=afterok:"$train" -J summarize \
  --wrap 'gcloud storage cp -r "$BUCKET/results" . && pixi run python summarize.py results > summary.tsv && gcloud storage cp summary.tsv "$BUCKET/"')
echo "setup $setup, train $train, summarize $summary"
```

From your laptop's checkout of the project:

```bash
bash submit.sh
```

You should see the ignored options of `train.sh`, then the ids:

```text
molab-slurm: not enforced on molab, ignored: --gres=gpu:1, --mem=32G
setup 2, train 3, summarize 4
```

```bash
molab-slurm squeue
```

```text
JOBID      PARTITION  NAME       USER  ST  TIME  NODES  NODELIST(REASON)
2          molab      setup      me    R   1:05  1      gpu
3_[0-3%1]  molab      train      me    PD  0:00  1      (Dependency)
4          molab      summarize  me    PD  0:00  1      (Dependency)
```

The pending array is collapsed into one row, as SLURM shows it. Everything is
on the box now; your laptop can sleep.

## 7. Read the logs

A task's output file is wherever its `--output` pattern put it, and
`molab-slurm tail` finds it by job id:

```bash
molab-slurm tail 2
```

Once setup has finished, you should see one line per step (each step's own
output is in its log):

```text
install_env: ok
fetch_reference: ok
fetch_inputs: ok
```

* setup's output is `logs/setup-2.out`, from `%x-%j`; task 0's is
  `logs/train_3_0.out`, from `%x_%A_%a`.
* `molab-slurm tail -n 20 3_0` shows the end of a training task's output;
  `molab-slurm tail -n 20 3` means the array's first task. `tail -f` would
  stream it, but it calls the box every few seconds for as long as the task
  runs: `molab-slurm wait 3_0`, then `tail -n 20 3_0`, instead
  ([For AI agents](../ai-agents.md)).
* The patterns are SLURM's: `%A` array job id, `%a` task index, `%j` job id,
  `%x` job name. Here an array task has no id of its own, so `%j` is
  `3_0` for task 0 of job 3. Without `-o`, output goes to `slurm-%j.out`, or
  `slurm-%A_%a.out` for arrays.
* `-e` sends stderr to a separate file; by default it goes to the output file.
* Files that are not a task's output — the setup step logs, say — are one
  `srun` away: `molab-slurm srun -D /marimo/myproject tail -n 20 logs/install_env.log`,
  or `molab-slurm get /marimo/myproject/logs/install_env.log`.

## 8. Cancel

```bash
molab-slurm scancel 3_2     # one task: if pending it never starts, if running its process tree is stopped
molab-slurm scancel 3       # the whole array
molab-slurm scancel --all   # everything pending or running
```

`scancel` prints nothing when it works, and `molab-slurm: scancel: invalid job id 9`
for an id the box does not have. A running task gets SIGTERM, then SIGKILL
after 10 seconds.

Cancelling breaks an `afterok` chain. After `molab-slurm scancel 3`, with task 1
running:

```bash
molab-slurm sacct -j 3,4
```

```text
JobID  JobName    State      ExitCode  Elapsed  Start                End
3_0    train      COMPLETED  0:0       48:12    2026-09-24T10:21:40  2026-09-24T11:09:52
3_1    train      CANCELLED  0:15      7:31     2026-09-24T11:09:53  2026-09-24T11:17:24
3_2    train      CANCELLED  0:0       0:00     Unknown              2026-09-24T11:17:24
3_3    train      CANCELLED  0:0       0:00     Unknown              2026-09-24T11:17:24
4      summarize  CANCELLED  0:0       0:00     Unknown              2026-09-24T11:17:25
```

`0:15` is SIGTERM; tasks cancelled before they started have no start time.
`sacct` has no reason column. `tail -f` on a job that ended other than
`COMPLETED` prints its state, and the reason when there is one:

```console
$ molab-slurm tail -f 4
molab-slurm: 4 CANCELLED (DependencyNeverSatisfied (job 3 did not complete))
```

To carry on, run `bash submit.sh` again. Fold 0 is in the bucket and is
skipped; the others train.

## 9. Keep heavy jobs one at a time

`%N` limits the tasks of one array. Nothing limits separate jobs: two jobs
submitted without a dependency start together, whatever `--mem` or `--gres`
they ask for, because there is no scheduler to hold one back. On a box with
about 32 GB that matters — a session of ours was killed after about 4.5 hours,
most likely out of memory, while two GPU training jobs ran at once.

To run several different heavy jobs, chain them with `afterany`. In
`submit.sh`, after the setup line:

```bash
a=$(molab-slurm sbatch --parsable "${on_box[@]}" --dependency=afterok:"$setup" train_a.sh)
b=$(molab-slurm sbatch --parsable "${on_box[@]}" --dependency=afterany:"$a" train_b.sh)
```

`afterany` starts `train_b.sh` when `train_a.sh` ends, whatever its state, so
one failure does not cancel the rest of the chain. Use `afterok` where a step
needs the one before it to have succeeded. Raise `%N` only for light tasks,
e.g. `--array=0-7%4 -c 1` for single-threaded preprocessing on four CPUs.

## Next

[When the session ends](new-session.md): what survives a session, and how
`submit.sh` picks up where the last one stopped.
