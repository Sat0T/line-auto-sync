# Invited beta testing guide — RC3 candidate, NOT an installation recommendation

An independent device and rights/privacy review are still required. Do not install
this candidate on the owner's active iPad. Do not use actual message content or an
actual GAS endpoint in automated tests. OpenChat, background startup, attachment
processing, oversized-message recovery and GUI are not implemented here.

## Offline source review

```
python -m unittest discover -s tests -v
python tools/build_private_beta.py
```

The compatibility command `tools/build_private_rc2.py` now invokes the current RC3
builder; it does not reproduce historical RC2 bytes. Version: `0.1.2~rc3`; output
`dist/line-auto-sync_0.1.2-rc3_PRIVATE.deb` plus `RC3_BUILD_REPORT.json`. Tests and
build verification do not install packages, contact a receiver, or validate iOS.
Archive metadata/time/order/newlines are fixed. Identical input in the same build
environment is tested byte-for-byte; compare hashes across environments rather
than assuming different compression/runtime versions always match.

## After explicit independent-device approval

Use an isolated rootless device with compatible Python/TLS/sqlite3 already present.
Have the package manager install only the reviewed private candidate. Run the CLI
as mobile, never as a root synchronization service:

```
line-auto-sync init
line-auto-sync configure
line-auto-sync doctor
line-auto-sync enable
line-auto-sync run
line-auto-sync status
line-auto-sync disable
```

`configure` takes a user-supplied HTTPS destination, unique stable source_id, an
authorized absolute database path, and a hidden token. It does not search private
app containers or transmit data. `doctor` performs read-only minimal schema/time
checks; success does not establish name mapping or receiver compatibility.
`enable` permits manual runs; it does not install a background job.

Use a disposable synthetic database and a receiver conforming to
[RECEIVER_PROTOCOL.md](RECEIVER_PROTOCOL.md) first. Verify default history skip,
one new text message, failure/timeout, retry with the same batch ID, and duplicates
after acceptance-before-local-commit. Verify no send while disabled, no state
deletion on removal, and no concurrent legacy worker. Follow
[PACKAGE_PLAN.md](PACKAGE_PLAN.md) for upgrades, backup and downgrade restrictions.

Do not bypass certificate errors or reset state to force success. Inadequate schema,
unknown database generation, corruption, changed destination, or an oversized head
must stop rather than silently skip data. Full queues need operator review; automatic
retention limits/pruning are not implemented. Collected text remains on device.

## Sanitized report

Report source commit, candidate hash, Python/iOS/jailbreak family, test case,
expected/observed result, and redacted status counts. Mark each case PASS / FAIL /
NOT TESTED. Never submit tokens, endpoint URLs, LINE DBs, chat/member IDs, real
names, conversation text, private paths or configuration/queue/cache backups.
Package installation, real schema and real receiver behavior remain NOT TESTED
until an identified tester actually performs them with explicit consent.
