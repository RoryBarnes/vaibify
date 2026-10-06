#!/bin/zsh
# Tab completion for vaibify and its push/pull helpers, in zsh.
#
# vaibify's first-run setup adds the line below to your shell configuration.
# To add it yourself:
#   [ -f "/path/to/vaibify/completions/vaibify.zsh" ] \
#       && . "/path/to/vaibify/completions/vaibify.zsh"
#
# This file knows POSITIONS only: which word is a subcommand, which
# argument of push/pull is a container path and which is a file on this
# machine. Everything else -- which project, where its workspace is, what
# is in it -- is answered by `vaibify complete-path`, so nothing here can
# drift from how the commands themselves resolve a path.

if ! typeset -f compdef > /dev/null 2>&1; then
    autoload -Uz compinit && compinit
fi

typeset -g _sVaibifySubcommands="build cat config connect destroy do doctor generate-standards gui init ls open pull push reconcile register remote remote-helper repair reproduce revoke run secret sessions setup start status stop test verify verify-step workflow"

# ---------------------------------------------------------------------------
# _fnScanTypedArgumentsZsh: Read the project and count the typed paths
# Arguments: iFirstArgument - index in $words where arguments start
# Sets (in the caller's scope): sProject, iTypedCount
#
# Only words BEFORE the cursor count, and the value that follows -p or
# --project is a project name, not a path.
# ---------------------------------------------------------------------------
_fnScanTypedArgumentsZsh() {
    local iIndex="$1"
    local sWord
    while (( iIndex < CURRENT )); do
        sWord="${words[iIndex]}"
        case "${sWord}" in
            -p|--project)
                iIndex=$(( iIndex + 1 ))
                if (( iIndex < CURRENT )); then
                    sProject="${(Q)words[iIndex]}"
                fi
                ;;
            --project=*) sProject="${(Q)sWord#--project=}" ;;
            -p?*) sProject="${(Q)sWord#-p}" ;;
            -?*) ;;
            *) iTypedCount=$(( iTypedCount + 1 )) ;;
        esac
        iIndex=$(( iIndex + 1 ))
    done
}

# ---------------------------------------------------------------------------
# _fnOfferContainerPathsZsh: Offer the paths inside the project's container
# Arguments: sProject - project named with -p, or empty
#            sPartial - the word being completed, with its quoting removed
#                       (the caller passes zsh's $PREFIX, which holds the
#                       word up to the cursor with an enclosing quote
#                       already stripped, through (Q) for the backslashes)
#
# Names go to compadd as data after `--`. compadd quotes whatever the
# shell needs when it inserts a match (never pass -Q), so a file named
# `a b; touch x` arrives on the command line as ONE word and runs nothing.
# Directories get no trailing space, so the next TAB can go deeper.
# ---------------------------------------------------------------------------
_fnOfferContainerPathsZsh() {
    local sProject="$1"
    local sPartial="$2"
    local -a saHelperArguments
    saHelperArguments=(complete-path --side container)
    if [[ -n "${sProject}" ]]; then
        saHelperArguments+=("--project=${sProject}")
    fi
    local sOutput
    sOutput="$(vaibify "${saHelperArguments[@]}" -- "${sPartial}" 2>/dev/null)"
    if [[ -z "${sOutput}" ]]; then
        return 1
    fi
    local -a daMatches daDirectories daFiles
    daMatches=("${(@f)sOutput}")
    daDirectories=("${(@M)daMatches:#*/}")
    daFiles=("${(@)daMatches:#*/}")
    if (( ${#daDirectories} )); then
        compadd -S '' -- "${daDirectories[@]}"
    fi
    if (( ${#daFiles} )); then
        compadd -- "${daFiles[@]}"
    fi
}

# ---------------------------------------------------------------------------
# _fnCompleteTransferArgumentZsh: Complete one push/pull argument
# Arguments: sDirection     - "push" or "pull"
#            iFirstArgument - index in $words where arguments start
#
# push reads from this machine and writes into the container, pull the
# other way round, so the same position means opposite things.
# ---------------------------------------------------------------------------
_fnCompleteTransferArgumentZsh() {
    emulate -L zsh
    local sDirection="$1"
    local iFirstArgument="$2"
    local sCurrent="${words[CURRENT]}"
    if [[ "${sCurrent}" == -* ]]; then
        compadd -- --project -p --help -h
        return
    fi
    local sProject=""
    local iTypedCount=0
    _fnScanTypedArgumentsZsh "${iFirstArgument}"
    if [[ "${words[CURRENT-1]}" == (-p|--project) ]]; then
        return 1
    fi
    if [[ "${sDirection}" == "pull" && "${iTypedCount}" -eq 0 ]] \
        || [[ "${sDirection}" == "push" && "${iTypedCount}" -ge 1 ]]; then
        _fnOfferContainerPathsZsh "${sProject}" "${(Q)PREFIX}"
    else
        _files
    fi
}

# ---------------------------------------------------------------------------
# _vaibify: Complete the subcommand, then hand push/pull on
# ---------------------------------------------------------------------------
_vaibify() {
    emulate -L zsh
    if (( CURRENT == 2 )); then
        if [[ "${words[CURRENT]}" == -* ]]; then
            compadd -- --help --version
        else
            compadd -- ${=_sVaibifySubcommands}
        fi
        return
    fi
    case "${words[2]}" in
        push|pull) _fnCompleteTransferArgumentZsh "${words[2]}" 3 ;;
    esac
}

# The helper aliases are `vaibify push` / `vaibify pull` by another name,
# so their arguments begin one word earlier. The names are the ones
# first-run setup creates (shellSetup.py); testShellCompletionWiring binds
# the two.
_vaibify_push() { _fnCompleteTransferArgumentZsh push 2 }
_vaibify_pull() { _fnCompleteTransferArgumentZsh pull 2 }

compdef _vaibify vaibify
compdef _vaibify vaib
compdef _vaibify_push vaibify_push
compdef _vaibify_push vaib_push
compdef _vaibify_pull vaibify_pull
compdef _vaibify_pull vaib_pull
