# CI/CD workflows

This directory contains two GitHub Actions workflows: [`ci.yaml`](./ci.yaml) for validation and [`cd.yaml`](./cd.yaml) for image publishing. The CI workflow runs directly, and the CD workflow is called from CI after the checks pass.

## CI workflow

The CI (Continuous Integration) workflow runs linting and tests on Ubuntu 24.04 whenever changes are made.

**Triggers:**

- Runs on every push to the `dev` branch.
- Runs on every pull request targeting the `dev` or `main` branches.
- Runs when a release is published.
- Supports manual execution with `workflow_dispatch`.

**Job: `lint-and-test`**

- Runs on [`ubuntu-24.04`](https://github.com/actions/runner-images/tree/main?tab=readme-ov-file#available-images).
- Checks out the current ref, or the release tag for release events.
- Verifies that `conda-linux-64.dev.lock` exists before continuing.
- Sets up a micromamba environment from `conda-linux-64.dev.lock`.
- Runs `pylint app/`.
- Runs `pytest -q`.

These checks help catch style issues and test failures early.

**Job: `trigger-cd`**

- Depends on `lint-and-test`.
- Only runs for these cases:
  - Pushes to `dev`.
  - Published releases whose target commit is `main`.
  - Manual runs on `dev`.
- Calls the CD workflow in [`cd.yaml`](./cd.yaml).
- Passes the `contents: read` and `packages: write` permissions needed for image publishing.

## CD workflow

The CD (Continuous Deployment) workflow builds and pushes container images to the GitHub Container Registry (GHCR).

**Trigger:**

- Runs only when called from the CI workflow using `workflow_call`.

**Job: `build-and-deploy`**

- Runs on [`ubuntu-24.04`](https://github.com/actions/runner-images/tree/main?tab=readme-ov-file#available-images).
- Uses `ghcr.io/${{ github.repository }}` as the image name.
- Sets `staging` as the tag for normal CI-triggered runs.
- Sets `latest` and the release tag for published releases.
- Checks out the current ref, or the release tag for release events.
- Logs in to GHCR with the GitHub Actions token.
- Builds the image with `podman build --pull`.
- Pushes the resulting image tags to GHCR with `podman push`.

For release events, the workflow publishes both `:latest` and `:<release-tag>`. For push and manual runs on `dev`, it publishes `:staging`.

---

For more information, see the official [GitHub Actions documentation](https://docs.github.com/en/actions).
