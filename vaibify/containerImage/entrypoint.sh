#!/bin/bash

WORKSPACE="${WORKSPACE:-/workspace}"
REPOS_CONF="${REPOS_CONF:-/etc/vaibify/container.conf}"
CONTAINER_USER="${CONTAINER_USER:-researcher}"
PACKAGE_MANAGER="${PACKAGE_MANAGER:-pip}"
VC_PROJECT_NAME="${VC_PROJECT_NAME:-Vaibify}"
CRAFT_GUIDE_PATH="${CRAFT_GUIDE_PATH:-/usr/share/vaibify/craftGuide.md}"

# The composed agent context each provider's repo-root name points at,
# and the banner separating vaibify's shipped guidance from the
# researcher's own. The host composes the same file when the Project
# Context editor saves, so this banner is duplicated -- deliberately,
# the way introspectionScript.py duplicates dataLoaders.py, because a
# container script cannot import from the host. The counterpart is
# S_COMPOSED_CONTEXT_SEPARATOR in vaibify/gui/routes/replayRoutes.py;
# testComposedContextSeparatorMatchesTheHost fails if they drift.
COMPOSED_CONTEXT_BASENAME="agentContext.md"
COMPOSED_CONTEXT_SEPARATOR="

---

# Project Context (authored by the researcher)

The section below is the researcher's own standing instructions for
this repository, kept in \`.vaibify/AGENTS.md\`. Where it conflicts with
anything above, it wins. That file is the authoritative copy: if it has
been edited since this container started, read it directly."

# saStartupWarnings: each entry is "<name>: <category>: <one-line-reason>".
# Surfaced by the GUI readiness probe so the user can act on partial-startup
# failures without scrolling the container log.
saStartupWarnings=()

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
# fnAppendStartupWarning: Record a structured warning for the readiness marker
# Arguments: sName sCategory sReason
# ---------------------------------------------------------------------------
fnAppendStartupWarning() {
    local sName="$1"
    local sCategory="$2"
    local sReason="$3"
    saStartupWarnings+=("${sName}: ${sCategory}: ${sReason}")
}

# ---------------------------------------------------------------------------
# fsEscapeJsonString: Escape backslash, quote, and control chars for JSON
# Arguments: sRaw
# Strips every U+0000..U+001F control character (JSON forbids them
# unescaped) so a stray ANSI/control byte from git stderr cannot
# break the readiness-marker JSON the host parses.
# ---------------------------------------------------------------------------
fsEscapeJsonString() {
    local sRaw="$1"
    sRaw="${sRaw//\\/\\\\}"
    sRaw="${sRaw//\"/\\\"}"
    sRaw=$(printf '%s' "${sRaw}" | LC_ALL=C tr -d '\000-\037')
    printf '%s' "${sRaw}"
}

# ---------------------------------------------------------------------------
# fsBuildWarningsJson: Render saStartupWarnings as a JSON array literal
# ---------------------------------------------------------------------------
fsBuildWarningsJson() {
    local iCount=${#saStartupWarnings[@]}
    if [ "${iCount}" -eq 0 ]; then
        printf '[]'
        return
    fi
    local sBuffer="["
    local i
    for (( i=0; i<iCount; i++ )); do
        local sEscaped
        sEscaped=$(fsEscapeJsonString "${saStartupWarnings[$i]}")
        if [ "${i}" -gt 0 ]; then
            sBuffer+=", "
        fi
        sBuffer+="\"${sEscaped}\""
    done
    sBuffer+="]"
    printf '%s' "${sBuffer}"
}

# ---------------------------------------------------------------------------
# fnWriteReadinessMarker: Write the structured readiness JSON marker
# Arguments: sStatus sReason
# ---------------------------------------------------------------------------
S_ENTRYPOINT_VERSION="2"

fnWriteReadinessMarker() {
    local sStatus="$1"
    local sReason="$2"
    local sMarker="${WORKSPACE}/.vaibify/.entrypoint_ready"
    mkdir -p "${WORKSPACE}/.vaibify" 2>/dev/null || true
    local sStatusEscaped
    sStatusEscaped=$(fsEscapeJsonString "${sStatus}")
    local sReasonEscaped
    sReasonEscaped=$(fsEscapeJsonString "${sReason}")
    local sWarnings
    sWarnings=$(fsBuildWarningsJson)
    printf '{"sStatus": "%s", "sReason": "%s", "saWarnings": %s, "sEntrypointVersion": "%s"}\n' \
        "${sStatusEscaped}" "${sReasonEscaped}" "${sWarnings}" \
        "${S_ENTRYPOINT_VERSION}" \
        > "${sMarker}"
}

# ---------------------------------------------------------------------------
# fsRedactCredentials: Strip credentials embedded in HTTP(S) URLs
# Arguments: sInput
# Returns (stdout): sInput with `https://user:token@host` rewritten to
# `https://REDACTED@host`. Defends git's clone stderr against leaking
# tokens that may be present in `~/.git-credentials` URLs.
# ---------------------------------------------------------------------------
fsRedactCredentials() {
    local sInput="$1"
    printf '%s' "${sInput}" | LC_ALL=C sed -E \
        's|(https?://)[^@[:space:]/]+@|\1REDACTED@|g'
}

# ---------------------------------------------------------------------------
# fsCategorizeCloneError: Map git-clone stderr to a category keyword
# Arguments: sStderr
# Returns (stdout): one of auth | network | branch | unknown
# ---------------------------------------------------------------------------
fsCategorizeCloneError() {
    local sStderr="$1"
    if echo "${sStderr}" | grep -qiE \
        "authentication failed|permission denied|403|401|could not read username"; then
        printf 'auth'
        return
    fi
    if echo "${sStderr}" | grep -qiE \
        "could not resolve host|connection refused|connection timed out|network is unreachable|operation timed out"; then
        printf 'network'
        return
    fi
    if echo "${sStderr}" | grep -qiE \
        "remote branch .* not found|did not match any|reference is not a tree"; then
        printf 'branch'
        return
    fi
    printf 'unknown'
}

# ---------------------------------------------------------------------------
# fnHandleCloneFailure: Print categorized clone error and record warning
# Arguments: sName sBranch sStderrFile
# ---------------------------------------------------------------------------
fnHandleCloneFailure() {
    local sName="$1"
    local sBranch="$2"
    local sStderrFile="$3"
    local sStderr=""
    [ -f "${sStderrFile}" ] && sStderr=$(cat "${sStderrFile}")
    sStderr=$(fsRedactCredentials "${sStderr}")
    local sCategory
    sCategory=$(fsCategorizeCloneError "${sStderr}")
    case "${sCategory}" in
        auth)
            echo "[vaib]   Clone failed for ${sName}: authentication required. Run 'gh auth login' on the host and rebuild."
            fnAppendStartupWarning "${sName}" "clone-auth" \
                "authentication required" ;;
        network)
            echo "[vaib]   Clone failed for ${sName}: network unreachable. Check your connection and rebuild."
            fnAppendStartupWarning "${sName}" "clone-network" \
                "network unreachable" ;;
        branch)
            echo "[vaib]   Clone failed for ${sName}: branch '${sBranch}' not found in remote."
            fnAppendStartupWarning "${sName}" "clone-branch" \
                "branch '${sBranch}' not found" ;;
        *)
            echo "[vaib]   Clone failed for ${sName} (may require authentication)."
            echo "${sStderr}" | head -3 | sed 's/^/[vaib]     /'
            local sFirstLine
            sFirstLine=$(echo "${sStderr}" | head -1)
            fnAppendStartupWarning "${sName}" "clone-unknown" \
                "${sFirstLine:-unspecified clone failure}" ;;
    esac
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
# fnInstallCredentialHelper: Write a callback helper that resolves on demand
#
# Git invokes the helper once per HTTPS operation with ``get`` on stdin
# and expects ``username=...\npassword=...\n`` on stdout. The helper
# reads the token from the read-only Docker secret mount at request
# time; it never writes the raw token to the container filesystem, so
# no ``.git-credentials`` file exists for an attacker (or a stale
# image) to harvest. ``store`` and ``erase`` are no-ops because the
# token has no persistent representation inside the container.
# ---------------------------------------------------------------------------
fnInstallCredentialHelper() {
    local sHelperPath="/usr/local/bin/vaibify-git-credential-helper"
    cat > "${sHelperPath}" << 'HELPER'
#!/bin/bash
# vaibify-git-credential-helper: stdout the GitHub token on demand.
# Answers ONLY for host=github.com, parsed from the git-credential
# request on stdin: an unconditional helper hijacks authentication
# for every other remote — git.overleaf.com would be handed the
# GitHub token and reject the operation as an auth failure while the
# correct Overleaf token sits unconsulted in a later helper.
# Reads the token from /run/secrets/gh_token (mounted read-only by the
# host as a mode-600 ephemeral file). Falls back to ``gh auth token``
# if present. Never writes the token to disk.
case "${1:-}" in
    get)
        sRequestHost=""
        while IFS= read -r sLine; do
            [ -z "${sLine}" ] && break
            case "${sLine}" in
                host=*) sRequestHost="${sLine#host=}" ;;
            esac
        done
        if [ "${sRequestHost}" != "github.com" ]; then
            exit 0
        fi
        sToken=""
        if [ -f /run/secrets/gh_token ]; then
            sToken=$(cat /run/secrets/gh_token)
        elif command -v gh > /dev/null 2>&1; then
            sToken=$(gh auth token 2>/dev/null || true)
        fi
        if [ -n "${sToken}" ]; then
            printf 'username=x-access-token\n'
            printf 'password=%s\n' "${sToken}"
        fi
        ;;
    store|erase)
        # No-op: token lifetime is the container's lifetime; there is
        # nothing to persist or wipe.
        cat > /dev/null
        ;;
esac
HELPER
    chmod 0755 "${sHelperPath}"
}

# ---------------------------------------------------------------------------
# fnConfigureGit: Wire git's credential lookup to the callback helper
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# fnInstallAgentSkills: Copy vaibify-shipped skills into each installed
# agent's native skills directory. The workspace phase already runs as
# the unprivileged user, so the copied files stay editable by that user.
# ---------------------------------------------------------------------------
fnInstallAgentSkills() {
    local sSourceDir="/usr/share/vaibify/skills"
    if [ ! -d "${sSourceDir}" ]; then
        return 0
    fi
    local sAgent
    for sAgent in claude codex gemini opencode cline openhands pi; do
        command -v "${sAgent}" > /dev/null 2>&1 || continue
        local sTargetDir="/home/${CONTAINER_USER}/.${sAgent}/skills"
        if [ "${sAgent}" = "opencode" ]; then
            sTargetDir="/home/${CONTAINER_USER}/.config/opencode/skills"
        elif [ "${sAgent}" = "cline" ]; then
            sTargetDir="/home/${CONTAINER_USER}/.cline/data/settings/skills"
        elif [ "${sAgent}" = "pi" ]; then
            sTargetDir="/home/${CONTAINER_USER}/.pi/agent/skills"
        fi
        mkdir -p "${sTargetDir}"
        cp -R "${sSourceDir}/." "${sTargetDir}/"
        echo "[vaib] ${sAgent} skills installed: " \
            "$(ls "${sSourceDir}" | tr '\n' ' ')"
    done
}

fnConfigureGit() {
    local sToken
    sToken=$(fsReadGitHubToken)
    git config --system url."https://github.com/".insteadOf \
        "git@github.com:"
    fnInstallCredentialHelper
    if [ -n "${sToken}" ]; then
        echo "[vaib] GitHub credentials detected (resolved on demand)."
        # URL-scoped registration: git consults this helper for
        # github.com only, so other remotes (git.overleaf.com) fall
        # through to their own per-command credential configuration.
        git config --system \
            credential.https://github.com.helper \
            "/usr/local/bin/vaibify-git-credential-helper"
    else
        echo "[vaib] No GitHub credentials found. Public repos only."
        echo "[vaib]   To access private repos, run on host: gh auth login"
        export GIT_TERMINAL_PROMPT=0
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
    saRepoDestinations=()

    if [ ! -f "${REPOS_CONF}" ]; then
        echo "[vaib] No container.conf found at ${REPOS_CONF}. Skipping repo sync."
        return
    fi

    while IFS='|' read -r sName sUrl sBranch sMethod sDestination; do
        [[ "${sName}" =~ ^#.*$ ]] && continue
        [[ -z "${sName}" ]] && continue
        saRepoNames+=("${sName}")
        saRepoUrls+=("${sUrl}")
        saRepoBranches+=("${sBranch}")
        saRepoMethods+=("${sMethod}")
        saRepoDestinations+=("${sDestination}")
    done < "${REPOS_CONF}"
}

# ---------------------------------------------------------------------------
# fnCloneRepo: Clone a repository that does not yet exist locally
# Arguments: sName sUrl sBranch
# ---------------------------------------------------------------------------
# fbRefLooksLikeCommit: True when a declared ref is a raw commit hash
# (7-40 hex characters) rather than a branch or tag name. Hash-pinned
# refs are the reproducible-binary story: a rebuild must produce the
# same source tree regardless of where the branch has since moved.
fbRefLooksLikeCommit() {
    case "$1" in
        *[!0-9a-f]*) return 1 ;;
    esac
    [ "${#1}" -ge 7 ] && [ "${#1}" -le 40 ]
}

fnCloneRepo() {
    local sName="$1"
    local sUrl="$2"
    local sRef="$3"
    local sRepoPath="${WORKSPACE}/${sName}"
    local sStderrFile
    sStderrFile=$(mktemp /tmp/vaib_clone_err.XXXXXX)

    echo "[vaib] Cloning ${sName} (ref: ${sRef})..."
    if fbRefLooksLikeCommit "${sRef}"; then
        # git clone --branch accepts branches and tags but never raw
        # commit hashes; a pinned commit needs clone-then-checkout.
        if ! git clone --verbose "${sUrl}" "${sRepoPath}" \
                2> "${sStderrFile}" \
            || ! git -C "${sRepoPath}" checkout --quiet "${sRef}" \
                2>> "${sStderrFile}"; then
            fnHandleCloneFailure "${sName}" "${sRef}" "${sStderrFile}"
            rm -f "${sStderrFile}"
            return 0
        fi
    elif ! git clone --verbose --branch "${sRef}" "${sUrl}" \
            "${sRepoPath}" 2> "${sStderrFile}"; then
        fnHandleCloneFailure "${sName}" "${sRef}" "${sStderrFile}"
        rm -f "${sStderrFile}"
        return 0
    fi
    rm -f "${sStderrFile}"
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

    echo "[vaib] Updating ${sName}..."
    cd "${sRepoPath}"
    git fetch origin --tags 2>/dev/null || \
        echo "[vaib]   Fetch skipped for ${sName} (may require authentication)."
    local sCurrentBranch
    sCurrentBranch=$(git rev-parse --abbrev-ref HEAD)
    if [ "${sCurrentBranch}" = "${sBranch}" ]; then
        git pull --ff-only origin "${sBranch}" 2>/dev/null || \
            echo "[vaib]   Pull skipped for ${sName} (local changes or diverged)."
    else
        echo "[vaib]   ${sName} on branch '${sCurrentBranch}', not '${sBranch}'. Skipping pull."
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
    echo "[vaib] Syncing repositories..."
    echo ""

    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        fnCloneOrPull "${saRepoNames[$i]}" "${saRepoUrls[$i]}" "${saRepoBranches[$i]}"
    done

    fnRelocateRepos

    echo ""
    echo "[vaib] All repositories synced."
}

# ---------------------------------------------------------------------------
# fnRelocateRepo: Move a cloned repo to a different workspace path
# Arguments: sName sDestination
# ---------------------------------------------------------------------------
fnRelocateRepo() {
    local sName="$1"
    local sDestination="$2"
    local sSourcePath="${WORKSPACE}/${sName}"
    local sDestPath="${WORKSPACE}/${sDestination}"

    if [ ! -d "${sSourcePath}" ]; then
        return
    fi
    if [ -d "${sDestPath}" ] && [ -d "${sDestPath}/.git" ]; then
        echo "[vaib]   ${sDestination} already exists, skipping relocation."
        return
    fi
    rm -rf "${sDestPath}"
    mv "${sSourcePath}" "${sDestPath}"
    echo "[vaib]   Relocated ${sName} -> ${sDestination}"
}

# ---------------------------------------------------------------------------
# fnRelocateRepos: Move repos that have a destination override
# ---------------------------------------------------------------------------
fnRelocateRepos() {
    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        local sDestination="${saRepoDestinations[$i]}"
        if [ -n "${sDestination}" ] && \
           [ "${sDestination}" != "${saRepoNames[$i]}" ]; then
            fnRelocateRepo "${saRepoNames[$i]}" "${sDestination}"
        fi
    done
}

# ---------------------------------------------------------------------------
# fnBuildBinaries: Compile native C binaries for repos using c_and_pip method
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# fnBuildSingleBinary: Compile one C repo and add its bin/ to PATH
# Arguments: sName sRepoPath
# Returns: 0 if binary was built, 1 otherwise
# ---------------------------------------------------------------------------
fnBuildSingleBinary() {
    local sName="$1"
    local sRepoPath="$2"

    if [ ! -d "${sRepoPath}" ]; then
        echo "[vaib]   ${sName} not found. Skipping build."
        fnAppendStartupWarning "${sName}" "c-build" \
            "repository directory missing"
        return 1
    fi
    echo "[vaib]   Building ${sName}..."
    cd "${sRepoPath}"
    if make opt; then
        local sBinDir="${sRepoPath}/bin"
        if [ -d "${sBinDir}" ] && [ -n "$(find "${sBinDir}" \
                -maxdepth 1 -type f -perm -u+x -print -quit \
                2>/dev/null)" ]; then
            export PATH="${sBinDir}:${PATH}"
            echo "[vaib]   ${sName} build ready (PATH: ${sBinDir})"
            cd "${WORKSPACE}"
            return 0
        fi
        echo "[vaib]   WARNING: ${sName} build succeeded but ${sBinDir}/ has no executable artifacts."
        fnAppendStartupWarning "${sName}" "c-build" \
            "make opt succeeded but bin/ has no executables"
    else
        echo "[vaib]   WARNING: Build failed for ${sName}. You can retry manually:"
        echo "[vaib]     cd ${sRepoPath} && make opt"
        fnAppendStartupWarning "${sName}" "c-build" \
            "make opt failed"
    fi
    cd "${WORKSPACE}"
    return 1
}

fnBuildBinaries() {
    echo "[vaib] Building native binaries..."

    local iCount=${#saRepoNames[@]}
    local bBuiltAny=false
    for (( i=0; i<iCount; i++ )); do
        if [ "${saRepoMethods[$i]}" = "c_and_pip" ]; then
            if fnBuildSingleBinary "${saRepoNames[$i]}" \
                "${WORKSPACE}/${saRepoNames[$i]}"; then
                bBuiltAny=true
            fi
        fi
    done

    if [ "${bBuiltAny}" = false ]; then
        echo "[vaib]   No C binaries to build."
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
    echo "[vaib] Installing ${sName}..."
    if ! pip install -e "${sRepoPath}" "$@" -q; then
        echo "[vaib]   WARNING: Failed to install ${sName}. Continuing."
        fnAppendStartupWarning "${sName}" "pip-install" \
            "pip install -e failed"
    fi
}

# ---------------------------------------------------------------------------
# fnCondaInstall: Run conda/mamba install for a repo
# Arguments: sRepoPath sName
# ---------------------------------------------------------------------------
fnCondaInstall() {
    local sRepoPath="$1"
    local sName="$2"
    echo "[vaib] Installing ${sName} via conda..."
    if command -v mamba > /dev/null 2>&1; then
        mamba install -y -c conda-forge "${sName}" 2>/dev/null || \
            pip install -e "${sRepoPath}" -q
    elif command -v conda > /dev/null 2>&1; then
        conda install -y -c conda-forge "${sName}" 2>/dev/null || \
            pip install -e "${sRepoPath}" -q
    else
        echo "[vaib]   WARNING: conda/mamba not found. Falling back to pip."
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
            fnPipInstall "${sRepoPath}" "${sName}" ;;
        scripts_only)
            echo "[vaib] ${sName} available via PYTHONPATH and PATH." ;;
        reference)
            echo "[vaib] ${sName} cloned for reference (not installed)." ;;
        *)
            echo "[vaib] WARNING: Unknown install method '${sMethod}' for ${sName}." ;;
    esac
}

# ---------------------------------------------------------------------------
# fnInstallAllRepos: Install Python packages in dependency order
# ---------------------------------------------------------------------------
fnInstallAllRepos() {
    echo ""
    echo "[vaib] Installing Python packages..."

    local iCount=${#saRepoNames[@]}
    for (( i=0; i<iCount; i++ )); do
        if [ -d "${WORKSPACE}/${saRepoNames[$i]}" ]; then
            fnInstallRepo "${saRepoNames[$i]}" "${saRepoMethods[$i]}"
        fi
    done

    echo ""
    echo "[vaib] All packages installed."
}

# ---------------------------------------------------------------------------
# fnInstallRepoRequirements: Install per-repo .vaibify/requirements.txt files
# ---------------------------------------------------------------------------
fnInstallRepoRequirements() {
    local bFoundAny=false
    for sReqFile in "${WORKSPACE}"/*/.vaibify/requirements.txt; do
        [ -f "${sReqFile}" ] || continue
        local sRepoDir
        sRepoDir=$(dirname "$(dirname "${sReqFile}")")
        local sRepoName
        sRepoName=$(basename "${sRepoDir}")
        echo "[vaib] Installing requirements for ${sRepoName}..."
        if ! pip install -r "${sReqFile}" -q; then
            echo "[vaib]   WARNING: Failed to install requirements for ${sRepoName}. Continuing."
            fnAppendStartupWarning "${sRepoName}" "pip-requirements" \
                "pip install -r requirements.txt failed"
        fi
        bFoundAny=true
    done
    if [ "${bFoundAny}" = true ]; then
        echo "[vaib] Per-repo requirements installed."
    fi
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
# fnPersistAgentConfig: Symlink one agent's config to the workspace volume
# ---------------------------------------------------------------------------
fnPersistAgentConfig() {
    local sAgent="$1"
    local sHomeConfig="${2:-/home/${CONTAINER_USER}/.${sAgent}}"
    local sVolumeConfig="${WORKSPACE}/.${sAgent}"
    mkdir -p "${sVolumeConfig}"
    mkdir -p "$(dirname "${sHomeConfig}")"
    if [ -d "${sHomeConfig}" ] && [ ! -L "${sHomeConfig}" ]; then
        cp -an "${sHomeConfig}/." "${sVolumeConfig}/" 2>/dev/null || true
        rm -rf "${sHomeConfig}"
    elif [ -e "${sHomeConfig}" ] && [ ! -L "${sHomeConfig}" ]; then
        rm -f "${sHomeConfig}"
    fi
    ln -sfn "${sVolumeConfig}" "${sHomeConfig}"
}

# ---------------------------------------------------------------------------
# fnPersistInstalledAgentConfigs: Persist every installed CLI's login and
# settings state on the workspace volume, matching Claude's existing model.
# ---------------------------------------------------------------------------
fnPersistInstalledAgentConfigs() {
    command -v claude > /dev/null 2>&1 && fnPersistAgentConfig claude
    command -v codex > /dev/null 2>&1 && fnPersistAgentConfig codex
    command -v gemini > /dev/null 2>&1 && fnPersistAgentConfig gemini
    command -v opencode > /dev/null 2>&1 && fnPersistAgentConfig \
        opencode "/home/${CONTAINER_USER}/.config/opencode"
    command -v cline > /dev/null 2>&1 && fnPersistAgentConfig cline
    command -v openhands > /dev/null 2>&1 && fnPersistAgentConfig openhands
    command -v pi > /dev/null 2>&1 && fnPersistAgentConfig pi
    # Optional CLIs are normally absent in a freshly built image.  The
    # final command probe must not become this helper's return status:
    # the workspace phase runs with ``set -e``.
    return 0
}

# ---------------------------------------------------------------------------
# fnLoadBinariesEnv: Export named binary variables and extend PATH
# ---------------------------------------------------------------------------
fnLoadBinariesEnv() {
    local sBinEnv="/etc/vaibify/binaries.env"
    if [ ! -s "${sBinEnv}" ]; then
        return
    fi

    echo "[vaib] Loading binary environment from binaries.env..."
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
        echo "[vaib]   ${sVarName}=${sBinPath}"
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
    if command -v codex > /dev/null 2>&1; then
        echo "  Codex:     $(codex --version 2>&1)"
    fi
    if command -v gemini > /dev/null 2>&1; then
        echo "  Gemini:    $(gemini --version 2>&1)"
    fi
    if command -v opencode > /dev/null 2>&1; then
        echo "  OpenCode:  $(opencode --version 2>&1)"
    fi
    if command -v cline > /dev/null 2>&1; then
        echo "  Cline:     $(cline --version 2>&1)"
    fi
    if command -v openhands > /dev/null 2>&1; then
        echo "  OpenHands: $(openhands --version 2>&1)"
    fi
    if command -v pi > /dev/null 2>&1; then
        echo "  Pi:        $(pi --version 2>&1)"
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
#
# Projects live in each repository at <repo>/.vaibify/projects/;
# /workspace/.vaibify/ holds only container-scoped scratch (logs).
# Remove any misplaced /workspace/.vaibify/projects/ (or the legacy
# /workspace/.vaibify/workflows/) left at the workspace root so
# dashboard and agent discovery both resolve to the repository
# location.
#
# director.py was installed here and advertised to the agent as the
# "standalone pipeline executor". It could not start -- package-relative
# imports with no siblings staged -- so every invocation, `--help`
# included, raised ImportError. A stale copy from an earlier image is
# swept for the same reason the advertisement is gone: an executable
# sitting where a document once pointed is worse than an absent one.
# `vaibify-do` is the supported in-container path.
# ---------------------------------------------------------------------------
fnCreateVaibifyDirectory() {
    mkdir -p "${WORKSPACE}/.vaibify/logs"
    for _sStrayDir in projects workflows; do
        if [ -d "${WORKSPACE}/.vaibify/${_sStrayDir}" ]; then
            rm -rf "${WORKSPACE}/.vaibify/${_sStrayDir}"
        fi
    done
    rm -f "${WORKSPACE}/.vaibify/director.py"
}

# ---------------------------------------------------------------------------
# fnWriteClaudeMd: Generate CLAUDE.md so Claude knows the environment
# ---------------------------------------------------------------------------
fnWriteClaudeMd() {
    local sClaudeMd="${WORKSPACE}/CLAUDE.md"
    cat > "${sClaudeMd}" << 'CLAUDEMD'
# Vaibify Container Environment

You are running inside a **Vaibify container** — a secure, isolated environment for AI-assisted scientific data analysis.

## How to refer to steps

Every step in a project JSON carries an `sLabel` field — `A09` for the 9th *automated* step, `I01` for the 1st *interactive* step. Labels are per-type sequential, so `A09` is **not** `listSteps[9]`; the 0-based index depends on how many interactive steps precede it.

**When you name a step in any output — status reports, tables, summaries, prose, `vaibify-do` arguments — use `sLabel` verbatim.** Never substitute a 0-based or 1-based positional index like `00`, `01`, `Step09`. Read the label straight out of the JSON; do not translate.

The `{step:<sStepId>.<stem>}` tokens you see *inside* command strings (e.g., `python plot.py {step:generate-samples.samples}`) are a separate, script-side filename-substitution syntax resolved when the step runs. They are not how you talk about steps; they are how scripts reference each other's output files. Leave them alone unless you are editing the commands themselves.

## Interacting with the vaibify dashboard

The vaibify dashboard is the researcher's ground truth; any action you would otherwise perform by clicking a UI button SHOULD go through the `vaibify-do` CLI so the dashboard stays in sync with reality.

`vaibify-do` is the **in-container CLI** for that purpose — it runs inside this container, reads its session config from `/tmp/vaibify-session.env` and the action catalog from `/tmp/vaibify-action-catalog.json`, and dispatches HTTP/WebSocket calls to the host vaibify backend. It is not host-only.

**Prefer `vaibify-do`** for editing `project.json` (it goes through schema validation and atomic save). Direct edits are now detected by the host's polling loop and the dashboard reloads on the next tick — but `vaibify-do` remains the canonical path. Files under `<repository>/.vaibify/test_markers/` and `/workspace/.vaibify/pipeline_state.json` are still outputs of backend actions; do not hand-edit them.

**Creating a new project from inside the container.** `vaibify-do` does not currently expose a `create-project` action. When the researcher is in toolkit mode (no project loaded — banner shows "Project: None") and asks for a project built around their existing toolkit work, write a fresh `project.json` directly at `<repository>/.vaibify/projects/<slug>.json`. The dashboard polls for new projects and surfaces yours within one tick: the toolkit banner gains a "N available" indicator and a toast offers to switch into it. Use `vaibify-do --describe create-step` (or any of the existing step actions) to learn the canonical step schema before writing the file by hand.

Usage:

- Run `vaibify-do --list` at session start to see the full vocabulary of actions.
- Run `vaibify-do --describe <action>` to see the argument shape for one action.
- Run `vaibify-do <action> [args...]` to execute.

`vaibify-do` accepts `sLabel` values (`A09`, `I01`) directly in every step argument. Natural-language intent maps to commands:

- "run step A09" → `vaibify-do run-step A09`
- "run steps A09 through A11" → `vaibify-do run-selected-steps A09 A10 A11`
- "rerun from step A05" → `vaibify-do run-from-step A05`
- "run all steps" → `vaibify-do run-all`
- "verify outputs without rerunning" → `vaibify-do verify-only`
- "run unit tests on step A09" → `vaibify-do run-unit-tests A09`
- "run all tests" → `vaibify-do run-all-tests`
- "commit the current state" → `vaibify-do commit-canonical`
- "pull a file from the container" → `vaibify-do pull-file <sPath>`
- "make step A09's figures the standard" → `vaibify-do accept-plots-as-standard A09` (USER-ONLY — surface the request, do not run)
- "push to GitHub" → `vaibify-do push-to-github`
- "push to Overleaf / Zenodo" → USER-ONLY — surface the request, do not run

**Diagnosing a failed run from inside the container.** When a pipeline reports exit-code -9999 ("runner disappeared") or the dashboard shows a step stuck in an unknown state:

- "what killed the last run?" → \`vaibify-do get-pipeline-state\` — returns the reconciled \`pipeline_state.json\` with \`sFailureReason\` (symptom, e.g. \`heartbeat_stale\`) and \`sFailureCauseHost\` (the actual host exception, e.g. an ASGI WebSocket close). \`iActiveStepAtDeath\` names the step that was running when the runner died.
- "show the host log for this container" → \`vaibify-do get-host-log-tail --lines 200\` (or \`--lines=200\`) — returns the last N lines of \`~/.vaibify/vaibify.log\` filtered to lines tagged with this container id, plus a \`listIncidents\` ring of recent host exceptions for the same id.

Both actions are read-only and agent-safe. Use them BEFORE asking the researcher to investigate from the host.

**Run steps through `vaibify-do`, not by executing scripts directly in a shell.** A `vaibify-do run-step`/`run-selected-steps` dispatch lights the step's marker as *running* on the dashboard; a bare `python …` you launch yourself is invisible to the dashboard as a running step — the researcher only sees dependent steps flip stale once its outputs land. If you must run something directly, tell the researcher what you ran and on which step. See the `running-steps` skill for the full protocol, including safe (compare-and-swap) project edits.

**Never compute a quantitative or statistical result with a throwaway construction** — no heredocs, `python -c`, inline one-liners, or REPL sessions. A number that isn't produced by a saved script is not reproducible and cannot become a step. When asked to compute, estimate, fit, sample, or analyze anything numeric, write it as a script — in the relevant step's directory if it extends a step, otherwise in `explorations/` at the repository root — with a self-explanatory verb-first name and a one-line docstring, taking inputs as arguments and writing outputs to files, then run it through `vaibify-do`. Even a quick exploratory answer is delivered as the script, and you `grep explorations/` for an existing one before writing a new one. See the `reproducible-analysis` skill for the full protocol.

**User-only action protocol.** If `vaibify-do` responds with a JSON object containing `sRefusal: "user-only-action"`, do NOT retry. Tell the researcher concisely what you were about to do and ask them to click the matching button in the dashboard.

**Remote-data overwrite protocol.** If a run action is answered with a `runRefused` event whose `sReason` is `remoteDataOverwrite`, the run would re-pull remote data over the canonical committed copy (the refusal names the steps and files). Do NOT confirm on your own authority: relay the question to the researcher, and only after their explicit yes re-issue the same command with `--confirm-remote-overwrite`. After a confirmed re-pull, the fresh data is NOT auto-committed — the researcher reviews and commits it (or reverts) through the normal canonical flow.

**Declaring input data.** Every step must state its input contract to reach Level 1: raw files the step reads go in its Input Data block (`vaibify-do add-input-data-file <step> sPath=<repo-relative path>`), and a step that reads no raw data is declared with `bNoInputData` (per step via `update-step`, or `vaibify-do declare-no-input-data` to declare every still-undeclared step at once). Files produced by other steps are NOT input data — reference them as `{StepNN.*}` tokens in commands.

**Failure modes.** If `vaibify-do` reports `vaibify session not initialized` or `/tmp/vaibify-session.env` is missing, vaibify is not currently connected to this container — tell the researcher to open the dashboard and click the container so it reconnects. This is a "not connected yet" condition, not a "vaibify-do is host-only" condition. If it reports the host is unreachable or the session token is invalid, same fix: reconnect from the dashboard. Do not try workarounds.

## Key Paths

- `/workspace/` — All repositories and working files
- `/workspace/<RepoName>/.vaibify/projects/` — Project JSON files (each repository can have its own)
- `/workspace/.vaibify/logs/` — Pipeline execution logs

## Project System

Each vaibified repository has a `.vaibify/projects/` directory with JSON files defining pipeline steps. Each step has:

- **Data Analysis Commands** (`saDataCommands`): Heavy computation
- **Data Files** (`saOutputDataFiles`): Output files from data analysis
- **Plot Commands** (`saPlotCommands`): Visualization commands
- **Plot Files** (`saPlotFiles`): Expected figure outputs
- **Test Commands** (`saTestCommands`): Unit tests for data outputs
- **Interactive** (`bInteractive`): Steps requiring human judgment

Cross-step filename references inside command strings use `{step:<sStepId>.<stem>}` syntax (e.g., `{step:generate-samples.samples}`), keyed on the target step's `sStepId`. This is a script-side variable-substitution contract only — it is not how you name steps when talking to the researcher (see **How to refer to steps** above).

The older positional form `{StepNN.stem}` still parses but is **deprecated**: `NN` is a 1-based index into `listSteps`, so it renumbers whenever a step is inserted, deleted, or reordered, and a reference that used to point at one step silently starts pointing at another. Write the symbolic form in anything new, and prefer converting a positional token when you are already editing that command.

Run a step: `vaibify-do run-step A09` (see **Acting on the researcher's behalf** above). There is no in-container pipeline executor to invoke directly — running work through `vaibify-do` is what keeps the researcher's dashboard showing the truth.

## Vaibified Repository Structure

A vaibified repo contains:
- One camelCase directory per step
- Scripts prefixed with `data` (analysis) or `plot` (visualization)
- A `Plot/` directory for output figures
- `.vaibify/projects/*.json` defining the pipeline
- `.vaibify/AGENTS.md` with project context (symlinked to repo root)

## Verification

Each step has a verification status with three components:
- **Unit Tests**: Automated tests on data outputs
- **User Approval**: Manual verification by the scientist
- **Dependencies**: All upstream steps must also pass

A test category whose `saCommands` list is empty is reported as
"N/A / unnecessary" — it has nothing to run, so it counts as green
when computing the L1 all-green gate. Use this rather than fabricating
trivial tests just to satisfy the dashboard.

## AI Containment Scale (AICS)

The AICS is a five-rung reproducibility ladder (L1 Self-Consistent,
L2 Published, L3 Reproducible; L4/L5 are non-goals). To raise or audit
a project's level, use the **aics-ladder** skill — it carries the
ordered L1->L3 gate walkthrough and the known audit traps.

Two rules that must never be violated, skill or not:

- **`iAICSLevel` from `vaibify-do check-l2-readiness` is the only
  authoritative level signal.** Never hand-roll a verification audit
  from raw files; when your file inspection disagrees with the
  backend, the backend wins. (`bVaibified` is retired — ignore it.)
- **Publication is user-only.** Never silently invoke
  `push-to-overleaf`, `publish-to-zenodo`, or
  `accept-plots-as-standard`; surface the request and let the
  researcher click. `push-to-github` is agent-callable on request.

## Creating New Pipeline Steps

To author a new analysis or plot step, use the
**create-pipeline-step** skill — it carries the 5-phase protocol
(discover dependencies, name, wire the cross-step tokens, write the
project entry, verify).

The one rule that must never be violated: **every file a script reads
from another step must be a CLI argument named in the project command
via a `{StepNN.varname}` token.** A hardcoded cross-step path is
invisible to the dependency parser and silently breaks the L1
contract. Own-step files may be hardcoded; the boundary is the step.

## Managing Package Dependencies

When creating or running a step, you may encounter missing Python packages. Follow this
procedure to fix them permanently.

### Detection and Immediate Fix

1. If a script raises `ModuleNotFoundError` or `ImportError`, identify the PyPI package name.
2. Install it immediately: `pip install <package>>=<minimum_version>`
3. Verify the script now runs.

### Persisting the Dependency

The immediate `pip install` is ephemeral — it is lost when the container is rebuilt. To make
it permanent:

1. Create or update the file `<repo>/.vaibify/requirements.txt` in the vaibified repository.
2. Add one line per package with a version constraint: `lightkurve>=2.0`
3. The vaibify entrypoint installs these automatically on container startup.

### Rules

- Only install packages from PyPI. Never `pip install` from arbitrary URLs.
- Always include a version lower bound (e.g., `>=1.0`).
- Before adding a package, check for version conflicts: `pip install --dry-run <package>`
- Do not add packages that duplicate functionality already available in the container.
- Distinguish **code dependencies** (packages — belong in requirements.txt) from **data
  dependencies** (files from other steps — belong in `{step:<sStepId>.<stem>}` references).

## Important

- Do not modify scientific calculations without explicit direction
- Test changes with `pytest` before committing
- All repositories are public or will be — never embed secrets in code
CLAUDEMD
    if [ -f "${CRAFT_GUIDE_PATH}" ]; then
        cat "${CRAFT_GUIDE_PATH}" >> "${sClaudeMd}"
    else
        fnAppendStartupWarning "craftGuide" "agent-docs" \
            "craft guide missing at ${CRAFT_GUIDE_PATH}; agents receive operational context only"
    fi
    if [ ! -e "${WORKSPACE}/AGENTS.md" ]; then
        ln -s "CLAUDE.md" "${WORKSPACE}/AGENTS.md"
    fi
    if [ ! -e "${WORKSPACE}/GEMINI.md" ]; then
        ln -s "CLAUDE.md" "${WORKSPACE}/GEMINI.md"
    fi
    echo "[vaib] Generated shared agent context."
    fnLinkRepoClaudeMd
}

# ---------------------------------------------------------------------------
# fnLinkRepoClaudeMd: Canonicalize project context on .vaibify/AGENTS.md
# and symlink provider-recognized repo-root names to it.
# ---------------------------------------------------------------------------
fnLinkRepoClaudeMd() {
    for sVaibDir in "${WORKSPACE}"/*/.vaibify; do
        [ -d "${sVaibDir}" ] || continue
        local sRepoDir
        sRepoDir=$(dirname "${sVaibDir}")
        local sSource="${sVaibDir}/AGENTS.md"
        # Legacy migration: a real .vaibify/CLAUDE.md becomes the
        # canonical AGENTS.md (never clobbering an existing one).
        if [ -f "${sVaibDir}/CLAUDE.md" ] && [ ! -L "${sVaibDir}/CLAUDE.md" ] \
            && [ ! -e "${sSource}" ]; then
            mv "${sVaibDir}/CLAUDE.md" "${sSource}"
            echo "[vaib]   Migrated .vaibify/CLAUDE.md to AGENTS.md"
        fi
        fnWriteRepoAgentContext "${sRepoDir}" || continue
        # Repo-root FILE conventions. Verified per provider rather than
        # assumed: Claude reads CLAUDE.md, Gemini reads GEMINI.md, and
        # Codex, OpenCode, OpenHands (v1) and Pi all read AGENTS.md. The
        # three names below therefore cover six of the seven agents.
        #
        # They point at the COMPOSED context, not at the researcher's
        # file directly: a provider reads exactly one file per name, so
        # composing is the only way both vaibify's craft guidance and
        # the researcher's instructions arrive without either
        # overwriting the other. Pointing at the repo root is also what
        # makes delivery independent of how each agent walks the
        # directory tree -- the workspace-root doc reaches only the
        # providers that traverse upward.
        local sName
        for sName in CLAUDE.md AGENTS.md GEMINI.md; do
            local sTarget="${sRepoDir}/${sName}"
            if [ ! -e "${sTarget}" ] && [ ! -L "${sTarget}" ]; then
                ln -s ".vaibify/${COMPOSED_CONTEXT_BASENAME}" "${sTarget}"
                echo "[vaib]   Linked ${sName} in $(basename "${sRepoDir}")"
            elif fbLinkPointsAtVaibifyContext "${sTarget}" ""; then
                # Vaibify's own link from before the craft guide
                # shipped. Repointing it is safe -- it is not the
                # researcher's file -- and without this an existing
                # repository would never receive the craft guidance.
                ln -sfn ".vaibify/${COMPOSED_CONTEXT_BASENAME}" "${sTarget}"
                echo "[vaib]   Repointed ${sName} in" \
                    "$(basename "${sRepoDir}") to the composed context"
            fi
        done
        fnLinkClineRules "${sRepoDir}"
    done
}

# ---------------------------------------------------------------------------
# fbLinkPointsAtVaibifyContext: True for a symlink vaibify itself created
# Arguments: sPath sPrefix
# ---------------------------------------------------------------------------
# Only vaibify's own links may be repointed; a REAL file at one of the
# provider names is the researcher's and is never touched.
#
# A dangling link counts as owned, and that case is not hypothetical:
# the legacy migration above renames .vaibify/CLAUDE.md to AGENTS.md
# and leaves an older root symlink aimed at a file that no longer
# exists. The previous guard tested [ ! -e ] && [ ! -L ], and a
# dangling symlink is -L true, so it was never repaired -- that
# provider silently read nothing at all.
fbLinkPointsAtVaibifyContext() {
    local sPath="$1"
    local sPrefix="$2"
    [ -L "${sPath}" ] || return 1
    local sLinkTarget
    sLinkTarget=$(readlink "${sPath}")
    case "${sLinkTarget}" in
        "${sPrefix}.vaibify/AGENTS.md") return 0 ;;
        "${sPrefix}.vaibify/CLAUDE.md") return 0 ;;
        "${sPrefix}.vaibify/${COMPOSED_CONTEXT_BASENAME}") return 0 ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# fnWriteRepoAgentContext: Compose shipped guidance + the researcher's own
# Arguments: sRepoDir
# ---------------------------------------------------------------------------
# The composed file is vaibify's, regenerated every start; the
# researcher's .vaibify/AGENTS.md is read and never written. Returns
# non-zero when the workspace context is missing so the caller leaves
# the repo-root names alone rather than linking them at nothing.
fnWriteRepoAgentContext() {
    local sRepoDir="$1"
    local sVaibDir="${sRepoDir}/.vaibify"
    local sComposed="${sVaibDir}/${COMPOSED_CONTEXT_BASENAME}"
    if [ ! -f "${WORKSPACE}/CLAUDE.md" ]; then
        return 1
    fi
    cat "${WORKSPACE}/CLAUDE.md" > "${sComposed}" || return 1
    if [ -f "${sVaibDir}/AGENTS.md" ]; then
        printf '%s\n\n' "${COMPOSED_CONTEXT_SEPARATOR}" >> "${sComposed}"
        cat "${sVaibDir}/AGENTS.md" >> "${sComposed}"
    fi
    fnIgnoreGeneratedContext "${sVaibDir}"
}

# ---------------------------------------------------------------------------
# fnIgnoreGeneratedContext: Keep the generated context out of git
# Arguments: sVaibDir
# ---------------------------------------------------------------------------
# It is vaibify's output, regenerated on every start, and it would
# otherwise land in the researcher's commits and their provenance
# record.
fnIgnoreGeneratedContext() {
    local sVaibDir="$1"
    local sIgnoreFile="${sVaibDir}/.gitignore"
    if [ -f "${sIgnoreFile}" ] && grep -qxF "${COMPOSED_CONTEXT_BASENAME}" \
        "${sIgnoreFile}"; then
        return 0
    fi
    printf '%s\n' "${COMPOSED_CONTEXT_BASENAME}" >> "${sIgnoreFile}"
}

# ---------------------------------------------------------------------------
# fnLinkClineRules: Give Cline the guidance the flat names cannot carry
# ---------------------------------------------------------------------------
# Cline is the one installed agent that does not read a repo-root
# markdown file. Its project convention is a `.clinerules/` DIRECTORY of
# markdown files, so a flat `.clinerules` symlink would be the wrong
# shape entirely -- which is why it cannot join the loop above.
#
# Gated on Cline actually being installed. The flat names are harmless
# to create unconditionally (a stray symlink costs nothing), but a
# directory appearing in the researcher's git repository is the kind of
# thing that gets committed by accident, so it is only made for a
# container that has Cline in it.
fnLinkClineRules() {
    local sRepoDir="$1"
    command -v cline > /dev/null 2>&1 || return 0
    local sRulesDir="${sRepoDir}/.clinerules"
    if [ -e "${sRulesDir}" ] && [ ! -d "${sRulesDir}" ]; then
        return 0
    fi
    mkdir -p "${sRulesDir}"
    local sTarget="${sRulesDir}/vaibify.md"
    if [ ! -e "${sTarget}" ] && [ ! -L "${sTarget}" ]; then
        ln -s "../.vaibify/${COMPOSED_CONTEXT_BASENAME}" "${sTarget}"
        echo "[vaib]   Linked .clinerules/vaibify.md in" \
            "$(basename "${sRepoDir}")"
    elif fbLinkPointsAtVaibifyContext "${sTarget}" "../"; then
        ln -sfn "../.vaibify/${COMPOSED_CONTEXT_BASENAME}" "${sTarget}"
        echo "[vaib]   Repointed .clinerules/vaibify.md in" \
            "$(basename "${sRepoDir}") to the composed context"
    fi
}

# ---------------------------------------------------------------------------
# fnConfigureClaudeTheme: Set Claude Code to dark theme for container terminal
# ---------------------------------------------------------------------------
fnConfigureClaudeTheme() {
    local sConfigDir="/home/${CONTAINER_USER}/.claude"
    local sSettingsFile="${sConfigDir}/settings.json"
    if [ -f "${sSettingsFile}" ]; then
        return
    fi
    mkdir -p "${sConfigDir}"
    cat > "${sSettingsFile}" << 'SETTINGS'
{
  "theme": "dark"
}
SETTINGS
    echo "[vaib] Set Claude Code theme to dark for container terminal."
}

# ---------------------------------------------------------------------------
# fnConfigureClaudeAutoUpdate: Merge autoUpdates key into Claude settings.json
# ---------------------------------------------------------------------------
fnConfigureClaudeAutoUpdate() {
    local sFlag="${VAIBIFY_CLAUDE_AUTO_UPDATE:-true}"
    local sConfigDir="/home/${CONTAINER_USER}/.claude"
    local sSettingsFile="${sConfigDir}/settings.json"
    mkdir -p "${sConfigDir}"
    [ -f "${sSettingsFile}" ] || echo '{}' > "${sSettingsFile}"
    VAIB_SETTINGS="${sSettingsFile}" VAIB_FLAG="${sFlag}" python3 - << 'PYEOF'
import json, os
sSettings = os.environ["VAIB_SETTINGS"]
bAutoUpdate = os.environ["VAIB_FLAG"] == "true"
with open(sSettings) as fileHandle:
    dictContents = json.load(fileHandle)
dictContents["autoUpdates"] = bAutoUpdate
with open(sSettings, "w") as fileHandle:
    json.dump(dictContents, fileHandle, indent=2)
PYEOF
    echo "[vaib] Claude auto-update set to ${sFlag}."
}

# ---------------------------------------------------------------------------
# fnConfigureCodexAutoUpdate: Update Codex at startup when explicitly enabled.
# Codex's supported update mechanism is its ``codex update`` command.
# ---------------------------------------------------------------------------
fnConfigureCodexAutoUpdate() {
    local sFlag="${VAIBIFY_CODEX_AUTO_UPDATE:-true}"
    if [ "${sFlag}" != "true" ]; then
        echo "[vaib] Codex auto-update disabled."
        return
    fi
    if [ "${VAIBIFY_NETWORK_ISOLATED:-false}" = "true" ]; then
        fnAppendStartupWarning "Codex" "agent-update-deferred" \
            "network isolation is enabled"
        return
    fi
    if codex update; then
        echo "[vaib] Codex auto-update completed."
    else
        fnAppendStartupWarning "Codex" "agent-update-failed" \
            "codex update exited non-zero"
    fi
}

# ---------------------------------------------------------------------------
# fnConfigureGeminiAutoUpdate: Persist Gemini's documented auto-update
# preference and AGENTS.md context convention without replacing user keys.
# ---------------------------------------------------------------------------
fnConfigureGeminiAutoUpdate() {
    local sFlag="${VAIBIFY_GEMINI_AUTO_UPDATE:-true}"
    local sConfigDir="/home/${CONTAINER_USER}/.gemini"
    local sSettingsFile="${sConfigDir}/settings.json"
    mkdir -p "${sConfigDir}"
    [ -f "${sSettingsFile}" ] || echo '{}' > "${sSettingsFile}"
    VAIB_SETTINGS="${sSettingsFile}" VAIB_FLAG="${sFlag}" python3 - << 'PYEOF'
import json, os
sSettings = os.environ["VAIB_SETTINGS"]
bAutoUpdate = os.environ["VAIB_FLAG"] == "true"
with open(sSettings) as fileHandle:
    dictContents = json.load(fileHandle)
if not isinstance(dictContents, dict):
    dictContents = {}
dictGeneral = dictContents.setdefault("general", {})
if not isinstance(dictGeneral, dict):
    dictGeneral = {}
    dictContents["general"] = dictGeneral
dictGeneral["enableAutoUpdate"] = bAutoUpdate
dictContext = dictContents.setdefault("context", {})
if not isinstance(dictContext, dict):
    dictContext = {}
    dictContents["context"] = dictContext
dictContext["fileName"] = ["AGENTS.md"]
with open(sSettings, "w") as fileHandle:
    json.dump(dictContents, fileHandle, indent=2)
PYEOF
    if [ "${sFlag}" = "true" ] && [ "${VAIBIFY_NETWORK_ISOLATED:-false}" = "true" ]; then
        fnAppendStartupWarning "Gemini" "agent-update-deferred" \
            "network isolation is enabled"
    fi
    echo "[vaib] Gemini auto-update set to ${sFlag}."
}

fnRunAgentAutoUpdate() {
    local sAgentName="$1"
    local sFlag="$2"
    shift 2
    if [ "${sFlag}" != "true" ]; then
        echo "[vaib] ${sAgentName} auto-update disabled."
        return
    fi
    if [ "${VAIBIFY_NETWORK_ISOLATED:-false}" = "true" ]; then
        fnAppendStartupWarning "${sAgentName}" "agent-update-deferred" \
            "network isolation is enabled"
        return
    fi
    if "$@"; then
        echo "[vaib] ${sAgentName} auto-update completed."
    else
        fnAppendStartupWarning "${sAgentName}" "agent-update-failed" \
            "update command exited non-zero"
    fi
}

# ---------------------------------------------------------------------------
# fnSourceBinariesInEnv: Re-establish binary PATH from root-phase profile.d
# ---------------------------------------------------------------------------
fnSourceBinariesInEnv() {
    local sProfilePath="/etc/profile.d/vaibify-binaries.sh"
    if [ -f "${sProfilePath}" ]; then
        # shellcheck source=/dev/null
        source "${sProfilePath}"
    fi
}

# ---------------------------------------------------------------------------
# fnListNestedWorkspaceMounts: emit every bind/other mount point nested
# STRICTLY BELOW ${WORKSPACE}, one per line, mountinfo octal escapes
# decoded. ${WORKSPACE} itself is a named-volume mount point, so it is a
# proper-descendant test, never an equal one. Returns non-zero (and emits
# nothing) if /proc/self/mountinfo cannot be read or awk cannot parse it,
# so the caller can fail closed. Newline-separation is safe here: a
# vaibify bind target cannot contain a newline (the host-side repository
# validator rejects it), and non-vaibify mounts never land under
# ${WORKSPACE}.
# ---------------------------------------------------------------------------
fnListNestedWorkspaceMounts() {
    local sMountInfo="${1:-/proc/self/mountinfo}"
    [ -r "${sMountInfo}" ] || return 1
    awk -v ws="${WORKSPACE}" '
        function decode(s) {
            gsub(/\\040/, " ", s); gsub(/\\011/, "\t", s)
            gsub(/\\012/, "\n", s); gsub(/\\134/, "\\", s)
            return s
        }
        {
            # A /proc mountinfo line is "ID PID MAJ:MIN ROOT MOUNT OPTS
            # [optional...] - FSTYPE SOURCE SUPEROPTS": at least ten
            # fields, an absolute mount point in $5, and a "-" separator
            # exactly three fields from the end. A readable-but-malformed
            # table must fail closed, not be read as "no nested mounts" —
            # the latter would let the caller chown into an undetected
            # bind. So flag the line and stop; results are buffered and
            # flushed only if EVERY line parsed, so a failure emits
            # nothing and exits non-zero, which the caller treats as
            # fail-closed.
            if (NF < 10 || substr($5, 1, 1) != "/" || $(NF - 3) != "-") {
                bMalformed = 1
                exit
            }
            mp = decode($5)
            # Proper descendant: under ws on a COMPONENT boundary (ws
            # followed by "/"), and not ws itself — so "/workspace-foo"
            # is not treated as under "/workspace".
            if (mp != ws && index(mp, ws "/") == 1) {
                aMountPoints[++iCount] = mp
            }
        }
        END {
            # A readable table with zero records is itself malformed: a
            # running process always has at least the ${WORKSPACE} mount,
            # so an empty read means the table did not come through, not
            # that there are no nested mounts. Fail closed rather than let
            # the caller read it as "nothing to prune".
            if (bMalformed || NR == 0) { exit 1 }
            for (i = 1; i <= iCount; i++) { print aMountPoints[i] }
        }
    ' "${sMountInfo}"
}

# ---------------------------------------------------------------------------
# fnMigrateWorkspaceOwnership: One-time chown for pre-split-entrypoint
# volumes. Both the detection scan and the chown are MOUNT-AWARE: they
# prune every bind mount nested under ${WORKSPACE} so neither descends
# into a mounted host directory. ${WORKSPACE} is a named volume, but a
# configured bind mount can be nested BENEATH it, and a blanket
# ``chown -R ${WORKSPACE}`` (or a bare ``find ${WORKSPACE}``) would
# traverse straight into that bind and rewrite ownership across the host
# directory. The scan stays deep within the volume's OWN storage (so it
# still catches nested residue such as ``.git/objects/3f`` a pre-split
# host-side git operation left) while skipping the bind subtrees — which
# also stops a root-owned file inside a skipped bind from re-triggering
# this "one-time" migration on every start.
#
# Fail closed: if the mount table cannot be read or parsed we cannot tell
# a bind from the volume's own storage, so we skip the migration rather
# than risk a recursive chown into a mount.
# ---------------------------------------------------------------------------
fnMigrateWorkspaceOwnership() {
    if [ ! -d "${WORKSPACE}" ]; then
        return
    fi

    local sMounts
    if ! sMounts=$(fnListNestedWorkspaceMounts); then
        echo "[vaib] WARNING: /proc/self/mountinfo unreadable or" \
             "unparseable; skipping workspace-ownership migration to" \
             "avoid a recursive chown that could touch a bind mount." >&2
        return
    fi

    # Build a find prune expression for every nested mount point.
    local -a saPruneArgs=()
    local sMountPoint
    while IFS= read -r sMountPoint; do
        [ -n "${sMountPoint}" ] || continue
        if [ "${#saPruneArgs[@]}" -gt 0 ]; then
            saPruneArgs+=( -o )
        fi
        saPruneArgs+=( -path "${sMountPoint}" )
    done <<< "${sMounts}"

    # Detection: any root-owned path in the volume's OWN storage (bind
    # subtrees pruned)? If none, there is nothing to migrate.
    local sFound
    if [ "${#saPruneArgs[@]}" -gt 0 ]; then
        sFound=$(find "${WORKSPACE}" \( "${saPruneArgs[@]}" \) -prune \
            -o -uid 0 -print -quit 2>/dev/null)
    else
        sFound=$(find "${WORKSPACE}" -uid 0 -print -quit 2>/dev/null)
    fi
    if [ -z "${sFound}" ]; then
        return
    fi

    echo "[vaib] One-time migration: adjusting workspace ownership..."
    if [ "${#saPruneArgs[@]}" -gt 0 ]; then
        find "${WORKSPACE}" \( "${saPruneArgs[@]}" \) -prune -o -print0 \
            2>/dev/null \
            | xargs -0 --no-run-if-empty chown --no-dereference \
                  "${CONTAINER_USER}:${CONTAINER_USER}"
    else
        find "${WORKSPACE}" -print0 2>/dev/null \
            | xargs -0 --no-run-if-empty chown --no-dereference \
                  "${CONTAINER_USER}:${CONTAINER_USER}"
    fi
    echo "[vaib] Migration complete."
}

# ---------------------------------------------------------------------------
# fnRunRootPhase: System-path operations that require root privileges
# ---------------------------------------------------------------------------
fnRunRootPhase() {
    fnConfigureGit
    fnLoadBinariesEnv
    fnMigrateWorkspaceOwnership
}

# ---------------------------------------------------------------------------
# fnRunWorkspacePhase: Workspace and home-directory setup as container user
# ---------------------------------------------------------------------------
fnRunWorkspacePhase() {
    # Overwrite any stale marker from a prior container session
    # before any long-running step. /workspace is a persistent named
    # volume, so a marker from a previous image lives on across
    # rebuilds; without this line the host probe reads the prior
    # marker (often a previous entrypoint version) during the multi-
    # minute workspace boot and surfaces a misleading version-
    # mismatch warning until the "ok" marker is written below.
    fnWriteReadinessMarker "booting" "container initializing"
    fnPrintBanner
    export PIP_USER=1
    fnSourceBinariesInEnv
    fnCreateVaibifyDirectory
    fnWriteClaudeMd
    fnPersistInstalledAgentConfigs
    fnInstallAgentSkills
    fnPersistGitConfig
    fnParseReposConf
    fnSyncAllRepos
    if command -v claude > /dev/null 2>&1; then
        fnConfigureClaudeTheme
        fnConfigureClaudeAutoUpdate
    fi
    if command -v codex > /dev/null 2>&1; then
        fnConfigureCodexAutoUpdate
    fi
    if command -v gemini > /dev/null 2>&1; then
        fnConfigureGeminiAutoUpdate
    fi
    if command -v opencode > /dev/null 2>&1; then
        fnRunAgentAutoUpdate "OpenCode" \
            "${VAIBIFY_OPENCODE_AUTO_UPDATE:-true}" opencode upgrade
    fi
    if command -v cline > /dev/null 2>&1; then
        fnRunAgentAutoUpdate "Cline" \
            "${VAIBIFY_CLINE_AUTO_UPDATE:-true}" cline update
    fi
    if command -v openhands > /dev/null 2>&1; then
        fnRunAgentAutoUpdate "OpenHands" \
            "${VAIBIFY_OPENHANDS_AUTO_UPDATE:-true}" \
            uv tool upgrade openhands
    fi
    if command -v pi > /dev/null 2>&1; then
        fnRunAgentAutoUpdate "Pi" \
            "${VAIBIFY_PI_AUTO_UPDATE:-true}" pi update --self
    fi
    fnBuildBinaries
    fnSourceBinariesInBashrc
    fnInstallAllRepos
    fnInstallRepoRequirements
    fnPrintSummary
}

# ---------------------------------------------------------------------------
# fnHandleStartupExit: EXIT trap — guarantee a readiness marker on failure
# Arguments: iExitCode
# ---------------------------------------------------------------------------
fnHandleStartupExit() {
    local iExitCode="$1"
    local sMarker="${WORKSPACE}/.vaibify/.entrypoint_ready"
    if [ -f "${sMarker}" ]; then
        return
    fi
    local sReason="entrypoint exited ${iExitCode} before completion"
    fnWriteReadinessMarker "failed" "${sReason}"
    echo "[vaib] Startup failed (exit ${iExitCode}); readiness marker recorded." >&2
}

# ===========================================================================
# Main — only runs when executed directly (not when sourced by tests)
#
# Two-phase design: the entrypoint re-invokes itself via gosu so that
# workspace files are created with correct ownership, eliminating the
# need for a blanket chown -R of the entire workspace volume.
#
#   Phase 1 (root):  system-path config → exec gosu … --workspace-phase
#   Phase 2 (user):  workspace setup    → exec $CMD
# ===========================================================================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail

    if [ "${1:-}" = "--workspace-phase" ]; then
        shift
        trap 'fnHandleStartupExit $?' EXIT
        fnRunWorkspacePhase
        fnWriteReadinessMarker "ok" ""
        trap - EXIT
        exec "$@"
    fi

    # Root phase — system-path writes only
    trap 'fnHandleStartupExit $?' EXIT
    fnRunRootPhase
    # exec replaces this process; the EXIT trap fires only on failure
    exec gosu "${CONTAINER_USER}" \
        /usr/local/bin/entrypoint.sh --workspace-phase "$@"
fi
