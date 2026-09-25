---
title: When the session ends
parent: Tutorials
nav_order: 3
---

# When the session ends

**Goal:** get back to work after a molab session ends: connect the new
session under the same name, check its GPU, set it up again, and resubmit so
that only unfinished work runs. It continues [A small pipeline](pipeline.md).

A session can end from idle shutdown, a crash, or running out of memory or
disk. Sessions have also ended, with no reason shown, while the box was being
polled frequently; the cause is not confirmed
([For AI agents](../ai-agents.md)). molab-slurm finds out the next time you
run a command, which then fails to reach the box:

```text
molab-slurm: cannot reach https://sb-0123456789abcdef.sb.molab.run: ...
```

or answers with an HTTP error listing sessions (`HTTP 410`, or `403`). Either
way, that URL and token belong to a session that is gone
([Troubleshooting](../troubleshooting.md)).

## What is lost and what is kept

Lost — everything that was on the box:

* **The jobs.** Running tasks ended with the box, and pending ones will never
  start. Jobs cannot outlive the session.
* **molab-slurm's state** in `/marimo/.molab`: the `squeue`/`sacct` history
  and the job id counter. Ids start again at 1, so an id from the old
  session means nothing on the new one.
* **Files and software.** Job logs, results not yet copied off, the installed
  environment. A new session may keep part of `/marimo`; the ones we have
  seen started with it empty — nothing of ours, not even pixi. Plan for an
  empty box.
* **The URL and token.** Every new session has new ones.

Kept — everything off the box:

* **Your saved box** in `~/.config/molab-slurm/config.json`. Its URL and token are
  stale; the `--cpus` and `--workdir` you gave it stay when you `init` again
  under the same name.
* **The bucket**, with every fold whose job reached its copy step, and the
  `done` markers.
* Files you fetched with `molab-slurm get` or `molab-slurm open`, and your repository.

molab-slurm keeps no copy of anything off the box. What survives a session is
what your jobs copied somewhere else.

## 1. Start the new session yourself

In molab, open the notebook again on a GPU machine, and leave its tab open.
molab-slurm cannot start or restart a session.

## 2. Connect it under the same name

In a cell of the new session's notebook:

```python
import molab_slurm as mos
mos.init_command(name="gpu")
```

If `import molab_slurm` fails there, add the package again with marimo's
package manager, as in [Your first job](first-job.md), step 3. Copy the line
it shows — it has the new URL and token — and paste it on your laptop:

```bash
molab-slurm init https://sb-fedcba9876543210.sb.molab.run/ <token> --name gpu
```

You should see `saved box 'gpu' to ...` and the box's description, with the
new URL. The same name keeps `molab-slurm --box gpu`, `MOLAB_BOX=gpu` and your
scripts working, and makes it the default again:

```bash
molab-slurm boxes
```

```text
   NAME  URL                                        CPUS  WORKDIR
*  gpu   https://sb-fedcba9876543210.sb.molab.run/  4     /marimo
```

## 3. Check the GPU

```bash
molab-slurm sinfo
```

The last lines should show one GPU and no jobs:

```text
gpus      1  (NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB)
disk      22.2 GB used under /marimo/.molab's filesystem
jobs      0 active, 0 task(s) running
```

A recreated session has come back without its GPU. Then `sinfo` says:

```text
gpus      0  -- the NVIDIA driver entry is there but no GPU is attached; GPU jobs will run on the CPU
```

Nothing fails on such a box; training just runs on the CPU, very slowly.
Start the session again on a GPU machine before submitting anything.

`molab-slurm sacct` on the new box prints only its header: the old history went
with the old box.

## 4. Set the box up again

The same first step as before, on an empty box:

```bash
molab-slurm srun git clone https://github.com/my-org/myproject /marimo/myproject
```

If the new session kept `/marimo/myproject`, the clone fails because the
directory exists; update it instead with
`molab-slurm srun -D /marimo/myproject git pull`.

The rest of the setup — the environment, the reference download, the inputs
from the bucket — is `setup.sh`, the first job `submit.sh` submits. On an
empty box it does all of it again; on one that kept `data/reference.dat`, it
skips that download.

## 5. Resubmit

From your laptop's checkout, the same command as the first time:

```bash
bash submit.sh
```

You should see:

```text
molab-slurm: not enforced on molab, ignored: --gres=gpu:1, --mem=32G
setup 2, train 3, summarize 4
```

The same ids as the first time in [A small pipeline](pipeline.md): on the
sessions we have seen the counter started over with the box.
`submit.sh` passes each id to the next job itself, which is why nothing in it
is written down by hand.

Once setup has finished, each training task first asks the bucket whether its
fold is done:

```bash
molab-slurm tail 3_0
```

```text
fold 0: already in gs://my-bucket/myproject/results/fold_0
```

A little later:

```bash
molab-slurm sacct
```

```text
JobID  JobName    State      ExitCode  Elapsed  Start                End
1      git        COMPLETED  0:0       0:03     2026-09-25T09:10:02  2026-09-25T09:10:05
2      setup      COMPLETED  0:0       11:47    2026-09-25T09:10:31  2026-09-25T09:22:18
3_0    train      COMPLETED  0:0       0:02     2026-09-25T09:22:19  2026-09-25T09:22:21
3_1    train      COMPLETED  0:0       0:02     2026-09-25T09:22:22  2026-09-25T09:22:24
3_2    train      RUNNING    0:0       14:05    2026-09-25T09:22:25  Unknown
3_3    train      PENDING    0:0       0:00     Unknown              Unknown
4      summarize  PENDING    0:0       0:00     Unknown              Unknown
```

Folds 0 and 1 reached the bucket in the old session and took seconds. Fold 2
was training when that session ended; its result never got off the box, so it
trains again. The summary still covers all four folds, because it reads them
from the bucket.

That is the whole recovery: the jobs copy each result off the box as soon as
it exists, and skip work whose output is already there. A session that ends
costs the work that was running, plus the setup.

## 6. Before you walk away again

* Keep the notebook open in a browser tab on a machine that stays awake.
  `molab-slurm keepalive --every 4m --for 8h` adds kernel API traffic, which may or
  may not be what molab's idle timer counts
  ([Keeping a session alive](../sessions.md#keeping-a-session-alive)); do not
  make it more frequent than that.
* Check on jobs when they should be done, not in a loop: no
  `watch molab-slurm squeue`, and no `tail -f` left running on long jobs
  ([For AI agents](../ai-agents.md)).
* If the session died while two heavy jobs ran together, run them one at a
  time: `%1` within an array, `--dependency=afterany` between jobs
  ([A small pipeline](pipeline.md), step 9).

## Next

[Sessions and recovery](../sessions.md) covers the same ground as a
reference; [Differences from SLURM](../differences.md) lists what molab-slurm
does not do.
