---
title: Sessions and recovery
nav_order: 8
---

# Sessions and recovery

## molab-slurm does not create sessions

**Starting a molab session is always something you do yourself**, in the
molab web interface: open (or reopen) the notebook, pick the machine, and
copy the connect snippet it shows — the notebook URL and a token.
`molab-slurm` cannot start, stop, resize or restart a session; it has no
access to molab's account or API, only to the notebook server of a session
that is already running.

`molab init` only **connects**: it checks the URL and token against that
notebook server, saves them under a name, and reports what the box has. It
does not install, configure or set up anything on the box.

```bash
molab init https://sb-<id>.sb.molab.run/ <token> --name gpu
```

## Setting up a box is your project's job

A fresh session has nothing of yours on it. Getting your code, data and
software onto the box is specific to your project, so it belongs in a script
in your project, run *through* molab-slurm like any other command:

```bash
molab srun -D /marimo/myproject bash setup.sh
```

molab-slurm never refers to such scripts; it stays transport and
scheduling only.

## When a session dies

A molab session can end — idle shutdown, a crash, running out of memory or
disk — and molab may then give you a **new** session: a fresh gVisor box with
a new URL, keeping only part of `/marimo`. Everything else on it is gone,
including the jobs molab-slurm was running and its state under
`/marimo/.molab` (`squeue` on the old URL simply cannot connect).

What to do:

1. **Start or reopen the session yourself** in molab and copy the new connect snippet
   (or run `mos.init_command(name="gpu")` in its notebook; see [Install and connect](install.md#connect-a-box)).
2. `molab init <new-url> <token> --name gpu` — the same name, so your scripts keep working.
3. `molab sinfo` — check the box, and **especially that the GPU is there**.
   A recreated session has come back without its GPU; nothing fails, GPU
   code just runs on the CPU, very slowly.
4. Re-run your project's setup (`molab srun ...`), then resubmit your jobs.

How much work that costs depends on your project, not on molab-slurm:
molab-slurm keeps no copy of anything off the box. Copy results somewhere
durable (a bucket) as each job finishes, and make your steps skip work whose
outputs are already there, and a resubmitted array redoes only what was
running when the session died.

## Keeping a session alive

molab's idle policy is not documented. What helps, in order:

* keep the notebook open in a browser tab on a machine that stays awake;
* `molab keepalive --every 4m --for 8h` from a machine that stays online —
  it generates kernel API traffic, which may or may not be what molab's idle
  timer counts.

Jobs themselves run detached on the box and do not need your laptop, but they
cannot outlive the session.
