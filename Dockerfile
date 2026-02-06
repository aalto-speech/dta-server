# Containerfile for the DTA server. Based on ContinuumIO's Miniconda3 image for easy environment management.
FROM docker.io/continuumio/miniconda3

ARG PROJECT_NAME=dta-server
ENV CONDA_ENV=${PROJECT_NAME}

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies and clean up to reduce image size
COPY environment.yaml requirements.txt ./
RUN conda env create -n "${CONDA_ENV}" --file environment.yaml \
    && conda clean -afy

# Add the conda environment to PATH for easier execution of commands within the container
ENV PATH=/opt/conda/envs/${CONDA_ENV}/bin:$PATH

# Copy the application code after installing dependencies
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
