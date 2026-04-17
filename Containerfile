# Builder stage: render an explicit linux-64 lock and create the runtime env
FROM docker.io/mambaorg/micromamba:2.5 AS builder

USER root
WORKDIR /tmp/build

COPY conda-lock.yaml ./

RUN micromamba install -n base -y -c conda-forge conda-lock \
    && conda-lock render \
        -f conda-lock.yaml \
        -p linux-64 \
        --kind explicit \
        --filename-template conda-{platform}.lock \
    && micromamba create -y -p /opt/conda/envs/app --file conda-linux-64.lock \
    && micromamba clean --all --yes


# Runner stage: copy the locked environment and app only
FROM docker.io/debian:trixie-slim AS runner

WORKDIR /app

# Runtime OS packages only
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/conda/envs/app /opt/conda/envs/app
COPY . .

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Cache Whisper models in the persistent volume
ENV WHISPER_CACHE_DIR=/hf/models
ENV PATH=/opt/conda/envs/app/bin:${PATH}

EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
