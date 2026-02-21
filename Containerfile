# Containerfile for the server application
# Based on ContinuumIO's Miniconda3 image for easy environment management
FROM docker.io/continuumio/miniconda3
WORKDIR /app

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Cache Whisper models in the persistent volume
ENV WHISPER_CACHE_DIR=/hf/models

# Install dependencies and clean up to reduce image size
COPY environment.yaml ./
RUN conda env update -n base -f environment.yaml \
    && conda clean -afy

# Copy application files after installing dependencies (see .containerignore for excluded files)
COPY . .

# Document the exposed port and set the default command to run the server
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
