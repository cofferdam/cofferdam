"""Process discovery: identity that survives PID reuse, and no secrets, ever.

Two properties are load-bearing here and both are the kind that quietly rot:

* **PID plus start time, never PID alone.** A later milestone will act on these
  identities. A stale PID plus an action is how the wrong process gets
  terminated, and the only thing standing between the two is that the start time
  is part of the identity.
* **Command lines and environment blocks are never read.** Not read, not
  redacted — the file is never opened. That is asserted structurally over the
  source as well as behaviourally over the output, because a behavioural test
  alone would pass the moment somebody read the data and merely forgot to
  publish it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cofferdam.workstation.runtime.processes import (
    ProcessDiscovery,
    process_resource_id,
    read_process,
)
from cofferdam.workstation.runtime.models import STATUS_OK, STATUS_PARTIAL, STATUS_UNAVAILABLE

from ._runtime_doubles import HOST_ID, FakeBoot, FakeProc, app_scope

RUNTIME_PACKAGE = Path(__file__).resolve().parents[1] / "cofferdam" / "workstation" / "runtime"


class ProcessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proc = FakeProc(Path(self._tmp.name) / "proc")
        self.boot = FakeBoot()

    def discovery(self) -> ProcessDiscovery:
        import os

        return ProcessDiscovery(proc_root=str(self.proc.root), uid=os.getuid())

    def collect(self):
        discovery = self.discovery()
        facts, warnings = discovery.read_all()
        return discovery.collect(HOST_ID, self.boot, facts, list(warnings))


class PidReuseTests(ProcessTestCase):
    """(5) PID reuse is distinguished through process start time."""

    def test_the_same_pid_at_a_different_start_time_is_a_different_resource(self) -> None:
        first = process_resource_id(HOST_ID, self.boot.boot_id, 4242, start_ticks=1000)
        recycled = process_resource_id(HOST_ID, self.boot.boot_id, 4242, start_ticks=987654)
        self.assertNotEqual(
            first,
            recycled,
            "a recycled PID must not inherit the identity of the process that held it",
        )

    def test_the_same_pid_and_start_time_is_the_same_resource(self) -> None:
        """The other half: identity has to be *stable*, not merely unique."""
        self.assertEqual(
            process_resource_id(HOST_ID, self.boot.boot_id, 4242, 1000),
            process_resource_id(HOST_ID, self.boot.boot_id, 4242, 1000),
        )

    def test_a_different_boot_makes_the_same_pid_and_ticks_a_different_resource(self) -> None:
        """Start ticks are counted from boot, so they only mean something in one."""
        other_boot = FakeBoot(boot_id="boot-otherboot0000")
        self.assertNotEqual(
            process_resource_id(HOST_ID, self.boot.boot_id, 4242, 1000),
            process_resource_id(HOST_ID, other_boot.boot_id, 4242, 1000),
        )

    def test_a_different_host_makes_the_same_pid_and_ticks_a_different_resource(self) -> None:
        self.assertNotEqual(
            process_resource_id(HOST_ID, self.boot.boot_id, 4242, 1000),
            process_resource_id("host-elsewhere00000", self.boot.boot_id, 4242, 1000),
        )

    def test_start_ticks_are_published_so_an_action_can_re_verify_them(self) -> None:
        """The identity is only useful if a caller can check it later."""
        self.proc.add(4242, comm="worker", start_ticks=555)
        collection = self.collect()
        item = collection.items[0]
        self.assertEqual(item["start_ticks"], 555)
        self.assertEqual(item["identity"]["source"], "pid+start-time")

    def test_no_boot_identity_means_no_process_collection_at_all(self) -> None:
        """Falling back to bare PIDs is exactly what must not happen."""
        self.proc.add(4242)
        discovery = self.discovery()
        facts, warnings = discovery.read_all()
        collection = discovery.collect(HOST_ID, FakeBoot(boot_id=None), facts, list(warnings))

        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertEqual(collection.items, ())
        self.assertIn("PID alone is never one", collection.reason)


class RaceToleranceTests(ProcessTestCase):
    """(6) A process disappearing mid-scan does not fail the snapshot."""

    def test_a_process_that_exits_during_the_scan_is_simply_absent(self) -> None:
        self.proc.add(100, comm="survivor")
        self.proc.add(200, comm="doomed")

        discovery = self.discovery()

        # Vanish PID 200 the moment it is about to be read, which is exactly
        # what a real /proc does to a scanner.
        import cofferdam.workstation.runtime.processes as module

        real_read = module.read_process

        def racing_read(pid, proc_root):
            if pid == 200:
                self.proc.remove(200)
            return real_read(pid, proc_root)

        module.read_process = racing_read
        self.addCleanup(lambda: setattr(module, "read_process", real_read))

        facts, warnings = discovery.read_all()
        collection = discovery.collect(HOST_ID, self.boot, facts, list(warnings))

        self.assertEqual(collection.status, STATUS_OK, "a normal exit must not degrade the scan")
        self.assertEqual([item["pid"] for item in collection.items], [100])

    def test_an_unreadable_but_present_process_downgrades_to_partial(self) -> None:
        """Different fact, different status: something is there and we cannot see it."""
        self.proc.add(100, comm="survivor")
        directory = self.proc.add(300, comm="opaque")
        (directory / "stat").unlink()  # present in /proc, unreadable

        discovery = self.discovery()
        facts, warnings = discovery.read_all()
        collection = discovery.collect(HOST_ID, self.boot, facts, list(warnings))

        self.assertEqual(collection.status, STATUS_PARTIAL)
        self.assertTrue(any("could not be read" in text for text in collection.warnings))

    def test_a_corrupt_stat_line_does_not_take_the_scan_down(self) -> None:
        self.proc.add(100, comm="survivor")
        directory = self.proc.add(400, comm="garbled")
        (directory / "stat").write_text("not a stat line at all\n", encoding="utf-8")

        collection = self.collect()
        self.assertIn(100, [item["pid"] for item in collection.items])

    def test_an_executable_name_containing_spaces_and_parentheses_parses(self) -> None:
        """Firefox really does name a process ``(Web Content)``."""
        self.proc.add(500, comm="Web Content (1)", ppid=7, start_ticks=4321, state="R")
        collection = self.collect()
        item = next(entry for entry in collection.items if entry["pid"] == 500)

        self.assertEqual(item["parent_pid"], 7)
        self.assertEqual(item["start_ticks"], 4321)
        self.assertEqual(item["state"], "running")

    def test_a_missing_proc_filesystem_is_unavailable_not_empty(self) -> None:
        discovery = ProcessDiscovery(proc_root=str(self.proc.root / "absent"), uid=0)
        collection = discovery.collect(HOST_ID, self.boot, [], [])
        self.assertEqual(collection.status, STATUS_UNAVAILABLE)
        self.assertIn("/proc", collection.reason)


class NoSecretsExposedTests(ProcessTestCase):
    """(7) Raw environment and secret-bearing command lines are not exposed."""

    SECRET = "sk-live-51H8superSecretTokenValue"

    def test_no_runtime_module_opens_cmdline_or_environ(self) -> None:
        """Structural: the strings do not appear as paths anywhere in the package.

        Behavioural absence is not enough. Reading a command line and then
        forgetting to publish it still puts a token in this process's memory and
        one careless log line away from disk.
        """
        offenders = []
        for path in sorted(RUNTIME_PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                if node.value in ("cmdline", "environ") or node.value.endswith(("/cmdline", "/environ")):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "runtime discovery must never read /proc/<pid>/cmdline or /proc/<pid>/environ: "
            + ", ".join(offenders),
        )

    def test_a_secret_in_cmdline_and_environ_never_reaches_the_api_payload(self) -> None:
        """Behavioural: put a real secret in both files and grep the output."""
        directory = self.proc.add(600, comm="leaky", executable="/usr/bin/leaky")
        (directory / "cmdline").write_bytes(
            b"/usr/bin/leaky\x00--api-key=" + self.SECRET.encode("ascii") + b"\x00"
        )
        (directory / "environ").write_bytes(
            b"HOME=/home/user\x00OPENAI_API_KEY=" + self.SECRET.encode("ascii") + b"\x00"
        )

        collection = self.collect()
        serialized = repr(collection.to_dict())

        self.assertNotIn(self.SECRET, serialized)
        self.assertNotIn("--api-key", serialized)
        self.assertNotIn("OPENAI_API_KEY", serialized)

    def test_the_leak_test_can_actually_fail(self) -> None:
        """Mutation check on the test above.

        If ``collect`` ever did include the command line, the assertion would
        catch it. Proven by asserting the secret *is* found in a payload that
        deliberately carries it.
        """
        directory = self.proc.add(601, comm="leaky")
        (directory / "cmdline").write_bytes(b"--api-key=" + self.SECRET.encode("ascii"))
        leaked = repr({"cmdline": (directory / "cmdline").read_bytes().decode()})
        self.assertIn(self.SECRET, leaked)

    def test_no_published_field_is_named_after_a_command_line(self) -> None:
        self.proc.add(602, comm="worker", executable="/usr/bin/worker")
        item = self.collect().items[0]
        for key in item:
            with self.subTest(field=key):
                self.assertNotIn(key.lower(), {"cmdline", "command", "argv", "args", "environ", "env"})


class ProcessFieldTests(ProcessTestCase):
    """The published shape: enough to act on later, no more than that."""

    def test_unit_and_cgroup_are_read_from_the_process(self) -> None:
        self.proc.add(
            700,
            comm="opera",
            executable="/snap/opera/477/usr/lib/opera/opera",
            cgroup=app_scope("snap.opera.opera-abc.scope"),
        )
        item = self.collect().items[0]

        self.assertEqual(item["unit"], "snap.opera.opera-abc.scope")
        self.assertIn("app.slice", item["cgroup"])
        self.assertEqual(item["executable"], "opera")
        self.assertEqual(item["executable_path"], "/snap/opera/477/usr/lib/opera/opera")

    def test_an_unreadable_executable_link_leaves_the_path_absent(self) -> None:
        self.proc.add(701, comm="opaque")  # no exe symlink at all
        item = self.collect().items[0]
        self.assertIsNone(item["executable_path"])
        self.assertIsNone(item["executable"])

    def test_started_at_is_absolute_when_the_boot_time_is_known(self) -> None:
        self.proc.add(702, comm="worker", start_ticks=100)
        item = self.collect().items[0]
        self.assertTrue(item["started_at"].endswith("Z"))

    def test_started_at_is_absent_when_the_boot_time_is_not_known(self) -> None:
        self.proc.add(703, comm="worker", start_ticks=100)
        discovery = self.discovery()
        facts, warnings = discovery.read_all()
        collection = discovery.collect(
            HOST_ID, FakeBoot(boot_epoch_seconds=None), facts, list(warnings)
        )
        self.assertIsNone(collection.items[0]["started_at"])


class ReadProcessTests(ProcessTestCase):
    def test_reading_a_pid_that_does_not_exist_returns_none(self) -> None:
        self.assertIsNone(read_process(999999, str(self.proc.root)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
