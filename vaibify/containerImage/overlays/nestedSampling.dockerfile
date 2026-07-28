# Vaibify — nested-sampling Bayesian inference overlay
#
# ultranest is pure pip; pymultinest is only a ctypes wrapper around the
# native MultiNest library, so MultiNest is built from source here and
# its shared libraries installed where the loader finds them — a bare
# "pip install pymultinest" imports fine and then fails at first use
# with "libmultinest.so not found".
#
# This lived in the BASE image until 2026-07-27. That meant every
# vaibify image carried a Fortran toolchain and a from-source numerical
# build whether or not the project used nested sampling — roughly a
# gigabyte and several minutes of build time imposed on users who will
# never call it. Nested sampling is a domain-specific dependency, so it
# belongs behind a feature flag like every other one.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# gfortran, cmake and LAPACK/BLAS exist solely to build MultiNest, so
# they travel with it rather than sitting in the baseline toolchain.
# They are installed here, not via the user-configurable
# systemPackages, because the overlay itself needs them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gfortran \
        cmake \
        liblapack-dev \
        libblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Build ONLY the library targets (multinest_shared / multinest_static)
# — a bare "make" also compiles the bundled example executables, and a
# single example's compile failure aborts the whole build (exit 2) even
# though libmultinest.so built cleanly. pymultinest needs only the
# shared library, so the examples are pure liability. The upstream
# CMake already adds -std=legacy -fallow-argument-mismatch for
# gfortran >= 10, so no compatibility flag is needed here.
#
# Pinned to a commit, not a branch. A moving default branch makes two
# builds of the "same" image link different numerical libraries, which
# is precisely the drift a reproducibility tool must not ship. Bump it
# deliberately; a shallow fetch of one SHA keeps the clone cheap.
ARG MULTINEST_COMMIT=a06c192c5a940a71a35cf6fb8a5fbf66df4ac0b6
RUN mkdir -p /tmp/MultiNest \
    && git -C /tmp/MultiNest init -q \
    && git -C /tmp/MultiNest remote add origin \
        https://github.com/JohannesBuchner/MultiNest.git \
    && git -C /tmp/MultiNest fetch -q --depth 1 origin \
        "${MULTINEST_COMMIT}" \
    && git -C /tmp/MultiNest checkout -q FETCH_HEAD \
    && cmake -S /tmp/MultiNest -B /tmp/MultiNest/build \
    && make -C /tmp/MultiNest/build -j"$(nproc)" \
        multinest_shared multinest_static \
    && cp -a /tmp/MultiNest/lib/. /usr/local/lib/ \
    && ldconfig \
    && rm -rf /tmp/MultiNest

RUN pip install --no-cache-dir ultranest pymultinest
