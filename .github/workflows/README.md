# CI/CD workflow

This document explains the purpose and step-by-step actions of the combined CI/CD workflow in this repository. The workflow is defined in [`ci-cd.yaml`](./ci-cd.yaml) in this directory.

## CI workflow

The CI (Continuous Integration) portion automatically checks code quality and runs tests on Ubuntu whenever changes are made.

**Triggers:**

- Runs on every push to the `dev` branch.
- Runs on every pull request targeting the `dev` or `main` branches.
- Runs on published releases (`release` trigger).
- Supports manual execution (`workflow_dispatch`).

**Job: lint-and-test**

- **Matrix Build:** Runs the workflow on [`ubuntu-24.04`](https://github.com/actions/runner-images/tree/main?tab=readme-ov-file#available-images) to ensure compatibility with staging and production environments.
- **Steps:**
  1.  **Checkout code:** Retrieves the latest code from the repository.
  2.  **Set up Miniconda:** Creates and updates a Conda environment from `environment.yaml`.
  3.  **Check Conda and Python version:** Outputs Conda and Python version information for debugging and reproducibility.
  4.  **Install lint and test dependencies:** Installs `pylint` and `pytest` using pip for fast installation.
  5.  **Lint with pylint:** Checks code style and programming errors in the `app/` directory.
  6.  **Run tests:** Runs all tests in the `tests/` directory using `pytest`.

This workflow helps maintain code quality and catches issues early in the development process.

## CD workflow

The CD (Continuous Deployment) portion builds and pushes container images to the GitHub Container Registry (GHCR) for deployments.

**Triggers:**

- Runs in the same workflow after `lint-and-test` succeeds (`needs: lint-and-test`).
- Deploys only for pushes to `dev`, published releases from `main`, or manual dispatch on `dev`.

**Job: build-and-deploy**

- **Matrix Build:** Runs the workflow on `ubuntu-24.04`
- **Job dependency:** Deploy runs only after CI succeeds in the same run.
- **Job guard:** The deploy job runs only when one of these is true:
  - Event is `push` and branch is `dev`.
  - Event is `release` and `release.target_commitish == main`.
  - Event is `workflow_dispatch` and branch is `dev`.
- **Environment Variables:**
  - `IMAGE_NAME`: The full image name for GHCR, e.g., `ghcr.io/OWNER/REPO`.
  - `TAG_NAME`: Set to `latest` for releases, `staging` for pushes/manual runs.
  - `RELEASE_TAG`: Set to the release tag name for release events.

**Steps:**

1. **Checkout code:**

- For regular branch events, checks out the current ref.
- For `release`, checks out the release tag.

2. **Login to GitHub Container Registry:** Authenticates to GHCR using the GitHub Actions token.
3. **Build and tag container image:**

- For `push` and `workflow_dispatch` on `dev`, builds one image tag: `:staging`.
- For `release`, builds two image tags: `:latest` and `:<release-tag>`.

4. **Push container images to GHCR:**

- For `push` and `workflow_dispatch` on `dev`, pushes `:staging`.
- For `release`, pushes `:latest` and `:<release-tag>`.

This combined workflow ensures development images are published only after CI passes on `dev`, and release images from `main` are published as both `latest` and the release-specific tag.

---

For more information, see the official [GitHub Actions documentation](https://docs.github.com/en/actions).
