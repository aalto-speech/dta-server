# DigiTala in Action server

[![CI](https://github.com/aalto-speech/dta-server/actions/workflows/ci.yaml/badge.svg)](https://github.com/aalto-speech/dta-server/actions/workflows/ci.yaml)

This repository contains the server-side code for the DigiTala in Action (DTA) project.

**More information:**

- Official project page: [DigiTala in Action – University of Helsinki](https://www.helsinki.fi/en/projects/digitala-action)
- SaySuomi Application: [CaptainA_unity (GitHub)](https://github.com/Usin2705/CaptainA_unity)

---

## Overview

DigiTala in Action is a FastAPI-based backend for language learning analytics, onboarding, speech assessment, and feedback collection. Data is stored in SQLite by default.

## Server setup and Deployment

1. See the [pouta](/docs/POUTA.md) documentation.
2. See the [setup](/docs/SETUP.md) documentation.

## Development Quickstart

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

# Contributors

<!-- readme: contributors -start -->
<table>
	<tbody>
		<tr>
            <td align="center">
                <a href="https://github.com/kahkaar">
                    <img src="https://avatars.githubusercontent.com/u/119842456?v=4" width="100;" alt="kahkaar"/>
                    <br />
                    <sub><b>Aaron</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/kimtakala">
                    <img src="https://avatars.githubusercontent.com/u/144361787?v=4" width="100;" alt="kimtakala"/>
                    <br />
                    <sub><b>Kim Takala</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/tontsa28">
                    <img src="https://avatars.githubusercontent.com/u/70319189?v=4" width="100;" alt="tontsa28"/>
                    <br />
                    <sub><b>tontsa28</b></sub>
                </a>
            </td>
            <td align="center">
                <a href="https://github.com/Usin2705">
                    <img src="https://avatars.githubusercontent.com/u/8575412?v=4" width="100;" alt="Usin2705"/>
                    <br />
                    <sub><b>Chi Nhan, Phan</b></sub>
                </a>
            </td>
		</tr>
	<tbody>
</table>
<!-- readme: contributors -end -->

---

For more, see the documentation in the [docs](/docs/) directory.
