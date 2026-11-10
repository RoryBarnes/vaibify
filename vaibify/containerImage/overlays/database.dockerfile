# Vaibify — PostgreSQL/SQLite client overlay
#
# Adds database client tools and Python database packages.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image ends as the unprivileged container user; installing
# system-wide needs root, and the last line hands the image back.
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
        libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        psycopg2-binary \
        sqlalchemy

USER ${CONTAINER_USER}
