# Release & Hash Policy

This document defines how official Lethesafe releases are created,
published, and verified.

The goal is to provide a clear and verifiable reference for users,
reviewers, and auditors.


## Canonical Source

The canonical source of the Lethesafe project is the public Git
repository hosted on GitHub.

The repository history itself is not considered a release artifact.


## Official Releases

Official releases are published exclusively via GitHub Releases
under this repository.

Each release represents a deliberate snapshot of the project at a
specific point in time.


## Release Artifacts

For each official release, the following artifacts may be provided:

- ZIP archives of the command-line tools (source)
- ZIP archives of the optional web interface (source)
- Prebuilt binaries for selected platforms, if provided
- Documentation bundles, if applicable

Release artifacts are derived from the corresponding Git commit but
are distributed separately for convenience and verification.

Prebuilt binaries are provided for convenience only.
The authoritative reference remains the published source code and
the documented build process.


## Hash Verification

Each release includes a file named `SHA256SUMS.txt` containing the
SHA-256 hashes of all published artifacts.

Users are expected to verify downloaded artifacts using standard
hash verification tools before use.


## What Is Not a Release

The following are explicitly not considered official releases:

- The current state of the repository tree
- Individual commits or branches
- Forks or derivative builds not published by the Lethesafe maintainers
- Locally built binaries without published hashes


## Signatures

At present, releases are verified using published SHA-256 hashes.

Cryptographic signatures (e.g. GPG or Sigstore) may be added in future
releases but are not required for initial versions.


## Responsibility and Trust

Only releases published through the official GitHub repository and
documented channels are considered authentic Lethesafe releases.

Users are responsible for verifying the integrity of downloaded
artifacts before use.
