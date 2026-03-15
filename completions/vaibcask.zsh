#!/bin/zsh
# Zsh tab-completion for vaibcask (vc), vc_push, and vc_pull.
#
# Source this file from your shell configuration:
#   [ -f "/path/to/VaibCask/completions/vaibcask.zsh" ] \
#       && . "/path/to/VaibCask/completions/vaibcask.zsh"

# Ensure the completion system is initialized
if ! typeset -f compdef > /dev/null 2>&1; then
    autoload -Uz compinit && compinit
fi

# ---------------------------------------------------------------------------
# _fnReadVcConfigZsh: Set VC_NAME and VC_WORKSPACE from vaibcask.yml
# ---------------------------------------------------------------------------
_fnReadVcConfigZsh() {
    VC_NAME=$(python3 -c "import yaml; print(yaml.safe_load(open('vaibcask.yml'))['projectName'])" 2>/dev/null || true)
    VC_WORKSPACE=$(python3 -c "import yaml; print(yaml.safe_load(open('vaibcask.yml')).get('workspaceRoot','/workspace'))" 2>/dev/null || true)
    if [ -z "${VC_NAME}" ]; then
        VC_NAME="vaibcask"
    fi
    if [ -z "${VC_WORKSPACE}" ]; then
        VC_WORKSPACE="/workspace"
    fi
}

# ---------------------------------------------------------------------------
# _fnListContainerPathsZsh: Query the running container for matching paths
# Arguments: sPartial - the partial path typed so far
# Returns: 0 if matches were added, 1 otherwise
# ---------------------------------------------------------------------------
_fnListContainerPathsZsh() {
    local sPartial="$1"
    _fnReadVcConfigZsh
    if ! command -v docker > /dev/null 2>&1; then
        return 1
    fi
    if ! docker container inspect "${VC_NAME}" > /dev/null 2>&1; then
        return 1
    fi
    local sOutput
    sOutput="$(docker exec "${VC_NAME}" sh -c "ls -1dp ${VC_WORKSPACE}/${sPartial}* 2>/dev/null" \
        | sed "s|^${VC_WORKSPACE}/||")"
    if [ -z "${sOutput}" ]; then
        return 1
    fi
    local daMatches=("${(@f)sOutput}")
    compadd -S '' -- "${daMatches[@]}"
    return 0
}

# ---------------------------------------------------------------------------
# _vaibcask: Complete subcommands and flags for vaibcask
# ---------------------------------------------------------------------------
_vaibcask() {
    local sCurrent="${words[CURRENT]}"
    local sPrevious="${words[CURRENT-1]}"

    case "${sPrevious}" in
        vaibcask|vc)
            compadd -- init build start stop status destroy connect verify push pull setup gui config publish
            return
            ;;
        config)
            compadd -- export import edit
            return
            ;;
        publish)
            compadd -- archive workflow
            return
            ;;
        init)
            compadd -- --template --force
            return
            ;;
        build)
            compadd -- --no-cache
            return
            ;;
        start)
            compadd -- --gui --jupyter
            return
            ;;
    esac

    if [[ "${sCurrent}" == -* ]]; then
        compadd -- --help -h
    fi
}
compdef _vaibcask vaibcask
compdef _vaibcask vc

# ---------------------------------------------------------------------------
# _vc_pull: Complete container paths for vc_pull sources
# ---------------------------------------------------------------------------
_vc_pull() {
    local sCurrent="${words[CURRENT]}"
    if [[ "${sCurrent}" == -* ]]; then
        compadd -- -a -L -r -R --help -h
        return
    fi
    _fnListContainerPathsZsh "${sCurrent}" || _files
}
compdef _vc_pull vc_pull

# ---------------------------------------------------------------------------
# _vc_push: Complete local files for sources, container paths for the
# destination (after at least one source has been typed)
# ---------------------------------------------------------------------------
_vc_push() {
    local sCurrent="${words[CURRENT]}"
    if [[ "${sCurrent}" == -* ]]; then
        compadd -- -a -L -r -R --help -h
        return
    fi
    local iNonOptionCount=0
    local iIndex
    for (( iIndex=2; iIndex < CURRENT; iIndex++ )); do
        case "${words[iIndex]}" in
            -*) ;;
            *)  iNonOptionCount=$(( iNonOptionCount + 1 )) ;;
        esac
    done
    if [ "${iNonOptionCount}" -ge 1 ]; then
        _fnListContainerPathsZsh "${sCurrent}" || _files
    else
        _files
    fi
}
compdef _vc_push vc_push
