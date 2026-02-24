# CI/CD workflow

This document explains the purpose and step-by-step actions of the CI and CD workflows in this repository. These workflows are defined in [`ci.yaml`](./ci.yaml) and [`cd.yaml`](./cd.yaml) in this directory.

## CI workflow

The CI (Continuous Integration) workflow automatically checks code quality and runs tests on multiple operating systems whenever changes are made.

**Triggers:**

- Runs on every push to the `dev` branch.
- Runs on every pull request targeting the `dev` or `main` branches.

**Job: test-and-lint**

- **Matrix Build:** Runs the workflow on both `ubuntu-22.04` and `ubuntu-latest` to ensure compatibility across environments.
- **Steps:**
  1.  **Checkout code:** Retrieves the latest code from the repository.
  2.  **Set up Miniconda:** Initializes a Conda environment using `environment.yaml` and ensures Conda is up to date.
  3.  **Check Conda and Python version:** Prints Conda and Python version information for debugging and reproducibility.
  4.  **Install test and linter dependencies:** Installs `pytest` and `pylint` using Conda to ensure consistent environments.
  5.  **Lint with pylint:** Runs `pylint` on the `app/` directory to check for code style and programming errors.
  6.  **Run tests:** Executes all tests in the `tests/` directory using `pytest`.

This workflow helps maintain code quality, ensures cross-platform compatibility, and catches issues early in the development process.

## CD workflow

WIP
