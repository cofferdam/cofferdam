"""PKCE, state, and the loopback callback (M2D checks 1–9, 55).

The properties here are the ones that decide whether an attacker with access to
the workstation's loopback interface, or to a stale browser tab, can obtain an
authorization Cofferdam would then store and use. Each is tested for what it
*refuses*, because every one of them is a guard whose failure mode is silent.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request

from cofferdam.workstation.spotifyplayer import oauth
from cofferdam.workstation.spotifyplayer.callback import (
    FAILURE_HTML,
    SUCCESS_HTML,
    CallbackListener,
    CallbackResult,
)


class RandomnessTests(unittest.TestCase):
    """Check 1: the state is unguessable, and check 2: PKCE is correct."""

    def test_state_is_long_random_and_never_repeats(self) -> None:
        states = {oauth.generate_state() for _ in range(200)}
        self.assertEqual(len(states), 200, "generated states collided")
        for state in states:
            # 32 random bytes -> 43 unpadded base64url characters.
            self.assertGreaterEqual(len(state), 43)
            self.assertRegex(state, r"^[A-Za-z0-9_-]+$")

    def test_state_comes_from_the_cryptographic_source(self) -> None:
        """Not ``random``. A predictable state is a forgeable callback."""
        import inspect

        source = inspect.getsource(oauth)
        self.assertIn("import secrets", source)
        self.assertNotIn("import random", source)
        self.assertIn("secrets.token_bytes", source)

    def test_verifier_length_is_inside_the_documented_range(self) -> None:
        for _ in range(50):
            verifier = oauth.generate_verifier()
            # Spotify documents 43–128 characters from the unreserved set.
            self.assertGreaterEqual(len(verifier), 43)
            self.assertLessEqual(len(verifier), 128)
            self.assertRegex(verifier, r"^[A-Za-z0-9._~-]+$")

    def test_verifiers_do_not_repeat(self) -> None:
        verifiers = {oauth.generate_verifier() for _ in range(200)}
        self.assertEqual(len(verifiers), 200)

    def test_s256_challenge_is_the_unpadded_base64url_sha256(self) -> None:
        """Computed independently here, from the spec rather than from the code."""
        for _ in range(20):
            verifier = oauth.generate_verifier()
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
                .decode("ascii")
                .rstrip("=")
            )
            self.assertEqual(oauth.challenge_for(verifier), expected)
            self.assertNotIn("=", oauth.challenge_for(verifier))

    def test_the_authorize_url_carries_s256_and_the_loopback_redirect(self) -> None:
        url = oauth.build_authorize_url("client-id", "the-state", "the-challenge", ("scope-a",))
        self.assertTrue(url.startswith("https://accounts.spotify.com/authorize?"))
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("response_type=code", url)
        self.assertIn("127.0.0.1%3A8888%2Fcallback", url)
        # The verifier is the secret half and must never travel in the URL.
        self.assertNotIn("code_verifier", url)

    def test_the_redirect_uri_is_an_explicit_loopback_address(self) -> None:
        """Spotify's rules permit a loopback IP over HTTP and refuse ``localhost``."""
        self.assertEqual(oauth.REDIRECT_URI, "http://127.0.0.1:8888/callback")
        self.assertNotIn("localhost", oauth.REDIRECT_URI)

    def test_only_the_three_player_scopes_are_requested(self) -> None:
        self.assertEqual(
            set(oauth.REQUIRED_SCOPES),
            {
                "user-read-playback-state",
                "user-read-currently-playing",
                "user-modify-playback-state",
            },
        )
        # Not requested: the Web Playback SDK scope, and anything about the
        # person rather than the player.
        for unwanted in ("streaming", "user-read-email", "user-read-private", "user-top-read"):
            self.assertNotIn(unwanted, oauth.REQUIRED_SCOPES)


class AttemptRegistryTests(unittest.TestCase):
    """Checks 3, 4, 5: mismatch, expiry and replay are all refused."""

    def setUp(self) -> None:
        self.now = 1000.0
        self.registry = oauth.AttemptRegistry(ttl_seconds=300, clock=lambda: self.now)

    def test_a_matching_state_consumes_the_attempt(self) -> None:
        attempt = self.registry.start("client-id")
        self.assertIsNotNone(self.registry.consume(attempt.state))

    def test_a_mismatched_state_is_refused(self) -> None:
        attempt = self.registry.start("client-id")
        self.assertIsNone(self.registry.consume(attempt.state + "x"))
        self.assertIsNone(self.registry.consume(""))
        self.assertIsNone(self.registry.consume(None))
        self.assertIsNone(self.registry.consume(12345))

    def test_a_mismatched_state_does_not_cancel_the_real_attempt(self) -> None:
        """An unrelated loopback request must not be able to interrupt a user."""
        attempt = self.registry.start("client-id")
        self.registry.consume("not-the-state")
        self.assertIsNotNone(self.registry.consume(attempt.state))

    def test_an_expired_attempt_is_refused_and_dropped(self) -> None:
        attempt = self.registry.start("client-id")
        self.now += 301
        self.assertIsNone(self.registry.consume(attempt.state))
        self.assertIsNone(self.registry.current())

    def test_a_replayed_callback_finds_nothing_to_consume(self) -> None:
        attempt = self.registry.start("client-id")
        self.assertIsNotNone(self.registry.consume(attempt.state))
        self.assertIsNone(self.registry.consume(attempt.state))

    def test_starting_again_invalidates_the_previous_attempt(self) -> None:
        first = self.registry.start("client-id")
        second = self.registry.start("client-id")
        self.assertIsNone(self.registry.consume(first.state))
        self.assertIsNotNone(self.registry.consume(second.state))

    def test_the_attempt_ttl_is_short_and_bounded(self) -> None:
        self.assertLessEqual(oauth.ATTEMPT_TTL_SECONDS, 600)
        self.assertGreaterEqual(oauth.ATTEMPT_TTL_SECONDS, 60)

    def test_current_reports_nothing_once_the_attempt_has_expired(self) -> None:
        """Check 55: a pending state expires rather than hanging."""
        self.registry.start("client-id")
        self.assertIsNotNone(self.registry.current())
        self.now += oauth.ATTEMPT_TTL_SECONDS + 1
        self.assertIsNone(self.registry.current())

    def test_the_public_view_carries_no_secret(self) -> None:
        attempt = self.registry.start("client-id")
        view = attempt.public_view()
        self.assertEqual(set(view), {"expires_in_seconds"})
        blob = repr(view)
        for secret in (attempt.state, attempt.verifier, attempt.challenge, attempt.authorize_url):
            self.assertNotIn(secret, blob)

    def test_state_comparison_is_constant_time(self) -> None:
        import inspect

        source = inspect.getsource(oauth.states_match)
        self.assertIn("compare_digest", source)


class CallbackListenerTests(unittest.TestCase):
    """Checks 6, 7, 8: loopback only, one path, and nothing written down."""

    def _listener(self, validator=lambda result: bool(result.code)):
        listener = CallbackListener(port=0)
        listener.start(validator)
        self.addCleanup(listener.stop)
        return listener

    def test_it_binds_only_to_loopback(self) -> None:
        listener = self._listener()
        host, _port = listener.bound_address
        self.assertEqual(host, "127.0.0.1")
        self.assertNotEqual(host, "0.0.0.0")

    def test_it_refuses_to_be_constructed_on_any_other_address(self) -> None:
        """Structural: no configuration mistake can widen this later."""
        for address in ("0.0.0.0", "::", "100.64.0.1", "localhost", ""):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    CallbackListener(host=address)

    def test_it_is_not_reachable_on_a_routable_address(self) -> None:
        listener = self._listener()
        _host, port = listener.bound_address
        probe = socket.socket()
        self.addCleanup(probe.close)
        probe.settimeout(1.0)
        # Its own hostname address, which is where a mistaken bind would show up.
        try:
            outward = socket.gethostbyname(socket.gethostname())
        except OSError:  # pragma: no cover - unusual host configuration
            self.skipTest("no resolvable hostname on this machine")
        if outward.startswith("127."):
            self.skipTest("this host resolves its own name to loopback")
        with self.assertRaises(OSError):
            probe.connect((outward, port))

    def _get(self, port: int, path: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def test_only_the_callback_path_is_served(self) -> None:
        listener = self._listener()
        _host, port = listener.bound_address
        for path in ("/", "/callbac", "/callback/extra", "/../callback", "/token"):
            with self.subTest(path=path):
                status, _body = self._get(port, path)
                self.assertEqual(status, 404)

    def test_the_callback_path_accepts_a_code_and_state(self) -> None:
        listener = self._listener(validator=lambda result: result.state == "the-state")
        _host, port = listener.bound_address
        status, body = self._get(port, "/callback?code=abc&state=the-state")
        self.assertEqual(status, 200)
        self.assertIn("Spotify connected", body)
        received = listener.wait(2.0)
        self.assertIsNotNone(received)
        self.assertEqual(received.code, "abc")

    def test_a_rejected_callback_gets_a_failure_page_and_delivers_nothing(self) -> None:
        listener = self._listener(validator=lambda result: False)
        _host, port = listener.bound_address
        status, body = self._get(port, "/callback?code=abc&state=wrong")
        self.assertEqual(status, 400)
        self.assertIn("not connected", body)
        self.assertIsNone(listener.wait(0.2))

    def test_a_second_delivery_is_refused(self) -> None:
        """Check 5 at the socket: a replayed redirect cannot land twice."""
        listener = self._listener(validator=lambda result: True)
        _host, port = listener.bound_address
        first, _ = self._get(port, "/callback?code=abc&state=s")
        second, _ = self._get(port, "/callback?code=abc&state=s")
        self.assertEqual(first, 200)
        self.assertEqual(second, 400)

    def test_post_is_not_the_callback(self) -> None:
        listener = self._listener()
        _host, port = listener.bound_address
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/callback", data=b"code=abc", method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 405)

    def test_a_repeated_parameter_is_not_a_valid_callback(self) -> None:
        seen = []
        listener = self._listener(validator=lambda result: seen.append(result) or False)
        _host, port = listener.bound_address
        self._get(port, "/callback?code=a&code=b&state=s")
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0].code)

    def test_the_page_a_person_sees_contains_no_credential(self) -> None:
        """The browser tab is the least private surface in the whole flow."""
        for page in (SUCCESS_HTML, FAILURE_HTML):
            lowered = page.lower()
            for word in ("token", "code=", "verifier", "client_id", "secret", "authorization:"):
                self.assertNotIn(word, lowered)

    def test_the_authorization_code_is_never_logged(self) -> None:
        """Check 8. ``BaseHTTPRequestHandler`` logs the request line by default.

        That line contains the code, so the default would put a credential into
        the daemon's stderr as a side effect of a successful authorization.
        """
        import contextlib

        listener = self._listener(validator=lambda result: True)
        _host, port = listener.bound_address
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            self._get(port, "/callback?code=" + "SUPERSECRETCODE" + "&state=s")
            time.sleep(0.05)
        self.assertNotIn("SUPERSECRETCODE", captured.getvalue())

    def test_the_handler_silences_the_default_access_log(self) -> None:
        from cofferdam.workstation.spotifyplayer import callback as callback_module

        self.assertIn("log_message", callback_module._Handler.__dict__)

    def test_a_very_long_request_line_is_refused_without_being_parsed(self) -> None:
        listener = self._listener(validator=lambda result: True)
        _host, port = listener.bound_address
        status, _body = self._get(port, "/callback?code=" + "a" * 9000)
        self.assertIn(status, (414, 400, 431))
        self.assertIsNone(listener.wait(0.2))

    def test_the_listener_stops_and_frees_the_socket(self) -> None:
        listener = CallbackListener(port=0)
        listener.start(lambda result: True)
        _host, port = listener.bound_address
        listener.stop()
        probe = socket.socket()
        probe.settimeout(1.0)
        with self.assertRaises(OSError):
            probe.connect(("127.0.0.1", port))
        probe.close()


class NoSecretInSourceTests(unittest.TestCase):
    """Check 9: the verifier is never returned and never written anywhere."""

    def test_the_verifier_is_not_in_any_public_view(self) -> None:
        registry = oauth.AttemptRegistry()
        attempt = registry.start("client-id")
        self.assertNotIn("verifier", attempt.public_view())

    def test_the_callback_result_carries_no_verifier_field(self) -> None:
        result = CallbackResult(code="c", state="s", error=None)
        self.assertEqual(
            {field for field in result.__dataclass_fields__}, {"code", "state", "error"}
        )

    def test_nothing_in_the_package_prints_or_logs(self) -> None:
        """No ``print``, no ``logging`` — the whole package handles credentials."""
        import pathlib

        import cofferdam.workstation.spotifyplayer as package

        root = pathlib.Path(package.__file__).parent
        for path in sorted(root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            # Strip docstrings and comments crudely: a mention in prose is fine,
            # a call is not.
            with self.subTest(module=path.name):
                self.assertIsNone(
                    re.search(r"^\s*print\(", source, re.MULTILINE),
                    f"{path.name} calls print()",
                )
                self.assertNotIn("import logging", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
