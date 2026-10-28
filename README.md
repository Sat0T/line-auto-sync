# LINE Auto Sync — experimental code review

**Status: source review only. Not production-ready; no validated updated `.deb` release.**

Unofficial Python prototype for reading **normal LINE** messages from a local SQLite database on a **rootless jailbroken iOS device** and sending selected messages to a user-provided HTTPS endpoint. Not affiliated with LINE or LY Corporation.

## Repository layout

- [normal-only-rc1/](normal-only-rc1/) — experimental Python source and manual CLI
- [tests/test_offline.py](tests/test_offline.py) — synthetic SQLite, CLI and fail-closed tests (no real messages or network)
- [docs/WORK_HANDOFF.md](docs/WORK_HANDOFF.md) — handoff for continuing development in ChatGPT Work
- [normal-only-rc1/README_TESTING.txt](normal-only-rc1/README_TESTING.txt) — historical RC1 test package instructions; **read caveats below first**

## CLI commands

`init`, `configure`, `doctor`, `enable`, `disable`, `run`.

The config starts disabled. The revised `configure` records `line_db`, `source_id`, `timestamp_unit` and `group_db` for the name resolver. The revised `doctor` checks the minimal message-table schema and a sample Unix-millisecond timestamp before allowing `enable`. These checks are **not** a proof of compatibility with a real LINE installation.

The `normal-only-rc1` folder name is retained for compatibility with earlier documentation. These source edits are **not** the same bytes as the previously generated `line-auto-sync_0.1.0-rc1_PRIVATE.deb`. A new package must be rebuilt and verified before device testing.

## Local offline tests

```sh
python -m unittest discover -s tests -v
```

The tests use a deliberately synthetic SQLite schema and `example.invalid`; they do not contact any external service. GitHub Actions is configured to run them on Python 3.9 and 3.12. CI passing means **only** these offline tests passed.

## Important limitations and risks

- **Rootless iOS installation is not yet validated on another device.** Python installation, package metadata and operating-system permissions need testing.
- Normal LINE only. **OpenChat is excluded**.
- No background service. **Manual execution only**.
- Oversized messages intentionally raise an error without sending or advancing the acknowledged cursor; recovery is not implemented.
- LINE DB schema, name lookups, timestamps, database locking, receiver ACK semantics and actual delivery to GAS/Calendar/tasks are not verified end-to-end.
- Transmitting conversation content to an external endpoint presents privacy and authorization concerns. Test only with data you have permission to process; never upload real messages or credentials in bug reports.
- There is no official endorsement or guarantee of compatibility with LINE's terms or ongoing database format.
- Code redistribution/licensing and full secrets review have **not** been signed off. No open-source license has been granted by this repository.

## Review wanted

Please focus on: schema assumptions; SQLite read-only behavior; cursor/ACK and duplicate/loss risks; settings and secrets; launcher and rootless package metadata; privacy; third-party code licensing.

To report an issue, include only sanitized steps, Python version, OS/jailbreak family and synthetic repro cases. **Never attach LINE databases, message logs, tokens, endpoints or user identifiers.**

This repo is offered to solicit technical feedback, **not** as a recommended installation.
