# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and this project does not yet have a stable
release.

## [Unreleased]

### Added

- Foundation docs (PR0): `LICENSE`, `README.md`, `PROVENANCE.md`, `SAFETY-AND-RISK.md`,
  `SECURITY.md`, `TESTING.md`, `DESIGN.md`, `THREAT-MODEL.md`, `AUTHORS.md`, `.gitignore`, and a
  license-scan CI check. No product code yet.
- CLI skeleton (PR1): the `cofferdam` package with `--version`, help output, an empty command
  dispatch registry, the exit-code convention, and the stdout/stderr split. Standard-library only;
  no guard, executor, approval, audit, provider, or network behaviour yet.
