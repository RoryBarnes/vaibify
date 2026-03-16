#!/bin/bash
# Bash tab-completion for vaibify (vc), vc_push, and vc_pull.
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

    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "--help -h" -- "${sCurrent}"))
    fi
}
complete -F _fnCompleteVaibify vaibify
complete -F _fnCompleteVaibify vc

# ---------------------------------------------------------------------------
# _fnCompleteVcPull: Complete container paths for vc_pull sources
# ---------------------------------------------------------------------------
_fnCompleteVcPull() {
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "-a -L -r -R --help -h" -- "${sCurrent}"))
        return
    fi
    _fnReadVcConfig
    local daMatches
    mapfile -t daMatches < <(_fnListContainerPaths "${sCurrent}" "${VC_NAME}" "${VC_WORKSPACE}")
    if [ ${#daMatches[@]} -gt 0 ]; then
        COMPREPLY=("${daMatches[@]}")
        compopt -o nospace
    fi
}
complete -o default -F _fnCompleteVcPull vc_pull

# ---------------------------------------------------------------------------
# _fnCompleteVcPush: Complete local files for sources, container paths
# for the destination (after at least one source has been typed)
# ---------------------------------------------------------------------------
_fnCompleteVcPush() {
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "-a -L -r -R --help -h" -- "${sCurrent}"))
        return
    fi
    local iNonOptionCount=0
    local iIndex
    for (( iIndex=1; iIndex < COMP_CWORD; iIndex++ )); do
        case "${COMP_WORDS[iIndex]}" in
            -*) ;;
            *)  iNonOptionCount=$(( iNonOptionCount + 1 )) ;;
        esac
    done
    if [ "${iNonOptionCount}" -ge 1 ]; then
        _fnReadVcConfig
        local daMatches
        mapfile -t daMatches < <(_fnListContainerPaths "${sCurrent}" "${VC_NAME}" "${VC_WORKSPACE}")
        if [ ${#daMatches[@]} -gt 0 ]; then
            COMPREPLY=("${daMatches[@]}")
            compopt -o nospace
        fi
    fi
}
complete -o default -F _fnCompleteVcPush vc_push
