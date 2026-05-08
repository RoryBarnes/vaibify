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
S_ENTRYPOINT_VERSION="1"

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
# fnConfigureGit: Configure GitHub authentication via token
# ---------------------------------------------------------------------------
fnConfigureGit() {
    local sToken
    sToken=$(fsReadGitHubToken)
    git config --system url."https://github.com/".insteadOf \
        "git@github.com:"
    if [ -n "${sToken}" ]; then
        echo "[vaib] GitHub credentials detected."
        git config --system credential.helper store
        local sCredLine="https://x-access-token:${sToken}@github.com"
        echo "${sCredLine}" > "${HOME}/.git-credentials"
        chmod 600 "${HOME}/.git-credentials"
        local sContainerUser
        sContainerUser="${CONTAINER_USER:-}"
        if [ -n "${sContainerUser}" ] && [ "${sContainerUser}" != "root" ]; then
            local sUserHome
            sUserHome=$(eval echo "~${sContainerUser}")
            echo "${sCredLine}" > "${sUserHome}/.git-credentials"
            chown "${sContainerUser}" "${sUserHome}/.git-credentials"
            chmod 600 "${sUserHome}/.git-credentials"
        fi
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
- "push to GitHub / Overleaf / Zenodo" → USER-ONLY — surface the request, do not run

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
6. **Accept paths as command-line arguments** so the director can resolve `{StepNN.stem}`,
   `{sPlotDirectory}`, and `{sFigureType}` variables
7. **Data outputs** go in the step's own directory
8. **Plot outputs** go in `{sPlotDirectory}/`

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
# fnRunStartupSequence: Execute the full pre-gosu startup pipeline
# Centralised so the EXIT trap can decide success vs failure from one $?.
# ---------------------------------------------------------------------------
fnRunStartupSequence() {
    fnPrintBanner
    fnCreateVaibifyDirectory
    fnWriteClaudeMd
    fnPersistGitConfig
    fnConfigureGit
    fnParseReposConf
    fnSyncAllRepos
    if command -v claude > /dev/null 2>&1; then
        fnPersistClaudeConfig
        fnConfigureClaudeTheme
        fnConfigureClaudeAutoUpdate
    fi
    fnBuildBinaries
    fnLoadBinariesEnv
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
# ===========================================================================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -euo pipefail
    trap 'fnHandleStartupExit $?' EXIT
    fnRunStartupSequence
    fnWriteReadinessMarker "ok" ""
    trap - EXIT
    chown -R "${CONTAINER_USER}:${CONTAINER_USER}" "${WORKSPACE}"
    exec gosu "${CONTAINER_USER}" "$@"
fi
