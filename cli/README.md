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

The CLI tools must be executed using Python’s module mode (`-m`)
from the **project root directory** of the Lethesafe CLI package
(the directory that contains `core/` and `cli/`).

### Linux / macOS

```bash
python3 -m cli.lethesafe_maker
python3 -m cli.lethesafe_unlocker
```

### Windows

```bash
python -m cli.lethesafe_maker
python -m cli.lethesafe_unlocker
```

### Note on Python launcher names

The command name (`python` vs `python3`) depends on the operating system
and local Python installation:

- On most Linux systems, Python 3 is invoked as `python3`
- On Windows, Python 3 is typically invoked as `python`

This is expected behavior and not specific to Lethesafe.

## Notes

All cryptographic logic resides in core/

The CLI performs no network operations

Computation time is enforced by sequential hashing

The CLI is designed to be executed interactively in a terminal window.
