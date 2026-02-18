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

The command-line tools and the web interface are released from the
same core codebase, but may be packaged and distributed separately.



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


# Releases  

## 1.1.0 (2026-02-18)

### Architecture & Hardening Phase

This release consolidates the canonical puzzle format,
enforces strict cryptographic boundaries,
and introduces fail-closed validation behavior.

No changes were made to the time-lock construction,
delay semantics, or cryptographic primitives.

---

### Core v1.1.0

- Strict integer enforcement for `rounds` and `iterations`
- Removal of implicit type casting
- Exact algorithm identifier enforcement
- Strict Base64 length validation for critical fields
- Strict checksum normalization and digest-length validation
- Unified fail-closed error model
- Hash-chain execution restricted exclusively to the core layer

---

### CLI v1.1.0

- Removal of cryptographic logic from frontend layer
- Strict capsule validation parity with core
- Unified artifact output format (`.txt`)
- Stable clone workflow without post-start interaction prompts

---

### Web v1.1.0

- Strict capsule validation parity with core
- JSON-only structured error responses
- Removal of frontend-side cryptographic execution
- Unified artifact output format (`.txt`)
- Corrected clone progress calculation

Progress in clone mode is now calculated against the
maximum required round target rather than clone-local
round subsets, ensuring mathematically continuous
sequential progress visualization.

---

### Documentation

- Whitepaper formal versioning introduced (Version 1.0)
- Clarified separation between model specification and reference implementation

---

### Unchanged

- Sequential SHA-256 hash-chain construction
- XOR-based secret binding
- PBKDF2-HMAC-SHA256 password protection
- Time-lock irreversibility guarantees


## 1.0.2 (2026-02-07)

### Changes

- CLI v1.0.2:
  - Tolerant UTF-8 parsing for capsule input (incl. BOM)
  - Normalization of line endings before parsing
  - Canonical JSON encoding for generated and cloned capsules
  - Unified encoding handling across maker, clone, and unlocker

- Web v1.0.1:
  - Tolerant UTF-8 parsing for capsule input (incl. BOM)
  - Canonical JSON encoding for generated and cloned capsules
  - Hardened password inputs against browser and OS autofill
  - Improved capsule download visibility and visual hierarchy
  - Capsule symbol added to clearly identify time capsule artifacts
  - File picker accepts all file types (content validated internally)

### Unchanged
- Core v1.0.0


## 1.0.1 (2026-01-15)

### Changes
- CLI v1.0.1: Prevent Windows Terminal from closing immediately
- CLI v1.0.1: Improved user interaction and exit confirmation prompts

### Unchanged
- Core v1.0.0
- Web v1.0.0


## 1.0.0 (2026-01-10)

### Initial public release
- Core v1.0.0
- CLI v1.0.0
- Web v1.0.0