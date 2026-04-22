# Dev Containers

This project uses [Dev Containers](https://containers.dev/) to provide a consistent and reproducible development environment. All required dependencies and tools are preconfigured within the container, eliminating the need for manual setup on your local machine.

## Why Dev Containers?

Dev Containers provide an isolated, containerized environment where all necessary tools, libraries, and settings are available. This ensures that the development workspace remains consistent across different operating systems and setups.

## Getting Started

1. Install the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) for Visual Studio Code.
2. Open this repository in Visual Studio Code.
3. When prompted, or by using the Command Palette (`F1` or `Ctrl+Shift+P`), select **"Dev Containers: Rebuild and Reopen in Container"**.
4. Visual Studio Code will build the container using the provided `Containerfile` and configure the environment as specified in `devcontainer.json`.

> [!NOTE]
> The initial build process may take 3 to 6 minutes, depending on network speed and dependency installation. Subsequent starts will be faster, as dependencies are cached.

After the container is ready, you can develop, run, and test the project within the preconfigured environment.

## Helpful Links

- [Dev Containers](https://containers.dev)
- [VS Code Dev Containers Documentation](https://code.visualstudio.com/docs/devcontainers/containers)

For troubleshooting or customization, refer to the official [Dev Containers documentation](https://containers.dev/).
