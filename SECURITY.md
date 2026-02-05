# Security Policy

Lethesafe is a security-critical project.
Security is not an afterthought. It is the result of explicit design decisions.

This document describes how security-relevant observations regarding
**lethesafe-core** can be reported responsibly.

---

## Reporting a Security Issue

If you believe you have identified a security issue in **lethesafe-core**,
please report it privately.

Contact:
security@lethesafe.org

A report should include:
- a precise description of the observation
- the affected version or commit
- steps required to reproduce the issue, if applicable

Public disclosure prior to contact is discouraged.

This channel is not intended for discussion, feature requests, or support.

---

## Scope

This policy applies exclusively to:
- **lethesafe-core** (cryptographic core logic and its reference implementation)

Out of scope:
- third-party infrastructure or integrations
- user configuration, operational mistakes, or misuse
- loss of secrets, forgotten parameters, or intentional destruction of data
- assumptions about recoverability

Please ensure that your report actually falls within this scope.

---

## Design Decisions as Security Boundaries

Lethesafe deliberately provides:

- no backdoors
- no reset mechanisms
- no alternative recovery mechanisms
- no emergency access

These are not missing features.
They define the security boundary of the system.

Reports requesting exceptions, shortcuts, or privileged access
do not constitute security improvements and will be rejected by design.

---

## What Is Not a Vulnerability

The following are not considered security issues:

- inability to access secrets before the defined time has elapsed
- the absence of alternative recovery mechanisms
  following deliberate non-retention of secrets
- irreversible consequences of deliberate configuration choices
- regret or inconvenience caused by enforced waiting periods
- the expected failure of the unlock process
  resulting from a modified or manipulated time capsule file

Such outcomes are expected.

## What Is a Security-Relevant Defect

Failure to produce the expected output after the defined time,
given correct parameters and an unmodified implementation,
is considered a security-relevant defect.

---

## Responsible Disclosure

Valid security reports will be reviewed carefully.
Fixes may be issued if they preserve the fundamental guarantees of the system.

There is:
- no bug bounty program
- no guaranteed response time
- no obligation to accept proposed changes

Correctness takes precedence over convenience.

---

## Supported Versions

Only the latest released versions are considered.
Earlier versions remain unchanged.

---

Lethesafe does not negotiate access.
It enforces consequences.
