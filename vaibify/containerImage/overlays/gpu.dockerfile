# Vaibify — NVIDIA CUDA overlay
#
# Adds CUDA Python packages on top of the base image. The actual GPU base
# image swap (e.g. nvidia/cuda) happens in imageBuilder.py at build time;
# this overlay only installs the Python-level CUDA bindings.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image ends as the unprivileged container user; installing
# system-wide needs root, and the last line hands the image back.
USER root

RUN pip install --no-cache-dir \
        cupy-cuda12x \
        nvidia-cuda-runtime-cu12

USER ${CONTAINER_USER}
