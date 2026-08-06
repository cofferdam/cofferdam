# M2F — Agent Task Core: live validation checklist

Run on the Ubuntu workstation, against the standalone clone for branch
`feat/agent-task-core-foundation`.

**Nothing here runs a real agent.** The validation adapter is deterministic: it
runs no program, calls no model and changes nothing on the machine. What is
being validated is the *task lifecycle* — that states are truthful, that a
restart does not leave a task claiming to run, and that the phone never shows a
success it has not observed.

---

## 0. Before anything: record the pre-state

Write these down. The rollback depends on knowing what was there.

```bash
systemctl --user show cofferdam-workstation.service \
  -p MainPID -p WorkingDirectory -p ExecStart -p NRestarts
```

```bash
sha256sum ~/.config/systemd/user/cofferdam-workstation.service.d/*.conf
```

```bash
ls -la "${COFFERDAM_HOME:-$HOME/cofferdam}/state/tasks" 2>/dev/null || echo "no task store yet"
```

The last one lists the task database's existence, size and permissions. **Do not
open it and do not print its contents** — it holds task text.

The expected pre-state is the M2E runtime: `WorkingDirectory` pointing at
`clones/youtube-dedicated-player`, ten drop-ins numbered 10 through 95.

---

## 1. Configure a project

Task Core resolves a project id to a verified root. Create the host-owned file:

```bash
mkdir -p "${COFFERDAM_HOME:-$HOME/cofferdam}/config"
```

Copy `examples/task-projects.json` to
`$COFFERDAM_HOME/config/task-projects.json` and edit `root` to a real directory
you own. It is never written by the service or by a phone.

---

## 2. Confirm the adapter is off before installing anything

On the **current** runtime, with the phone connected:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7101/api/task-adapters
```

Expect `{"adapters": []}` — or a 404 if the current runtime predates this
branch. Either proves the validation adapter is not reachable by default.

---

## 3. Install the validation layer

```bash
cp deploy/validation/96-agent-task-core-validation.conf \
   ~/.config/systemd/user/cofferdam-workstation.service.d/
```

```bash
systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

Confirm the layer actually won — by reading the effective value, not the
filename:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory
```

`ExecStart` must contain `clones/agent-task-core` **and**
`--enable-validation-task-adapter`.

The journal should carry the announcement that the validation adapter is
enabled:

```bash
journalctl --user -u cofferdam-workstation.service -n 20 --no-pager
```

---

## 4. Phone and tablet validation

1. Confirm the **Tasks** area appears.
2. Confirm the adapter is labelled **Validation task adapter**, with the note
   that it runs no program and calls no model.
3. Start the **Complete** scenario (leave the prompt as ordinary text, or begin
   it with `scenario: complete`).
4. **Double-tap Start.** Confirm exactly one task is created.
5. Watch it move through queued → starting → running → progress → completed.
6. Confirm the final result is visible.
7. Confirm the default view is **not** a raw terminal log — the event stream is
   behind *Advanced*.
8. Start the **Wait for follow-up** scenario (`scenario: wait`).
9. Confirm `waiting for an answer` is shown clearly and the task is in the
   *Waiting for you* group.
10. Send one follow-up.
11. Confirm it returns to running and completes.
12. Start the **Fail** scenario (`scenario: fail`).
13. Confirm `failed` reads differently from `interrupted`.
14. Start the **Cancel** scenario (`scenario: cancel`).
15. Cancel it.
16. Confirm cancelling → cancelled.
17. Start the **Restart interruption** scenario (`scenario: interrupt`) and
    leave it running.
18. Restart **only** the validation runtime:
    ```bash
    systemctl --user restart cofferdam-workstation.service
    ```
19. Confirm the task becomes **interrupted**, not running, and that the phone
    explains it as a restart rather than a failure.
20. Confirm the completed, failed and cancelled tasks are **unchanged** —
    same state, same result, same timestamps.
21. Reload the PWA and confirm task history persists.
22. Confirm prompts and results do **not** appear in the daemon log:
    ```bash
    journalctl --user -u cofferdam-workstation.service --since "1 hour ago" --no-pager \
      | grep -i -c "<a distinctive word from one of your prompts>"
    ```
    Expect `0`.
23. Confirm no horizontal overflow on phone and tablet widths, and that no
    action appears twice.
24. Confirm Spotify, the YouTube player and Computer Audio still work.

---

## 5. Rollback

Remove **only** the file this checklist installed:

```bash
rm ~/.config/systemd/user/cofferdam-workstation.service.d/96-agent-task-core-validation.conf
```

```bash
systemctl --user daemon-reload && systemctl --user restart cofferdam-workstation.service
```

Confirm the pre-state is back:

```bash
systemctl --user show cofferdam-workstation.service -p ExecStart -p WorkingDirectory
```

`WorkingDirectory` must point at `clones/youtube-dedicated-player` again, and
the drop-in checksums from step 0 must be unchanged.

**After rollback the validation adapter is gone** — `/api/task-adapters` is
empty again. That is the intended post-merge state: this adapter is a validation
tool, not a feature, and it must not be available merely because the PR merged.
