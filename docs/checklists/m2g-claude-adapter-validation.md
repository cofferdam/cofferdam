# M2G — Claude Code adapter: live validation

The first Cofferdam runtime that can start a real agent and change real files
because somebody typed a sentence into a phone. Read
[`../CLAUDE_CODE_ADAPTER.md`](../CLAUDE_CODE_ADAPTER.md) first; this is the
operational procedure, not the design.

Do not run any of this until: implementation complete, full suite green on both
CI paths, CI green on the PR, the adapter disabled by default, the unsafe
permission bypass absent, the prompt proven absent from argv and logs, the
cancellation isolation tests passing, and the disposable sandbox registered.

## 0. The disposable sandbox

A small standalone Git repository holding no secrets, no links to credential or
browser data, and no Cofferdam production state. Safe to delete and recreate.

```bash
ls -la ~/cofferdam/validation/claude-adapter-sandbox
```

It must contain a `README.md`, a trivial module, a deterministic local test, and
its own `.git`. **Never run the first real Claude write test against Cofferdam's
own source clone.**

Register it in `$COFFERDAM_HOME/config/task-projects.json`:

```json
{
  "projects": [
    {
      "project_id": "claude-sandbox",
      "display_name": "Claude adapter sandbox",
      "root": "/home/nrgis/cofferdam/validation/claude-adapter-sandbox",
      "adapters": ["claude-code", "validation"],
      "notes": "Disposable. Safe to delete and recreate."
    }
  ]
}
```

The path is host-owned and written on purpose. Cofferdam never discovers or
registers a directory by itself.

## 1. Record the pre-state

Before touching anything. Keep this output; it is what a rollback is checked
against.

```bash
systemctl --user show cofferdam-workstation.service -p MainPID -p NRestarts -p ActiveState -p SubState -p ExecStart -p WorkingDirectory -p KillMode
```

```bash
sha256sum ~/.config/systemd/user/cofferdam-workstation.service.d/*.conf
```

Eleven files, `10-` through `96-`. **Their checksums must be identical after the
validation.** Nothing in this milestone edits them.

Task store metadata, without content:

```bash
sqlite3 ~/cofferdam/state/tasks.db "select state, count(*) from tasks group by state;"
```

Adapter-owned processes now, which should be none:

```bash
pgrep -a -f 'cofferdam.workstation' | head
```

`pgrep` is fine here — a human reading a terminal. Cofferdam itself never
matches a process by name, which is the point of the identity checks in
`process.py`.

## 2. Install the 97 layer

```bash
cp ~/cofferdam/clones/claude-code-adapter/deploy/validation/97-claude-code-adapter-validation.conf ~/.config/systemd/user/cofferdam-workstation.service.d/
```

```bash
systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

Confirm the effective runtime moved and nothing else did:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory -p MainPID -p NRestarts
```

`ExecStart` must name the `claude-code-adapter` clone and carry both
`--enable-claude-code-adapter` and `--enable-validation-task-adapter`.

### Rollback — the exact command

```bash
rm ~/.config/systemd/user/cofferdam-workstation.service.d/97-claude-code-adapter-validation.conf && systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

Removes **only** that one file and returns the service to the unchanged 96
runtime. Re-run the `sha256sum` from step 1 to confirm 10–96 are byte-identical.

## A. Merged Task Core baseline

Re-run on this branch, because PR #20 merged before full phone validation. A
defect found here belongs to the foundation, not to the adapter.

1. Tasks panel loads on the phone.
2. **Validation task adapter** is still labelled as a validation adapter, and
   still says it runs no program and calls no model.
3. `scenario: complete` → runs and completes with a result.
4. `scenario: wait` → waits, one follow-up, completes.
5. `scenario: fail` → fails with the synthetic error.
6. `scenario: cancel` → stays running, cancels cleanly.
7. `scenario: interrupt`, then restart the 97 runtime.
8. The interrupted task reads `interrupted`, never `failed`; history survives;
   a repeated create with the same retry key makes no second task.
9. No task content in the broad daemon log:
   ```bash
   journalctl --user -u cofferdam-workstation --since '30 min ago' | grep -c 'SENTINEL' || echo "clean"
   ```

## B. Claude Code availability

10. **Claude Code** appears in the adapter picker. Confirm it is there because
    the 97 configuration enabled it: it must be absent from a runtime without
    the flag.
11. Only the sandbox project is offered.
12. The composer has **no** field for a command, executable, working directory,
    flags, environment, model, permission mode, tool list or session id. The
    adapter's limitations are shown.

## C. One real Claude task

13. Start one bounded task in the sandbox. Something like: *"Read README.md,
    then add a short Usage section to it describing how to run the test."*
    Do not ask it to inspect other directories, and never ask it for secrets,
    browser data or account content.
14. Exactly one task and one process:
    ```bash
    pgrep -a -f 'claude' | grep -v grep
    ```
15. The task passes `queued → starting → running`.
16. Meaningful activity appears — tool activity, assistant text — without raw
    terminal output.
17. The file change is actually there:
    ```bash
    git -C ~/cofferdam/validation/claude-adapter-sandbox status --porcelain
    ```
18. Evidence is labelled by source: what Claude *said* reads as its claim, and
    what Cofferdam observed reads as observed.
19. The final result is visible.
20. The prompt is in neither argv nor the journal:
    ```bash
    tr '\0' ' ' < /proc/$(pgrep -f 'claude -p' | head -1)/cmdline; echo
    ```
    Run this *while the task is running*. It must show only the fixed template.

## D. Follow-up

21. Send one bounded follow-up asking for a second safe change.
22. Same task, same session — the pid does not change.
23. Tapping send twice delivers it once.
24. The second change is observed on disk.
25. The task reaches a truthful final state.

## E. Cancellation

26. Start a bounded long-running task.
27. Cancel from the phone.
28. `running → cancelling → cancelled`.
29. Only that process stopped.
30. If a Claude session is open in a terminal, it is untouched.
31. Spotify, Opera and the Cofferdam daemon are alive.

## F. Restart interruption

32. Start another bounded Claude task.
33. Restart only the 97 runtime.
34. The task becomes `interrupted`.
35. It is never shown as running afterwards.
36. No uncontrolled Claude child remains:
    ```bash
    pgrep -a -f 'claude -p' || echo "none"
    ```
37. Completed and cancelled tasks are unchanged.

## G. Compatibility

38. Spotify playback works.
39. YouTube dedicated player works.
40. Computer audio works.
41. No horizontal overflow, no duplicate actions, on phone and tablet.
42. An authentication wait shows a sentence and **no secret form**.
43. Prompts and results are still absent from the broad logs.

## After validation

The 97 layer stays **validation-only**. It enables the deterministic validation
adapter alongside Claude Code, which normal daily configuration must not do.

For ordinary use after merge, a host would enable exactly one thing —
`--enable-claude-code-adapter`, or `enable_claude_code_adapter` in
`config.json` — and register the projects it actually wants, without the
validation adapter and without the sandbox.
