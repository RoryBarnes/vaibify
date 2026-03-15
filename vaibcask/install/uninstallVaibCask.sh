#!/bin/sh
# uninstallVaibCask.sh - Remove VaibCask and its Docker resources.
#
# Removes the Docker image, volume, and container created by VaibCask,
# the symlinks and PATH entries created by installVaibCask.sh, and the
# .claude_enabled marker if present. Does not uninstall Docker, Colima, or
# the GitHub CLI.
#
# Usage:
#   sh uninstallVaibCask.sh

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
# fsReadProjectName: Read the project name from vaibcask.yml if present
# Prints: the project name, or "vaibcask" as fallback
# ---------------------------------------------------------------------------
fsReadProjectName() {
    local sName
    sName=$(python3 -c \
        "import yaml; print(yaml.safe_load(open('vaibcask.yml'))['projectName'])" \
        2>/dev/null || true)
    if [ -z "${sName}" ]; then
        sName="vaibcask"
    fi
    echo "${sName}"
}

# ---------------------------------------------------------------------------
# fnStopContainer: Stop any running VaibCask
# ---------------------------------------------------------------------------
fnStopContainer() {
    local sContainerName
    sContainerName=$(fsReadProjectName)
    if command -v docker > /dev/null 2>&1 \
            && docker container inspect "${sContainerName}" > /dev/null 2>&1; then
        echo "[uninstall] Stopping VaibCask '${sContainerName}'..."
        docker stop "${sContainerName}" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# fnRemoveImage: Remove the VaibCask Docker image
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
# fnRemoveSymlinks: Remove vaibcask and vc symlinks from bin directories
# ---------------------------------------------------------------------------
fnRemoveSymlinks() {
    for sCommand in vaibcask vc; do
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
# fnRemoveVcLinesFromFile: Strip VaibCask-added lines from an RC file
# Arguments: sFile
# ---------------------------------------------------------------------------
fnRemoveVcLinesFromFile() {
    local sFile="$1"
    if ! grep -q "Added by VaibCask installer" "${sFile}" 2>/dev/null; then
        return
    fi
    local sTempFile="${sFile}.vc_uninstall_tmp"
    { grep -v "Added by VaibCask installer" "${sFile}" \
        | grep -v "/VaibCask/bin" \
        | grep -v "/VaibCask/completions/"; } > "${sTempFile}" || true
    mv "${sTempFile}" "${sFile}"
    echo "[uninstall] Removed VaibCask PATH entry from ${sFile}."
}

# ---------------------------------------------------------------------------
# fnRemovePathEntry: Remove VaibCask PATH lines from shell configs
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
echo "  VaibCask Uninstaller"
echo "=========================================="
echo ""
echo "This will remove:"
echo "  - The VaibCask Docker image"
echo "  - The VaibCask workspace volume (with confirmation)"
echo "  - The vaibcask and vc symlinks"
echo "  - VaibCask PATH entries from shell configuration"
echo ""
echo "This will NOT remove Docker, Colima, gh, or the VaibCask repository."
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
echo "[uninstall] VaibCask has been uninstalled."
echo "[uninstall] To remove the VaibCask repository, delete this directory:"
echo "  rm -rf ${sRepoDir}"
