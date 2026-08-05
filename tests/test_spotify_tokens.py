"""Where the user's authorization lives, and how it is written (M2D 10–17).

A refresh token is the durable half of "this person let Cofferdam control their
music". Everything here is about the two ways that goes wrong: it becomes
readable by somebody else, or it gets lost by a write that half-happened. Both
are silent, and both are only visible in tests that look at the filesystem.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from cofferdam.workstation.config import load_config
from cofferdam.workstation.spotifyplayer.mutestate import MuteStateStore
from cofferdam.workstation.spotifyplayer.oauth import REQUIRED_SCOPES
from cofferdam.workstation.spotifyplayer.tokens import (
    TOKEN_FILENAME,
    TokenStore,
    UserTokens,
    tokens_from_response,
)

from ._spotifyplayer_doubles import (
    FAKE_ACCESS_TOKEN,
    FAKE_REFRESH_TOKEN,
    FAKE_ROTATED_REFRESH_TOKEN,
    GRANTED_SCOPES,
)


class TokenStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()
        self.store = TokenStore(self.config)

    def _saved(self) -> UserTokens:
        tokens = UserTokens(
            refresh_token=FAKE_REFRESH_TOKEN,
            scopes=tuple(GRANTED_SCOPES.split()),
            display_name="Test Listener",
            connected_at="2026-08-05T11:00:00.000Z",
        )
        self.store.save(tokens)
        return tokens


class PermissionTests(TokenStoreTestCase):
    """Checks 11 and 12: 0600 on the file, 0700 on the directory."""

    def test_the_token_file_is_owner_only(self) -> None:
        self._saved()
        mode = stat.S_IMODE(self.store.path.stat().st_mode)
        self.assertEqual(mode, 0o600, f"token file is {oct(mode)}")

    def test_the_secrets_directory_is_owner_only(self) -> None:
        self._saved()
        mode = stat.S_IMODE(Path(self.config.secrets_dir).stat().st_mode)
        self.assertEqual(mode, 0o700, f"secrets directory is {oct(mode)}")

    def test_a_loosened_directory_is_tightened_on_the_next_write(self) -> None:
        os.chmod(self.config.secrets_dir, 0o755)
        self._saved()
        self.assertEqual(stat.S_IMODE(Path(self.config.secrets_dir).stat().st_mode), 0o700)

    def test_a_loose_file_produces_a_warning_rather_than_silence(self) -> None:
        self._saved()
        self.assertIsNone(self.store.permissions_note())
        os.chmod(self.store.path, 0o644)
        note = self.store.permissions_note()
        self.assertIsNotNone(note)
        self.assertIn("chmod 600", note)
        # The warning is about the file, and says nothing about its contents.
        self.assertNotIn(FAKE_REFRESH_TOKEN, note)

    def test_the_token_never_reaches_disk_world_readable_even_briefly(self) -> None:
        """The temporary file is 0600 from creation, not after the fact.

        A ``mkstemp`` + ``chmod`` pattern is what closes the window between
        creating the file and tightening it; this asserts the window is not
        opened in the first place, by watching the modes of everything that
        appears in the directory during the write.
        """
        observed = []
        real_chmod = os.chmod
        directory = Path(self.config.secrets_dir)

        original_fdopen = os.fdopen

        def watching_fdopen(fd, *args, **kwargs):
            for entry in directory.iterdir():
                observed.append((entry.name, stat.S_IMODE(entry.stat().st_mode)))
            return original_fdopen(fd, *args, **kwargs)

        os.fdopen = watching_fdopen
        try:
            self._saved()
        finally:
            os.fdopen = original_fdopen
            os.chmod = real_chmod

        self.assertTrue(observed, "no file existed during the write")
        for name, mode in observed:
            self.assertEqual(mode & 0o077, 0, f"{name} was group/world accessible at {oct(mode)}")


class AtomicWriteTests(TokenStoreTestCase):
    """Check 13: a reader sees the old file or the new one, never a torn one."""

    def test_the_write_goes_through_os_replace(self) -> None:
        import inspect

        source = inspect.getsource(TokenStore.save)
        self.assertIn("mkstemp", source)
        self.assertIn("os.replace", source)
        self.assertNotIn("open(self.path", source)

    def test_a_failed_replace_leaves_the_previous_token_intact(self) -> None:
        self._saved()
        before = self.store.path.read_text(encoding="utf-8")

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("disk full")

        os.replace = failing_replace
        try:
            with self.assertRaises(OSError):
                self.store.save(UserTokens(refresh_token="a-different-token"))
        finally:
            os.replace = real_replace

        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before)

    def test_a_failed_write_leaves_no_temporary_file_behind(self) -> None:
        real_replace = os.replace
        os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                self.store.save(UserTokens(refresh_token=FAKE_REFRESH_TOKEN))
        finally:
            os.replace = real_replace
        leftovers = [p.name for p in Path(self.config.secrets_dir).iterdir()]
        self.assertEqual(leftovers, [], f"temporary files left behind: {leftovers}")

    def test_the_written_document_holds_no_access_token(self) -> None:
        tokens = self._saved()
        tokens.access_token = FAKE_ACCESS_TOKEN
        self.store.save(tokens)
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertNotIn("access_token", document)
        self.assertNotIn(FAKE_ACCESS_TOKEN, self.store.path.read_text(encoding="utf-8"))


class RotationTests(TokenStoreTestCase):
    """Check 14: a refresh without a new token keeps the one already held.

    Spotify's PKCE documentation states plainly that the refresh response "might
    not include a new refresh token". Treating that absence as "the token is
    gone" would disconnect a working account at the next restart, and it would
    look like the user's fault.
    """

    def test_a_response_without_a_refresh_token_keeps_the_stored_one(self) -> None:
        tokens = self._saved()
        self.store.apply_refresh(
            tokens,
            {"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600, "scope": GRANTED_SCOPES},
        )
        self.assertEqual(tokens.refresh_token, FAKE_REFRESH_TOKEN)
        self.store.persist_if_changed(tokens)
        stored = TokenStore(self.config).load()
        self.assertEqual(stored.refresh_token, FAKE_REFRESH_TOKEN)

    def test_a_rotated_refresh_token_replaces_the_old_one_and_is_persisted(self) -> None:
        tokens = self._saved()
        self.store.apply_refresh(
            tokens,
            {
                "access_token": FAKE_ACCESS_TOKEN,
                "expires_in": 3600,
                "refresh_token": FAKE_ROTATED_REFRESH_TOKEN,
                "scope": GRANTED_SCOPES,
            },
        )
        self.assertEqual(tokens.refresh_token, FAKE_ROTATED_REFRESH_TOKEN)
        self.store.persist_if_changed(tokens)
        stored = TokenStore(self.config).load()
        self.assertEqual(stored.refresh_token, FAKE_ROTATED_REFRESH_TOKEN)
        # The newest token wins, and the old one is gone from the file.
        self.assertNotIn(FAKE_REFRESH_TOKEN, self.store.path.read_text(encoding="utf-8"))

    def test_a_blank_refresh_token_in_a_response_is_ignored(self) -> None:
        tokens = self._saved()
        self.store.apply_refresh(
            tokens, {"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600, "refresh_token": "  "}
        )
        self.assertEqual(tokens.refresh_token, FAKE_REFRESH_TOKEN)

    def test_an_unchanged_refresh_token_does_not_rewrite_the_file(self) -> None:
        tokens = self._saved()
        first = self.store.path.stat().st_mtime_ns
        time.sleep(0.01)
        self.store.apply_refresh(tokens, {"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600})
        self.store.persist_if_changed(tokens)
        self.assertEqual(self.store.path.stat().st_mtime_ns, first)

    def test_a_token_response_with_no_refresh_token_is_not_stored_at_all(self) -> None:
        """Check 15: no false connected state from an unrenewable grant."""
        self.assertIsNone(
            tokens_from_response({"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600})
        )
        self.assertIsNone(tokens_from_response({"refresh_token": "   "}))
        self.assertIsNone(tokens_from_response({}))

    def test_a_malformed_expiry_does_not_produce_a_token_valid_forever(self) -> None:
        tokens = tokens_from_response(
            {
                "access_token": FAKE_ACCESS_TOKEN,
                "refresh_token": FAKE_REFRESH_TOKEN,
                "expires_in": "not a number",
            },
            now=1000.0,
        )
        self.assertIsNotNone(tokens)
        self.assertFalse(tokens.access_token_valid(1000.0))


class ScopeTests(TokenStoreTestCase):
    """Check 16: a short authorization is detected before an action fails."""

    def test_missing_scopes_are_reported(self) -> None:
        tokens = UserTokens(
            refresh_token=FAKE_REFRESH_TOKEN, scopes=("user-read-playback-state",)
        )
        self.assertEqual(
            set(tokens.missing_scopes(REQUIRED_SCOPES)),
            {"user-read-currently-playing", "user-modify-playback-state"},
        )

    def test_a_complete_authorization_is_missing_nothing(self) -> None:
        tokens = UserTokens(refresh_token=FAKE_REFRESH_TOKEN, scopes=tuple(GRANTED_SCOPES.split()))
        self.assertEqual(tokens.missing_scopes(REQUIRED_SCOPES), ())

    def test_granted_scopes_survive_a_round_trip_through_the_file(self) -> None:
        self._saved()
        stored = TokenStore(self.config).load()
        self.assertEqual(set(stored.scopes), set(GRANTED_SCOPES.split()))


class DisconnectTests(TokenStoreTestCase):
    """Check 17: disconnect removes the local authorization, atomically."""

    def test_clear_removes_the_file_and_reports_that_it_did(self) -> None:
        self._saved()
        self.assertTrue(self.store.clear())
        self.assertFalse(self.store.path.exists())
        self.assertIsNone(TokenStore(self.config).load())

    def test_clear_on_a_disconnected_host_is_not_an_error(self) -> None:
        self.assertFalse(self.store.clear())

    def test_clear_uses_unlink_which_is_atomic(self) -> None:
        import inspect

        source = inspect.getsource(TokenStore.clear)
        self.assertIn("unlink", source)
        # Never truncate-in-place: a reader could observe an empty file that is
        # still there, which reads as a corrupt authorization rather than none.
        self.assertNotIn("write_text", source)
        self.assertNotIn('open(', source)

    def test_clear_does_not_claim_provider_revocation(self) -> None:
        source = TokenStore.clear.__doc__ or ""
        self.assertIn("does not revoke", source.lower())


class ReadTests(TokenStoreTestCase):
    """A broken file means "not connected", not a crashed status endpoint."""

    def test_a_missing_file_reads_as_no_authorization(self) -> None:
        self.assertIsNone(self.store.load())

    def test_a_corrupt_file_reads_as_no_authorization(self) -> None:
        self.store.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(TokenStore(self.config).load())

    def test_a_document_without_a_refresh_token_reads_as_no_authorization(self) -> None:
        self.store.path.write_text(json.dumps({"version": 1, "scopes": []}), encoding="utf-8")
        self.assertIsNone(TokenStore(self.config).load())

    def test_the_repr_does_not_carry_the_token_into_a_traceback(self) -> None:
        tokens = UserTokens(refresh_token=FAKE_REFRESH_TOKEN)
        self.assertNotIn(FAKE_REFRESH_TOKEN, repr(tokens))
        self.assertIn("redacted", repr(tokens))

    def test_the_public_view_has_no_token_hash_prefix_or_length(self) -> None:
        tokens = self._saved()
        view = tokens.public_view()
        self.assertEqual(set(view), {"scopes", "display_name", "connected_at"})
        blob = json.dumps(view)
        self.assertNotIn(FAKE_REFRESH_TOKEN, blob)
        self.assertNotIn(FAKE_REFRESH_TOKEN[:8], blob)
        self.assertNotIn(str(len(FAKE_REFRESH_TOKEN)), blob)


class MuteStateTests(unittest.TestCase):
    """The restore level lives beside the state, never inside the secret."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = load_config(home=Path(self._tmp.name))
        self.config.ensure_dirs()
        self.store = MuteStateStore(self.config)

    def test_it_is_not_written_into_the_oauth_secret_file(self) -> None:
        self.store.remember("spdev-a", 60)
        self.assertNotEqual(self.store.path.name, TOKEN_FILENAME)
        self.assertIn("state", str(self.store.path))

    def test_a_remembered_level_comes_back(self) -> None:
        self.store.remember("spdev-a", 60)
        self.assertEqual(self.store.restore_value("spdev-a"), 60)

    def test_zero_is_never_remembered_as_something_to_restore_to(self) -> None:
        self.store.remember("spdev-a", 0)
        self.assertIsNone(self.store.restore_value("spdev-a"))

    def test_an_unknown_device_has_no_restore_value(self) -> None:
        """Check 47: after a restart, nothing is guessed."""
        self.assertIsNone(self.store.restore_value("spdev-never-seen"))

    def test_records_are_per_device(self) -> None:
        self.store.remember("spdev-a", 60)
        self.store.remember("spdev-b", 20)
        self.assertEqual(self.store.restore_value("spdev-a"), 60)
        self.assertEqual(self.store.restore_value("spdev-b"), 20)

    def test_the_file_is_bounded(self) -> None:
        from cofferdam.workstation.spotifyplayer.mutestate import MAX_RECORDS

        for index in range(MAX_RECORDS + 8):
            self.store.remember(f"spdev-{index}", 50)
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertLessEqual(len(document["devices"]), MAX_RECORDS)

    def test_clear_drops_everything(self) -> None:
        self.store.remember("spdev-a", 60)
        self.store.clear()
        self.assertIsNone(self.store.restore_value("spdev-a"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
