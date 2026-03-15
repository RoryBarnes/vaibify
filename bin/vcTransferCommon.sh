#!/bin/sh
# vcTransferCommon.sh - Shared functions for vc_push and vc_pull.
#
# Sourced by vc_push and vc_pull to avoid duplicating container
# verification, path resolution, and user confirmation logic.
# Reads VC_CONTAINER and VC_WORKSPACE from vaibcask.yml in the
# current directory.

# ---------------------------------------------------------------------------
# fnReadContainerConfig: Populate VC_CONTAINER and VC_WORKSPACE from YAML
# ---------------------------------------------------------------------------
fnReadContainerConfig() {
    VC_CONTAINER=$(python3 -c \
        "import yaml; print(yaml.safe_load(open('vaibcask.yml'))['projectName'])" \
        2>/dev/null || true)
    VC_WORKSPACE=$(python3 -c \
        "import yaml; print(yaml.safe_load(open('vaibcask.yml')).get('workspaceRoot','/workspace'))" \
        2>/dev/null || true)

    if [ -z "${VC_CONTAINER}" ]; then
        fnPrintError "No vaibcask.yml found in current directory."
        exit 1
    fi
    if [ -z "${VC_WORKSPACE}" ]; then
        VC_WORKSPACE="/workspace"
    fi
}

# ---------------------------------------------------------------------------
fnPrintError() { echo "Error: $1" >&2; }

# ---------------------------------------------------------------------------
# fnCheckContainer: Verify the VaibCask is running
# ---------------------------------------------------------------------------
fnCheckContainer() {
    if ! command -v docker > /dev/null 2>&1; then
        fnPrintError "docker not found on PATH."
        exit 1
    fi
    fnReadContainerConfig
    if ! docker container inspect "${VC_CONTAINER}" > /dev/null 2>&1; then
        fnPrintError "VaibCask '${VC_CONTAINER}' is not running. Start it with: vaibcask start"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# fsResolveContainerPath: Map a user-facing path to a workspace-absolute path
# Arguments: sRelativePath
# Prints: the resolved absolute path inside the container
# ---------------------------------------------------------------------------
fsResolveContainerPath() {
    case "$1" in
        /*) echo "$1" ;;
        .)  echo "${VC_WORKSPACE}" ;;
        *)  echo "${VC_WORKSPACE}/$1" ;;
    esac
}

# ---------------------------------------------------------------------------
# fbConfirmRecursive: Prompt the user before copying a directory
# Arguments: sDisplayPath
# Returns: 0 if confirmed, 1 otherwise
# ---------------------------------------------------------------------------
fbConfirmRecursive() {
    printf "'%s' is a directory. Copy recursively? [y/N] " "$1"
    local sAnswer
    read -r sAnswer
    case "${sAnswer}" in
        [Yy]) return 0 ;;
        *)    return 1 ;;
    esac
}
