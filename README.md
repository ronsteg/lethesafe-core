# Lethesafe® Core

This repository contains the open-source core of the Lethesafe project.

Lethesafe is a time-lock system.
It does not encrypt secrets.
It enforces unavoidable delay before access becomes possible.

This repository provides the cryptographic core and reference implementations
for local use. Lethesafe does not operate any hosted service.

## Repository Scope

- `core/` – cryptographic core (auditable, deterministic)
- `cli/` – reference command-line tools (local execution)
- `web/` – optional local web interface

## Documentation

The official Lethesafe Whitepaper and Concept documents are maintained in the
`lethesafe-core` repository under `docs/`:

https://github.com/ronsteg/lethesafe-core/tree/main/docs

They are provided to allow reviewers and auditors to inspect the complete
technical and conceptual context alongside the source code.

## Security Model

- No backdoors
- No hidden state
- No network dependency
- Sequential, deterministic computation
- Verifiable output

## Project Website

https://lethesafe.org

## Disclaimer

Lethesafe is experimental open-source software.

It is provided “as is”, without any warranty of any kind, express or implied,
including but not limited to the warranties of merchantability, fitness for a
particular purpose, or non-infringement.

The authors and contributors shall not be liable for any damages, data loss,
or other harm arising from the use, misuse, or inability to use this software.

Lethesafe is not a service, does not provide guarantees, and is not a substitute
for backups or other safety measures. Users are solely responsible for evaluating
the suitability of the software for their use case.

No security mechanism is absolute. Lethesafe makes no claims of being
unbreakable or immune to failure.


## License

GNU Affero General Public License v3 (AGPLv3)


## Trademark Notice

Lethesafe® is a registered trademark of Ronald Stegmiller.

The source code is licensed under the GNU Affero General Public License v3 (AGPLv3).
Forks and derivative works are permitted under the AGPLv3, but may not use the name "Lethesafe" without permission.