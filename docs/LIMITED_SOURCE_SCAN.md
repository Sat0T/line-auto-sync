# Limited public source content scan — 2026-10-28

Target: PR #5 head commit c9ad70b9adbc02f28fc434543579974e3ceb803e
(the RC3 draft), not the private iPad or GAS. Only the 29 tracked text
files in that one Git tree were checked with known-pattern searches.

Patterns searched: obvious deployed GAS /macros/s/.../exec URLs, email address
shapes, common GitHub personal-access tokens, Google API key shapes, private key
PEM headers, OpenAI API keys, and personal Windows user-home paths.

Result: no matching obvious credentials or personal Windows-home paths in
28 files; one email-shaped match in tests/test_delivery.py is the intentionally
synthetic string secret@example.invalid inside a URL-credential rejection
test. It is not a real email address. No real-looking GAS deployment URL,
GitHub PAT, Google API key, private-key marker or OpenAI key was detected.

IMPORTANT: This does NOT certify secrets absent. The check did not inspect
all Git history commits, commit author metadata, release/artifact archives,
environment configuration, untracked files, old backups, iPad data or any
external GAS/Calendar/Gmail systems. Regexes cannot reliably detect unknown
token formats or copied conversation text. No rights or privacy attestation.

Next independent release checks:
- Scan all Git history and GitHub release assets with an established scanner,
  review findings privately, and rotate any exposed secret rather than merely
  deleting code.
- Confirm code provenance, licenses, supported recipient permissions and
  consent. A public repository is not permission to redistribute third-party
  code or other people's communications.
- Inspect package archive and build logs for private messages, config,
  endpoint identifiers and names. Work with synthetic data only.
- Repeat these reviews at a pinned release commit and document sign-offs.

See SECURITY_AND_RIGHTS.md and BETA_RELEASE_CHECKLIST.md. No production
configuration or private data was accessed by this scan.
