LINE AUTO SYNC - ROOTLESS TEST CANDIDATE RC1

STATUS
PRIVATE / TEST CANDIDATE
Not verified on a second iPad.
Do not publish or redistribute until privacy and code-rights review.

SCOPE
Normal LINE only.
OpenChat is NOT included.
No automatic background launch.
Oversized messages stop processing safely instead of advancing
the acknowledged message cursor.

REQUIREMENTS
- Rootless jailbreak with /var/jb
- Python 3.9 or compatible Python 3
- Readable local LINE SQLite database
- Your own authorized HTTPS destination and token
- Permission to process the selected conversation data

INSTALLATION TEST WORKFLOW
1. Install package on an isolated TEST iPad only.
2. Run as the mobile user:
   line-auto-sync init
3. Run:
   line-auto-sync configure
4. Enter endpoint, source_id, SQLite DB absolute path and token.
   The token is not echoed; config stays disabled after configure.
5. Run:
   line-auto-sync doctor
6. When ready:
   line-auto-sync enable
7. Run manually:
   line-auto-sync run
8. Stop new manual runs:
   line-auto-sync disable

IMPORTANT LIMITATIONS
- SQLite schema has NOT been verified on another installation.
- Name-enrichment behavior is not fully integration-tested.
- Initial baseline intentionally starts at the current highest ID.
- Oversized messages block further processing until resolved.
- No automatic launch daemon is installed by this package.
- A successful HTTP acknowledgment is not proof of end-to-end
  Google Calendar or task creation.
- This package contains no real user configuration files.
- The installer does not delete runtime state on uninstall.
- The package does not include a Python interpreter.
- Some rooted environments use relocated rootless prefixes;
  this launcher targets the standard /var/jb location.

DATA AND PRIVACY
Configuration and state:
  /var/mobile/Library/Application Support/LineAutoSync/
Never include real messages, endpoint, tokens or account IDs
when sharing diagnostics.
Do not run against other people's chats without authorization.
