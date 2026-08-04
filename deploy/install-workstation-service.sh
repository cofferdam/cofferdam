#!/usr/bin/env bash
#
# Install or migrate the Cofferdam workstation user service.
#
# Idempotent and transactional: it inventories what is installed, backs up
# every Cofferdam-owned unit file and enablement symlink it is about to touch,
# then installs the corrected unit and enables it under the one target that is
# safe before login (default.target).
#
# It exists mainly to migrate hosts off the M1 unit that caused the login loop
# (Wants=graphical-session.target under lingering — see docs/SERVICE_LIFECYCLE.md).
#
# What this script will NEVER do:
#   * touch any file it does not own (only cofferdam-workstation.service and
#     Cofferdam's own enablement symlinks are ever written or removed);
#   * delete ~/.config, ~/.local, ~/.cache, or any part of them;
#   * reset dconf or change any GNOME setting;
#   * remove unrelated user units;
#   * start, stop, restart, or isolate graphical-session.target;
#   * terminate a login session, a user manager, or GNOME;
#   * enable automatic login;
#   * write a secret into a unit file;
#   * use pkill/killall, or signal any process it did not verify as its own.
#
# Usage:
#   deploy/install-workstation-service.sh            # install/migrate and start
#   deploy/install-workstation-service.sh --dry-run  # print the plan, change nothing
#
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--dry-run]" >&2
    exit 2
fi

UNIT_NAME="cofferdam-workstation.service"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SOURCE_UNIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$UNIT_NAME"
COFFERDAM_HOME="${COFFERDAM_HOME:-$HOME/cofferdam}"
BACKUP_ROOT="$COFFERDAM_HOME/state/service-backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  would run: $*"
    else
        "$@"
    fi
}

say() { printf '%s\n' "$*"; }

if [ ! -f "$SOURCE_UNIT" ]; then
    echo "error: unit template not found at $SOURCE_UNIT" >&2
    exit 1
fi

# --- 1. inventory ----------------------------------------------------------
say "== 1. Inventory =="
say "unit template:   $SOURCE_UNIT"
say "install dir:     $USER_UNIT_DIR"
say "installed unit:  $([ -f "$USER_UNIT_DIR/$UNIT_NAME" ] && echo present || echo absent)"
say "linger:          $(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo unknown)"
say "active state:    $(systemctl --user is-active "$UNIT_NAME" 2>/dev/null || true)"
say "enabled state:   $(systemctl --user is-enabled "$UNIT_NAME" 2>/dev/null || true)"

# Every enablement symlink pointing at our unit, wherever it was installed.
mapfile -t COFFERDAM_LINKS < <(
    find "${USER_UNIT_DIR}" "${USER_UNIT_DIR}.disabled" -name "$UNIT_NAME" -type l 2>/dev/null || true
)
if [ "${#COFFERDAM_LINKS[@]}" -gt 0 ]; then
    say "enablement symlinks found:"
    for link in "${COFFERDAM_LINKS[@]}"; do say "  $link"; done
else
    say "enablement symlinks found: none"
fi

# --- 2. backup -------------------------------------------------------------
say
say "== 2. Backup (Cofferdam-owned files only) =="
say "backup dir: $BACKUP_DIR"
run mkdir -p "$BACKUP_DIR"
if [ -f "$USER_UNIT_DIR/$UNIT_NAME" ]; then
    run cp -a "$USER_UNIT_DIR/$UNIT_NAME" "$BACKUP_DIR/$UNIT_NAME"
    say "backed up installed unit"
fi
# Record the symlinks as a restorable list rather than copying them.
if [ "${#COFFERDAM_LINKS[@]}" -gt 0 ] && [ "$DRY_RUN" = "0" ]; then
    printf '%s\n' "${COFFERDAM_LINKS[@]}" > "$BACKUP_DIR/enablement-symlinks.txt"
fi

# --- 3. disable the unsafe enablement path ---------------------------------
say
say "== 3. Disable the previous enablement path =="
# `disable` removes only the symlinks systemd created for THIS unit.
run systemctl --user disable "$UNIT_NAME" 2>/dev/null || true

# --- 4. stop only Cofferdam-owned services ---------------------------------
say
say "== 4. Stop Cofferdam-owned units =="
run systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
# Transient application units are Cofferdam-owned by name prefix. They are the
# user's browser windows, so they are deliberately left running; only failed
# ones are swept.
run systemctl --user reset-failed 'cofferdam-app-*' 2>/dev/null || true

# --- 5. daemon-reload ------------------------------------------------------
say
say "== 5. daemon-reload =="
run systemctl --user daemon-reload

# --- 6. install the corrected unit -----------------------------------------
say
say "== 6. Install corrected unit =="
run mkdir -p "$USER_UNIT_DIR"
run install -m 0644 "$SOURCE_UNIT" "$USER_UNIT_DIR/$UNIT_NAME"
run systemctl --user daemon-reload

# --- 7. validate before enabling -------------------------------------------
say
say "== 7. Validate =="
if [ "$DRY_RUN" = "0" ]; then
    # systemd-analyze verify exits non-zero on syntax errors.
    if ! systemd-analyze --user verify "$USER_UNIT_DIR/$UNIT_NAME" 2>&1; then
        echo "error: unit failed verification; not enabling. Backup is at $BACKUP_DIR" >&2
        exit 1
    fi
    # The regression guard: this unit must not reference the graphical target.
    if grep -nE '^\s*(Wants|Requires|BindsTo|PartOf|Upholds|Requisite|WantedBy|RequiredBy)\s*=.*graphical-session\.target' \
        "$USER_UNIT_DIR/$UNIT_NAME"; then
        echo "error: installed unit declares a dependency on graphical-session.target." >&2
        echo "       That is the login-loop regression. Refusing to enable." >&2
        exit 1
    fi
    say "unit verified: no graphical-session.target dependency"
else
    say "  would run: systemd-analyze --user verify $USER_UNIT_DIR/$UNIT_NAME"
fi

# --- 8. enable under the correct target ------------------------------------
say
say "== 8. Enable (default.target only) =="
run systemctl --user enable "$UNIT_NAME"

# --- 9. start --------------------------------------------------------------
say
say "== 9. Start =="
run systemctl --user start "$UNIT_NAME"

# --- 10. verify ------------------------------------------------------------
say
say "== 10. Verify =="
if [ "$DRY_RUN" = "0" ]; then
    sleep 2
    systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
    say
    say "graphical-session.target state (must be driven by GNOME, never by us):"
    systemctl --user is-active graphical-session.target || true
    say
    say "Reverse dependencies of graphical-session.target — Cofferdam must NOT appear:"
    systemctl --user list-dependencies --reverse graphical-session.target --no-pager 2>/dev/null |
        grep -i cofferdam && {
            echo "error: Cofferdam still appears in the graphical target's reverse deps." >&2
            exit 1
        } || say "  (clean)"
fi

say
say "Done. Backup of the previous state: $BACKUP_DIR"
say "Rollback:  deploy/uninstall-workstation-service.sh"
say "See docs/SERVICE_LIFECYCLE.md for the validation matrix that must still be run."
