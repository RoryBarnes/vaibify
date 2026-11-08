"""The rcfile every vaibify terminal's bash reads, container and host alike.

Defined once here so the two launchers cannot drift: the container
terminal's wrapper (``vaibify.gui.terminalContainment``) and the host
terminal's launch stub (``vaibify.host.hostConnection``) both hand bash
this exact text with ``--rcfile``. It must run under the bash 3.2 that
macOS ships: no associative arrays, no ``${var,,}``, no ``mapfile``.

In order, it

1. deletes itself and its directory (bash has read it whole before it
   runs anything);
2. reads the researcher's own ``~/.bashrc``, as bash would have;
3. defines the ``claude``, ``codex`` and ``gemini`` wrappers that record
   which agent chat this terminal holds (below);
4. appends, LAST, a prompt hook that switches off the xterm input modes
   a program killed by SIGKILL leaves on. The hook goes on the array
   form of ``PROMPT_COMMAND`` when there is one, newline-joined to the
   string form otherwise, and returns the status it was given, so it is
   invisible to any hook after it.

The wrappers. Each is defined with the ``function`` keyword, because
the ``name() {}`` form is a syntax error when ``~/.bashrc`` has already
defined an alias of the same name (measured on bash 3.2 and 5.2). They
are not exported, so an agent's own subshells never see them. Each
appends one JSON line per event to the file named by
``VAIBIFY_TERMINAL_LIFECYCLE``, which the hub sets per terminal; with no
such variable a wrapper only runs the CLI. A start line names the
provider, the session id the wrapper injected (or the one a resume
named), whether a switch hook was supplied, the start time, the working
directory and the arguments; an exit line follows when the CLI returns
to bash. A CLI killed by leaving the dashboard writes no exit line.

A flag is injected only when the CLI's own ``--help`` offers it. The
help is read once per shell per binary, keyed by its resolved path and
the dereferenced file's size and time (``ls -lL``), because Gemini
replaces itself in place on an interactive launch.

- ``claude``: ``--session-id <new uuid>`` unless the researcher resumes,
  continues, forks or names a session; and a ``SessionStart`` hook
  through ``--settings`` unless the researcher passes ``--settings``
  themselves. The hook merges with the researcher's own hooks (measured)
  and fires on startup, ``/clear``, ``/resume``, ``--continue`` and a
  fork; its constant command appends Claude's own JSON payload to this
  terminal's record, so the record follows a switch inside the CLI.
- ``gemini``: ``--session-id <new uuid>`` unless the researcher resumes
  or names one. No switch hook: the only delivery that leaves the
  researcher's settings untouched replaces the machine's system file.
- ``codex``: nothing is injected (it has no launch-time id); a
  ``codex resume <uuid>`` records that id.

Everything a wrapper records is written by the researcher's uid, which
any in-container agent shares, so the hub treats the record as
discovered data and validates every field before using it.
"""

__all__ = [
    "S_AGENT_CHAT_WRAPPERS",
    "S_CLAUDE_SWITCH_HOOK_SETTINGS",
    "S_LIFECYCLE_ENVIRONMENT_VARIABLE",
    "S_PROMPT_RESET_RCFILE",
]

S_LIFECYCLE_ENVIRONMENT_VARIABLE = "VAIBIFY_TERMINAL_LIFECYCLE"

# Claude Code's --settings JSON: one SessionStart hook whose constant
# command appends the hook's stdin (one JSON object, no newline) and a
# newline to this terminal's lifecycle record.
S_CLAUDE_SWITCH_HOOK_SETTINGS = (
    '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":'
    '"cat >> \\"$' + S_LIFECYCLE_ENVIRONMENT_VARIABLE + '\\"; echo >> '
    '\\"$' + S_LIFECYCLE_ENVIRONMENT_VARIABLE + '\\""}]}]}}'
)

_S_SELF_DELETE_AND_BASHRC = (
    'rm -f -- "${BASH_SOURCE[0]}" && rmdir -- "${BASH_SOURCE[0]%/*}"\n'
    "[ -f ~/.bashrc ] && . ~/.bashrc\n"
)

_S_RECORDING_HELPERS = r"""fsVaibifyJsonString() {
    local sText="$1"
    sText=${sText//\\/\\\\}
    sText=${sText//\"/\\\"}
    sText=${sText//$'\t'/\\t}
    sText=${sText//$'\n'/\\n}
    sText=${sText//$'\r'/\\r}
    printf '"%s"' "$sText"
}
fnVaibifyRecordLifecycle() {
    [ -n "${VAIBIFY_TERMINAL_LIFECYCLE:-}" ] || return 0
    ( umask 077; printf '%s\n' "$1" >> "$VAIBIFY_TERMINAL_LIFECYCLE" ) 2>/dev/null
    return 0
}
fsVaibifyLowerUuid() {
    local sId
    sId=$(printf '%s' "$1" | tr 'A-F' 'a-f')
    case "$sId" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
            printf '%s' "$sId" ;;
    esac
}
fsVaibifyNewSessionId() {
    if [ -r /proc/sys/kernel/random/uuid ]; then
        fsVaibifyLowerUuid "$(cat /proc/sys/kernel/random/uuid)"
    else
        fsVaibifyLowerUuid "$(uuidgen 2>/dev/null)"
    fi
}
fsVaibifyProbeHelp() {
    local sPath sKey sKeyName sHelpName
    sPath=$(type -P "$1" 2>/dev/null) || return 1
    [ -n "$sPath" ] || return 1
    sKey="$sPath $(ls -lL "$sPath" 2>/dev/null)"
    sKeyName="_sVaibifyProbeKey_$1"
    sHelpName="_sVaibifyProbeHelp_$1"
    if [ "$sKey" != "${!sKeyName}" ]; then
        printf -v "$sHelpName" '%s' "$(command "$1" --help 2>&1)"
        printf -v "$sKeyName" '%s' "$sKey"
    fi
    printf '%s' "${!sHelpName}"
}
fbVaibifyHelpOffers() {
    case "$1" in
        *"$2"*) return 0 ;;
    esac
    return 1
}
fnVaibifyRecordStart() {
    local sProvider="$1" sSessionId="$2" sSwitchHook="$3" sArgument sArguments=""
    shift 3
    for sArgument in "$@"; do
        sArguments="$sArguments${sArguments:+,}$(fsVaibifyJsonString "$sArgument")"
    done
    fnVaibifyRecordLifecycle "{\"sEvent\":\"start\",\"sProvider\":\"$sProvider\",\"sSessionId\":\"$sSessionId\",\"bSwitchHook\":$sSwitchHook,\"iStartedEpoch\":$(date +%s),\"sCwd\":$(fsVaibifyJsonString "$PWD"),\"listArguments\":[$sArguments]}"
}
fnVaibifyRecordExit() {
    fnVaibifyRecordLifecycle "{\"sEvent\":\"exit\",\"iStatus\":$1}"
}
fsVaibifyResumedId() {
    local sArgument sPrevious=""
    for sArgument in "$@"; do
        case "$sPrevious" in
            -r|--resume) fsVaibifyLowerUuid "$sArgument"; return 0 ;;
        esac
        case "$sArgument" in
            --resume=*) fsVaibifyLowerUuid "${sArgument#--resume=}"; return 0 ;;
        esac
        sPrevious="$sArgument"
    done
}
"""

_S_CLAUDE_WRAPPER = r"""function claude {
    local sHelp sArgument sSessionId="" bChat=1 bInject=1 bHook=1 iStatus
    local -a listInjected
    sHelp=$(fsVaibifyProbeHelp claude) || { command claude "$@"; return $?; }
    case "${1:-}" in
        mcp|config|update|doctor|install|plugin|plugins|setup-token|migrate-installer|auth|agents|api-key) bChat=0 ;;
    esac
    for sArgument in "$@"; do
        case "$sArgument" in
            -p|--print|-h|--help|-v|--version) bChat=0 ;;
            -r|--resume|--resume=*|-c|--continue|--session-id|--session-id=*|--fork-session) bInject=0 ;;
            --settings|--settings=*) bHook=0 ;;
        esac
    done
    if [ "$bChat" = 0 ]; then command claude "$@"; return $?; fi
    listInjected=()
    if [ "$bInject" = 1 ] && fbVaibifyHelpOffers "$sHelp" "--session-id"; then
        sSessionId=$(fsVaibifyNewSessionId)
        [ -n "$sSessionId" ] && listInjected=(--session-id "$sSessionId")
    elif [ "$bInject" = 0 ]; then
        sSessionId=$(fsVaibifyResumedId "$@")
    fi
    if [ "$bHook" = 1 ] && [ -n "${VAIBIFY_TERMINAL_LIFECYCLE:-}" ] \
            && fbVaibifyHelpOffers "$sHelp" "--settings"; then
        listInjected=(${listInjected[@]+"${listInjected[@]}"} --settings '""" + S_CLAUDE_SWITCH_HOOK_SETTINGS + r"""')
    else
        bHook=0
    fi
    if [ "$bHook" = 1 ]; then
        fnVaibifyRecordStart claude "$sSessionId" true "$@"
    else
        fnVaibifyRecordStart claude "$sSessionId" false "$@"
    fi
    command claude ${listInjected[@]+"${listInjected[@]}"} "$@"
    iStatus=$?
    fnVaibifyRecordExit "$iStatus"
    return "$iStatus"
}
"""

_S_GEMINI_WRAPPER = r"""function gemini {
    local sHelp sArgument sSessionId="" bChat=1 bInject=1 iStatus
    local -a listInjected
    sHelp=$(fsVaibifyProbeHelp gemini) || { command gemini "$@"; return $?; }
    case "${1:-}" in
        mcp|extensions|extension|skills|skill|hooks|hook|gemma) bChat=0 ;;
    esac
    for sArgument in "$@"; do
        case "$sArgument" in
            -p|--prompt|--prompt=*|-h|--help|-v|--version|-l|--list-extensions|--list-sessions|--delete-session|--delete-session=*) bChat=0 ;;
            -r|--resume|--resume=*|--session-id|--session-id=*) bInject=0 ;;
        esac
    done
    if [ "$bChat" = 0 ]; then command gemini "$@"; return $?; fi
    listInjected=()
    if [ "$bInject" = 1 ] && fbVaibifyHelpOffers "$sHelp" "--session-id"; then
        sSessionId=$(fsVaibifyNewSessionId)
        [ -n "$sSessionId" ] && listInjected=(--session-id "$sSessionId")
    elif [ "$bInject" = 0 ]; then
        sSessionId=$(fsVaibifyResumedId "$@")
    fi
    fnVaibifyRecordStart gemini "$sSessionId" false "$@"
    command gemini ${listInjected[@]+"${listInjected[@]}"} "$@"
    iStatus=$?
    fnVaibifyRecordExit "$iStatus"
    return "$iStatus"
}
"""

_S_CODEX_WRAPPER = r"""function codex {
    local sArgument sSessionId="" iStatus
    case "${1:-}" in
        resume) sSessionId=$(fsVaibifyLowerUuid "${2:-}") ;;
        exec|e|review|login|logout|mcp|plugin|app-server|remote-control|completion|update|doctor|sandbox|debug|apply|a|queue|archive|delete|migrate-rollouts|migrate-nfs-db|unarchive|fork|cloud|exec-server|features|help|agents)
            command codex "$@"; return $? ;;
    esac
    for sArgument in "$@"; do
        case "$sArgument" in
            -h|--help|-V|--version) command codex "$@"; return $? ;;
        esac
    done
    fnVaibifyRecordStart codex "$sSessionId" false "$@"
    command codex "$@"
    iStatus=$?
    fnVaibifyRecordExit "$iStatus"
    return "$iStatus"
}
"""

S_AGENT_CHAT_WRAPPERS = (
    _S_RECORDING_HELPERS + _S_CLAUDE_WRAPPER + _S_GEMINI_WRAPPER
    + _S_CODEX_WRAPPER
)

_S_INPUT_MODE_RESET_HOOK = (
    "fnVaibifyResetInputModes() {\n"
    "    local iStatus=$?\n"
    "    printf '\\033[?1000l\\033[?1002l\\033[?1003l\\033[?1005l"
    "\\033[?1006l\\033[?1015l\\033[?1004l\\033[?25h'\n"
    '    return "$iStatus"\n'
    "}\n"
    'if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]];'
    " then\n"
    "    PROMPT_COMMAND+=(fnVaibifyResetInputModes)\n"
    "else\n"
    "    PROMPT_COMMAND=\"${PROMPT_COMMAND:+$PROMPT_COMMAND$'\\n'}"
    'fnVaibifyResetInputModes"\n'
    "fi\n"
)

S_PROMPT_RESET_RCFILE = (
    _S_SELF_DELETE_AND_BASHRC + S_AGENT_CHAT_WRAPPERS
    + _S_INPUT_MODE_RESET_HOOK
)
