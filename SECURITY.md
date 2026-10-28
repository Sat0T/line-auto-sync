# Security and privacy reporting

This is an **unofficial, experimental source-review project** handling potentially sensitive conversation data. It has **not** passed a comprehensive security audit.

Please **do not** open public Issues or Pull Requests containing private conversation records, real LINE SQLite databases, credentials, tokens, private endpoints, account IDs or screenshots with identifying information.

For security vulnerabilities, use GitHub's repository security reporting/private vulnerability reporting feature **if enabled**, rather than a public issue. If private reporting is unavailable, contact the repository maintainer through an existing private channel **before** sharing sensitive reproduction material.

If you suspect a credential was exposed, revoke or rotate it immediately; deleting a GitHub commit or hiding a repository does not guarantee that previous copies have disappeared.

Until the risks listed in [release blockers](https://github.com/Sat0T/line-auto-sync/issues/3) are addressed, avoid live-data tests or distribution of installable binaries.

The code reads third-party conversation content. Only process data and communication you are authorized to access and transfer.
