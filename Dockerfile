FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE keeps the image free of .pyc files that would be
# written once and never reused, since the container filesystem is discarded.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    BASIN_DB=/app/serving.db

WORKDIR /app

# The whole package is copied before installing because hatchling reads src/ at
# build time, so there is no pyproject-only layer to cache separately. The
# dependency set is three packages, which makes the rebuild cheap regardless.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[web]'

# The store ships in the image. It is ~15MB -- the facts, citations and
# verification rows the dashboard reads, without the ~3GB document index that
# only ingestion touches -- so there is no volume to mount, nothing pinning the
# app to one machine, and no upload-and-swap between building a store and
# serving it. Build it first:
#
#     python scripts/build_serving_store.py
#
# Last layer because it is the one that changes on every data refresh; the
# dependency install above stays cached.
COPY build/serving.db ./serving.db

EXPOSE 8080
CMD ["python", "-m", "basin.web"]
