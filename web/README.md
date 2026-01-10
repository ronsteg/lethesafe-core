# Lethesafe Web

This directory contains the optional local web interface for Lethesafe.

The web interface provides a browser-based UI for:
- creating time-lock puzzle files
- unlocking existing puzzle files
- monitoring progress during computation

All cryptographic operations are executed locally.
No data is sent to external services.


## Requirements

- Python 3.10 or newer
- A modern web browser
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

## Run the Web App

Start the local server:

```bash
python app.py
```

Open your browser and navigate to:

```bash
http://127.0.0.1:5000
```