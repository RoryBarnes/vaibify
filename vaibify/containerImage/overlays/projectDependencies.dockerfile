# Vaibify — every project's own declared packages
#
# A container can hold several projects, and each declares what it
# needs in its own repository (pyproject.toml, requirements.in or
# requirements.txt). The build reads each declaration at one commit
# (vaibify/cli/projectDeclarations.py) and stages it in the context as
# project-requirements/<repository>.txt, whose first line names the
# file and the commit, beside an index.json the container's start reads.
#
# ONE pip install resolves the image's own list, every feature's list
# and every project's together. Installed one after another, a later
# list would silently replace a version an earlier one needed; resolved
# together, two declarations no single version satisfies stop the build,
# and the lines after pip's refusal name which source declared each
# side. pip check then refuses an installed set that is inconsistent.
#
# This stage sits below the first coding agent, inside the environment
# a result is computed in, because the Level 3 shadow and reproduce.sh
# run the image with the container's start bypassed: a package that
# arrived only at start is one they would never see.

ARG BASE_IMAGE=vaibify:latest
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The base image ends as the unprivileged container user; installing
# system-wide needs root, and the last line hands the image back.
USER root

COPY project-requirements/ /etc/vaibify/projectRequirements/
COPY pip-flags.txt /tmp/pip-flags.txt

RUN chmod -R a+rX /etc/vaibify/projectRequirements \
    && saSources=(/etc/vaibify/requirements.txt) \
    && for sFile in /etc/vaibify/requirements.d/*.txt \
            /etc/vaibify/projectRequirements/*.txt; do \
        if [ -f "${sFile}" ]; then saSources+=("${sFile}"); fi; \
    done \
    && saArguments=() \
    && for sFile in "${saSources[@]}"; do saArguments+=(-r "${sFile}"); done \
    && PIP_FLAGS="$(tr -d '\n' < /tmp/pip-flags.txt)" \
    && if ! pip install --no-cache-dir ${PIP_FLAGS} "${saArguments[@]}" \
            2>&1 | tee /tmp/pip-install.log; then \
        sed -n 's/^ *The user requested //p' /tmp/pip-install.log | sort -u \
        | while read -r sRequested; do \
            for sFile in "${saSources[@]}"; do \
                case "${sFile}" in \
                    /etc/vaibify/requirements.txt) \
                        sSource="pythonPackages in vaibify.yml" ;; \
                    /etc/vaibify/requirements.d/*) \
                        sSource="the $(basename "${sFile}" .txt) feature" ;; \
                    *) sSource="project $(basename "${sFile}" .txt)" ;; \
                esac; \
                if grep -qixF -- "${sRequested}" "${sFile}"; then \
                    echo "vaibify: '${sRequested}' is declared by ${sSource}"; \
                fi; \
            done; \
        done; \
        exit 1; \
    fi \
    && pip check \
    && rm -f /tmp/pip-flags.txt /tmp/pip-install.log

USER ${CONTAINER_USER}
