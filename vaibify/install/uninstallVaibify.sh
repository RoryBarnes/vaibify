#!/bin/sh
# uninstallVaibify.sh - Remove Vaibify and its Docker resources.
#
# Removes the Docker image, volume, and container created by Vaibify,
# the symlinks and PATH entries created by installVaibify.sh, and the
# .claude_enabled marker if present. Does not uninstall Docker, Colima, or
# the GitHub CLI.
#
# Usage:
#   sh uninstallVaibify.sh

set -e

# ---------------------------------------------------------------------------
fnPrintError() { echo "ERROR: $1" >&2; }

# ---------------------------------------------------------------------------
# fbConfirmAction: Prompt the user for yes/no confirmation
# Arguments: sPrompt
# Returns: 0 if confirmed, 1 otherwise
# ---------------------------------------------------------------------------
fbConfirmAction() {
    local sPrompt="$1"
    printf "%s [y/N] " "${sPrompt}"
    local sAnswer
    read -r sAnswer
    case "${sAnswer}" in
        [Yy]) return 0 ;;
        *)    return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# fsReadProjectName: Read the project name from vaibify.yml if present
# Prints: the project name, or "vaibify" as fallback
# ---------------------------------------------------------------------------
fsReadProjectName() {
    local sName
    sName=$(python3 -c \
        "import yaml; print(yaml.safe_load(open('vaibify.yml'))['projectName'])" \
        2>/dev/null || true)
    if [ -z "${sName}" ]; then
        sName="vaibify"
    fi
    echo "${sName}"
}

# ---------------------------------------------------------------------------
# fnStopContainer: Stop any running Vaibify
# ---------------------------------------------------------------------------
fnStopContainer() {
    local sContainerName
    sContainerName=$(fsReadProjectName)
    if command -v docker > /dev/null 2>&1 \
            && docker container inspect "${sContainerName}" > /dev/null 2>&1; then
        echo "[uninstall] Stopping Vaibify '${sContainerName}'..."
        docker stop "${sContainerName}" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# fnRemoveImage: Remove the Vaibify Docker image
# ---------------------------------------------------------------------------
fnRemoveImage() {
    if ! command -v docker > /dev/null 2>&1; then
        echo "[uninstall] Docker not found. Skipping image removal."
        return
    fi
    local sContainerName
    sContainerName=$(fsReadProjectName)
    if docker image inspect "${sContainerName}:latest" > /dev/null 2>&1; then
        echo "[uninstall] Removing Docker image ${sContainerName}:latest..."
        docker rmi "${sContainerName}:latest" 2>/dev/null || true
    else
        echo "[uninstall] Docker image ${sContainerName}:latest not found."
    fi
}

# ---------------------------------------------------------------------------
# fnRemoveVolume: Remove the workspace volume (requires confirmation)
# ---------------------------------------------------------------------------
fnRemoveVolume() {
    if ! command -v docker > /dev/null 2>&1; then
        return
    fi
    local sContainerName
    sContainerName=$(fsReadProjectName)
    local sVolumeName="${sContainerName}-workspace"
    if ! docker volume inspect "${sVolumeName}" > /dev/null 2>&1; then
        echo "[uninstall] Workspace volume not found."
        return
    fi
    echo ""
    echo "WARNING: The workspace volume contains all cloned repositories,"
    echo "local commits, and branch checkouts."
    if fbConfirmAction "Delete the ${sVolumeName} volume?"; then
        docker volume rm "${sVolumeName}" 2>/dev/null || true
        echo "[uninstall] Workspace volume removed."
    else
        echo "[uninstall] Keeping workspace volume."
    fi
}

# ---------------------------------------------------------------------------
# fnRemoveSymlinks: Remove vaibify and vc symlinks from bin directories
# ---------------------------------------------------------------------------
fnRemoveSymlinks() {
    for sCommand in vaibify vc; do
        for sPath in /opt/local/bin/${sCommand} /usr/local/bin/${sCommand}; do
            if [ -L "${sPath}" ]; then
                echo "[uninstall] Removing symlink ${sPath}..."
                sudo rm -f "${sPath}"
            fi
        done

        if command -v brew > /dev/null 2>&1; then
            local sBrewLink="$(brew --prefix)/bin/${sCommand}"
            if [ -L "${sBrewLink}" ]; then
                echo "[uninstall] Removing symlink ${sBrewLink}..."
                sudo rm -f "${sBrewLink}"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# fnRemoveVcLinesFromFile: Strip Vaibify-added lines from an RC file
# Arguments: sFile
# ---------------------------------------------------------------------------
fnRemoveVcLinesFromFile() {
    local sFile="$1"
    if ! grep -q "Added by Vaibify installer" "${sFile}" 2>/dev/null; then
        return
    fi
    local sTempFile="${sFile}.vc_uninstall_tmp"
    { grep -v "Added by Vaibify installer" "${sFile}" \
        | grep -v "/Vaibify/bin" \
        | grep -v "/Vaibify/completions/"; } > "${sTempFile}" || true
    mv "${sTempFile}" "${sFile}"
    echo "[uninstall] Removed Vaibify PATH entry from ${sFile}."
}

# ---------------------------------------------------------------------------
# fnRemovePathEntry: Remove Vaibify PATH lines from shell configs
# ---------------------------------------------------------------------------
fnRemovePathEntry() {
    for sFile in \
        "${HOME}/.zshrc" \
        "${HOME}/.bashrc" \
        "${HOME}/.bash_profile" \
        "${HOME}/.profile" \
        "${HOME}/.config/fish/config.fish"
    do
        if [ -f "${sFile}" ]; then
            fnRemoveVcLinesFromFile "${sFile}"
        fi
    done
}

# ---------------------------------------------------------------------------
# fnRemoveClaudeMarker: Remove the .claude_enabled marker file
# ---------------------------------------------------------------------------
fnRemoveClaudeMarker() {
    local sMarkerDir="$1"
    if [ -f "${sMarkerDir}/.claude_enabled" ]; then
        rm -f "${sMarkerDir}/.claude_enabled"
        echo "[uninstall] Removed Claude Code marker."
    fi
}

# ===========================================================================
# Main -- only runs when executed directly (not when sourced by tests)
# ===========================================================================
if [ -n "${VC_TESTING:-}" ]; then
    # shellcheck disable=SC2317
    return 0 2>/dev/null || true
fi

echo "=========================================="
echo "  Vaibify Uninstaller"
echo "=========================================="
echo ""
echo "This will remove:"
echo "  - The Vaibify Docker image"
echo "  - The Vaibify workspace volume (with confirmation)"
echo "  - The vaibify and vc symlinks"
echo "  - Vaibify PATH entries from shell configuration"
echo ""
echo "This will NOT remove Docker, Colima, gh, or the Vaibify repository."
echo ""

if ! fbConfirmAction "Proceed with uninstall?"; then
    echo "[uninstall] Cancelled."
    exit 0
fi

echo ""
fnStopContainer
fnRemoveImage
fnRemoveVolume
fnRemoveSymlinks
fnRemovePathEntry
sRepoDir="$(cd "$(dirname "$0")" && pwd)"
fnRemoveClaudeMarker "${sRepoDir}"

echo ""
echo "[uninstall] Vaibify has been uninstalled."
echo "[uninstall] To remove the Vaibify repository, delete this directory:"
echo "  rm -rf ${sRepoDir}"
