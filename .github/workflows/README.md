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

- Runs on every push to the `dev` branch.
- Runs on every release event (published release).
- Runs on every pull request targeting the `dev` branch.

**Job: build-and-deploy**

- **Matrix Build:** Runs the workflow on `ubuntu-24.04`
- **Environment Variables:**
  - `IMAGE_NAME`: The full image name for GHCR, e.g., `ghcr.io/OWNER/REPO`.
  - `TAG_NAME`: The tag for the image, set to the current ref name (branch or tag).

**Steps:**

1. **Checkout code:** Retrieves the latest code from the repository.
2. **Login to GitHub Container Registry:** Authenticates to GHCR using the GitHub Actions token.
3. **Build and tag container image:**

- If the workflow is triggered by a release event, builds the image and tags it as `latest` and with the release tag name.
- Otherwise, builds the image and tags it with the current branch or tag name.

4. **Push container images to GHCR:**

- If the workflow is triggered by a release event, pushes both the `latest` and the release tag images.
- Otherwise, pushes the image tagged with the current branch or tag name.

This workflow ensures that container images are automatically built and published for development and release events, supporting continuous deployment practices.

---

For more information, see the official [GitHub Actions documentation](https://docs.github.com/en/actions).
