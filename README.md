# LINE Auto Sync

Experimental message synchronization tool for rootless jailbroken iOS.

**Status: Experimental / Code Review Requested**

## Overview

A Python-based project that reads message data from a local LINE SQLite database and forwards new messages to a user-configured HTTPS endpoint.

This project is unofficial and is not affiliated with LINE or LY Corporation.

## Current Features

- Rootless iOS package structure (`.deb`)
- Python 3.9-compatible source code
- Normal LINE message synchronization
- Incremental SQLite extraction
- HTTP delivery and retry handling
- Initial configuration and diagnostic CLI
- Disabled-by-default configuration

## Known Limitations

- Not tested on other iOS devices
- OpenChat is not supported
- Automatic background execution is not supported
- Oversized messages stop processing
- Compatibility with different LINE database versions is unknown
- End-to-end message processing has not been independently verified

## Testing

Offline tests have passed for configuration, message batching, retries, SQLite extraction with a synthetic schema, and package integrity.

These tests do not establish compatibility with real devices or guarantee against message loss.

## Code Review Wanted

Feedback is especially welcome on:

- Rootless package layout and Python dependencies
- SQLite query compatibility and data integrity
- Cursor management and retry behavior
- Privacy, security and credential handling
- Packaging and installation practices
- Risks associated with third-party service integrations

## Disclaimer

Experimental software for technical review only. Do not use with important or private data without appropriate authorization, backups and an understanding of the risks.

This project is not ready for general production use.
