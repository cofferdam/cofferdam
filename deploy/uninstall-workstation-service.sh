#!/usr/bin/env bash
#
# Remove the Cofferdam workstation user service — and nothing else.
#
# This is also the ROLLBACK path for the login-lifecycle migration, and the
# recovery path from a TTY if a graphical login ever fails again. It restores a
# login-capable state by removing Cofferdam's own enablement symlinks, without
# touching GNOME or any unrelated user configuration.
#
# Recovery from a TTY (Ctrl+Alt+F3, log in on the text console):
#
#   systemctl --user disable --now cofferdam-workstation.service
#   systemctl --user daemon-reload
#
# ...or run this script. Then reboot, or switch back to Ctrl+Alt+F1/F2.
#
# What this script removes (exhaustively):
#   * ~/.config/systemd/user/cofferdam-workstation.service
#   * enablement symlinks systemd created for that unit
#   * failed transient cofferdam-app-* unit state
#
# What it NEVER touches:
#   * ~/.config, ~/.local, ~/.cache, or dconf, beyond the one unit file above
#   * any unit it does not own
#   * GNOME settings, autologin, or the graphical session
#   * the device token, workstation.env, action records, or any other private
#     state under ~/cofferdam (use --purge-state to be asked about those)
#   * lingering (see --disable-linger)
#
set -euo pipefail

DISABLE_LINGER=0
for arg in "$@"; do
    case "$arg" in
        --disable-linger) DISABLE_LINGER=1 ;;
        *) echo "usage: $0 [--disable-linger]" >&2; exit 2 ;;
    esac
done

UNIT_NAME="cofferdam-workstation.service"
USER_UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "== Stopping and disabling $UNIT_NAME =="
# `disable` removes only the symlinks systemd created for this unit.
systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true

echo "== Removing the unit file =="
if [ -f "$USER_UNIT_DIR/$UNIT_NAME" ]; then
    rm -f -- "$USER_UNIT_DIR/$UNIT_NAME"
    echo "removed $USER_UNIT_DIR/$UNIT_NAME"
else
    echo "not installed at $USER_UNIT_DIR/$UNIT_NAME — nothing to remove"
fi

# Sweep any enablement symlink left behind by an older layout. Each is checked
# by name AND by resolving to our unit, so nothing else can ever be unlinked.
echo "== Sweeping stale enablement symlinks =="
while IFS= read -r link; do
    [ -n "$link" ] || continue
    target="$(readlink -f -- "$link" 2>/dev/null || true)"
    case "$target" in
        *"$UNIT_NAME") rm -f -- "$link"; echo "removed $link" ;;
        "") rm -f -- "$link"; echo "removed dangling $link" ;;
        *) echo "left alone (not ours): $link" ;;
    esac
done < <(find "$USER_UNIT_DIR" -name "$UNIT_NAME" -type l 2>/dev/null || true)

echo "== daemon-reload =="
systemctl --user daemon-reload
systemctl --user reset-failed "$UNIT_NAME" 2>/dev/null || true
systemctl --user reset-failed 'cofferdam-app-*' 2>/dev/null || true

if [ "$DISABLE_LINGER" = "1" ]; then
    echo "== Disabling lingering =="
    echo "note: this also stops the user manager from starting at boot, which"
    echo "      affects any other user service you rely on."
    loginctl disable-linger "$USER"
else
    echo
    echo "Lingering left as-is (it is a user-level setting, not Cofferdam-owned)."
    echo "To turn it off as well:  loginctl disable-linger $USER"
fi

echo
echo "Done. GNOME, dconf, and all unrelated user configuration are untouched."
echo "Private state under ~/cofferdam (token, env, action records) is untouched."
