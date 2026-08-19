FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE keeps the image free of .pyc files that would be
# written once and never reused, since the container filesystem is discarded.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    BASIN_DB=/data/basin.db

WORKDIR /app

# The whole package is copied before installing because hatchling reads src/ at
# build time, so there is no pyproject-only layer to cache separately. The
# dependency set is three packages, which makes the rebuild cheap regardless.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]'

# A placeholder for the mount. Without it the app resolves BASIN_DB to a path
# whose parent does not exist and reports 503 rather than a mount failure.
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "-m", "basin.web"]
