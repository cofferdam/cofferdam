---
name: Adapter or action proposal
about: Propose a new typed action or host adapter capability
title: "[proposal] "
labels: proposal
---

## Capability

<!-- What should the user be able to do from the phone? -->

## Proposed typed action

- Action name:
- Parameters (types, and the closed allowlist for anything naming a program):

Cofferdam accepts typed actions, never commands. The schema must forbid unknown
fields and must not expose a command, args, argv, shell, executable, path, or
script field — see CONTRIBUTING.md.

## Adapter implementation

- Platform(s):
- System commands or APIs used (semantic APIs preferred over mouse/keyboard
  coordinates):
- New dependencies, if any — including license:

## Failure modes

<!-- What should happen when the tool is missing, the session is Wayland, or the
     target application is not installed? -->

## Milestone

<!-- Which milestone in ROADMAP.md does this belong to, and why now? -->
