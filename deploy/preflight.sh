#!/usr/bin/env bash
# Cofferdam unattended-reboot preflight — READ ONLY.
#
# Answers one question: if this machine rebooted right now and nobody logged
# into the desktop, would the phone be able to reach Cofferdam?
#
# It inspects and reports. It starts nothing, stops nothing, enables nothing,
# removes nothing and reloads nothing — a test asserts that, because a
# "preflight" that repairs what it finds is a preflight nobody can run to learn
# the truth. Every fix it suggests is printed for a person to run deliberately.
#
# It never prints the device token, a Remote Control URL, or any credential.
#
#   bash deploy/preflight.sh
#
# Exit status is 0 when every required condition holds, 1 otherwise.

set -uo pipefail

UNIT="cofferdam-workstation.service"
DROPIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${UNIT}.d"
SLOT_MARKER="cofferdam/slots/"
FAILURES=0

pass() { printf '  ok    %s\n' "$1"; }
warn() { printf '  warn  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

printf '\nCofferdam unattended-reboot preflight (read only)\n\n'

# -- 1. linger ---------------------------------------------------------------
# Without it the user manager only starts at interactive login, so a rebooted
# machine sits with no Cofferdam until somebody walks to it and signs in.
printf 'user manager\n'
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
  pass "linger is enabled for $USER (user manager starts at boot)"
else
  fail "linger is disabled — the user manager will not start until you log in"
  printf '        fix: loginctl enable-linger %s      (undo: disable-linger)\n' "$USER"
fi

# -- 2. the unit is enabled and boot-scoped ----------------------------------
printf '\nservice\n'
if [ "$(systemctl --user is-enabled "$UNIT" 2>/dev/null)" = "enabled" ]; then
  pass "$UNIT is enabled"
else
  fail "$UNIT is not enabled"
  printf '        fix: systemctl --user enable %s\n' "$UNIT"
fi

wanted_by=$(systemctl --user show "$UNIT" -p WantedBy --value 2>/dev/null)
case "$wanted_by" in
  *default.target*) pass "wanted by default.target" ;;
  *) fail "not wanted by default.target (got: ${wanted_by:-none})" ;;
esac

deps=$(systemctl --user show "$UNIT" -p Wants -p Requires -p BindsTo -p PartOf 2>/dev/null)
case "$deps" in
  *graphical*) fail "depends on a graphical target — it cannot start before login" ;;
  *) pass "no graphical-session dependency" ;;
esac

# -- 3. what it will actually run --------------------------------------------
# The failure this whole script exists for: a milestone's validation drop-in
# left behind, quietly pinning production to a feature worktree.
printf '\ndeployed code\n'
exec_start=$(systemctl --user show "$UNIT" -p ExecStart --value 2>/dev/null)
case "$exec_start" in
  *"$SLOT_MARKER"*) pass "ExecStart runs from the A/B slot" ;;
  *clones/*|*worktrees/*)
    fail "ExecStart runs from a development checkout, not the slot"
    printf '        %s\n' "$exec_start"
    printf '        fix: remove the stale drop-in(s) below, then daemon-reload and restart\n' ;;
  "") fail "ExecStart is empty — is the unit installed?" ;;
  *) warn "ExecStart is neither a slot nor a known checkout: $exec_start" ;;
esac

case "$exec_start" in
  *--enable-validation-task-adapter*)
    fail "the validation task adapter is enabled on production" ;;
  *) pass "no validation-only adapter enabled" ;;
esac

if [ -d "$DROPIN_DIR" ]; then
  stale=$(grep -l -E '^(ExecStart|WorkingDirectory)=' "$DROPIN_DIR"/*.conf 2>/dev/null | wc -l)
  if [ "$stale" -gt 0 ]; then
    fail "$stale drop-in(s) override where production runs from:"
    grep -l -E '^(ExecStart|WorkingDirectory)=' "$DROPIN_DIR"/*.conf 2>/dev/null |
      while read -r f; do printf '        %s\n' "$f"; done
  else
    pass "no drop-in overrides ExecStart or WorkingDirectory"
  fi
else
  pass "no drop-in directory"
fi

# -- 4. the private interface -------------------------------------------------
printf '\nprivate network\n'
if [ "$(systemctl is-enabled tailscaled.service 2>/dev/null)" = "enabled" ]; then
  pass "tailscaled is enabled at boot"
else
  fail "tailscaled is not enabled at boot"
fi

bind_host=""
env_file="$HOME/cofferdam/workstation.env"
[ -r "$env_file" ] && bind_host=$(grep -E '^COFFERDAM_BIND_HOST=' "$env_file" | cut -d= -f2-)
if [ -n "$bind_host" ]; then
  case "$bind_host" in
    0.0.0.0|"") fail "bind host is public or unset: ${bind_host:-unset}" ;;
    *) if ip -o addr show 2>/dev/null | grep -q " ${bind_host}/"; then
         pass "configured bind address ${bind_host} is assigned"
       else
         warn "configured bind address ${bind_host} is not assigned right now"
         printf '        the daemon waits for it at startup rather than binding elsewhere\n'
       fi ;;
  esac
else
  warn "no COFFERDAM_BIND_HOST found to check"
fi

# -- 5. authentication survives a reboot -------------------------------------
# Existence and mode only. The value is never read, printed or compared.
printf '\nauthentication\n'
token_file="$HOME/cofferdam/secrets/token"
if [ -f "$token_file" ]; then
  mode=$(stat -c %a "$token_file" 2>/dev/null)
  if [ "$mode" = "600" ]; then
    pass "device token file present, mode 600"
  else
    fail "device token file mode is $mode, expected 600"
  fi
else
  fail "no device token file — the phone's saved token will not match"
fi

# -- 6. nothing left running from a previous validation ----------------------
printf '\nremote control\n'
rc_units=$(systemctl --user list-units 'cofferdam-rc*' --all --no-legend 2>/dev/null | wc -l)
if [ "$rc_units" -eq 0 ]; then
  pass "no Remote Control unit is loaded"
else
  warn "$rc_units Remote Control unit(s) loaded — a host should not persist across a reboot"
fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  printf 'preflight: ready for an unattended reboot\n\n'
  exit 0
fi
printf 'preflight: %d problem(s) — not ready\n\n' "$FAILURES"
exit 1
