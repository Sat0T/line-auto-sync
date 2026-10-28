# Rootless package inventory and lifecycle

Scope: isolated normal-LINE beta source; **not the owner's running iPad pipeline**.
The live personal system also has OpenChat/cloud/calendar/task features. Those
features are not claimed by this package, and no personal environment is imported.

## Inventory and split

| Component | Current role | Package decision |
|---|---|---|
| cli/runtime_paths/setup_normal | consent-driven config, doctor, manual on/off/status | included; starts disabled |
| sync/delivery_state/checkpoint | read-only source snapshot, transactional rows/cursors, legacy import | included |
| sender/normal_hold | legacy POST, immutable retry, valid prefix before oversized head | included; no fragmentation |
| legacy_names/resolver/presentation | display names, unknown-ID fallback, B/C1 encoding | included |
| transport | validation utilities and separately opt-in HMAC transport | included as existing dependency; not the default wire protocol |
| launchd | no service in this beta tree | not installed or loaded; personal local.linesync untouched |
| GAS/Gmail/AI/calendar/task state | externally supplied processing | excluded from .deb; separate backend contract |
| runtime config/DB pointers/IDs/token/endpoint/logs/cache/queue/backups | user-owned private data | never in package or repository |

The twelve source modules, one launcher and one generic README form the explicit
payload allowlist. Fixtures/diagnostics/build reports and other archives are not
packaged. `dist/` is ignored; CI does not upload binaries or private artifacts.

## Proposed/implemented layout

```
/var/jb/usr/bin/line-auto-sync                root-owned 0755 launcher
/var/jb/usr/libexec/line-auto-sync/*.py        root-owned 0644 source
/var/jb/usr/libexec/line-auto-sync/README_PRIVATE.txt
<mobile Application Support>/LineAutoSync/   created by mobile, not dpkg
  config/config.json                        0600; token + endpoint stay here
  state/                                    0700
    dbpath                                  private source pointer
    normal-delivery.sqlite[-wal/-shm]        queue/cursors/frozen retries
    named-cache.sqlite[-wal/-shm]            name cache
```

Rootless `/var/jb` and `iphoneos-arm64` follow [Theos rootless documentation](https://theos.dev/docs/rootless).
Code is not injected into LINE. No LINE database, binary, or private framework is
redistributed. The user's chosen LINE database is opened in read-only/query-only
mode. RootHide/relocated-prefix variants are not validated.

Python 3.9+ with sqlite3, SSL/TLS and the standard library is an external runtime.
The launcher preflights version/imports at the two currently supported rootless
paths. `firmware >=15.0` is declared using the [documented package convention](https://theos.dev/docs/packaging).
Python provider package IDs/versions and ownership/access on a real target remain
unverified; do not invent a universal Python Depends entry or claim Sileo will
install the runtime automatically. No Python or SQLite binary is bundled.

## Install, upgrade, remove, recover

1. **Before device testing:** resolve rights/privacy/receiver blockers and identify
   an authorized isolated test device. Check Python provider, package architecture,
   DB schema/access and package identity. Obtain a private, consistent backup.
2. **Install:** package manager installs code only. No postinst, networking,
   automatic data discovery, root-running sync, or launchd registration. As mobile,
   init/configure/doctor; inspect settings before enable. First run skips history.
3. **Upgrade to RC3:** stop manual invocations and wait for any in-flight attempt.
   Back up matched config + legacy state + caches. Adoption imports pending CSV and
   cursor together. Do not run RC2 and RC3 against the same state. Ambiguity fails
   closed; never reset cursors just to clear an error.
4. **Disable/remove:** disable; wait for in-flight work; inspect status and preserve
   all pending state. Remove only this package with the package manager. Runtime
   data is outside the package and is retained, including on package removal. No
   script stops another daemon or recursively deletes user data. A separate explicit
   private-data deletion workflow is future work, not an uninstall side effect.
5. **Recovery:** restore a matched code/config/state snapshot. Use SQLite's backup
   API after quiescing writers, or a complete coherent DB/WAL snapshot; copying only
   an active .sqlite file is insufficient. Preserve source_id, pending rows and frozen
   requests. Receiver dedupe must tolerate replay. Do not restore the LINE DB itself.
6. **Downgrade:** older senders ignore the new queue and see stale legacy cursors.
   Do not downgrade in place or regenerate text cursors from maxima. An actual
   package-manager downgrade blocker/migration is not implemented; this remains a
   test/release blocker. No unattended installer should perform it.

State/config are plaintext private files protected by directory/file permissions,
not an encrypted credential vault. Permission and backup-encryption policy require
independent-device validation; never attach a runtime directory to an Issue.

## Required order and acceptance gates

1. Offline reliability + mock receiver tests (this change).
2. Source/history/artifact secret review and author/provenance/permission sign-off.
3. Confirm real receiver idempotency separately; no deployment into the personal GAS.
4. Isolated rootless Python/dependency/install/remove/upgrade/permission tests.
5. Schema-only compatibility checks, then explicitly authorized synthetic delivery.
6. Only after those gates: invited technical beta handoff with known limits.
7. General publication/repository release is a separate approval stage.
8. Native GUI follows beta safety; no GUI code was added in this pass.
