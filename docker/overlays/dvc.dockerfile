# VaibCask — Data Version Control overlay
#
# Adds DVC for versioning datasets and ML models alongside code.

ARG BASE_IMAGE=vaibcask:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN pip install --no-cache-dir dvc
