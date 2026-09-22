#!/bin/zsh
# Zsh tab-completion for vaibify (vc) and its push/pull helpers.
#
# Source this file from your shell configuration:
#   [ -f "/path/to/Vaibify/completions/vaibify.zsh" ] \
#       && . "/path/to/Vaibify/completions/vaibify.zsh"

# Ensure the completion system is initialized
if ! typeset -f compdef > /dev/null 2>&1; then
    autoload -Uz compinit && compinit
fi

# ---------------------------------------------------------------------------
# _fnReadVcConfigZsh: Set VC_NAME and VC_WORKSPACE from vaibify.yml
# ---------------------------------------------------------------------------
_fnReadVcConfigZsh() {
    VC_NAME=$(python3 -c "import yaml; print(yaml.safe_load(open('vaibify.yml'))['projectName'])" 2>/dev/null || true)
    VC_WORKSPACE=$(python3 -c "import yaml; print(yaml.safe_load(open('vaibify.yml')).get('workspaceRoot','/workspace'))" 2>/dev/null || true)
    if [ -z "${VC_NAME}" ]; then
        VC_NAME="vaibify"
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
# _vaibify: Complete subcommands and flags for vaibify
# ---------------------------------------------------------------------------
_vaibify() {
    local sCurrent="${words[CURRENT]}"
    local sPrevious="${words[CURRENT-1]}"

    case "${sPrevious}" in
        vaibify|vc)
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

    # The subcommand, not the previous word: `vaibify push a b` asks
    # about position, and words[CURRENT-1] is the previous ARGUMENT
    # once one has been typed.
    local sSubcommand="${words[2]}"
    if [[ "${sSubcommand}" == "push" || "${sSubcommand}" == "pull" ]]; then
        _fnCompleteTransferArgumentZsh "${sSubcommand}" 3
        return
    fi

    if [[ "${sCurrent}" == -* ]]; then
        compadd -- --help -h
    fi
}
compdef _vaibify vaibify
compdef _vaibify vc

# ---------------------------------------------------------------------------
# _fnCompleteTransferArgumentZsh: Complete one push/pull argument
# Arguments: sDirection    - "push" or "pull"
#            iFirstArgument - index in $words where arguments start
#
# push reads from the host and writes into the container, pull the
# other way round, so the same position means opposite things. One
# function serves `vaibify push`, `vaibify pull`, and the helper
# aliases, which is why the argument offset is a parameter.
# ---------------------------------------------------------------------------
_fnCompleteTransferArgumentZsh() {
    local sDirection="$1"
    local iFirstArgument="$2"
    local sCurrent="${words[CURRENT]}"
    if [[ "${sCurrent}" == -* ]]; then
        compadd -- --project -p --help -h
        return
    fi
    local iTypedCount=0
    local iIndex
    for (( iIndex=iFirstArgument; iIndex < CURRENT; iIndex++ )); do
        case "${words[iIndex]}" in
            -*) ;;
            *)  iTypedCount=$(( iTypedCount + 1 )) ;;
        esac
    done
    local bWantsContainerPath=0
    if [[ "${sDirection}" == "pull" && "${iTypedCount}" -eq 0 ]]; then
        bWantsContainerPath=1
    fi
    if [[ "${sDirection}" == "push" && "${iTypedCount}" -ge 1 ]]; then
        bWantsContainerPath=1
    fi
    if [ "${bWantsContainerPath}" -eq 1 ]; then
        _fnListContainerPathsZsh "${sCurrent}" || _files
    else
        _files
    fi
}

# The helper aliases are `vaibify push` / `vaibify pull` by another
# name, so their arguments begin one word earlier than the subcommand
# form. Bound to the names shellSetup.py actually creates: these were
# `vc_push` / `vc_pull` until they were renamed, and the completions
# kept registering against the retired names, which is how both
# helpers silently stopped completing anything.
_vaibify_push() { _fnCompleteTransferArgumentZsh push 2 }
_vaibify_pull() { _fnCompleteTransferArgumentZsh pull 2 }
compdef _vaibify_push vaibify_push
compdef _vaibify_push vaib_push
compdef _vaibify_pull vaibify_pull
compdef _vaibify_pull vaib_pull
