# Security, privacy and redistribution review

This is an audit inventory, not a security or legal sign-off.

| Material | Origin/current handling | Release gate |
|---|---|---|
| Twelve Python modules | Existing repo lineage plus this change | Owner must confirm provenance/redistribution rights; no license is invented |
| Python stdlib / sqlite3 / TLS | Device runtime, not redistributed by .deb | Verify actual provider package and its notices on target |
| Theos / GUI frameworks | Not bundled or executed by current Python builder | Review licenses if future GUI introduces them |
| LINE app/DB/messages | Not redistributed; authorized read-only source only | Consent, service terms, supported versions and retention review remain |
| Config / endpoint / token / IDs / queues / logs | Created locally, excluded by payload allowlist | Review permissions, backup handling and operator consent |
| Cloud GAS / AI / calendar | External, excluded from .deb | Own endpoint, authentication, idempotency and account authorization |

No open-source redistribution license has yet been granted by this repository.
Public source visibility is not itself a redistribution permission. The current
owner must establish the applicable rights before a third-party beta binary is
shared. This pass does not certify LINE terms compliance or another party's consent.

The private builder has a strict module allowlist and checks known secret patterns,
Python syntax, byte-level payload equality and package metadata. These are limited
mechanical checks, not an exhaustive source/history/privacy audit. New code keeps
credentials out of argv, prevents credential-bearing URLs, bounds ACK reads,
suppresses server/exception text, binds the queue to source/database/destination,
and uses private runtime files plus read-only source access.

Still open: full source and Git-history review including commit metadata, entropy-
based/unknown secret patterns, filesystem ancestor/ownership threat model, private
backup encryption, receiver-side authorization/dedupe, independent-device access
and storage limits, native dependencies, rights and notices. Do not claim an audit
PASS while these are unresolved. If a credential is discovered, do not copy it to
an Issue or rely only on deleting a file/history; resolve it privately with its owner.

No binaries, private runtime captures, owner cloud settings or real messages are
published by this PR. Only synthetic fixtures and generic documentation belong in
review or CI. Never copy the owner's personal workspace wholesale into a release.
