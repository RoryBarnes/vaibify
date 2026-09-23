#!/bin/bash
# Bash tab-completion for vaibify (vc) and its push/pull helpers.
#
# Source this file from your shell configuration:
#   [ -f "/path/to/Vaibify/completions/vaibify.bash" ] \
#       && . "/path/to/Vaibify/completions/vaibify.bash"

# ---------------------------------------------------------------------------
# _fnListContainerPaths: Query the running container for matching paths
# Arguments: sPartial - the partial path typed so far
#            sContainerName - the Docker container name
#            sWorkspaceRoot - the workspace root inside the container
# Prints: matching paths relative to the workspace root, one per line
# ---------------------------------------------------------------------------
_fnListContainerPaths() {
    local sPartial="$1"
    local sContainerName="$2"
    local sWorkspaceRoot="$3"
    if ! command -v docker > /dev/null 2>&1; then
        return
    fi
    if ! docker container inspect "${sContainerName}" > /dev/null 2>&1; then
        return
    fi
    docker exec "${sContainerName}" sh -c "ls -1dp ${sWorkspaceRoot}/${sPartial}* 2>/dev/null" \
        | sed "s|^${sWorkspaceRoot}/||"
}

# ---------------------------------------------------------------------------
# _fnReadVcConfig: Set VC_NAME and VC_WORKSPACE from vaibify.yml
# ---------------------------------------------------------------------------
_fnReadVcConfig() {
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
# _fnCompleteVaibify: Complete subcommands and flags for vaibify
# ---------------------------------------------------------------------------
_fnCompleteVaibify() {
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    local sPrevious="${COMP_WORDS[COMP_CWORD-1]}"

    case "${sPrevious}" in
        vaibify|vc)
            COMPREPLY=($(compgen -W "init build start stop status destroy connect verify push pull setup gui config publish" -- "${sCurrent}"))
            return
            ;;
        config)
            COMPREPLY=($(compgen -W "export import edit" -- "${sCurrent}"))
            return
            ;;
        publish)
            COMPREPLY=($(compgen -W "archive workflow" -- "${sCurrent}"))
            return
            ;;
        init)
            COMPREPLY=($(compgen -W "--template --force" -- "${sCurrent}"))
            return
            ;;
        build)
            COMPREPLY=($(compgen -W "--no-cache" -- "${sCurrent}"))
            return
            ;;
        start)
            COMPREPLY=($(compgen -W "--gui --jupyter" -- "${sCurrent}"))
            return
            ;;
    esac

    # The subcommand, not the previous word: once an argument has
    # been typed, COMP_WORDS[COMP_CWORD-1] is that argument.
    local sSubcommand="${COMP_WORDS[1]}"
    if [[ "${sSubcommand}" == "push" || "${sSubcommand}" == "pull" ]]; then
        _fnCompleteTransferArgument "${sSubcommand}" 2
        return
    fi

    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "--help -h" -- "${sCurrent}"))
    fi
}
complete -o default -F _fnCompleteVaibify vaibify
complete -o default -F _fnCompleteVaibify vc

# ---------------------------------------------------------------------------
# _fnCompleteTransferArgument: Complete one push/pull argument
# Arguments: sDirection     - "push" or "pull"
#            iFirstArgument - index in COMP_WORDS where arguments start
#
# push reads from the host and writes into the container, pull the
# other way round, so the same position means opposite things. One
# function serves `vaibify push`, `vaibify pull`, and the helper
# aliases, which is why the argument offset is a parameter.
# ---------------------------------------------------------------------------
_fnCompleteTransferArgument() {
    local sDirection="$1"
    local iFirstArgument="$2"
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "--project -p --help -h" -- "${sCurrent}"))
        return
    fi
    local iTypedCount=0
    local iIndex
    for (( iIndex=iFirstArgument; iIndex < COMP_CWORD; iIndex++ )); do
        case "${COMP_WORDS[iIndex]}" in
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
    if [ "${bWantsContainerPath}" -eq 0 ]; then
        return
    fi
    _fnReadVcConfig
    # A read loop, not `mapfile`: that builtin arrived in bash 4 and
    # macOS still ships 3.2 as /bin/bash, where it is simply not found
    # and the completion silently offered nothing. Container-path
    # completion had never worked on a stock Mac.
    local daMatches=()
    local sMatch
    while IFS= read -r sMatch; do
        daMatches+=("${sMatch}")
    done < <(_fnListContainerPaths "${sCurrent}" "${VC_NAME}" "${VC_WORKSPACE}")
    if [ ${#daMatches[@]} -gt 0 ]; then
        COMPREPLY=("${daMatches[@]}")
        compopt -o nospace
    fi
}

# The helper aliases are `vaibify push` / `vaibify pull` by another
# name, so their arguments begin one word earlier than the subcommand
# form. Bound to the names shellSetup.py actually creates: these were
# `vc_push` / `vc_pull` until they were renamed, and the completions
# kept registering against the retired names, which is how both
# helpers silently stopped completing anything.
_fnCompleteVaibifyPush() { _fnCompleteTransferArgument push 1; }
_fnCompleteVaibifyPull() { _fnCompleteTransferArgument pull 1; }
complete -o default -F _fnCompleteVaibifyPush vaibify_push
complete -o default -F _fnCompleteVaibifyPush vaib_push
complete -o default -F _fnCompleteVaibifyPull vaibify_pull
complete -o default -F _fnCompleteVaibifyPull vaib_pull
