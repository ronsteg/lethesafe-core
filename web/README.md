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
Install dependencies:

```bash
pip install flask
```

## Run the Web App

Start the web application from the **project root directory**
of the Lethesafe Web package (the directory that contains
`core/`, `web/`, and `web_core/`).


### Linux / macOS

```bash
python3 -m web.app
```

### Windows

```bash
python -m web.app
```

### Open your browser and navigate to:

```bash
http://127.0.0.1:5000
```