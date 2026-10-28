---
name: Offline bug report (no private data)
about: Report a reproducible issue using synthetic data only
title: "[Bug] "
labels: []
assignees: []
---

**Please do not upload real LINE databases, message text, tokens, GAS URLs, contacts, device identifiers, or private system logs.** Redact or replace all user data. Reports containing secrets should be removed and credentials rotated.

### What happened?
A concise description of the actual error.

### Expected behavior
What should have happened instead.

### Offline reproduction
Use dummy records or a synthetic SQLite schema. Include the smallest steps and sanitized snippets needed to reproduce.

### Environment
- Python version:
- OS / rootless jailbreak family (if relevant):
- Branch and commit SHA:
- Test method (offline only / device with consent):

### Safety impact
Could this cause missing/duplicate records, unintended network transmission, or cursor changes?

### Observed test result
Paste sanitized, minimal output only. Never paste actual chat data or credentials.
