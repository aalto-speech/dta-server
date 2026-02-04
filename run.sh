#!/usr/bin/env bash
set -euo pipefail

# ? Maybe a Makefile instead of a bash script for running?
# ? Which could run for an example: make build, make run, make stop, make clean, etc.
# ? make build would run podman compose build (also good if seperated from run)
# ? make run would run podman compose up -d --build (run detached with build to ensure latest)
# ? make stop would run podman compose down (stop contianers but keep images/volumes)
# ? make clean would prune unused containers, images, volumes, networks, etc.

COMPOSE_FILE=${COMPOSE_FILE:-container-compose.yaml}

podman compose -f ${COMPOSE_FILE} up -d --build
