#!/bin/sh
# installVaibify.sh - Install Vaibify and its host dependencies.
#
# Detects the operating system and package manager, installs Docker and
# the GitHub CLI, clones the Vaibify repository, creates symlinks
# so that "vaibify" and "vc" are available on PATH, and adds the
# Vaibify bin directory to the user's shell configuration.
#
# Usage:
#   sh installVaibify.sh [-y|--yes] [--claude]

set -e

VC_REPO="https://github.com/RoryBarnes/Vaibify.git"
bInstallClaude=false
bAssumeYes=false

# ---------------------------------------------------------------------------
fnPrintError() { echo "ERROR: $1" >&2; }

# ---------------------------------------------------------------------------
# fnParseArguments: Handle command-line flags
# ---------------------------------------------------------------------------
fnParseArguments() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -y|--yes)
                bAssumeYes=true
                ;;
            --claude)
                bInstallClaude=true
                ;;
            *)
                fnPrintError "Unknown option: $1"
                echo "Usage: sh installVaibify.sh [-y|--yes] [--claude]" >&2
                exit 1
                ;;
        esac
        shift
    done
}

# ---------------------------------------------------------------------------
# fnDetectPlatform: Set sPlatform to "Darwin" or "Linux"
# ---------------------------------------------------------------------------
fnDetectPlatform() {
    sPlatform="$(uname -s)"
    if [ "${sPlatform}" != "Darwin" ] && [ "${sPlatform}" != "Linux" ]; then
        fnPrintError "Unsupported platform: ${sPlatform}"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# fnDetectMacPackageManager: Set sMacPackageManager to "port", "brew", or exit
# ---------------------------------------------------------------------------
fnDetectMacPackageManager() {
    if command -v port > /dev/null 2>&1; then
        sMacPackageManager="port"
    elif command -v brew > /dev/null 2>&1; then
        sMacPackageManager="brew"
    else
        fnPrintError "Neither MacPorts nor Homebrew found."
        echo "Install one of:" >&2
        echo "  MacPorts: https://www.macports.org/install.php" >&2
        echo "  Homebrew: https://brew.sh/" >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# fnInstallMacPorts: Install Docker, Colima, and gh via MacPorts
# ---------------------------------------------------------------------------
fnInstallMacPorts() {
    echo "[install] Installing docker, colima, gh, XQuartz, and xhost via MacPorts..."
    if [ "${bAssumeYes}" = true ]; then
        sudo port -N install docker colima gh xorg-server xhost
    else
        sudo port install docker colima gh xorg-server xhost
    fi
}

# ---------------------------------------------------------------------------
# fnInstallHomebrew: Install Docker, Colima, and gh via Homebrew
# ---------------------------------------------------------------------------
fnInstallHomebrew() {
    echo "[install] Installing docker, colima, gh, and XQuartz via Homebrew..."
    if [ "${bAssumeYes}" = true ]; then
        NONINTERACTIVE=1 brew install docker colima gh
        NONINTERACTIVE=1 brew install --cask xquartz
    else
        brew install docker colima gh
        brew install --cask xquartz
    fi
}

# ---------------------------------------------------------------------------
# fnConfigureXquartz: Enable TCP connections for X11 display forwarding
# ---------------------------------------------------------------------------
fnConfigureXquartz() {
    echo "[install] Enabling XQuartz network connections for display forwarding..."
    defaults write org.xquartz.X11 nolisten_tcp -bool false
    defaults write org.macports.X11 nolisten_tcp -bool false
    echo "[install] XQuartz configured. Restart XQuartz for this to take effect."
}

# ---------------------------------------------------------------------------
# fnInstallDebianDocker: Add Docker APT repository and install engine
# ---------------------------------------------------------------------------
fnInstallDebianDocker() {
    echo "[install] Installing Docker Engine on Debian/Ubuntu..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io
}

# ---------------------------------------------------------------------------
# fnInstallDebianGitHub: Add GitHub CLI APT repository and install gh
# ---------------------------------------------------------------------------
fnInstallDebianGitHub() {
    echo "[install] Installing GitHub CLI..."
    sudo mkdir -p -m 755 /etc/apt/keyrings
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
    sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
        https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y gh
}

# ---------------------------------------------------------------------------
# fnInstallDebian: Install Docker Engine and gh on Debian/Ubuntu
# ---------------------------------------------------------------------------
fnInstallDebian() {
    fnInstallDebianDocker
    fnInstallDebianGitHub
    sudo systemctl enable --now docker
    sudo usermod -aG docker "${USER}"
    echo "[install] Log out and back in for Docker group to take effect."
}

# ---------------------------------------------------------------------------
# fnInstallFedora: Install Docker Engine and gh on Fedora/RHEL
# ---------------------------------------------------------------------------
fnInstallFedora() {
    echo "[install] Installing Docker Engine on Fedora/RHEL..."
    sudo dnf install -y dnf-plugins-core
    sudo dnf config-manager --add-repo \
        https://download.docker.com/linux/fedora/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io

    echo "[install] Installing GitHub CLI..."
    sudo dnf install -y 'dnf-command(config-manager)'
    sudo dnf config-manager --add-repo \
        https://cli.github.com/packages/rpm/gh-cli.repo
    sudo dnf install -y gh

    sudo systemctl enable --now docker
    sudo usermod -aG docker "${USER}"
    echo "[install] Log out and back in for Docker group to take effect."
}

# ---------------------------------------------------------------------------
# fnCloneAndLink: Clone Vaibify and create symlinks
# ---------------------------------------------------------------------------
fnCloneAndLink() {
    local sBinDir="$1"

    if [ -x "./vaibify" ] && [ -d "./docker" ]; then
        echo "[install] Already inside the Vaibify repository."
    elif [ -d "Vaibify" ]; then
        echo "[install] Vaibify directory already exists. Skipping clone."
        cd Vaibify
    else
        echo "[install] Cloning Vaibify..."
        git clone "${VC_REPO}"
        cd Vaibify
    fi

    echo "[install] Creating symlink at ${sBinDir}/vaibify..."
    sudo ln -sf "$(pwd)/vaibify/cli/main.py" "${sBinDir}/vaibify"
    echo "[install] Creating symlink at ${sBinDir}/vc..."
    sudo ln -sf "$(pwd)/vaibify/cli/main.py" "${sBinDir}/vc"

    fnConfigureShellPath "$(pwd)/bin"
    fnConfigureCompletions "$(pwd)"
}

# ---------------------------------------------------------------------------
# fsDetectShellRcFile: Return the RC file path for the given shell
# Arguments: sShellName
# ---------------------------------------------------------------------------
fsDetectShellRcFile() {
    local sShellName="$1"
    case "${sShellName}" in
        zsh)  echo "${HOME}/.zshrc" ;;
        bash)
            if [ "$(uname -s)" = "Darwin" ]; then
                echo "${HOME}/.bash_profile"
            else
                echo "${HOME}/.bashrc"
            fi
            ;;
        fish) echo "${HOME}/.config/fish/config.fish" ;;
        *)    echo "${HOME}/.profile" ;;
    esac
}

# ---------------------------------------------------------------------------
# fsFormatExportLine: Return the PATH export statement for the given shell
# Arguments: sShellName sBinDirectory
# ---------------------------------------------------------------------------
fsFormatExportLine() {
    local sShellName="$1"
    local sBinDirectory="$2"
    if [ "${sShellName}" = "fish" ]; then
        echo "set -gx PATH ${sBinDirectory} \$PATH"
    else
        echo "export PATH=\"${sBinDirectory}:\$PATH\""
    fi
}

# ---------------------------------------------------------------------------
# fnAppendToShellConfig: Append a tagged line to an RC file
# Arguments: sRcFile sLine
# ---------------------------------------------------------------------------
fnAppendToShellConfig() {
    local sRcFile="$1"
    local sLine="$2"
    {
        echo ""
        echo "# Added by Vaibify installer"
        echo "${sLine}"
    } >> "${sRcFile}"
}

# ---------------------------------------------------------------------------
# fnConfigureShellPath: Add the Vaibify bin directory to PATH
# Arguments: sBinDirectory
# ---------------------------------------------------------------------------
fnConfigureShellPath() {
    local sBinDirectory="$1"
    local sShellName
    sShellName="$(basename "${SHELL:-/bin/sh}")"
    local sRcFile
    sRcFile=$(fsDetectShellRcFile "${sShellName}")
    if [ -z "${sRcFile}" ]; then
        echo "[install] Could not determine shell config. Add manually:"
        echo "  export PATH=\"${sBinDirectory}:\$PATH\""
        return
    fi
    if [ -f "${sRcFile}" ] && grep -qF "${sBinDirectory}" "${sRcFile}" 2>/dev/null; then
        echo "[install] PATH already configured in ${sRcFile}."
        return
    fi
    fnAppendToShellConfig "${sRcFile}" "$(fsFormatExportLine "${sShellName}" "${sBinDirectory}")"
    echo "[install] Added ${sBinDirectory} to PATH in ${sRcFile}."
    echo "[install] Open a new terminal or run: . ${sRcFile}"
}

# ---------------------------------------------------------------------------
# fnConfigureCompletions: Source the appropriate tab-completion script
# Arguments: sVcDirectory
# ---------------------------------------------------------------------------
fnConfigureCompletions() {
    local sVcDirectory="$1"
    local sShellName
    sShellName="$(basename "${SHELL:-/bin/sh}")"
    local sCompletionFile=""
    case "${sShellName}" in
        bash) sCompletionFile="${sVcDirectory}/completions/vaibify.bash" ;;
        zsh)  sCompletionFile="${sVcDirectory}/completions/vaibify.zsh" ;;
        *)    return ;;
    esac
    local sRcFile
    sRcFile=$(fsDetectShellRcFile "${sShellName}")
    if [ -f "${sRcFile}" ] && grep -qF "/Vaibify/completions/" "${sRcFile}" 2>/dev/null; then
        echo "[install] Completions already configured in ${sRcFile}."
        return
    fi
    fnAppendToShellConfig "${sRcFile}" "[ -f \"${sCompletionFile}\" ] && . \"${sCompletionFile}\""
    echo "[install] Added tab-completion to ${sRcFile}."
}

# ---------------------------------------------------------------------------
# fnLinkColimaSocket: Symlink Colima socket to the standard Docker path
# ---------------------------------------------------------------------------
fnLinkColimaSocket() {
    local sColimaSocket="${HOME}/.colima/default/docker.sock"
    local sStandardSocket="/var/run/docker.sock"
    if [ -S "${sStandardSocket}" ]; then
        echo "[install] ${sStandardSocket} already exists."
        return
    fi
    echo "[install] Linking Colima socket to ${sStandardSocket}..."
    sudo ln -sf "${sColimaSocket}" "${sStandardSocket}"
}

# ---------------------------------------------------------------------------
# fnEnableClaude: Mark Vaibify to include Claude Code in Docker image
# ---------------------------------------------------------------------------
fnEnableClaude() {
    local sVcDirectory="$1"

    touch "${sVcDirectory}/.claude_enabled"
    echo "[install] Claude Code will be included in the Docker image."
    echo "[install] The image will be built with Claude Code on first run."
}

# ===========================================================================
# Main
# ===========================================================================
fnParseArguments "$@"
fnDetectPlatform
echo "[install] Detected platform: ${sPlatform}"

if [ "${sPlatform}" = "Darwin" ]; then
    fnDetectMacPackageManager
    echo "[install] Using package manager: ${sMacPackageManager}"
    if [ "${sMacPackageManager}" = "port" ]; then
        fnInstallMacPorts
        fnCloneAndLink "/opt/local/bin"
    else
        fnInstallHomebrew
        fnCloneAndLink "$(brew --prefix)/bin"
    fi
    fnConfigureXquartz
    fnLinkColimaSocket
    echo ""
    echo "[install] Installation complete."
    echo "[install] Start Colima before first use:"
    iCores=$(sysctl -n hw.ncpu)
    echo "  colima start --cpu $(( iCores - 1 )) --memory 8"

elif [ "${sPlatform}" = "Linux" ]; then
    if command -v apt-get > /dev/null 2>&1; then
        fnInstallDebian
        fnCloneAndLink "/usr/local/bin"
    elif command -v dnf > /dev/null 2>&1; then
        fnInstallFedora
        fnCloneAndLink "/usr/local/bin"
    else
        fnPrintError "Unsupported Linux distribution."
        echo "Vaibify requires apt (Debian/Ubuntu) or dnf (Fedora/RHEL)." >&2
        exit 1
    fi
    echo ""
    echo "[install] Installation complete."
fi

if [ "${bInstallClaude}" = true ]; then
    fnEnableClaude "$(pwd)"
fi

if [ "${sPlatform}" = "Darwin" ]; then
    echo "[install] After starting Colima, run 'vaibify init' to begin."
else
    echo "[install] After logging out and back in, run 'vaibify init' to begin."
fi
