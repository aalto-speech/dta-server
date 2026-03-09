# CI/CD workflow

This document explains the purpose and step-by-step actions of the CI and CD workflows in this repository. These workflows are defined in [`ci.yaml`](./ci.yaml) and [`cd.yaml`](./cd.yaml) in this directory.

## CI workflow

The CI (Continuous Integration) workflow automatically checks code quality and runs tests on Ubuntu whenever changes are made.

**Triggers:**

- Runs on every push to the `dev` branch.
- Runs on every pull request targeting the `dev` or `main` branches.

**Job: lint-and-test**

- **Matrix Build:** Runs the workflow on [`ubuntu-24.04`](https://github.com/actions/runner-images/tree/main?tab=readme-ov-file#available-images) to ensure compatibility with staging and production environments.
- **Steps:**
  1.  **Checkout code:** Retrieves the latest code from the repository.
  2.  **Set up Miniconda:** Creates and updates a Conda environment from `environment.yaml`.
  3.  **Check Conda and Python version:** Outputs Conda and Python version information for debugging and reproducibility.
  4.  **Install lint and test dependencies:** Installs `pylint` and `pytest` using Conda for a consistent environment.
  5.  **Lint with pylint:** Checks code style and programming errors in the `app/` directory.
  6.  **Run tests:** Runs all tests in the `tests/` directory using `pytest`.

This workflow helps maintain code quality and catches issues early in the development process.

## CD workflow

The CD (Continuous Deployment) workflow builds and pushes container images to the GitHub Container Registry (GHCR) for deployments.

**Triggers:**

- Runs when the `CI` workflow completes on the `dev` branch (`workflow_run` trigger).
- Runs on published releases (`release` trigger).

**Job: build-and-deploy**

- **Matrix Build:** Runs the workflow on `ubuntu-24.04`
- **Job guard:** The job runs only when one of these is true:
  - `CI` completed successfully for `dev` (`workflow_run.conclusion == success` and branch is `dev`).
  - A release is published from `main` (`release.target_commitish == main`).
- **Environment Variables:**
  - `IMAGE_NAME`: The full image name for GHCR, e.g., `ghcr.io/OWNER/REPO`.

**Steps:**

1. **Checkout code:**

- For `workflow_run`, checks out the exact commit SHA that passed CI.
- For `release`, checks out the release tag.

2. **Login to GitHub Container Registry:** Authenticates to GHCR using the GitHub Actions token.
3. **Resolve target image tags:**

- For `workflow_run`, sets `TAG_NAME=dev`.
- For `release`, sets `TAG_NAME=latest` and `RELEASE_TAG=<release-tag>`.

4. **Build and tag container image:**

- For `workflow_run`, builds one image tag: `:dev`.
- For `release`, builds two image tags: `:latest` and `:<release-tag>`.

5. **Push container images to GHCR:**

- For `workflow_run`, pushes `:dev`.
- For `release`, pushes `:latest` and `:<release-tag>`.

This workflow ensures development images are published only after CI passes on `dev`, and release images from `main` are published as both `latest` and the release-specific tag.

---

For more information, see the official [GitHub Actions documentation](https://docs.github.com/en/actions).
