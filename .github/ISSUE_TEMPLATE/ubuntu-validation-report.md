---
name: Ubuntu validation report
about: Report what actually happened when running Cofferdam on a real Ubuntu host
title: "[validation] "
labels: validation
---

## Host

- Ubuntu version:
- Session type (`echo $XDG_SESSION_TYPE`): <!-- x11 / wayland -->
- Desktop environment:
- Displays (`xrandr --listmonitors`):
- Cofferdam commit:

## Adapter in use

From `GET /api/status`:

- `adapter`:
- `stub`: <!-- must be false — stub results are not platform validation -->

## What was run

<!-- Which checklist steps, or which exact request/command. -->

## What happened

<!-- Exact output or structured error. Redact tokens, Tailscale addresses, and
     hostnames you do not want public. -->

## Constraints discovered

<!-- Platform behaviour worth recording even if nothing "failed" — restricted
     screenshots, snap browser paths, window placement quirks, etc. -->
