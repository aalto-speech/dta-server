# Development guide

## Overview

The DTA server is a FastAPI application with SQLite storage. Startup initializes the database from `app/schema.sql` if the database file does not exist.

## Local environment

Use either the devcontainer (recommended) or a local conda environment.

### Option 1: Development container (recommended)

- Install the devcontainer extension in VSCode. See [dev containers](/.devcontainer/README.md)

The devcontainer installs the required tooling (`python`, `conda`, `pytest`, `fastapi`).

### Option 2: Local conda environment

The repo uses conda environment files:

```bash
conda env create -f environment.yaml
conda env update -n dta-server -f environment.dev.yaml
conda activate dta-server
```

## Adding or removing new packages

- To **add** a package to the conda environment, run the following command:

  ```bash
  conda install <package_name>
  ```

- To **remove** a package from the conda environment, run the following command:

```bash
conda remove <package_name>
```

After adding or removing packages, please [update and render](/docs/DEVELOPMENT.md#update-and-render-lockfiles) the lockfiles.

### Update and render lockfiles

When dependency definitions change, regenerate lockfiles (do not edit lockfiles manually):

```bash
conda-lock -f environment.yaml --lockfile conda-lock.yaml
conda-lock -f environment.yaml -f environment.dev.yaml --lockfile conda-lock.dev.yaml
```

To update one package without re-solving everything:

```bash
conda-lock lock --lockfile conda-lock.yaml --update PACKAGE
conda-lock lock --lockfile conda-lock.dev.yaml --update PACKAGE
```

To render the lockfile for CI and local development run:

```bash
conda-lock render conda-lock.dev.yaml --filename-template conda-{platform}.dev.lock
```

## Adding system packages to the server

See the [setup](/docs/SETUP.md#updating-the-file) documentation.

## Run the app

The container and local runtime both serve the API with FastAPI:

```bash
fastapi run app/main.py --host 0.0.0.0 --port 8000
```

## Key environment variables

- `APP_ENV`: `development`, `test`, `staging`, or `production`.
- `DATABASE`: SQLite file path for non-local environments.
- `AUDIO_SAVE_DIR`: upload storage directory for non-local environments.
- `ADMIN_API_KEY`: required for `production`.
- `MIN_COHORT_SIZE`: minimum cohort size for comparison analytics.
- `MIN_USER_ASSESSMENTS`: minimum scored assessments for user comparison.

## Tests

Run lint checks with:

```bash
pylint app/
```

Run the test suite with:

```bash
pytest -q
```

Run the test coverage report with:

```bash
pytest -q --cov=app --cov-report=html
```

The test setup forces `APP_ENV=test` and cleans up the test database and audio directory before and after each test.
