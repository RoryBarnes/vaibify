#!/bin/bash

WORKSPACE="${WORKSPACE:-/workspace}"
REPOS_CONF="${REPOS_CONF:-/etc/vaibify/container.conf}"
CONTAINER_USER="${CONTAINER_USER:-researcher}"
PACKAGE_MANAGER="${PACKAGE_MANAGER:-pip}"
VC_PROJECT_NAME="${VC_PROJECT_NAME:-Vaibify}"

# ---------------------------------------------------------------------------
# fnPrintBanner: Display startup header
# ---------------------------------------------------------------------------
fnPrintBanner() {
    echo "=========================================="
    echo "  Vaibify - ${VC_PROJECT_NAME}"
    echo "=========================================="
    echo ""
}

# ---------------------------------------------------------------------------
# fsReadGitHubToken: Find and return a GitHub token from secrets or gh CLI
# ---------------------------------------------------------------------------
fsReadGitHubToken() {
    local sTokenFile="/run/secrets/gh_token"
    if [ -f "${sTokenFile}" ]; then
        cat "${sTokenFile}"
        return
    fi
    if command -v gh > /dev/null 2>&1; then
        gh auth token 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# fnConfigureGit: Configure GitHub authentication via token
# ---------------------------------------------------------------------------
fnConfigureGit() {
    local sToken
    sToken=$(fsReadGitHubToken)
    if [ -n "${sToken}" ]; then
        echo "[vc] GitHub credentials detected."
        git config --system url."https://github.com/".insteadOf \
            "git@github.com:"
        git config --system credential.https://github.com.helper \
            "!f() { echo \"protocol=https\"; echo \"host=github.com\"; echo \"username=x-access-token\"; echo \"password=\$(cat /run/secrets/gh_token 2>/dev/null || gh auth token 2>/dev/null)\"; }; f"
    else
        echo "[vc] No GitHub credentials found. Public repos only."
        echo "[vc]   To access private repos, run on host: gh auth login"
        git config --system url."https://github.com/".insteadOf \
            "git@github.com:"
    fi
}

# ---------------------------------------------------------------------------
# fnParseReposConf: Read container.conf into parallel arrays
# ---------------------------------------------------------------------------
fnParseReposConf() {
    saRepoNames=()
    saRepoUrls=()
    saRepoBranches=()
    saRepoMethods=()

    if [ ! -f "${REPOS_CONF}" ]; then
        echo "[vc] No container.conf found at ${REPOS_CONF}. Skipping repo sync."
        return
    fi

    while IFS='|' read -r sName sUrl sBranch sMethod; do
        [[ "${sName}" =~ ^#.*$ ]] && continue
        [[ -z "${sName}" ]] && continue
        saRepoNames+=("${sName}")
        saRepoUrls+=("${sUrl}")
        saRepoBranches+=("${sBranch}")
        saRepoMethods+=("${sMethod}")
    done < "${REPOS_CONF}"
}

# ---------------------------------------------------------------------------
# fnCloneRepo: Clone a repository that does not yet exist locally
# Arguments: sName sUrl sBranch
# ---------------------------------------------------------------------------
fnCloneRepo() {
    local sName="$1"
    local sUrl="$2"
    local sBranch="$3"
    local sRepoPath="${WORKSPACE}/${sName}"

    echo "[vc] Cloning ${sName} (branch: ${sBranch})..."
    if ! git clone --branch "${sBranch}" "${sUrl}" "${sRepoPath}" 2>&1; then
        echo "[vc]   Clone failed for ${sName} (may require authentication)."
        return 0
    fi
    cd "${sRepoPath}"
    git fetch --tags origin
    cd "${WORKSPACE}"
}

# ---------------------------------------------------------------------------
# fnUpdateRepo: Pull latest changes for an existing repository
# Arguments: sName sBranch
# ---------------------------------------------------------------------------
fnUpdateRepo() {
    local sName="$1"
    local sBranch="$2"
    local sRepoPath="${WORKSPACE}/${sName}"

    echo "[vc] Updating ${sName}..."
    cd "${sRepoPath}"
    git fetch origin --tags 2>/dev/null || \
        echo "[vc]   Fetch skipped for ${sName} (may require authentication)."
    local sCurrentBranch
    sCurrentBranch=$(git rev-parse --abbrev-ref HEAD)
    if [ "${sCurrentBranch}" = "${sBranch}" ]; then
        git pull --ff-only origin "${sBranch}" 2>/dev/null || \
            echo "[vc]   Pull skipped for ${sName} (local changes or diverged)."
    else
        echo "[vc]   ${sName} on branch '${sCurrentBranch}', not '${sBranch}'. Skipping pull."
    fi
    cd "${WORKSPACE}"
}

# ---------------------------------------------------------------------------
# fnCloneOrPull: Clone a repo if absent, pull if present
# Arguments: sName sUrl sBranch
# ---------------------------------------------------------------------------
fnCloneOrPull() {
    local sName="$1"
    local sUrl="$2"
    local sBranch="$3"

    if [ ! -d "${WORKSPACE}/${sName}/.git" ]; then
        fnCloneRepo "${sName}" "${sUrl}" "${sBranch}"
    else
        fnUpdateRepo "${sName}" "${sBranch}"
    fi
}

# ---------------------------------------------------------------------------
# fnSyncAllRepos: Clone or pull every repo in container.conf
# ---------------------------------------------------------------------------
fnSyncAllRepos() {
    echo "[vc] Syncing repositories..."
    echo ""

    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        fnCloneOrPull "${saRepoNames[$i]}" "${saRepoUrls[$i]}" "${saRepoBranches[$i]}"
    done

    echo ""
    echo "[vc] All repositories synced."
}

# ---------------------------------------------------------------------------
# fnBuildBinaries: Compile native C binaries for repos using c_and_pip method
# ---------------------------------------------------------------------------
fnBuildBinaries() {
    echo "[vc] Building native binaries..."

    local iCount=${#saRepoNames[@]}
    local bBuiltAny=false
    for (( i=0; i<iCount; i++ )); do
        if [ "${saRepoMethods[$i]}" = "c_and_pip" ]; then
            local sName="${saRepoNames[$i]}"
            local sRepoPath="${WORKSPACE}/${sName}"

            if [ ! -d "${sRepoPath}" ]; then
                echo "[vc]   ${sName} not found. Skipping build."
                continue
            fi

            echo "[vc]   Building ${sName}..."
            cd "${sRepoPath}"
            if make opt; then
                local sBinaryPath="${sRepoPath}/bin/${sName}"
                if [ -x "${sBinaryPath}" ]; then
                    export PATH="${sRepoPath}/bin:${PATH}"
                    echo "[vc]   ${sName} binary ready: ${sBinaryPath}"
                    bBuiltAny=true
                else
                    echo "[vc]   WARNING: Expected binary not found at ${sBinaryPath}."
                fi
            else
                echo "[vc]   WARNING: Build failed for ${sName}. You can retry manually:"
                echo "[vc]     cd ${sRepoPath} && make opt"
            fi
            cd "${WORKSPACE}"
        fi
    done

    if [ "${bBuiltAny}" = false ]; then
        echo "[vc]   No C binaries to build."
    fi
}

# ---------------------------------------------------------------------------
# fnPipInstall: Run pip install with the given flags
# Arguments: sRepoPath sName [pip flags...]
# ---------------------------------------------------------------------------
fnPipInstall() {
    local sRepoPath="$1"
    local sName="$2"
    shift 2
    echo "[vc] Installing ${sName}..."
    pip install -e "${sRepoPath}" "$@" -q
}

# ---------------------------------------------------------------------------
# fnCondaInstall: Run conda/mamba install for a repo
# Arguments: sRepoPath sName
# ---------------------------------------------------------------------------
fnCondaInstall() {
    local sRepoPath="$1"
    local sName="$2"
    echo "[vc] Installing ${sName} via conda..."
    if command -v mamba > /dev/null 2>&1; then
        mamba install -y -c conda-forge "${sName}" 2>/dev/null || \
            pip install -e "${sRepoPath}" -q
    elif command -v conda > /dev/null 2>&1; then
        conda install -y -c conda-forge "${sName}" 2>/dev/null || \
            pip install -e "${sRepoPath}" -q
    else
        echo "[vc]   WARNING: conda/mamba not found. Falling back to pip."
        pip install -e "${sRepoPath}" -q
    fi
}

# ---------------------------------------------------------------------------
# fnInstallRepo: Install a single repo per its install method
# Arguments: sName sMethod
# ---------------------------------------------------------------------------
fnInstallRepo() {
    local sName="$1"
    local sMethod="$2"
    local sRepoPath="${WORKSPACE}/${sName}"

    if [ "${PACKAGE_MANAGER}" != "pip" ] && [ "${sMethod}" = "pip_editable" ]; then
        fnCondaInstall "${sRepoPath}" "${sName}"
        return
    fi

    case "${sMethod}" in
        c_and_pip|pip_no_deps)
            fnPipInstall "${sRepoPath}" "${sName}" --no-deps --no-build-isolation ;;
        pip_editable)
            fnPipInstall "${sRepoPath}" "${sName}" --no-build-isolation ;;
        scripts_only)
            echo "[vc] ${sName} available via PYTHONPATH and PATH." ;;
        reference)
            echo "[vc] ${sName} cloned for reference (not installed)." ;;
        *)
            echo "[vc] WARNING: Unknown install method '${sMethod}' for ${sName}." ;;
    esac
}

# ---------------------------------------------------------------------------
# fnInstallAllRepos: Install Python packages in dependency order
# ---------------------------------------------------------------------------
fnInstallAllRepos() {
    echo ""
    echo "[vc] Installing Python packages..."

    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        if [ -d "${WORKSPACE}/${saRepoNames[$i]}" ]; then
            fnInstallRepo "${saRepoNames[$i]}" "${saRepoMethods[$i]}"
        fi
    done

    echo ""
    echo "[vc] All packages installed."
}

# ---------------------------------------------------------------------------
# fnPersistGitConfig: Symlink .gitconfig to the workspace volume
# ---------------------------------------------------------------------------
fnPersistGitConfig() {
    local sVolumeConfig="${WORKSPACE}/.gitconfig"

    touch "${sVolumeConfig}"
    ln -sfn "${sVolumeConfig}" "/home/${CONTAINER_USER}/.gitconfig"
}

# ---------------------------------------------------------------------------
# fnSourceBinariesInBashrc: Ensure interactive shells load binary env vars
# ---------------------------------------------------------------------------
fnSourceBinariesInBashrc() {
    local sBashrc="/home/${CONTAINER_USER}/.bashrc"
    local sMarker="# vaibify-binaries"
    local sProfilePath="/etc/profile.d/vaibify-binaries.sh"
    if [ -f "${sProfilePath}" ] && ! grep -q "${sMarker}" "${sBashrc}" 2>/dev/null; then
        echo "${sMarker}" >> "${sBashrc}"
        echo "[ -f ${sProfilePath} ] && . ${sProfilePath}" >> "${sBashrc}"
    fi
}

# ---------------------------------------------------------------------------
# fnPersistClaudeConfig: Symlink Claude Code config to the workspace volume
# ---------------------------------------------------------------------------
fnPersistClaudeConfig() {
    mkdir -p "${WORKSPACE}/.claude"
    ln -sfn "${WORKSPACE}/.claude" "/home/${CONTAINER_USER}/.claude"
}

# ---------------------------------------------------------------------------
# fnLoadBinariesEnv: Export named binary variables and extend PATH
# ---------------------------------------------------------------------------
fnLoadBinariesEnv() {
    local sBinEnv="/etc/vaibify/binaries.env"
    if [ ! -s "${sBinEnv}" ]; then
        return
    fi

    echo "[vc] Loading binary environment from binaries.env..."
    local sProfilePath="/etc/profile.d/vaibify-binaries.sh"
    echo "# Generated by Vaibify entrypoint" > "${sProfilePath}"
    while IFS='=' read -r sVarName sBinPath; do
        [[ -z "${sVarName}" ]] && continue
        [[ "${sVarName}" =~ ^#.*$ ]] && continue
        export "${sVarName}=${sBinPath}"
        echo "export ${sVarName}=${sBinPath}" >> "${sProfilePath}"
        local sBinDir
        sBinDir=$(dirname "${sBinPath}")
        case ":${PATH}:" in
            *:"${sBinDir}":*) ;;
            *) export PATH="${sBinDir}:${PATH}"
               echo "export PATH=\"${sBinDir}:\${PATH}\"" >> "${sProfilePath}" ;;
        esac
        echo "[vc]   ${sVarName}=${sBinPath}"
    done < "${sBinEnv}"
    chmod 644 "${sProfilePath}"
}

# ---------------------------------------------------------------------------
# fnPrintBinarySummary: List all built binaries from c_and_pip repos
# ---------------------------------------------------------------------------
fnPrintBinarySummary() {
    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        if [ "${saRepoMethods[$i]}" = "c_and_pip" ]; then
            local sName="${saRepoNames[$i]}"
            local sBinaryPath="${WORKSPACE}/${sName}/bin/${sName}"
            if [ -x "${sBinaryPath}" ]; then
                echo "  ${sName}:    ${sBinaryPath}"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# fnPrintSummary: Display environment summary
# ---------------------------------------------------------------------------
fnPrintSummary() {
    echo ""
    echo "=========================================="
    echo "  Environment Ready"
    echo "=========================================="
    echo "  Python:    $(python --version 2>&1)"
    echo "  GCC:       $(gcc --version | head -1)"
    fnPrintBinarySummary
    echo "  Workspace: ${WORKSPACE}"
    if command -v node > /dev/null 2>&1; then
        echo "  Node.js:   $(node --version 2>&1)"
    fi
    if command -v claude > /dev/null 2>&1; then
        echo "  Claude:    $(claude --version 2>&1)"
    fi
    if command -v R > /dev/null 2>&1; then
        echo "  R:         $(R --version | head -1)"
    fi
    if command -v julia > /dev/null 2>&1; then
        echo "  Julia:     $(julia --version 2>&1)"
    fi
    echo "  Cores:     $(nproc)"
    echo "  Package Manager: ${PACKAGE_MANAGER}"
    echo "=========================================="
    echo ""
}

# ---------------------------------------------------------------------------
# fnCreateVaibifyDirectory: Create .vaibify structure in workspace
# ---------------------------------------------------------------------------
fnCreateVaibifyDirectory() {
    mkdir -p "${WORKSPACE}/.vaibify/workflows"
    mkdir -p "${WORKSPACE}/.vaibify/logs"
    if [ -f /usr/share/vaibify/director.py ]; then
        cp /usr/share/vaibify/director.py "${WORKSPACE}/.vaibify/director.py"
        chmod +x "${WORKSPACE}/.vaibify/director.py"
    fi
}

# ===========================================================================
# Main — only runs when executed directly (not when sourced by tests)
# ===========================================================================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail
    fnPrintBanner
    fnCreateVaibifyDirectory
    fnPersistGitConfig
    if command -v claude > /dev/null 2>&1; then
        fnPersistClaudeConfig
    fi
    fnConfigureGit
    fnParseReposConf
    fnSyncAllRepos
    fnBuildBinaries
    fnLoadBinariesEnv
    fnSourceBinariesInBashrc
    fnInstallAllRepos
    fnPrintSummary

    chown -R "${CONTAINER_USER}:${CONTAINER_USER}" "${WORKSPACE}"
    exec gosu "${CONTAINER_USER}" "$@"
fi
