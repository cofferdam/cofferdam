"""Report the licenses of every installed distribution; fail on copyleft.

Run after installing the extras you care about::

    python -m pip install -e ".[workstation,dev]"
    python .github/scripts/license_report.py            # table + exit code
    python .github/scripts/license_report.py --json out.json

License text is read from installed package metadata (``License-Expression``,
then ``License``, then the ``License ::`` classifiers) — the same source pip
and PyPI use. Anomalies are flagged for a human rather than guessed at: this
script is a tripwire, not an authority. Verify anything it flags against the
project's own repository before accepting it.

Policy (see CONTRIBUTING.md → Dependency policy): permissive licenses are fine;
GPL/AGPL/SSPL/proprietary must never be a distributed dependency; MPL-2.0 is
tolerated as a non-vendored dependency (file-level copyleft) but is reported.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import sys

# Substrings that mark an acceptable permissive license.
PERMISSIVE = (
    "MIT",
    "BSD",
    "APACHE",
    "ISC",
    "PSF",
    "PYTHON SOFTWARE FOUNDATION",
    "UNLICENSE",
    "ZLIB",
)

# Substrings that must never appear in a distributed dependency.
FORBIDDEN = ("AGPL", "SSPL", "PROPRIETARY", "COMMONS CLAUSE", "BUSL", "ELASTIC LICENSE")

# Tolerated, but always reported so the choice stays deliberate.
REPORT_ONLY = ("MPL", "LGPL")


def license_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    value = meta.get("License-Expression") or meta.get("License") or ""
    # Some projects paste the whole license text into the License field.
    if not value or len(value) > 60 or "\n" in value:
        classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        derived = "; ".join(c.split("::")[-1].strip() for c in classifiers)
        value = derived or (value.splitlines()[0][:57] + "…" if value else "UNKNOWN")
    return value.strip()


def classify(license_text: str) -> str:
    upper = license_text.upper()
    if any(bad in upper for bad in FORBIDDEN):
        return "forbidden"
    # Plain "GPL" without an L prefix, and not part of "LGPL".
    if "GPL" in upper and "LGPL" not in upper and "AGPL" not in upper:
        return "forbidden"
    if any(flag in upper for flag in REPORT_ONLY):
        return "report"
    if any(ok in upper for ok in PERMISSIVE):
        return "permissive"
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", help="also write the inventory as JSON")
    args = parser.parse_args(argv)

    rows = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        text = license_of(dist)
        rows.append(
            {
                "name": name,
                "version": dist.version,
                "license": text,
                "classification": classify(text),
            }
        )
    rows.sort(key=lambda r: r["name"].lower())

    width = max((len(r["name"]) for r in rows), default=10) + 2
    print(f"{'package':{width}}{'version':14}{'license':32}classification")
    print("-" * (width + 60))
    for row in rows:
        print(f"{row['name']:{width}}{row['version']:14}{row['license']:32}{row['classification']}")

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump({"distributions": rows}, handle, indent=2, sort_keys=True)
        print(f"\nwrote {args.json_path}")

    forbidden = [r for r in rows if r["classification"] == "forbidden"]
    unknown = [r for r in rows if r["classification"] == "unknown"]
    reported = [r for r in rows if r["classification"] == "report"]

    if reported:
        print("\nWeak-copyleft dependencies (allowed only while NOT vendored/bundled):")
        for row in reported:
            print(f"  {row['name']} {row['version']} — {row['license']}")

    if unknown:
        print("\nUnrecognised licenses — verify manually against the upstream project:")
        for row in unknown:
            print(f"  {row['name']} {row['version']} — {row['license']}")

    if forbidden:
        print("\nFORBIDDEN licenses found — these must not be distributed dependencies:")
        for row in forbidden:
            print(f"  {row['name']} {row['version']} — {row['license']}")
        return 1

    if unknown:
        return 1

    print("\nAll installed distributions carry recognised, acceptable licenses.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
