# DigiTala in Action server

[![CI](https://github.com/aalto-speech/dta-server/actions/workflows/ci.yaml/badge.svg)](https://github.com/aalto-speech/dta-server/actions/workflows/ci.yaml)

This repository contains the server-side code for the DigiTala in Action (DTA) project.

**More information:**

- Official project page: [DigiTala in Action – University of Helsinki](https://www.helsinki.fi/en/projects/digitala-action)
- SaySuomi Application: [CaptainA_unity (GitHub)](https://github.com/Usin2705/CaptainA_unity)

---

## Overview

DigiTala in Action is a FastAPI-based backend for language learning analytics, onboarding, speech assessment, and feedback collection. Data is stored in SQLite by default.

## Quickstart

1. Clone the repository.
2. Open in VS Code and use the devcontainer, or set up a local Conda environment (see [Development guide](/docs/DEVELOPMENT.md)).
3. Run the API:
   ```bash
   fastapi run app/main.py --host 0.0.0.0 --port 8000
   ```
4. Visit `/ping` to check the server.

> [!NOTE]
> The `/api/v1/` endpoint is handled by the reverse proxy, so use `http://localhost:8000/ping` for local API requests.

## Development

- See [Development guide](/docs/DEVELOPMENT.md) for local setup, running tests, and lockfile maintenance.
- See [API reference](/docs/API.md) for endpoints and request details.

## Deployment

- See [Deployment setup](/docs/SETUP.md) for production installation and environment variables.

---

For more, see the documentation in the [docs](/docs/) directory.
