#!/bin/bash

WORKSPACE="${WORKSPACE:-/workspace}"
REPOS_CONF="${REPOS_CONF:-/etc/vaibify/container.conf}"
CONTAINER_USER="${CONTAINER_USER:-researcher}"
PACKAGE_MANAGER="${PACKAGE_MANAGER:-pip}"
VC_PROJECT_NAME="${VC_PROJECT_NAME:-Vaibify}"

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
# Reads the token from /run/secrets/gh_token (mounted read-only by the
# host as a mode-600 ephemeral file). Falls back to ``gh auth token``
# if present. Never writes the token to disk.
case "${1:-}" in
    get)
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
fnConfigureGit() {
    local sToken
    sToken=$(fsReadGitHubToken)
    git config --system url."https://github.com/".insteadOf \
        "git@github.com:"
    fnInstallCredentialHelper
    if [ -n "${sToken}" ]; then
        echo "[vaib] GitHub credentials detected (resolved on demand)."
        git config --system credential.helper \
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
fnCloneRepo() {
    local sName="$1"
    local sUrl="$2"
    local sBranch="$3"
    local sRepoPath="${WORKSPACE}/${sName}"
    local sStderrFile
    sStderrFile=$(mktemp /tmp/vaib_clone_err.XXXXXX)

    echo "[vaib] Cloning ${sName} (branch: ${sBranch})..."
    if ! git clone --verbose --branch "${sBranch}" "${sUrl}" \
        "${sRepoPath}" 2> "${sStderrFile}"; then
        fnHandleCloneFailure "${sName}" "${sBranch}" "${sStderrFile}"
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
# Workflows live in each project repo at <repo>/.vaibify/workflows/;
# /workspace/.vaibify/ holds only container-scoped scratch (logs,
# director.py). Remove any legacy /workspace/.vaibify/workflows/ left
# over from pre-2026-04-20 containers so dashboard and agent
# discovery both resolve to the project-repo location.
# ---------------------------------------------------------------------------
fnCreateVaibifyDirectory() {
    mkdir -p "${WORKSPACE}/.vaibify/logs"
    if [ -d "${WORKSPACE}/.vaibify/workflows" ]; then
        rm -rf "${WORKSPACE}/.vaibify/workflows"
    fi
    if [ -f /usr/share/vaibify/director.py ]; then
        cp /usr/share/vaibify/director.py "${WORKSPACE}/.vaibify/director.py"
        chmod +x "${WORKSPACE}/.vaibify/director.py"
    fi
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

Every step in a workflow JSON carries an `sLabel` field — `A09` for the 9th *automated* step, `I01` for the 1st *interactive* step. Labels are per-type sequential, so `A09` is **not** `listSteps[9]`; the 0-based index depends on how many interactive steps precede it.

**When you name a step in any output — status reports, tables, summaries, prose, `vaibify-do` arguments — use `sLabel` verbatim.** Never substitute a 0-based or 1-based positional index like `00`, `01`, `Step09`. Read the label straight out of the JSON; do not translate.

The `{StepNN.stem}` tokens you see *inside* command strings (e.g., `python plot.py {Step08.samples}`) are a separate, script-side filename-substitution syntax resolved by the director at run time. They are not how you talk about steps; they are how scripts reference each other's output files. Leave them alone unless you are editing the commands themselves.

## Interacting with the vaibify dashboard

The vaibify dashboard is the researcher's ground truth; any action you would otherwise perform by clicking a UI button SHOULD go through the `vaibify-do` CLI so the dashboard stays in sync with reality.

`vaibify-do` is the **in-container CLI** for that purpose — it runs inside this container, reads its session config from `/tmp/vaibify-session.env` and the action catalog from `/tmp/vaibify-action-catalog.json`, and dispatches HTTP/WebSocket calls to the host vaibify backend. It is not host-only.

**Prefer `vaibify-do`** for editing `workflow.json` (it goes through schema validation and atomic save). Direct edits are now detected by the host's polling loop and the dashboard reloads on the next tick — but `vaibify-do` remains the canonical path. Files under `<project-repo>/.vaibify/test_markers/` and `/workspace/.vaibify/pipeline_state.json` are still outputs of backend actions; do not hand-edit them.

**Creating a new workflow from inside the container.** `vaibify-do` does not currently expose a `create-workflow` action. When the researcher is in toolkit mode (no workflow loaded — banner shows "Workflow: None") and asks for a workflow built around their existing toolkit work, write a fresh `workflow.json` directly at `<project-repo>/.vaibify/workflows/<slug>.json`. The dashboard polls for new workflows and surfaces yours within one tick: the toolkit banner gains a "N available" indicator and a toast offers to switch into it. Use `vaibify-do --describe create-step` (or any of the existing step actions) to learn the canonical step schema before writing the file by hand.

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

**User-only action protocol.** If `vaibify-do` responds with a JSON object containing `sRefusal: "user-only-action"`, do NOT retry. Tell the researcher concisely what you were about to do and ask them to click the matching button in the dashboard.

**Failure modes.** If `vaibify-do` reports `vaibify session not initialized` or `/tmp/vaibify-session.env` is missing, vaibify is not currently connected to this container — tell the researcher to open the dashboard and click the container so it reconnects. This is a "not connected yet" condition, not a "vaibify-do is host-only" condition. If it reports the host is unreachable or the session token is invalid, same fix: reconnect from the dashboard. Do not try workarounds.

## Key Paths

- `/workspace/` — All repositories and working files
- `/workspace/<RepoName>/.vaibify/workflows/` — Workflow JSON files (each repo can have its own)
- `/workspace/.vaibify/logs/` — Pipeline execution logs
- `/workspace/.vaibify/director.py` — Standalone pipeline executor

## Workflow System

Each vaibified repository has a `.vaibify/workflows/` directory with JSON files defining pipeline steps. Each step has:

- **Data Analysis Commands** (`saDataCommands`): Heavy computation
- **Data Files** (`saDataFiles`): Output files from data analysis
- **Plot Commands** (`saPlotCommands`): Visualization commands
- **Plot Files** (`saPlotFiles`): Expected figure outputs
- **Test Commands** (`saTestCommands`): Unit tests for data outputs
- **Interactive** (`bInteractive`): Steps requiring human judgment

Cross-step filename references inside command strings use `{StepNN.stem}` syntax (e.g., `{Step01.output_stem}`), where `NN` is the 1-based positional index of the step in `listSteps`. This is a script-side variable-substitution contract only — it is not how you name steps when talking to the researcher (see **How to refer to steps** above).

Run a workflow: `python /workspace/.vaibify/director.py --config <workflow.json>`

## Vaibified Repository Structure

A vaibified repo contains:
- One camelCase directory per step
- Scripts prefixed with `data` (analysis) or `plot` (visualization)
- A `Plot/` directory for output figures
- `.vaibify/workflows/*.json` defining the pipeline
- `.vaibify/CLAUDE.md` with project-specific context (symlinked to repo root)

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

When a researcher asks you to "make this workflow reach AICS Level N" or
"raise this to Level N", the AICS is a five-rung reproducibility ladder
defined in the project's vision document. Each rung is strictly stronger
than the last. Vaibify implements L1-L3; L4 and L5 are deliberate
non-goals at present.

Full definitions and what each rung proves vs. does not prove live in
`docs/vision.md` and `docs/reproducibility.md` in the host repo. The
summaries below are operational: what you need to do to get there.

### L1 — Self-Consistent

All workflow tests pass; every declared output file's content hash
matches what was recorded at verification time. The workflow must live
inside a git repository (vaibify enforces this at connect time; if a
researcher hits a "no git repo" error, the fix is `git init` in the
project directory).

Agent actions to reach L1:

1. `vaibify-do run-all` — execute the pipeline end to end.
2. `vaibify-do run-all-tests` — run every step's unit, integrity,
   qualitative, and quantitative tests.
3. `vaibify-do verify-only` — confirm declared outputs exist and their
   hashes match the recorded baseline.
4. `vaibify-do verify-manifest` — confirm `MANIFEST.sha256` at the
   project-repo root matches the current declared-output set.
5. `vaibify-do commit-canonical` — commit the verified state to the
   project repo.
6. Confirm `dictWorkflow["bVaibified"]` is `True`. If `False` after the
   prior steps succeeded, surface the discrepancy to the researcher —
   the flag is the ground truth.

`MANIFEST.sha256`, `requirements.lock`, and `.vaibify/environment.json`
are regenerated automatically when every step goes green; you do not
write them by hand.

### L2 — Published

Every canonical file's hash matches what is published at an immutable
remote authority (GitHub commit SHA, Overleaf revision, Zenodo DOI).

Agent actions to reach L2:

1. Confirm L1 first.
2. Confirm the reproducibility envelope exists at the project-repo
   root: `MANIFEST.sha256`, `requirements.lock`, and
   `.vaibify/environment.json`. If any is missing, the workflow is not
   all-green yet — return to L1.
3. **Surface to the user, do not invoke:**
   - "Push the current commit and manifest to GitHub" → researcher
     clicks Push to GitHub.
   - "Sync the manuscript files to Overleaf" → researcher clicks Push
     to Overleaf.
   - "Publish this to Zenodo for a permanent DOI" → researcher clicks
     Publish to Zenodo.

   These are trust-boundary actions. `push-to-github` is agent-safe
   (the route verifies the token owner matches the remote before
   pushing). `push-to-overleaf`, `publish-to-zenodo`, and
   `accept-plots-as-standard` remain user-only — publication to
   archival services requires human attestation.

4. After the researcher pushes, `vaibify-do verify-remote` confirms the
   remote hashes still match the local manifest.

### L3 — Reproducible

A third party can re-fetch the published artefacts, get byte-identical
files, and (with the recorded Docker image + pinned dependencies)
re-execute the workflow from source.

Agent actions to reach L3:

1. Confirm L2 first.
2. `vaibify-do check-l3-readiness` — returns per-criterion pass/fail
   for the six L3 readiness verifiers: manifest complete, dependency
   lock hash-pinned, environment digest-pinned, Dockerfile pinned,
   reproduce.sh present + in manifest, determinism declared. Use the
   gap dict to drive the rest of the L3 ladder.
3. `vaibify-do audit-determinism` — alias of check-l3-readiness for
   determinism-focused queries (RNG seeds, BLAS pinning,
   CUBLAS_WORKSPACE_CONFIG, /dev/urandom reads). Translate the
   determinism row into a per-step fix list for the researcher.
4. `vaibify-do generate-l3-envelope` — read the readiness card's
   missing-envelope rows and regenerate the manifest, requirements
   lock, and environment.json so the L3 verifiers go green.
5. `vaibify-do generate-reproduce-script` — render `reproduce.sh`
   from the active workflow when the readiness card flags it as
   absent or out of date.
6. `vaibify-do view-l3-attestation` — return the current
   `.vaibify/l3_attestation.json` plus the archived history of
   attempts. Useful to confirm whether a rebuild has been done or
   to explain why the L3 badge has not lit up.
7. `vaibify-do pin-base-image-digest` (user-only) — surface the
   Dockerfile rewrite suggestion. The actual Dockerfile edit is a
   researcher decision; never invoke silently.
8. `vaibify-do verify-l3-reproducibility` (user-only) — kicks off
   the expensive rebuild + hash compare that writes the L3
   attestation. Surface as a researcher request, never as an
   autonomous action; the rebuild can take hours.

### L4 — Archived, L5 — Attested

Out of vaibify's current scope. If a researcher asks for L4 or L5, tell
them honestly that vaibify targets L3 as its ceiling and point them at
`docs/vision.md` for the full ladder. L4 requires a manifest of every
external input with hashes plus archival snapshots; L5 requires
independent third parties to sign attestations in a transparency log.

### How vaibify tracks ladder state

Each rung the researcher reaches is observable from the workflow's
backend state, not just from the agent's reasoning. When you report
"L1 reached", correlate with the state the researcher can see:

- **L1** is signalled by `dictWorkflow["bVaibified"] = True`, set when
  every step's verification (unit tests, integrity, qualitative,
  quantitative, dependencies, user attestation) is green. The dashboard
  surfaces this with a theme change and a checkmark next to the
  workflow name. If you claim L1 but `bVaibified` is still `False`,
  something is missing — re-check the per-step verification status
  before reporting.
- **L2 and L3** do not yet have analogous backend flags or dashboard
  signals; that work is on the roadmap. For now, report L2/L3 status by
  enumerating what you confirmed (envelope files present, remote hashes
  match, container digest captured, no unseeded randomness) rather than
  by inspecting a single flag.

When the L2/L3 signals land, the convention will follow L1: a boolean
on the workflow dict, surfaced visually in the dashboard. Update this
section then.

### Quick reference

When asked to reach Level N, walk these gates in order, stopping at N:

- **L1**: `run-all` → `run-all-tests` → `verify-only` →
  `verify-manifest` → `commit-canonical` → confirm `bVaibified=True`.
- **L2**: L1 + envelope present + **surface** push requests.
- **L3**: L2 + container digest in `environment.json` + hash-pinned
  `requirements.lock` + no unseeded-randomness badges + recommend
  `vaibify reproduce`.
- **L4, L5**: not supported; explain and stop.

Never silently invoke `push-to-overleaf`, `publish-to-zenodo`, or
`accept-plots-as-standard` — those are user-only by design. Surface
the request; let the researcher click. `push-to-github` is
agent-callable when the researcher requests it.

## Conventions

- Follow Hungarian notation for variable names (b=bool, i=int, f=float, s=string, etc.)
- Function names start with return-type prefix (fb, fi, fs, fn, flist, fdict)
- Functions should be under 20 lines
- Output figures go in `Plot/` subdirectories

## Creating New Pipeline Steps

When a user asks you to create a new analysis or plot (e.g., "Create a script that computes
the probability distribution of water the Earth formed with and a plot of it"), follow this
protocol. The goal is a fully wired step: scripts, outputs, dependencies, and workflow JSON
entry — with zero untracked files.

### Phase 1: Discover Context

1. Find the workflow JSON: `find /workspace -maxdepth 4 -path '*/.vaibify/workflows/*.json'`
2. Read `listSteps` to understand existing steps, their outputs, and available variables.
3. Identify **backward dependencies**: which existing steps produce data this new step needs?
   Look for output files in `saDataFiles` that match the needed inputs.
4. Identify **forward dependents**: search all steps for `{StepNN.*}` references that would
   be affected if you insert (rather than append) the new step. **Strongly prefer appending**
   new steps at the end to avoid renumbering. If insertion is required, enumerate every
   reference that must change and confirm with the user before proceeding.
5. Determine placement: the new step must come after all its dependencies.

### Phase 2: Name and Structure

1. Choose a **camelCase directory name** that captures the scientific goal, not the method.
   No abbreviations for words under 8 characters. Examples: `waterProbabilityDistribution`,
   `cumulativeXuvFlux`, `keplerFlareFit`.
2. Create the directory at the same level as other step directories in the repository.
3. Name scripts with standard prefixes:
   - `data<Purpose>.py` for data analysis (e.g., `dataWaterProbability.py`)
   - `plot<Purpose>.py` for visualization (e.g., `plotWaterProbability.py`)
4. Name output files to match the step directory or scientific content:
   - Data: `waterProbability_samples.npy`, `waterProbability_stats.json`
   - Plot: `{sPlotDirectory}/WaterProbability.{sFigureType}`

### Phase 3: Write the Scripts

Follow the style guide (check the repo's CLAUDE.md first, then the workspace CLAUDE.md):

1. **Hungarian notation** for all variables (b=bool, i=int, f=float, s=string, da=array of doubles, etc.)
2. **Function prefixes** based on return type (`fb`, `fi`, `fs`, `fn`, `fda`, `fdict`, `flist`)
3. **Functions under 20 lines** — extract reusable blocks into separate functions
4. **No abbreviations** for words under 8 characters
5. **Import vplot** for any matplotlib plotting
6. **Accept inputs as command-line arguments — this is a strict requirement, not a style suggestion**. Every file your script reads from another step *must* be a CLI argument, and the workflow JSON command *must* reference it via a \`{StepNN.varname}\` token. Hardcoded paths to another step's outputs (e.g. \`open("../OtherStep/output.json")\`) are invisible to vaibify's dependency parser and silently break the AICS Level 1 contract. Your own step-directory files may be hardcoded; the boundary is the step.
7. **CLI naming convention**: kebab-case for the argument (\`--flare-samples\`), snake_case for the matching token (\`{Step02.flare_samples}\`). The variable name in the token is the basename (without extension) of the producer step's \`saDataFiles\` entry.
8. **Use argparse, not raw sys.argv**, so the contract is explicit.
9. **Data outputs** go in the step's own directory
10. **Plot outputs** go in \`{sPlotDirectory}/\`

Worked example — A02 (producer) declares its output; A03 (consumer) reads it via token:

\`\`\`json
{
  "iIndex": 2, "sName": "KeplerFfd",
  "saDataCommands": ["python dataKeplerFfd.py"],
  "saDataFiles": ["flare_samples.npy"]
}
{
  "iIndex": 3, "sName": "FfdAgeComparison",
  "saPlotCommands": [
    "python plotFfd.py --flare-samples {Step02.flare_samples} {sPlotDirectory}/ffd.{sFigureType}"
  ]
}
\`\`\`

A03's plot script uses argparse to accept \`--flare-samples\`; the director substitutes the actual path at runtime. The A02 → A03 edge becomes visible to vaibify automatically.

Data script pattern:
```python
#!/usr/bin/env python3
"""One-line description of what this script computes."""
import sys
# ... imports ...

def fda<Core>(...):
    """Core computation."""
    ...

def fn<Save>(...):
    """Save results to disk."""
    ...

if __name__ == "__main__":
    # Parse arguments, load upstream data, compute, save
```

Plot script pattern:
```python
#!/usr/bin/env python3
"""One-line description of what this plots."""
import sys
import matplotlib.pyplot as plt
import vplot

def fnPlot<Name>(daData, sOutputPath):
    """Generate the figure."""
    ...

if __name__ == "__main__":
    sOutputPath = sys.argv[1]
    # Load data from step directory, generate plot
```

### Phase 4: Update the Workflow JSON

Add a new entry to `listSteps`:

```json
{
    "sName": "Human-Readable Step Name",
    "sDirectory": "stepDirectoryName",
    "bRunEnabled": true,
    "bPlotOnly": false,
    "bInteractive": false,
    "saDataCommands": [
        "python dataWaterProbability.py {Step06.lxuv_constraints}"
    ],
    "saDataFiles": [
        "waterProbability_samples.npy",
        "waterProbability_stats.json"
    ],
    "saTestCommands": [],
    "saPlotCommands": [
        "python plotWaterProbability.py {sPlotDirectory}/WaterProbability.{sFigureType}"
    ],
    "saPlotFiles": [
        "{sPlotDirectory}/WaterProbability.{sFigureType}"
    ]
}
```

Rules:
- Every output file MUST be declared in `saDataFiles` or `saPlotFiles` — no untracked files.
- Every input from another step MUST use `{StepNN.stem}` syntax — no implicit imports.
- `saTestCommands` should include a basic sanity check (e.g., file exists, has expected shape).
- `bPlotOnly: true` only if the step has no data commands and only plots pre-existing data.
- `bInteractive: true` only for steps requiring human judgment (e.g., visual inspection).

### Phase 5: Verify

1. Run the data script to confirm it executes without errors.
2. Run the plot script to confirm it produces a figure.
3. Run `python /workspace/.vaibify/director.py --config <workflow.json> --verify-only` if available.
4. Report the step number, directory, and output files to the user.

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
  dependencies** (files from other steps — belong in `{StepNN.stem}` references).

## Important

- Do not modify scientific calculations without explicit direction
- Test changes with `pytest` before committing
- All repositories are public or will be — never embed secrets in code
CLAUDEMD
    echo "[vaib] Generated workspace CLAUDE.md."
    fnLinkRepoClaudeMd
}

# ---------------------------------------------------------------------------
# fnLinkRepoClaudeMd: Symlink .vaibify/CLAUDE.md to repo root for each repo
# ---------------------------------------------------------------------------
fnLinkRepoClaudeMd() {
    for sVaibDir in "${WORKSPACE}"/*/.vaibify; do
        [ -d "${sVaibDir}" ] || continue
        local sRepoDir
        sRepoDir=$(dirname "${sVaibDir}")
        local sSource="${sVaibDir}/CLAUDE.md"
        local sTarget="${sRepoDir}/CLAUDE.md"
        if [ -f "${sSource}" ] && [ ! -f "${sTarget}" ]; then
            ln -s ".vaibify/CLAUDE.md" "${sTarget}"
            echo "[vaib]   Linked CLAUDE.md in $(basename "${sRepoDir}")"
        fi
    done
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
# fnMigrateWorkspaceOwnership: One-time chown for pre-split-entrypoint volumes
# Uses ``find -uid 0 -print -quit`` so the scan is deep (catches nested
# residue such as ``.git/objects/3f`` left by a pre-split host-side git
# operation, which an earlier top-level-only check missed and which
# blocks the in-container agent from writing further objects into that
# prefix) but still fast in the common case — find exits on the first
# match, so a clean volume costs at most one directory walk.
# ---------------------------------------------------------------------------
fnMigrateWorkspaceOwnership() {
    if [ ! -d "${WORKSPACE}" ]; then
        return
    fi
    if [ -z "$(find "${WORKSPACE}" -uid 0 -print -quit 2>/dev/null)" ]; then
        return
    fi
    echo "[vaib] One-time migration: adjusting workspace ownership..."
    chown -R --no-dereference \
        "${CONTAINER_USER}:${CONTAINER_USER}" "${WORKSPACE}"
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
    fnPersistGitConfig
    fnParseReposConf
    fnSyncAllRepos
    if command -v claude > /dev/null 2>&1; then
        fnPersistClaudeConfig
        fnConfigureClaudeTheme
        fnConfigureClaudeAutoUpdate
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
