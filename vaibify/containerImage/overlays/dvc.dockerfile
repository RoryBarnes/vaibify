# Vaibify — Data Version Control overlay
#
# Adds DVC for versioning datasets and ML models alongside code.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image ends as the unprivileged container user; installing
# system-wide needs root, and the last line hands the image back.
USER root

# Installed FROM its record, so the mirror, the lock and the startup
# check see exactly what this overlay put in the image.
RUN mkdir -p /etc/vaibify/requirements.d \
    && printf '%s\n' dvc > /etc/vaibify/requirements.d/dvc.txt \
    && pip install --no-cache-dir -r /etc/vaibify/requirements.d/dvc.txt

USER ${CONTAINER_USER}
