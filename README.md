# Lethesafe Core

This repository contains the open-source core of the Lethesafe project.

Lethesafe is a time-lock system.
It does not encrypt secrets.
It enforces unavoidable delay before access becomes possible.

## Repository Scope

- `core/` – cryptographic core (auditable, deterministic)
- `cli/` – reference command-line tools
- `web/` – optional web interface

## Documentation

This repository includes copies of the Lethesafe Whitepaper and Concept
documents for audit and reference purposes.

The canonical and authoritative versions of these documents are published
on the official project website:

https://lethesafe.org/materialien.html

Copies in this repository are provided to allow reviewers and auditors
to inspect the complete technical and conceptual context alongside the
source code.

## Security Model

- No backdoors
- No hidden state
- No network dependency
- Sequential, deterministic computation
- Verifiable output

## Project Website

https://lethesafe.org

## License

GNU Affero General Public License v3 (AGPLv3)
