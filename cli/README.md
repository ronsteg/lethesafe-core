# Lethesafe CLI

This directory contains the command-line interface (CLI) tools for Lethesafe.

The CLI allows you to:
- create new time-lock puzzle files (Maker)
- unlock existing puzzle files after the required computation time (Unlocker)

The CLI uses the shared cryptographic core located in `core/`.


## Requirements

- Python 3.10 or newer
- No network access required


## Setup (recommended)

Create a virtual environment:

### Linux / macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
```
### Windows (PowerShell)
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
```
Install dependencies (if required):

```bash
pip install -r requirements.txt
```

If no `requirements.txt` is present, the CLI uses only Python standard library modules.

## Usage

Create new time-lock capsules:

```bash
python -m cli.lethesafe_maker
```

Unlock an existing capsule:

```bash
python -m cli.lethesafe_unlocker
```

## Notes

All cryptographic logic resides in core/

The CLI performs no network operations

Computation time is enforced by sequential hashing