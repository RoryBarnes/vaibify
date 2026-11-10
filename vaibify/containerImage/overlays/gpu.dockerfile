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

# Installed FROM its record, so the mirror, the lock and the startup
# check see exactly what this overlay put in the image.
RUN mkdir -p /etc/vaibify/requirements.d \
    && printf '%s\n' cupy-cuda12x nvidia-cuda-runtime-cu12 \
        > /etc/vaibify/requirements.d/gpu.txt \
    && pip install --no-cache-dir -r /etc/vaibify/requirements.d/gpu.txt

USER ${CONTAINER_USER}
