---
title: How it works
nav_order: 9
---

# How it works

```text
 your laptop                         molab box (gVisor sandbox)
 ───────────                         ──────────────────────────
 molab sbatch ──HTTPS──► notebook server ──► kernel scratchpad
                        POST /api/kernel/execute     │ writes jobs/<id>/job.json
                                                     │ starts ──► runner.py <job dir>   (detached)
 molab squeue ──HTTPS──► ... reads jobs/*/tasks/*.json             │ starts, watches, stops
 molab scancel ─HTTPS──► ... writes jobs/<id>/cancel               ▼
                                                            your script (one process tree per task)
```

## The only way in

A molab box accepts no inbound connections except its marimo notebook
server. That server exposes `POST /api/kernel/execute`, which runs Python in
the kernel's scratchpad and streams stdout back as server-sent events — the
same API the marimo-pair skill uses. Every molab command is one or a few such
requests: a short snippet, parameters passed as JSON (never spliced into the
code as text), one marked JSON line back.

Requests are kept **short**: a request that runs for minutes has been seen
to come back empty, and one reply is truncated at about 1 MB (measured).
So nothing long ever happens inside a request, and files move in 512 KB
pieces.

## The runner

`molab sbatch` builds a job spec on your machine — `#SBATCH` lines parsed,
command-line options merged, output patterns chosen — and one request on the
box then:

1. allocates the next integer job id from a locked counter;
2. writes `jobs/<id>/job.json`;
3. copies `runner.py` into place (every submit, so upgrades reach the box);
4. starts `python3 runner.py jobs/<id>` detached (`setsid`), with a cleaned
   environment, and returns.

The runner is stdlib Python and the only thing that touches a job's
processes. It waits for dependencies, starts tasks as the `%N` throttle
allows, enforces `--time`, writes each task's state to `tasks/<index>.json`
atomically, and heartbeats `runner.json` every 5 seconds.

### Why cancellation is a file, not a signal

Under gVisor the notebook kernel sees a **different PID namespace** from the
processes it starts: the PID `subprocess.Popen` hands back is not one that
`ps` or `kill` on the box can find, and process groups showed up as `1`. So
the kernel never signals anything. `molab scancel` drops a `cancel` (or
`cancel.<index>`) file; the runner, which shares a namespace with its
children, sees it within a second, walks its task's process tree **parents
first**, sends SIGTERM, then SIGKILL after 10 seconds, and records
`CANCELLED`.

Parents first matters: signalled deepest-first, a shell watches its child die
and simply carries on — `sleep 120; echo never` printed `never` and exited 0.

### NODE_FAIL

A task that is not in a final state while its runner's heartbeat is more than
60 seconds old lost its runner — the box restarted, or something killed it —
and is reported `NODE_FAIL`.

## State on the box

```text
/marimo/.molab/
├── next_id
├── runner.py
└── jobs/<id>/
    ├── job.json          spec
    ├── runner.json       {"pid": ..., "heartbeat": ...}
    ├── runner.log        the runner's own output
    ├── wrap.sh           for --wrap / srun
    ├── tasks/<i>.json    state, reason, start, end, exit_code, output
    └── cancel[.<i>]      cancel requests
```

It lives only on the box. See [Sessions and recovery](sessions.md) for what
that means when a session ends.
