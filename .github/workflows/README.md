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

The CD (Continuous Deployment) workflow builds and pushes **two** container images to the GitHub Container Registry (GHCR):

| Image | Built from | Contents |
| --- | --- | --- |
| `ghcr.io/aalto-speech/dta-server` | repo root `Containerfile` | the FastAPI app |
| `ghcr.io/aalto-speech/dta-server/inference` | `inference/Containerfile` | the M-CASA speech scorer (~10 GB, CUDA + torch) |

Both are always pushed with the **same tag from the same commit**, so a deployment never runs mismatched app/scorer code.

**Trigger:**

- Runs only when called from the CI workflow using `workflow_call`.

**Job: `changes`**

- Uses `dorny/paths-filter` to detect whether the push touched `inference/**`.
- The filter step only runs on `push` events (it needs a base commit to diff
  against); on release events it is skipped and the expression forces the build.
- The output comparison is `steps.filter.outputs.inference == 'true'` — step
  outputs are **strings**, and a bare `outputs.inference || ...` would treat the
  string `'false'` as truthy and skip the inference build on releases.
- Net effect: pushes that only touch `app/` skip the slow inference build;
  **releases always build both images** so the pair stays complete.

**Job: `build-and-deploy`**

- Runs on [`ubuntu-24.04`](https://github.com/actions/runner-images/tree/main?tab=readme-ov-file#available-images).
- Sets `staging` as the tag for normal CI-triggered runs.
- Sets `latest` and the release tag for published releases.
- Checks out the current ref, or the release tag for release events.
- Logs in to GHCR with the GitHub Actions token.
- Builds the image(s) with `podman build --pull`.
- Pushes the resulting image tags to GHCR with `podman push`.

For release events, the workflow publishes both `:latest` and `:<release-tag>`. For push and manual runs on `dev`, it publishes `:staging`.

Servers are **not** deployed by these workflows — a human pulls the images and restarts the service on each server. See [docs/WORKFLOW.md](../../docs/WORKFLOW.md).

---

For more information, see the official [GitHub Actions documentation](https://docs.github.com/en/actions).
