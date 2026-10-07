#!/bin/bash
# Tab completion for vaibify and its push/pull helpers, in bash 3.2 or newer.
#
# vaibify's first-run setup adds the line below to your shell configuration.
# To add it yourself:
#   [ -f "/path/to/vaibify/completions/vaibify.bash" ] \
#       && . "/path/to/vaibify/completions/vaibify.bash"
#
# This file knows POSITIONS only: which word is a subcommand, which
# argument of push/pull is a container path and which is a file on this
# machine. Everything else -- which project, where its workspace is, what
# is in it -- is answered by `vaibify complete-path`, so nothing here can
# drift from how the commands themselves resolve a path.
#
# bash 3.2 is the floor because macOS still ships it as /bin/bash:
# no mapfile, no associative arrays, and `compopt` exists only from
# bash 4. A word containing `=` or `:` is split by bash itself before
# this file sees it, so container paths containing those characters do
# not complete.

_sVaibifySubcommands="build cat config connect destroy do doctor generate-standards gui init ls open pull push reconcile register remote remote-helper repair reproduce revoke run secret sessions setup start status stop test verify verify-step workflow"

# ---------------------------------------------------------------------------
# _fnScanTypedArguments: Read the project and count the typed paths
# Arguments: iFirstArgument - index in COMP_WORDS where arguments start
# Sets (in the caller's scope): sProject, iTypedCount
#
# Only words BEFORE the cursor count. The value that follows -p or
# --project is a project name, not a path, and bash splits
# `--project=name` into three words around the `=`.
# ---------------------------------------------------------------------------
_fnScanTypedArguments() {
    local iIndex="$1"
    local sWord
    while (( iIndex < COMP_CWORD )); do
        sWord="${COMP_WORDS[iIndex]}"
        case "${sWord}" in
            -p|--project)
                iIndex=$(( iIndex + 1 ))
                if [[ "${COMP_WORDS[iIndex]}" == "=" ]]; then
                    iIndex=$(( iIndex + 1 ))
                fi
                if (( iIndex < COMP_CWORD )); then
                    sProject="${COMP_WORDS[iIndex]}"
                fi
                ;;
            --project=*) sProject="${sWord#--project=}" ;;
            -p?*) sProject="${sWord#-p}" ;;
            -?*) ;;
            *) iTypedCount=$(( iTypedCount + 1 )) ;;
        esac
        iIndex=$(( iIndex + 1 ))
    done
}

# ---------------------------------------------------------------------------
# _fsRemoveTypedQuoting: Print the text a typed word stands for
# Arguments: sTyped - the word as typed, quotes and backslashes included
#
# Never evaluated: a name is data, and this walks it one character at a
# time so that nothing in it can run.
# ---------------------------------------------------------------------------
_fsRemoveTypedQuoting() {
    local sTyped="$1"
    local sResult=""
    local sCharacter
    local iIndex
    case "${sTyped}" in
        \'*) printf '%s' "${sTyped#\'}"; return ;;
        \"*) sTyped="${sTyped#\"}" ;;
    esac
    for (( iIndex=0; iIndex < ${#sTyped}; iIndex++ )); do
        sCharacter="${sTyped:iIndex:1}"
        if [[ "${sCharacter}" == "\\" ]]; then
            iIndex=$(( iIndex + 1 ))
            sCharacter="${sTyped:iIndex:1}"
        fi
        sResult+="${sCharacter}"
    done
    printf '%s' "${sResult}"
}

# ---------------------------------------------------------------------------
# _fnEscapeOfferedName: Escape a name for the word it will join
# Arguments: sName  - the name, as data
#            sTyped - the word being completed, as typed
# Sets (in the caller's scope): sEscaped
#
# bash inserts what it is offered verbatim, so the escaping is ours. A
# word that STARTS with a quote is completed INSIDE that quote (bash keeps
# the opening quote and appends the match), where only the characters
# that are special within that quote need a backslash (a `!` leaves a
# double quote, because history expansion ignores a backslash inside
# one); anywhere else the name is escaped as an ordinary word with
# printf %q. No subshell: this runs once per name.
# ---------------------------------------------------------------------------
_fnEscapeOfferedName() {
    local sName="$1"
    local sTyped="$2"
    local sCharacter
    local iIndex
    sEscaped=""
    case "${sTyped}" in
        \'*) for (( iIndex=0; iIndex < ${#sName}; iIndex++ )); do
                 sCharacter="${sName:iIndex:1}"
                 if [[ "${sCharacter}" == "'" ]]; then
                     sEscaped+="'\\''"
                 else
                     sEscaped+="${sCharacter}"
                 fi
             done ;;
        \"*) for (( iIndex=0; iIndex < ${#sName}; iIndex++ )); do
                 sCharacter="${sName:iIndex:1}"
                 case "${sCharacter}" in
                     [\\\"\$\`]) sEscaped+="\\${sCharacter}" ;;
                     '!') sEscaped+="\"'!'\"" ;;
                     *) sEscaped+="${sCharacter}" ;;
                 esac
             done ;;
        *) printf -v sEscaped '%q' "${sName}" ;;
    esac
}

# ---------------------------------------------------------------------------
# _fnOfferContainerPaths: Offer the paths inside the project's container
# Arguments: sProject - project named with -p, or empty
#            sCurrent - the word being completed, as typed
#
# Each name is escaped for the shell before it is offered, so a file named
# `a b; touch x` is inserted as ONE word and runs nothing.
# ---------------------------------------------------------------------------
_fnOfferContainerPaths() {
    local sProject="$1"
    local sCurrent="$2"
    local saHelperArguments=(complete-path --side container)
    if [ -n "${sProject}" ]; then
        saHelperArguments+=("--project=${sProject}")
    fi
    local sMatch
    local sEscaped
    local daMatches=()
    while IFS= read -r sMatch; do
        _fnEscapeOfferedName "${sMatch}" "${sCurrent}"
        daMatches+=("${sEscaped}")
    done < <(vaibify "${saHelperArguments[@]}" -- "$(_fsRemoveTypedQuoting "${sCurrent}")" 2>/dev/null)
    if [ ${#daMatches[@]} -gt 0 ]; then
        COMPREPLY=("${daMatches[@]}")
        if type compopt > /dev/null 2>&1; then
            compopt -o nospace
        fi
    fi
}

# ---------------------------------------------------------------------------
# _fnCompleteTransferArgument: Complete one push/pull argument
# Arguments: sDirection     - "push" or "pull"
#            iFirstArgument - index in COMP_WORDS where arguments start
#
# push reads from this machine and writes into the container, pull the
# other way round, so the same position means opposite things. The
# machine's own files are left to bash (`complete -o default`).
# ---------------------------------------------------------------------------
_fnCompleteTransferArgument() {
    local sDirection="$1"
    local iFirstArgument="$2"
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    if [[ "${sCurrent}" == -* ]]; then
        COMPREPLY=($(compgen -W "--project -p --help -h" -- "${sCurrent}"))
        return
    fi
    local sProject=""
    local iTypedCount=0
    _fnScanTypedArguments "${iFirstArgument}"
    case "${COMP_WORDS[COMP_CWORD-1]}" in
        -p|--project|=) return ;;
    esac
    if [[ "${sDirection}" == "pull" && "${iTypedCount}" -eq 0 ]] \
        || [[ "${sDirection}" == "push" && "${iTypedCount}" -ge 1 ]]; then
        _fnOfferContainerPaths "${sProject}" "${sCurrent}"
    fi
}

# ---------------------------------------------------------------------------
# _fnCompleteVaibify: Complete the subcommand, then hand push/pull on
# ---------------------------------------------------------------------------
_fnCompleteVaibify() {
    local sCurrent="${COMP_WORDS[COMP_CWORD]}"
    if (( COMP_CWORD == 1 )); then
        if [[ "${sCurrent}" == -* ]]; then
            COMPREPLY=($(compgen -W "--help --version" -- "${sCurrent}"))
        else
            COMPREPLY=($(compgen -W "${_sVaibifySubcommands}" -- "${sCurrent}"))
        fi
        return
    fi
    case "${COMP_WORDS[1]}" in
        push|pull) _fnCompleteTransferArgument "${COMP_WORDS[1]}" 2 ;;
    esac
}

# The helper aliases are `vaibify push` / `vaibify pull` by another name,
# so their arguments begin one word earlier. The names are the ones
# first-run setup creates (shellSetup.py); testShellCompletionWiring binds
# the two.
_fnCompleteVaibifyPush() { _fnCompleteTransferArgument push 1; }
_fnCompleteVaibifyPull() { _fnCompleteTransferArgument pull 1; }

complete -o default -F _fnCompleteVaibify vaibify
complete -o default -F _fnCompleteVaibify vaib
complete -o default -F _fnCompleteVaibifyPush vaibify_push
complete -o default -F _fnCompleteVaibifyPush vaib_push
complete -o default -F _fnCompleteVaibifyPull vaibify_pull
complete -o default -F _fnCompleteVaibifyPull vaib_pull
