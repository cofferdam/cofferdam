"""The loopback player listener and its trust boundary (M2E 43–45).

These tests bind a **real socket** and speak real HTTP to it. That is deliberate
and is the one place in this milestone where a fake would prove nothing: the
properties under test are properties of the listener — which address it is bound
to, which Host headers it accepts, which content types it requires, and what it
never sends back — and every one of them lives below the Python API the rest of
the tests exercise.

The four defences, each with a test:

* it binds to 127.0.0.1 and to nothing else;
* a Host header that is not a loopback authority is refused (DNS rebinding);
* a channel request without ``application/json`` is refused, which is what forces
  a cross-origin caller into a preflight;
* no CORS header is ever sent, on any path, on any status, including ``OPTIONS``.

And one property that is not a defence but a requirement, added after real-host
validation found it broken: the page must be able to tell YouTube **which page
is embedding it**, or the embed is refused with error 153. See
:class:`EmbeddingIdentity`.
"""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from cofferdam.workstation.youtubeplayer.channel import PlayerChannel
from cofferdam.workstation.youtubeplayer.endpoint import (
    LOOPBACK_HOST,
    MAX_BODY_BYTES,
    PATH_ACK,
    PATH_COMMANDS,
    PATH_PLAYER,
    PATH_REGISTER,
    PATH_STATE,
    PlayerEndpoint,
)


def _python_code_only(source: str) -> str:
    """Python source with comments and docstrings removed.

    The Python counterpart of ``_runtime_doubles.code_only``. Structural guards
    ask what a module can *do*, so they must scan code rather than the prose
    explaining why it does not do it.
    """
    import ast
    import io
    import tokenize

    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add((body[0].lineno, body[0].col_offset))

    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and token.start in docstrings:
            continue
        kept.append(token.string)
    return "\n".join(kept)


def _html_code_only(source: str) -> str:
    """HTML with ``<!-- ... -->`` comments removed, for the same reason."""
    import re

    return re.sub(r"<!--.*?-->", " ", source, flags=re.DOTALL)


class EndpointTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.channel = PlayerChannel()
        self.endpoint = PlayerEndpoint(self.channel)
        self.port = self.endpoint.start()
        self.addCleanup(self.endpoint.stop)

    def url(self, path: str) -> str:
        return "http://127.0.0.1:" + str(self.port) + path

    def request(
        self,
        path: str,
        payload=None,
        method: str = "POST",
        content_type: str = "application/json",
        host: str = None,
        origin: str = None,
        raw: bytes = None,
    ):
        body = raw if raw is not None else (
            json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
        )
        request = urllib.request.Request(self.url(path), data=body, method=method)
        if content_type is not None and body is not None:
            request.add_header("Content-Type", content_type)
        if host is not None:
            request.add_header("Host", host)
        if origin is not None:
            request.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()


class Binding(EndpointTestCase):
    def test_binds_only_to_loopback(self):
        """43. The listener is on 127.0.0.1 and the address is a constant."""
        self.assertEqual(LOOPBACK_HOST, "127.0.0.1")
        # It answers on loopback...
        status, _, _ = self.request(PATH_PLAYER, method="GET")
        self.assertEqual(status, 200)

    def test_the_socket_is_bound_to_loopback(self):
        """43. The operating system agrees about which address is bound.

        Kept separate from the connection probe below so this assertion always
        runs and always reports, on every host, rather than being skipped along
        with a probe that needs a routable address to be meaningful.
        """
        self.assertEqual(self.endpoint._server.server_address[0], "127.0.0.1")

    def test_is_not_reachable_on_a_non_loopback_address(self):
        """43. A routable address on this host refuses the same port."""
        hostname_address = None
        try:
            hostname_address = socket.gethostbyname(socket.gethostname())
        except OSError:  # pragma: no cover - a host with no resolvable name
            self.skipTest("this host has no resolvable non-loopback address")
        if hostname_address.startswith("127."):
            self.skipTest("this host resolves its own name to loopback")

        probe = socket.socket()
        probe.settimeout(2)
        with self.assertRaises(OSError):
            probe.connect((hostname_address, self.port))
        probe.close()

    def test_bind_address_is_not_configurable(self):
        """No environment variable or config key can widen the bind.

        Scanned with comments and docstrings stripped, for the reason the
        repository's own license scanner documents: the module *explains* that it
        never binds ``0.0.0.0``, and a scan that matched its own explanation
        would push the next author to delete the sentence rather than keep the
        property.
        """
        import ast
        import inspect

        from cofferdam.workstation.youtubeplayer import endpoint as module

        source = _python_code_only(inspect.getsource(module))
        self.assertNotIn("0.0.0.0", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)
        # And the constant itself is a plain string literal, not an expression
        # that could resolve to something else at import time.
        tree = ast.parse(inspect.getsource(module))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "LOOPBACK_HOST" for target in node.targets
            ):
                self.assertIsInstance(node.value, ast.Constant)
                self.assertEqual(node.value.value, "127.0.0.1")
                break
        else:  # pragma: no cover - the constant must exist
            self.fail("LOOPBACK_HOST is not a module-level constant")


class HostHeader(EndpointTestCase):
    def test_a_foreign_host_header_is_refused(self):
        """The DNS-rebinding defence. A page's own domain is not loopback."""
        for hostile in ("evil.example.com", "attacker.test:1234", "cofferdam.local"):
            status, _, _ = self.request(
                PATH_REGISTER, {}, host=hostile
            )
            self.assertEqual(status, 421, hostile)

    def test_loopback_authorities_are_accepted(self):
        for allowed in ("127.0.0.1:" + str(self.port), "localhost:" + str(self.port)):
            status, _, _ = self.request(PATH_REGISTER, {}, host=allowed)
            self.assertEqual(status, 200, allowed)

    def test_the_host_check_runs_before_the_body_is_read(self):
        """A refused Host must not cause work on behalf of the caller."""
        status, _, body = self.request(
            PATH_STATE, {"instance_id": "x", "state": {}}, host="evil.example.com"
        )
        self.assertEqual(status, 421)
        self.assertNotIn(b"superseded", body)


class ContentType(EndpointTestCase):
    def test_channel_requests_require_application_json(self):
        """This is what forces a cross-origin caller into a preflight."""
        for content_type in (
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "text/plain;charset=UTF-8",
        ):
            status, _, _ = self.request(PATH_REGISTER, {}, content_type=content_type)
            self.assertEqual(status, 415, content_type)

    def test_every_channel_path_enforces_it(self):
        """No exception on any path — the rule is what makes it a protection."""
        for path in (PATH_REGISTER, PATH_COMMANDS, PATH_STATE, PATH_ACK):
            status, _, _ = self.request(path, {}, content_type="text/plain")
            self.assertEqual(status, 415, path)

    def test_an_oversized_body_is_refused(self):
        status, _, _ = self.request(PATH_STATE, raw=b"{" + b"a" * (MAX_BODY_BYTES + 10))
        self.assertEqual(status, 413)

    def test_malformed_json_is_refused(self):
        status, _, _ = self.request(PATH_REGISTER, raw=b"not json at all")
        self.assertEqual(status, 400)

    def test_a_json_array_is_refused(self):
        status, _, _ = self.request(PATH_REGISTER, raw=b"[1,2,3]")
        self.assertEqual(status, 400)


class Cors(EndpointTestCase):
    def _assert_no_cors(self, headers, where):
        for header in headers:
            self.assertFalse(
                header.lower().startswith("access-control-"),
                where + " sent " + header,
            )

    def test_no_cors_header_on_success(self):
        _, headers, _ = self.request(PATH_REGISTER, {})
        self._assert_no_cors(headers, "register")

    def test_no_cors_header_on_the_preflight(self):
        """An OPTIONS that gets no allow-origin means the real request never happens."""
        _, headers, _ = self.request(PATH_REGISTER, method="OPTIONS", content_type=None)
        self._assert_no_cors(headers, "preflight")

    def test_no_cors_header_on_a_refusal(self):
        _, headers, _ = self.request(PATH_REGISTER, {}, content_type="text/plain")
        self._assert_no_cors(headers, "415")

    def test_no_cors_header_on_the_player_document(self):
        _, headers, _ = self.request(PATH_PLAYER, method="GET")
        self._assert_no_cors(headers, "document")

    def test_a_foreign_origin_is_refused(self):
        status, _, _ = self.request(
            PATH_REGISTER, {}, origin="https://evil.example.com"
        )
        self.assertEqual(status, 403)


class Paths(EndpointTestCase):
    def test_only_fixed_paths_exist(self):
        for unknown in ("/", "/admin", "/api/status", "/channel", "/channel/eval"):
            status, _, _ = self.request(unknown, {}, method="POST")
            self.assertEqual(status, 404, unknown)

    def test_no_path_traversal_is_possible(self):
        """Nothing maps a request path to a filesystem path."""
        for hostile in (
            "/../../etc/passwd",
            "/player/../../../etc/passwd",
            "/player.js/../../secrets/token",
        ):
            status, _, _ = self.request(hostile, method="GET")
            self.assertIn(status, (400, 404), hostile)

    def test_channel_operations_are_not_reachable_by_get(self):
        """No GET mutates, and no instance id ever travels in a URL."""
        for path in (PATH_REGISTER, PATH_COMMANDS, PATH_STATE, PATH_ACK):
            status, _, _ = self.request(path, method="GET")
            self.assertEqual(status, 404, path)

    def test_the_player_document_is_served_with_a_restrictive_policy(self):
        status, headers, body = self.request(PATH_PLAYER, method="GET")
        self.assertEqual(status, 200)
        policy = headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-src https://www.youtube.com", policy)
        self.assertIn("connect-src 'self'", policy)
        # No inline script is permitted, so a modified document cannot run one.
        self.assertNotIn("'unsafe-inline'", policy.split("script-src")[1].split(";")[0])
        self.assertIn(b"Cofferdam YouTube player", body)

    def test_the_player_document_carries_no_token(self):
        """Nothing secret is served to the tab, because nothing needs to be.

        Comments are stripped first: both files *say* that they carry no token,
        and a scan tripping on that sentence would be an argument for deleting
        the sentence.
        """
        from ._runtime_doubles import code_only

        _, _, document = self.request(PATH_PLAYER, method="GET")
        _, _, script = self.request("/player.js", method="GET")

        document_code = _html_code_only(document.decode("utf-8")).lower()
        script_code = code_only(script.decode("utf-8")).lower()

        for blob, where in ((document_code, "player.html"), (script_code, "player.js")):
            for forbidden in ("authorization", "bearer", "token", "localstorage",
                              "sessionstorage", "document.cookie"):
                self.assertNotIn(forbidden, blob, where + " mentions " + forbidden)

    def test_the_player_script_never_logs(self):
        """What is playing is a fact about somebody's evening."""
        from ._runtime_doubles import code_only

        _, _, script = self.request("/player.js", method="GET")
        self.assertNotIn("console.", code_only(script.decode("utf-8")))


class EmbeddingIdentity(EndpointTestCase):
    """The player page must be able to say which page is embedding YouTube.

    Real-host validation found the opposite: three independent suppressions —
    a ``no-referrer`` response header, a ``no-referrer`` meta tag, and a
    ``referrerpolicy="no-referrer"`` on the iframe — each enough on its own to
    strip the ``Referer``. The measured outgoing header was ``null`` and YouTube
    answered every embed with **error 153**.

    Each of the three has a test here, because removing two of three would have
    left the bug in place and a suite that never noticed.
    """

    def test_the_player_response_permits_the_origin_to_be_sent(self):
        """The policy allows the origin cross-origin, and is not `no-referrer`."""
        _, headers, _ = self.request(PATH_PLAYER, method="GET")
        policy = (headers.get("Referrer-Policy") or "").strip().lower()
        self.assertEqual(policy, "strict-origin-when-cross-origin")

    def test_no_response_suppresses_the_referrer(self):
        """Not on the document, not on the script, not on the channel.

        The script matters as much as the document: it is same-origin, so it
        inherits nothing useful from a stricter header, and a suppressing policy
        served here would be one refactor away from applying to the page.
        """
        for path, method in (
            (PATH_PLAYER, "GET"),
            ("/player.js", "GET"),
            (PATH_REGISTER, "POST"),
        ):
            _, headers, _ = self.request(path, {} if method == "POST" else None, method=method)
            policy = (headers.get("Referrer-Policy") or "").strip().lower()
            self.assertNotIn("no-referrer", policy, path + " suppresses the referrer")

    def test_the_document_carries_no_suppressing_meta_tag(self):
        """A meta tag would override the header, silently and invisibly."""
        _, _, document = self.request(PATH_PLAYER, method="GET")
        markup = _html_code_only(document.decode("utf-8")).lower()
        self.assertNotIn("no-referrer", markup)
        # Asserted as the absence of the mechanism, not just of one value: a
        # `<meta name="referrer">` with any value is a second author for a
        # decision that has one place to live.
        self.assertNotIn('name="referrer"', markup)

    def test_the_iframe_does_not_suppress_the_referrer(self):
        """The half that is easiest to miss: a per-iframe policy overrides the page."""
        from ._runtime_doubles import code_only

        _, _, script = self.request("/player.js", method="GET")
        source = code_only(script.decode("utf-8"))
        self.assertNotIn("no-referrer", source)
        self.assertIn('"referrerpolicy", "strict-origin-when-cross-origin"', source)

    def test_no_launch_uses_the_noreferrer_window_feature(self):
        """`noreferrer` on an opener strips it too, from a different direction.

        Scanned across the whole player package and the player page rather than
        one file: the launch path runs through the existing ``open_url`` adapter
        today, and this is what would notice if it were ever replaced with a
        ``window.open`` carrying the feature.
        """
        from ._runtime_doubles import code_only

        package = Path(__file__).resolve().parents[1] / "cofferdam" / "workstation"
        sources = [package / "youtubeplayer" / name for name in (
            "launcher.py", "endpoint.py", "service.py", "actions.py", "channel.py"
        )]
        for path in sources:
            self.assertNotIn(
                "noreferrer", _python_code_only(path.read_text("utf-8")), str(path)
            )
        _, _, script = self.request("/player.js", method="GET")
        self.assertNotIn("noreferrer", code_only(script.decode("utf-8")))

    def test_the_origin_is_the_complete_loopback_origin_with_the_real_port(self):
        """Scheme, loopback host and the port this socket is actually bound on.

        A different port is a different origin to a browser, so an ``origin``
        parameter without one would be a lie the IFrame Player API checks.
        """
        _, _, body = self.request(PATH_REGISTER, {})
        origin = json.loads(body)["player_origin"]
        self.assertEqual(origin, "http://127.0.0.1:" + str(self.port))
        self.assertTrue(origin.startswith("http://127.0.0.1:"))
        self.assertEqual(origin, self.endpoint.player_origin())
        # And the page Opera is pointed at is served from that same origin.
        self.assertEqual(self.endpoint.player_url(), origin + PATH_PLAYER)

    def test_a_different_dynamic_port_produces_a_different_origin(self):
        """The port is read from the live socket, never remembered or assumed."""
        other = PlayerEndpoint(PlayerChannel())
        other_port = other.start()
        self.addCleanup(other.stop)
        self.assertNotEqual(other_port, self.port)
        self.assertNotEqual(other.player_origin(), self.endpoint.player_origin())
        self.assertEqual(other.player_origin(), "http://127.0.0.1:" + str(other_port))

    def test_a_client_cannot_supply_or_override_the_origin(self):
        """There is no field an origin could arrive in, and sending one changes nothing."""
        for hostile in (
            "https://evil.example.com",
            "http://127.0.0.1:1",
            "http://localhost:" + str(self.port),
            None,
        ):
            _, _, body = self.request(
                PATH_REGISTER, {"player_origin": hostile, "origin": hostile}
            )
            self.assertEqual(
                json.loads(body)["player_origin"],
                "http://127.0.0.1:" + str(self.port),
                "a client-supplied origin reached the response",
            )

    def test_the_origin_is_built_from_the_module_constant(self):
        """Structural: one builder, and it names the loopback constant."""
        from cofferdam.workstation.youtubeplayer.endpoint import build_player_origin

        self.assertEqual(build_player_origin(40187), "http://127.0.0.1:40187")
        self.assertEqual(build_player_origin(1), "http://127.0.0.1:1")


class RegistrationFlow(EndpointTestCase):
    def test_a_player_registers_and_is_then_connected(self):
        status, _, body = self.request(PATH_REGISTER, {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("instance_id", payload)
        self.assertTrue(self.channel.connected())

        status, _, _ = self.request(
            PATH_STATE,
            {"instance_id": payload["instance_id"], "state": {"player_state": 1}},
        )
        self.assertEqual(status, 200)

    def test_a_superseded_instance_is_told_to_stop(self):
        _, _, first = self.request(PATH_REGISTER, {})
        first_id = json.loads(first)["instance_id"]
        self.request(PATH_REGISTER, {})

        status, _, _ = self.request(
            PATH_STATE, {"instance_id": first_id, "state": {"player_state": 1}}
        )
        self.assertEqual(status, 409)

    def test_an_unknown_instance_cannot_post_state(self):
        self.request(PATH_REGISTER, {})
        status, _, _ = self.request(
            PATH_STATE, {"instance_id": "guessed", "state": {"player_state": 1}}
        )
        self.assertEqual(status, 409)

    def test_the_command_poll_is_bounded(self):
        """A parked connection is released rather than held open forever."""
        _, _, body = self.request(PATH_REGISTER, {})
        instance = json.loads(body)["instance_id"]

        import cofferdam.workstation.youtubeplayer.endpoint as module

        original = module.POLL_WAIT_SECONDS
        module.POLL_WAIT_SECONDS = 0.2
        self.addCleanup(setattr, module, "POLL_WAIT_SECONDS", original)

        status, _, body = self.request(PATH_COMMANDS, {"instance_id": instance, "after": 0})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["commands"], [])

    def test_a_command_is_delivered_to_the_registered_player(self):
        _, _, body = self.request(PATH_REGISTER, {})
        instance = json.loads(body)["instance_id"]
        self.channel.send("load_video", video_id="dQw4w9WgXcQ", autoplay=True)

        status, _, body = self.request(PATH_COMMANDS, {"instance_id": instance, "after": 0})
        self.assertEqual(status, 200)
        commands = json.loads(body)["commands"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["command"], "load_video")

    def test_the_endpoint_binds_lazily_and_stops_cleanly(self):
        """A host where nobody opens a player never binds a socket."""
        endpoint = PlayerEndpoint(PlayerChannel())
        self.assertFalse(endpoint.running)
        self.assertIsNone(endpoint.port)
        url = endpoint.player_url()
        self.assertTrue(endpoint.running)
        self.assertIn("127.0.0.1", url)
        endpoint.stop()
        self.assertFalse(endpoint.running)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
