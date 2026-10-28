# LINE Auto Sync — experimental code review

**Status: source review only. Not production-ready; no validated updated `.deb` release.**

Unofficial Python prototype for reading **normal LINE** messages from a local SQLite database on a **rootless jailbroken iOS device** and sending selected messages to a user-provided HTTPS endpoint. Not affiliated with LINE or LY Corporation.

## Repository layout

- [normal-only-rc1/](normal-only-rc1/) — experimental Python source and manual CLI
- [tests/test_offline.py](tests/test_offline.py) — synthetic SQLite, CLI and fail-closed tests (no real messages or network)
- [tests/test_delivery.py](tests/test_delivery.py) — durable queue, crash, timeout, concurrent sender and mock receiver regressions
- [docs/BETA_TESTER_GUIDE.md](docs/BETA_TESTER_GUIDE.md) — current private RC3 testing instructions
- [docs/RECEIVER_PROTOCOL.md](docs/RECEIVER_PROTOCOL.md) — legacy OK vs HMAC v2; required receiver idempotency
- [docs/PACKAGE_PLAN.md](docs/PACKAGE_PLAN.md) — component split, rootless layout, install/remove/backup/recovery gates
- [docs/SECURITY_AND_RIGHTS.md](docs/SECURITY_AND_RIGHTS.md) — privacy and redistribution blockers
- [docs/GUI_OPTIONS.md](docs/GUI_OPTIONS.md) — phase-2 GUI comparison, not an implemented GUI
- [docs/WORK_HANDOFF.md](docs/WORK_HANDOFF.md) — handoff for continuing development in ChatGPT Work
- [docs/RC3_CHAT_REVIEW.md](docs/RC3_CHAT_REVIEW.md) — limited code review and confirmed follow-up fixes
- [docs/BETA_RELEASE_CHECKLIST.md](docs/BETA_RELEASE_CHECKLIST.md) — mandatory private-beta acceptance gates
- [docs/GAS_RECEIVER_DESIGN.md](docs/GAS_RECEIVER_DESIGN.md) — separate backend receiver and durable dedupe design
- [docs/LIMITED_SOURCE_SCAN.md](docs/LIMITED_SOURCE_SCAN.md) — scoped secret-pattern source scan and limitations
- [normal-only-rc1/README_TESTING.txt](normal-only-rc1/README_TESTING.txt) — historical RC1 test package instructions; **read caveats below first**

## CLI commands

`init`, `configure`, `doctor`, `enable`, `disable`, `run`, `status`.

The config starts disabled. The revised `configure` records `line_db`, `source_id`, `timestamp_unit` and `group_db` for the name resolver. The revised `doctor` checks the minimal message-table schema and a sample Unix-millisecond timestamp before allowing `enable`. These checks are **not** a proof of compatibility with a real LINE installation.

The `normal-only-rc1` folder name is retained for compatibility. Current source builds private RC3 (`0.1.2~rc3`), not historical RC1/RC2 bytes. `python tools/build_private_beta.py` runs offline tests and checks an allowlisted archive. No binary is uploaded by CI or approved for distribution.

## RC3 delivery changes

Text rows and the collected cursor now commit together to a private SQLite queue.
An exact request body commits before HTTP and remains immutable across failures,
new arrivals, and crashes. OK commits the acknowledged IDs and send clock together.
The hourly gate applies to new batches; uncertain requests retry with backoff.
Legacy CSV/cursors are imported once and are not subsequently overwritten.
`status` exposes only counts/cursors, without reading LINE or printing credentials.

Plain OK does **not** prove receiver-side idempotency or AI completion. The receiver
must durably dedupe stable batch/message IDs. Acceptance-before-local-commit can
replay the same request; this is at-least-once, not guaranteed exactly-once.
Do not run old and new workers against the same runtime state, or downgrade blindly.

## Local offline tests

```sh
python -m unittest discover -s tests -v
```

The tests use a deliberately synthetic SQLite schema and `example.invalid`; they do not contact any external service. GitHub Actions is configured to run them on Python 3.9 and 3.12. CI passing means **only** these offline tests passed.

## Important limitations and risks

- **Rootless iOS installation is not yet validated on another device.** Python installation, package metadata and operating-system permissions need testing.
- Normal LINE only. **OpenChat is excluded**.
- No background service. **Manual execution only**.
- A valid prefix before an oversized message may be delivered. An oversized head and subsequent rows remain queued; recovery/fragmentation is not implemented.
- LINE DB schema, name lookups, timestamps, database locking, receiver ACK semantics and actual delivery to GAS/Calendar/tasks are not verified end-to-end.
- Transmitting conversation content to an external endpoint presents privacy and authorization concerns. Test only with data you have permission to process; never upload real messages or credentials in bug reports.
- There is no official endorsement or guarantee of compatibility with LINE's terms or ongoing database format.
- Code redistribution/licensing and full secrets review have **not** been signed off. No open-source license has been granted by this repository.

## Review wanted

Please focus on: schema assumptions; SQLite read-only behavior; cursor/ACK and duplicate/loss risks; settings and secrets; launcher and rootless package metadata; privacy; third-party code licensing.

To report an issue, include only sanitized steps, Python version, OS/jailbreak family and synthetic repro cases. **Never attach LINE databases, message logs, tokens, endpoints or user identifiers.**

This repo is offered to solicit technical feedback, **not** as a recommended installation.
