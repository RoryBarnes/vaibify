# Vaibify — Parameterized base image for scientific computing containers
#
# Build ARGs allow projects to customize the image without editing this file.
# Application-specific packages belong in a config-generated requirements.txt.

# The base image is pinned by multi-arch manifest-list digest so a
# rebuild months later cannot change underneath an unchanged recipe.
# This is build STABILITY, not result reproducibility: reproduce.sh
# never rebuilds this file -- it pulls the exact image digest recorded
# in the envelope (.vaibify/environment.json) at capture time. Bump the
# digest deliberately when rebuilding for new features so the image
# picks up upstream security fixes; refresh the digest for a tag with:
#   docker pull ubuntu:24.04 \
#     && docker image inspect --format '{{index .RepoDigests 0}}' ubuntu:24.04
ARG BASE_IMAGE=ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90
FROM ${BASE_IMAGE}

ARG PYTHON_VERSION=3.12
ARG CONTAINER_USER=researcher
ARG WORKSPACE_ROOT=/workspace
ARG INSTALL_LATEX=true
ARG INSTALL_X11=true
ARG PACKAGE_MANAGER=pip
ARG VC_PROJECT_NAME=Vaibify

# Declared so a build can be given a fixed timestamp base, and so this
# file satisfies vaibify's own L3 lint rather than being copied into a
# researcher's repo carrying a gap vaibify shipped. The default is the
# reproducible-builds convention; override it at build time
# (--build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)) to stamp
# a build against a commit. Declaring it does not by itself make every
# layer timestamp-deterministic -- apt and pip largely ignore it --
# which is the same limit the apt waiver above describes.
ARG SOURCE_DATE_EPOCH=0

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

# APT VERSION PINS ARE WAIVED IN THIS IMAGE, DELIBERATELY, and each
# apt block below carries a "# allow-unpinned" marker saying so to
# vaibify's own L3 Dockerfile lint. The waiver is recorded here rather
# than injected when the file is copied into a researcher's repo,
# because it is a claim about THIS image and so is vaibify's to make
# and to be audited on -- generating it at copy time would be vaibify
# silently waiving a check on the researcher's behalf.
#
# What the waiver costs, stated plainly: `apt-get install` resolves
# against live Ubuntu archives, so rebuilding from this file will not
# reproduce the image byte-for-byte, and the archives eventually drop
# the versions a rebuild would need. Pinning all ~58 packages would
# claim otherwise while breaking on every archive update, which trades
# an honest limit for a brittle fiction.
#
# The reproducibility claim does not rest on a rebuild. Level 3's
# recipe is `reproduce.sh`, which runs `docker pull` against the image
# DIGEST recorded in .vaibify/environment.json -- it never builds. So
# this file documents provenance ("how the image was made"), while the
# digest carries reproduction ("the exact image, byte-for-byte"). To
# tighten it, pin the packages here and drop the markers; nothing
# downstream assumes the waiver.
#
# Generic build tools required by the base image. Kept deliberately
# small: anything needed by only one feature belongs in that feature's
# overlay, not here. gfortran, cmake and LAPACK/BLAS used to sit in
# this list to serve a MultiNest build that every image ran whether or
# not the project used nested sampling; they moved to
# overlays/nestedSampling.dockerfile on 2026-07-27 along with it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    curl \
    ca-certificates \
    gnupg \
    software-properties-common \
    gosu \
    nano \
    vim \
    pkg-config \
    graphviz \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*  # allow-unpinned

# THE COMPILER TOOLCHAIN IS PINNED, and deliberately NOT waived: it is
# the one apt block above whose contents can change a NUMBER. A
# scientific binary compiled by a different gcc is a different binary,
# and may produce different results at the last few significant
# figures. Everything in the waived block above (editors, viewers,
# graphviz) cannot reach a result, so its versions float; these
# cannot, so they do not. Adding an "allow-unpinned" marker to this
# block would silently reopen exactly that gap.
#
# gcc AND gcc-13 are both pinned because pinning "gcc" alone does not
# pin the compiler. `gcc` is a metapackage (4:13.2.0-7ubuntu1) that
# depends on `gcc-13 (>= ...)`, so a rebuild could satisfy the
# metapackage pin and still install a newer gcc-13. The version that
# appears in `gcc --version`, and in .vaibify/environment.json's
# dictSystemTools.sGcc, is gcc-13's.
#
# THE WHOLE CLOSURE IS PINNED, not just the compiler. All 45 packages
# that `apt-get install --no-install-recommends gcc g++ make` resolves
# on top of the pinned base are listed, including the assembler and
# linker (binutils), the C library and its headers (libc6, libc6-dev,
# linux-libc-dev), gcc's internal arbitrary-precision libraries
# (libisl23, libmpc3, libmpfr6 -- these participate in constant
# folding, so they can change emitted values) and the runtime
# libraries. An earlier revision pinned only gcc/g++/make and called
# the rest a maintenance battle; that was hand-waving. The closure is
# finite, it is 45 lines, and a partial pin is the worst of the three
# options -- it can also make apt UNSATISFIABLE, because a pinned
# libc6-dev whose libc6 has moved on is a conflict rather than a
# resolution.
#
# Regenerate the list after any base-image bump with:
#   docker run --rm <BASE_IMAGE> sh -c 'apt-get update -qq >/dev/null
#     && apt-get install -s -y --no-install-recommends gcc g++ make
#     | grep "^Inst "'
# taking the version in PARENTHESES, not the one in brackets: an
# upgrade line reads `Inst libc6 [old] (new ...)`, and reading the
# bracketed field pins the version being replaced. libc6 and libc-bin
# are upgrades from the base image, so they are exactly the two this
# would silently get wrong.
#
# The `-x86-64-linux-gnu` names are safe to pin because the base image
# digest above resolves to a single-architecture linux/amd64 image
# (verified: `docker image inspect` reports amd64), not a manifest
# list. Repointing BASE_IMAGE at another architecture requires
# regenerating this whole list, and the diagnostic below says so.
#
# Still NOT pinned, and named so the boundary is stated rather than
# implied: anything already present in the base image and not upgraded
# here. Those are fixed by the base image digest, which is the
# stronger guarantee -- they cannot float while the digest holds.
#
# WHEN THIS BLOCK FAILS TO BUILD, THAT IS THE FEATURE. Ubuntu drops
# superseded versions from the archive pool within weeks or months, so
# this pin eventually becomes unsatisfiable and the build stops. That
# is the intended behaviour: the alternative is a rebuild that quietly
# swaps the compiler underneath a researcher who believes they
# reproduced something. The diagnostic below tells them what happened
# and what their options are; see docs/reproducibility.md, "When a
# pinned toolchain version disappears".
RUN apt-get update \
    && if ! apt-get install -y --no-install-recommends \
            binutils=2.42-4ubuntu2.10 \
            binutils-common=2.42-4ubuntu2.10 \
            binutils-x86-64-linux-gnu=2.42-4ubuntu2.10 \
            cpp=4:13.2.0-7ubuntu1 \
            cpp-13=13.3.0-6ubuntu2~24.04.1 \
            cpp-13-x86-64-linux-gnu=13.3.0-6ubuntu2~24.04.1 \
            cpp-x86-64-linux-gnu=4:13.2.0-7ubuntu1 \
            g++=4:13.2.0-7ubuntu1 \
            g++-13=13.3.0-6ubuntu2~24.04.1 \
            g++-13-x86-64-linux-gnu=13.3.0-6ubuntu2~24.04.1 \
            g++-x86-64-linux-gnu=4:13.2.0-7ubuntu1 \
            gcc=4:13.2.0-7ubuntu1 \
            gcc-13=13.3.0-6ubuntu2~24.04.1 \
            gcc-13-base=13.3.0-6ubuntu2~24.04.1 \
            gcc-13-x86-64-linux-gnu=13.3.0-6ubuntu2~24.04.1 \
            gcc-x86-64-linux-gnu=4:13.2.0-7ubuntu1 \
            libasan8=14.2.0-4ubuntu2~24.04.1 \
            libatomic1=14.2.0-4ubuntu2~24.04.1 \
            libbinutils=2.42-4ubuntu2.10 \
            libc-bin=2.39-0ubuntu8.8 \
            libc-dev-bin=2.39-0ubuntu8.8 \
            libc6=2.39-0ubuntu8.8 \
            libc6-dev=2.39-0ubuntu8.8 \
            libcc1-0=14.2.0-4ubuntu2~24.04.1 \
            libcrypt-dev=1:4.4.36-4build1 \
            libctf-nobfd0=2.42-4ubuntu2.10 \
            libctf0=2.42-4ubuntu2.10 \
            libgcc-13-dev=13.3.0-6ubuntu2~24.04.1 \
            libgomp1=14.2.0-4ubuntu2~24.04.1 \
            libgprofng0=2.42-4ubuntu2.10 \
            libhwasan0=14.2.0-4ubuntu2~24.04.1 \
            libisl23=0.26-3build1.1 \
            libitm1=14.2.0-4ubuntu2~24.04.1 \
            libjansson4=2.14-2build2 \
            liblsan0=14.2.0-4ubuntu2~24.04.1 \
            libmpc3=1.3.1-1build1.1 \
            libmpfr6=4.2.1-1build1.1 \
            libquadmath0=14.2.0-4ubuntu2~24.04.1 \
            libsframe1=2.42-4ubuntu2.10 \
            libstdc++-13-dev=13.3.0-6ubuntu2~24.04.1 \
            libtsan2=14.2.0-4ubuntu2~24.04.1 \
            libubsan1=14.2.0-4ubuntu2~24.04.1 \
            linux-libc-dev=6.8.0-138.138 \
            make=4.3-4.1build2 \
            rpcsvc-proto=1.4.2-0ubuntu7; then \
        printf '\n%s\n' \
            "=============================================================" >&2; \
        printf '%s\n' \
            "vaibify: the pinned compiler toolchain is no longer available." >&2; \
        printf '%s\n' \
            "" >&2; \
        printf '%s\n' \
            "Ubuntu has removed one of the 45 pinned toolchain versions" >&2; \
        printf '%s\n' \
            "from its archive. apt names the offender directly above this" >&2; \
        printf '%s\n' \
            "banner ('Version ... was not found')." >&2; \
        printf '%s\n' \
            "" >&2; \
        printf '%s\n' \
            "(If you repointed BASE_IMAGE at a different ARCHITECTURE, the" >&2; \
        printf '%s\n' \
            " cause is different: the '-x86-64-linux-gnu' package names do" >&2; \
        printf '%s\n' \
            " not exist there. Regenerate the whole pin list for that arch.)" >&2; \
        printf '%s\n' \
            "" >&2; \
        printf '%s\n' \
            "This build stopped on purpose. The compiler is an INPUT to your" >&2; \
        printf '%s\n' \
            "results: code compiled by a different gcc can differ in the last" >&2; \
        printf '%s\n' \
            "significant figures. Rather than swap it silently, vaibify stops" >&2; \
        printf '%s\n' \
            "and asks you to choose:" >&2; \
        printf '%s\n' \
            "" >&2; \
        printf '%s\n' \
            "  1. REPRODUCE THE ORIGINAL. Do not rebuild. Pull the published" >&2; \
        printf '%s\n' \
            "     image by digest (.vaibify/environment.json ->" >&2; \
        printf '%s\n' \
            "     dictContainer.sImageDigest); reproduce.sh already does this." >&2; \
        printf '%s\n' \
            "     The original toolchain is inside that image." >&2; \
        printf '%s\n' \
            "  2. ACCEPT A NEWER TOOLCHAIN. Update the pins in this block to" >&2; \
        printf '%s\n' \
            "     the versions listed below, then RE-RUN AND RE-VERIFY: your" >&2; \
        printf '%s\n' \
            "     outputs may legitimately change, and the manifest hashes" >&2; \
        printf '%s\n' \
            "     will say so." >&2; \
        printf '%s\n' \
            "  3. FETCH THE OLD PACKAGES. snapshot.ubuntu.com serves the" >&2; \
        printf '%s\n' \
            "     archive as it stood on a given date; point apt at the" >&2; \
        printf '%s\n' \
            "     snapshot covering this image's build date and keep the pins." >&2; \
        printf '%s\n' \
            "" >&2; \
        printf '%s\n' \
            "Currently available in this archive:" >&2; \
        apt-cache policy gcc gcc-13 g++ g++-13 make >&2 || true; \
        printf '%s\n' \
            "=============================================================" >&2; \
        exit 1; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Project-specific system packages from vaibify.yml systemPackages
COPY system-packages.txt /tmp/system-packages.txt
RUN if [ -s /tmp/system-packages.txt ]; then \
        apt-get update \
        && xargs -a /tmp/system-packages.txt \
           apt-get install -y --no-install-recommends \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && rm -f /tmp/system-packages.txt  # allow-unpinned

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
    fi  # allow-unpinned

# Conditional LaTeX for matplotlib tex rendering
RUN if [ "${INSTALL_LATEX}" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            texlive-latex-base \
            texlive-latex-extra \
            texlive-fonts-recommended \
            cm-super \
            dvipng \
        && rm -rf /var/lib/apt/lists/*; \
    fi  # allow-unpinned

# Note:
# When adding network-dependent RUN steps to this or overlay
# Dockerfiles, prefer curl with --retry, --connect-timeout, and
# --fail; on failure, emit a diagnostic via `echo` to stderr and
# exit 1 with an actionable message. See the deadsnakes block
# below for the exact template.

# Python: prefer the base image's system repos, but fall back to the
# deadsnakes PPA when the requested version is either absent OR only
# available as a pre-release. Ubuntu 22.04 predates Python 3.11 final,
# so its archive ships python3.11 as 3.11.0~rc1 — a release candidate
# that modern build backends reject ("requires Python >=3.11, running
# on 3.11.0rc1"). deadsnakes carries a stable build (3.11.15) for such
# cases. The default config (ubuntu:24.04 + Python 3.12) ships a stable
# release, so it keeps its no-launchpad.net path. When the PPA is needed
# and unreachable (TLS / proxy / network), exit with an actionable
# diagnostic instead of an opaque SSLEOFError traceback.
ARG BASE_IMAGE
RUN apt-get update \
    && PYTHON_CANDIDATE="$(apt-cache policy python${PYTHON_VERSION} 2>/dev/null | awk '/Candidate:/{print $2}')" \
    && if [ -z "${PYTHON_CANDIDATE}" ] || [ "${PYTHON_CANDIDATE}" = "(none)" ] \
          || printf '%s' "${PYTHON_CANDIDATE}" | grep -Eq '~(rc|alpha|beta|a[0-9]|b[0-9])'; then \
           if ! add-apt-repository -y ppa:deadsnakes/ppa; then \
               echo "" >&2; \
               printf '%s\n' "vaibify build: cannot install Python ${PYTHON_VERSION}." >&2; \
               printf '%s\n' "  - It is absent, or only a pre-release, in" >&2; \
               printf '%s\n' "    ${BASE_IMAGE}'s default apt archive." >&2; \
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
    && rm -rf /var/lib/apt/lists/*  # allow-unpinned

# Make the selected Python version the default and install pip.
# Upgrade pip itself, not just setuptools: distro-stock pip on older
# bases (e.g. Ubuntu 22.04 ships pip 22.0.2) has a build-isolation bug
# that misplaces console scripts of build backends — a ``--no-binary``
# source build of any meson-python package (matplotlib, ...) then dies
# with ``meson executable "meson" not found``. A current pip resolves
# the build-env script path correctly, so every from-source install
# works regardless of the base image's pip vintage.
#
# ``--ignore-installed`` is load-bearing, not belt-and-braces. Without
# it, pip tries to UNINSTALL the distro pip first and dies with
# "Cannot uninstall pip 24.0, RECORD file not found. Hint: the package
# was installed by debian" -- a Debian-packaged pip carries no RECORD,
# so pip cannot remove it. Installing over the top instead is the
# supported route. This surfaced the first time the image was ever
# built from scratch in CI; every earlier build reused a cached layer.
#
# pip and setuptools are pinned to versions verified importable in a
# built image (commit 47a8b460) so "latest from PyPI" cannot break a
# rebuild of an unchanged recipe. Bump them alongside the BASE_IMAGE
# digest above.
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python${PYTHON_VERSION} 1 \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3-pip \
    && rm -f /usr/lib/python*/EXTERNALLY-MANAGED \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --ignore-installed \
        'pip==26.1.2' 'setuptools==83.0.0'  # allow-unpinned

# Keyring backend for in-container credential storage.
# keyrings.alt provides a file-based PlaintextKeyring that works in
# headless containers where the default SecretService backend is
# unavailable and where EncryptedKeyring's interactive master-password
# prompt cannot run. The container is ephemeral and the credential
# file is chmod 600; nothing leaks to the host. PYTHON_KEYRING_BACKEND
# pins the backend for every user (including root), which is what
# vaibify's docker exec defaults to; the keyringrc.cfg written later
# is a belt-and-suspenders backup for interactive shells that read it.
# requests rides along because it is a VAIBIFY dependency, not a
# project one: the in-container Zenodo token validation and the staged
# zenodoClient.py both import it. Without this explicit install it was
# only present when the researcher's own requirements happened to pull
# it in, so a project with no requests dependency could not validate a
# Zenodo token or publish a deposit — and the failure surfaced as
# "token not accepted".
RUN pip install --no-cache-dir keyring keyrings.alt requests
ENV PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring

# Mutation-testing engine for the per-step falsification attestation
# (the dashboard's "Check test teeth" action). Installed at the
# baseline so every project container can grade its quantitative
# tests' fault-detection sensitivity without per-project
# configuration. Runs as the unprivileged container user against
# files inside the workspace volume; adds no network service.
RUN pip install --no-cache-dir cosmic-ray

# claude-monitor: the session-usage instrument behind the
# session-budget agent skill. Parses the LOCAL Claude Code
# transcripts only (an account-wide lower bound, never an exact
# gauge — the skill documents this caveat); pure Python, no Node
# runtime, no network service.
RUN pip install --no-cache-dir claude-monitor

# Nested sampling (MultiNest, ultranest, pymultinest) is NOT installed
# here. It is a domain-specific dependency and lives in
# overlays/nestedSampling.dockerfile behind the bNestedSampling feature
# flag, together with the gfortran/cmake/LAPACK/BLAS toolchain that
# exists only to build it.

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

# Console scripts from ``pip install`` land in the user's
# ``~/.local/bin`` on PEP 668 (externally-managed) bases such as
# Ubuntu 24.04 — every ``pip_no_deps`` / ``pip_editable`` repo
# (vconverge, vspace, bigplanet, pytest, ...) installs there. The
# non-login ``bash -c`` used for ``docker exec`` does not source the
# profile that would add it, so both the preflight command check and
# the actual step run would report those tools "command not found".
# Put it on PATH for every dispatched command. (c_and_pip binaries
# like vplanet install to /usr/local/bin and are already covered.)
ENV PATH="/home/${CONTAINER_USER}/.local/bin:${PATH}"

# Project-specific Python packages from vaibify.yml pythonPackages.
#
# THE LIST IS KEPT IN THE IMAGE, at /etc/vaibify/requirements.txt, and
# that is load-bearing rather than housekeeping. vaibify.yml is the one
# place a researcher declares dependencies (2026-09-01 ruling); the
# entrypoint mirrors this file into each repository's
# .vaibify/requirements.txt, and PROOF Level 3 compiles
# requirements.lock from that mirror. Keeping the list means the image,
# the mirror and the lock agree BY CONSTRUCTION rather than by three
# people remembering to edit three files.
#
# What the previous shape cost: the list was deleted after install, so
# nothing in the image recorded what it had been built with, and the
# repository's .vaibify/requirements.txt was an INDEPENDENT declaration
# that the entrypoint pip-installed at container start. A package added
# there was present in the running container and in the lock, and
# absent from the image -- so it worked for the researcher and was
# missing from the image anyone else would pull.
COPY requirements.txt /tmp/requirements.txt
COPY pip-flags.txt /tmp/pip-flags.txt
RUN mkdir -p /etc/vaibify \
    && cp /tmp/requirements.txt /etc/vaibify/requirements.txt \
    && chmod 0644 /etc/vaibify/requirements.txt \
    && if [ -s /tmp/requirements.txt ]; then \
        PIP_FLAGS="$(cat /tmp/pip-flags.txt 2>/dev/null | tr -d '\n')" \
        && pip install --no-cache-dir ${PIP_FLAGS} -r /tmp/requirements.txt; \
    fi \
    && rm -f /tmp/requirements.txt /tmp/pip-flags.txt

# Create non-root user with UID 1000 (remove default ubuntu user if needed).
# No sudoers drop-in: the entrypoint runs as root to do the setup that
# legitimately needs root, then drops to ${CONTAINER_USER} via gosu before
# exec'ing the user command. Application code, ``docker exec`` sessions,
# and the in-container agent never need passwordless sudo.
RUN if id -u ${CONTAINER_USER} >/dev/null 2>&1; then \
        true; \
    else \
        userdel -r ubuntu 2>/dev/null || true; \
        useradd -m -s /bin/bash -u 1000 ${CONTAINER_USER}; \
    fi

# Copy entrypoint and configuration files
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY checkIsolation.sh /home/${CONTAINER_USER}/checkIsolation.sh
COPY vaibifyDo.py /usr/local/bin/vaibify-do

RUN mkdir -p /etc/vaibify
# Config files generated by 'vaibify build' into this context
COPY container.conf /etc/vaibify/container.conf
COPY binaries.env /etc/vaibify/binaries.env
COPY skills /usr/share/vaibify/skills
COPY docs-staged /usr/share/vaibify/docs
COPY overleafSync.py /usr/share/vaibify/overleafSync.py
COPY latexConnector.py /usr/share/vaibify/latexConnector.py
COPY zenodoClient.py /usr/share/vaibify/zenodoClient.py
COPY credentialRedactor.py /usr/share/vaibify/credentialRedactor.py

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && chmod 0755 /usr/local/bin/vaibify-do \
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

# Default identity for ``docker exec`` is the unprivileged container user.
# ``docker run`` invocations that need to execute the entrypoint as root
# (to chown the workspace and drop via gosu) pass ``--user 0`` explicitly.
USER ${CONTAINER_USER}

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
