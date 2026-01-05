# Lethesafe Core

This repository contains the open-source core of the Lethesafe project.

Lethesafe is a time-lock system.
It does not encrypt secrets.
It enforces unavoidable delay before access becomes possible.

## Repository Scope

- `core/` – cryptographic core (auditable, deterministic)
- `cli/` – reference command-line tools
- `web/` – optional web interface

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
