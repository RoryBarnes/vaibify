# Vaibify — Data Version Control overlay
#
# Adds DVC for versioning datasets and ML models alongside code.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image ends as the unprivileged container user; installing
# system-wide needs root, and the last line hands the image back.
USER root

RUN pip install --no-cache-dir dvc

USER ${CONTAINER_USER}
