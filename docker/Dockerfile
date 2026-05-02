# Vaibify — Parameterized base image for scientific computing containers
#
# Build ARGs allow projects to customize the image without editing this file.
# Application-specific packages belong in a config-generated requirements.txt.

ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG PYTHON_VERSION=3.12
ARG CONTAINER_USER=researcher
ARG WORKSPACE_ROOT=/workspace
ARG INSTALL_LATEX=true
ARG INSTALL_X11=true
ARG PACKAGE_MANAGER=pip
ARG VC_PROJECT_NAME=Vaibify

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_ROOT_USER_ACTION=ignore

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Disable the _apt sandbox user for apt-get. In containerised environments
# the unprivileged _apt user's home is /nonexistent, which makes gpgv fail
# signature verification with an empty error summary — apt reports
# "At least one invalid signature was encountered" even though the signature
# is valid and gpgv run directly as root succeeds. Running apt as root is
# already the norm during image build; the sandbox provides no additional
# isolation here.
RUN echo 'APT::Sandbox::User "root";' > /etc/apt/apt.conf.d/99-no-sandbox

# Generic build tools required by the base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    git \
    openssh-client \
    curl \
    ca-certificates \
    gnupg \
    software-properties-common \
    gosu \
    sudo \
    nano \
    vim \
    pkg-config \
    graphviz \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Project-specific system packages from vaibify.yml systemPackages
COPY system-packages.txt /tmp/system-packages.txt
RUN if [ -s /tmp/system-packages.txt ]; then \
        apt-get update \
        && xargs -a /tmp/system-packages.txt \
           apt-get install -y --no-install-recommends \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && rm -f /tmp/system-packages.txt

# Create HDF5 symlinks if libhdf5-dev was installed (h5py needs them)
RUN if [ -d /usr/include/hdf5/serial ]; then \
        ln -sf /usr/include/hdf5/serial/*.h /usr/include/ \
        && ln -sf /usr/lib/"$(uname -m)"-linux-gnu/hdf5/serial/libhdf5*.so* \
                  /usr/lib/"$(uname -m)"-linux-gnu/ 2>/dev/null || true; \
    fi

# Conditional X11 viewers for displaying figures and documents on the host
RUN if [ "${INSTALL_X11}" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            eog \
            evince \
            feh \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Conditional LaTeX for matplotlib tex rendering
RUN if [ "${INSTALL_LATEX}" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            texlive-latex-base \
            texlive-latex-extra \
            texlive-fonts-recommended \
            cm-super \
            dvipng \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Note:
# When adding network-dependent RUN steps to this or overlay
# Dockerfiles, prefer curl with --retry, --connect-timeout, and
# --fail; on failure, emit a diagnostic via `echo` to stderr and
# exit 1 with an actionable message. See the deadsnakes block
# below for the exact template.

# Python: prefer system repos; fall back to deadsnakes PPA only if
# the requested version is not in the base image's archive. The
# default config (ubuntu:24.04 + Python 3.12) has no launchpad.net
# dependency. When the PPA is needed and unreachable (TLS / proxy /
# network), exit with an actionable diagnostic instead of an opaque
# SSLEOFError traceback from add-apt-repository.
ARG BASE_IMAGE
RUN apt-get update \
    && if ! apt-cache show python${PYTHON_VERSION} >/dev/null 2>&1; then \
           if ! add-apt-repository -y ppa:deadsnakes/ppa; then \
               echo "" >&2; \
               printf '%s\n' "vaibify build: cannot install Python ${PYTHON_VERSION}." >&2; \
               printf '%s\n' "  - It is not in ${BASE_IMAGE}'s default apt archive." >&2; \
               printf '%s\n' "  - The deadsnakes PPA could not be added (likely a TLS" >&2; \
               printf '%s\n' "    or network failure between this build host and" >&2; \
               printf '%s\n' "    launchpad.net)." >&2; \
               printf '%s\n' "Pick one workaround:" >&2; \
               printf '%s\n' "  1. Set pythonVersion in vaibify.yml to the base image" >&2; \
               printf '%s\n' "     default (e.g. 3.12 for ubuntu:24.04, 3.10 for" >&2; \
               printf '%s\n' "     ubuntu:22.04) so no PPA is needed." >&2; \
               printf '%s\n' "  2. Switch baseImage to one that ships Python" >&2; \
               printf '%s\n' "     ${PYTHON_VERSION} natively." >&2; \
               printf '%s\n' "  3. Resolve your build host's network path to" >&2; \
               printf '%s\n' "     launchpad.net (proxy, IPv6, MTU, TLS inspection)" >&2; \
               printf '%s\n' "     and rebuild." >&2; \
               echo "" >&2; \
               exit 1; \
           fi; \
           apt-get update; \
       fi \
    && apt-get install -y --no-install-recommends \
        python${PYTHON_VERSION} \
        python${PYTHON_VERSION}-dev \
        python${PYTHON_VERSION}-venv \
        python${PYTHON_VERSION}-tk \
        tk-dev \
    && (apt-get install -y --no-install-recommends \
        python${PYTHON_VERSION}-distutils 2>/dev/null || true) \
    && rm -rf /var/lib/apt/lists/*

# Make the selected Python version the default and install pip
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python${PYTHON_VERSION} 1 \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3-pip \
    && rm -f /usr/lib/python*/EXTERNALLY-MANAGED \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade setuptools

# Keyring backend for in-container credential storage.
# keyrings.alt provides a file-based PlaintextKeyring that works in
# headless containers where the default SecretService backend is
# unavailable and where EncryptedKeyring's interactive master-password
# prompt cannot run. The container is ephemeral and the credential
# file is chmod 600; nothing leaks to the host. PYTHON_KEYRING_BACKEND
# pins the backend for every user (including root), which is what
# vaibify's docker exec defaults to; the keyringrc.cfg written later
# is a belt-and-suspenders backup for interactive shells that read it.
RUN pip install --no-cache-dir keyring keyrings.alt
ENV PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring

# Conditional conda/mamba install for non-pip package managers.
# Wrap the network call so a TLS or routing failure to GitHub /
# objects.githubusercontent.com produces an actionable diagnostic —
# same template as the deadsnakes block above.
RUN if [ "${PACKAGE_MANAGER}" != "pip" ]; then \
        MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"; \
        if ! curl -fsSL --retry 3 --retry-connrefused --retry-delay 5 \
                --connect-timeout 30 \
                "${MINIFORGE_URL}" -o /tmp/miniforge.sh; then \
            echo "" >&2; \
            printf '%s\n' "vaibify build: cannot download the Miniforge installer." >&2; \
            printf '%s\n' "  - Installer lives at ${MINIFORGE_URL}." >&2; \
            printf '%s\n' "  - Download failed (likely a TLS or network failure between" >&2; \
            printf '%s\n' "    this build host and github.com /" >&2; \
            printf '%s\n' "    objects.githubusercontent.com)." >&2; \
            printf '%s\n' "Pick one workaround:" >&2; \
            printf '%s\n' "  1. Set packageManager to 'pip' in vaibify.yml — the" >&2; \
            printf '%s\n' "     Miniforge installer is only fetched for non-pip" >&2; \
            printf '%s\n' "     package managers." >&2; \
            printf '%s\n' "  2. Resolve your build host's network path to github.com" >&2; \
            printf '%s\n' "     (proxy, IPv6, MTU, TLS inspection) and rebuild." >&2; \
            echo "" >&2; \
            exit 1; \
        fi \
        && bash /tmp/miniforge.sh -b -p /opt/conda \
        && rm /tmp/miniforge.sh \
        && /opt/conda/bin/conda init bash \
        && /opt/conda/bin/conda clean -afy; \
    fi

ENV PATH="${PACKAGE_MANAGER:+/opt/conda/bin:}${PATH}"

# Project-specific Python packages from vaibify.yml pythonPackages
COPY requirements.txt /tmp/requirements.txt
COPY pip-flags.txt /tmp/pip-flags.txt
RUN if [ -s /tmp/requirements.txt ]; then \
        PIP_FLAGS="$(cat /tmp/pip-flags.txt 2>/dev/null | tr -d '\n')" \
        && pip install --no-cache-dir ${PIP_FLAGS} -r /tmp/requirements.txt; \
    fi \
    && rm -f /tmp/requirements.txt /tmp/pip-flags.txt

# Create non-root user with UID 1000 (remove default ubuntu user if needed)
RUN if id -u ${CONTAINER_USER} >/dev/null 2>&1; then \
        true; \
    else \
        userdel -r ubuntu 2>/dev/null || true; \
        useradd -m -s /bin/bash -u 1000 ${CONTAINER_USER}; \
    fi \
    && echo "${CONTAINER_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${CONTAINER_USER}

# Copy entrypoint and configuration files
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY checkIsolation.sh /home/${CONTAINER_USER}/checkIsolation.sh

RUN mkdir -p /etc/vaibify
# Config files generated by 'vaibify build' into this context
COPY container.conf /etc/vaibify/container.conf
COPY binaries.env /etc/vaibify/binaries.env
COPY director.py /usr/share/vaibify/director.py
COPY overleafSync.py /usr/share/vaibify/overleafSync.py
COPY latexConnector.py /usr/share/vaibify/latexConnector.py
COPY zenodoClient.py /usr/share/vaibify/zenodoClient.py

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && chmod +x /home/${CONTAINER_USER}/checkIsolation.sh \
    && chown ${CONTAINER_USER}:${CONTAINER_USER} /home/${CONTAINER_USER}/checkIsolation.sh \
    && git config --system advice.detachedHead false \
    && git config --system --add safe.directory '*' \
    && mkdir -p ${WORKSPACE_ROOT} \
    && chown ${CONTAINER_USER}:${CONTAINER_USER} ${WORKSPACE_ROOT} \
    && mkdir -p /home/${CONTAINER_USER}/.config/python_keyring \
    && printf '[backend]\ndefault-keyring=keyrings.alt.file.PlaintextKeyring\n' \
        > /home/${CONTAINER_USER}/.config/python_keyring/keyringrc.cfg \
    && chown -R ${CONTAINER_USER}:${CONTAINER_USER} \
        /home/${CONTAINER_USER}/.config \
    && chmod 700 /home/${CONTAINER_USER}/.config/python_keyring \
    && chmod 600 /home/${CONTAINER_USER}/.config/python_keyring/keyringrc.cfg \
    && mkdir -p /home/${CONTAINER_USER}/.local/share/python_keyring \
    && chown -R ${CONTAINER_USER}:${CONTAINER_USER} \
        /home/${CONTAINER_USER}/.local \
    && chmod 700 /home/${CONTAINER_USER}/.local/share/python_keyring

ENV GIT_EDITOR=nano
ENV WORKSPACE=${WORKSPACE_ROOT}
ENV VC_PROJECT_NAME=${VC_PROJECT_NAME}
ENV CONTAINER_USER=${CONTAINER_USER}
ENV PACKAGE_MANAGER=${PACKAGE_MANAGER}

WORKDIR ${WORKSPACE_ROOT}

LABEL devcontainer.metadata="[{\"workspaceFolder\": \"${WORKSPACE_ROOT}\", \"remoteUser\": \"${CONTAINER_USER}\", \"extensions\": [\"ms-python.python\", \"ms-vscode.cpptools\"]}]"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
