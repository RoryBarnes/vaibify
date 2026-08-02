"""Generate one host CLI subcommand per agent-action catalog entry.

``vaibify.gui.actionCatalog.LIST_AGENT_ACTIONS`` is the enforced
inventory of every state-mutating thing a researcher can do from the
dashboard. This module turns that inventory, mechanically, into
``vaibify do <action-name>`` subcommands, so the host CLI cannot drift
from the dashboard: an action added to the catalog is a CLI command on
the next import, and an action the generator cannot dispatch fails
``testEveryCatalogActionHasCliCommand`` instead of silently vanishing.

Placement is deliberate. Every generated command lives under the ``do``
group, never at the top level, because several catalog names collide
with existing top-level commands that mean something different —
``vaibify push`` copies host files into the container, while
``push-to-github`` is the Level-2 publication flow. Nesting keeps the
catalog's vocabulary identical to the in-container ``vaibify-do`` CLI's
without overwriting a single existing command.

The lane is the RESEARCHER lane throughout (see
:mod:`vaibify.cli.hubSession`): a per-browser credential, bootstrapped
headlessly over the hub's host control socket, plus the per-claim lease,
never the per-container agent token. ``bAgentSafe`` constrains a
compromised in-container agent, not the researcher at their own
terminal, so user-only actions are generated here like any other.

One session per container is enforced against this lane like any other,
so a container a dashboard tab already holds answers the claim with 409
and the command stops with that refusal explained — it never transfers
or revokes the dashboard's session to get in.
"""

import json
import sys

import click

from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from . import hubSession


# Catalog actions with no host CLI command. Each entry needs a written
# rationale on the same line; an entry naming an action that does not
# exist is itself a test failure, so this set cannot rot silently.
SET_ACTIONS_WITHOUT_CLI = frozenset({
    # (empty — every catalog action is reachable from the host CLI)
})

_SET_DISPATCHABLE_METHODS = frozenset(
    {"GET", "POST", "PUT", "DELETE", "WS"}
)

# Path placeholders the CLI supplies itself, never as a CLI argument.
_SET_SESSION_PLACEHOLDERS = frozenset({"sContainerId"})

_DICT_ARGUMENT_METAVARS = {
    "iStepIndex": "STEP",
    "iPosition": "POSITION",
    "sFilePath": "FILE_PATH",
    "sService": "SERVICE",
    "sName": "NAME",
    "sRepoName": "REPO_NAME",
}


def flistPathPlaceholders(sPath):
    """Return the ``{placeholder}`` names of a route template, in order.

    ``{sFilePath:path}`` yields ``sFilePath`` — the converter suffix is
    part of the route template, not of the value the caller supplies.
    """
    listNames = []
    iCursor = 0
    while iCursor < len(sPath):
        if sPath[iCursor] != "{":
            iCursor += 1
            continue
        iEnd = sPath.find("}", iCursor)
        if iEnd < 0:
            break
        listNames.append(sPath[iCursor + 1:iEnd].split(":")[0])
        iCursor = iEnd + 1
    return listNames


def flistArgumentPlaceholders(dictEntry):
    """Return the placeholders a caller must supply on the command line."""
    if dictEntry["sMethod"] == "WS":
        return []
    return [
        sPlaceholder
        for sPlaceholder in flistPathPlaceholders(dictEntry["sPath"])
        if sPlaceholder not in _SET_SESSION_PLACEHOLDERS
    ]


def fsMetavarForPlaceholder(sPlaceholder):
    """Return the metavar shown for one path placeholder."""
    if sPlaceholder in _DICT_ARGUMENT_METAVARS:
        return _DICT_ARGUMENT_METAVARS[sPlaceholder]
    if sPlaceholder[:1].islower():
        return sPlaceholder[1:].upper()
    return sPlaceholder.upper()


def fnCoerceFieldValue(sValue):
    """Coerce a ``key=value`` string to bool, int, float, or JSON."""
    if sValue.lower() in ("true", "false"):
        return sValue.lower() == "true"
    for fnParse in (int, float):
        try:
            return fnParse(sValue)
        except ValueError:
            pass
    if sValue.startswith("[") or sValue.startswith("{"):
        try:
            return json.loads(sValue)
        except ValueError:
            return sValue
    return sValue


def ftSplitFieldArguments(tFields):
    """Split trailing CLI words into positionals and a body dict.

    Accepts ``key=value`` pairs, a whole ``{...}`` JSON object, and bare
    words (step labels for the execution actions).
    """
    listPositional = []
    dictFields = {}
    for sArgument in tFields:
        if sArgument.startswith("{"):
            dictFields.update(json.loads(sArgument))
        elif "=" in sArgument:
            sKey, sValue = sArgument.split("=", 1)
            dictFields[sKey.lstrip("-")] = fnCoerceFieldValue(sValue)
        else:
            listPositional.append(sArgument)
    return listPositional, dictFields


def fbLooksLikeStepLabel(sValue):
    """Return True when a value has the ``A09`` / ``I01`` label shape."""
    import re
    return bool(re.match(r"^[AIai]\d{1,3}$", sValue or ""))


def _fnPopulateStepSelectors(dictPayload, sActionName, listPositional):
    """Place bare positionals into the run action's step selectors."""
    if not listPositional:
        return
    if sActionName == "run-from-step":
        sFirst = listPositional[0]
        if fbLooksLikeStepLabel(sFirst):
            dictPayload["sStartStepLabel"] = sFirst
        else:
            dictPayload["iStartStep"] = int(sFirst)
        return
    listLabels = [s for s in listPositional if not s.lstrip("-").isdigit()]
    listIndices = [int(s) for s in listPositional if s.lstrip("-").isdigit()]
    if listLabels:
        dictPayload["listStepLabels"] = listLabels
    if listIndices:
        dictPayload["listStepIndices"] = listIndices


_DICT_RUN_MODES = {
    "run-data-only": "dataOnly",
    "run-plots-only": "plotsOnly",
}


def fdictBuildWebSocketPayload(dictEntry, listPositional, dictFields):
    """Return the pipeline-socket message for one WS catalog action."""
    dictPayload = {"sAction": dictEntry["sPath"]}
    dictPayload.update(dictFields)
    _fnPopulateStepSelectors(
        dictPayload, dictEntry["sName"], listPositional,
    )
    if dictEntry["sName"] in _DICT_RUN_MODES:
        dictPayload["sRunMode"] = _DICT_RUN_MODES[dictEntry["sName"]]
    return dictPayload


def fsBuildRequestPath(dictEntry, dictSession, dictPathValues):
    """Substitute the session and caller values into the route template."""
    from urllib.parse import quote
    sPath = dictEntry["sPath"]
    dictValues = dict(dictPathValues)
    dictValues["sContainerId"] = dictSession["sContainerId"]
    for sKey, sValue in dictValues.items():
        sQuoted = quote(str(sValue), safe="/")
        sPath = sPath.replace("{%s}" % sKey, sQuoted)
        sPath = sPath.replace("{%s:path}" % sKey, sQuoted)
    return sPath


def ftSplitQueryFromBodyFields(dictEntry, dictFields):
    """Split caller fields by the transport the ROUTE actually reads.

    Every generated action used to send its fields as a JSON body, but
    some routes declare their parameters as QUERY parameters -- and
    FastAPI reads those from the query string only. A field sent in the
    body of such a route is silently ignored and the parameter takes its
    default, so ``get-host-log-tail iLines=50`` returned 200 lines and
    ``write-file ... sWorkdir=/x`` wrote somewhere else entirely. The
    catalog names those fields in ``saQueryFields``, and
    ``testGeneratedActionsSendFieldsOnTheTransportTheRouteReads``
    fails the build if a route grows one that the catalog does not.
    """
    setQueryFields = set(dictEntry.get("saQueryFields") or ())
    dictQuery = {
        sKey: objValue for sKey, objValue in dictFields.items()
        if sKey in setQueryFields
    }
    dictBody = {
        sKey: objValue for sKey, objValue in dictFields.items()
        if sKey not in setQueryFields
    }
    return dictQuery, dictBody


def fsAppendQueryString(sPath, dictQuery):
    """Return the path with the caller's query fields appended."""
    if not dictQuery:
        return sPath
    from urllib.parse import urlencode
    sSeparator = "&" if "?" in sPath else "?"
    return sPath + sSeparator + urlencode(
        {sKey: str(objValue) for sKey, objValue in dictQuery.items()},
    )


def fdictResolvePathValues(dictSession, listPlaceholders, dictParams):
    """Return the path values, translating a step label to its index."""
    dictPathValues = {}
    for sPlaceholder in listPlaceholders:
        sValue = dictParams[_fsParameterName(sPlaceholder)]
        if sPlaceholder == "iStepIndex" and fbLooksLikeStepLabel(sValue):
            sValue = hubSession.fiResolveStepLabel(dictSession, sValue)
        dictPathValues[sPlaceholder] = sValue
    return dictPathValues


def _fsParameterName(sPlaceholder):
    """Return the click parameter name carrying one path placeholder.

    Click lowercases the declared name of a positional argument, so the
    declaration and the callback keyword agree only when the name is
    already lowercase.
    """
    return sPlaceholder.lower()


def _flistBuildCommandParameters(dictEntry):
    """Return the click parameters for one generated action command."""
    listParameters = [
        click.Argument(
            [_fsParameterName(sPlaceholder)],
            metavar=fsMetavarForPlaceholder(sPlaceholder),
        )
        for sPlaceholder in flistArgumentPlaceholders(dictEntry)
    ]
    listParameters.append(
        click.Argument(["tfields"], nargs=-1, metavar="[FIELD=VALUE]...")
    )
    listParameters.extend([
        click.Option(
            ["--project", "-p", "sProjectName"], default=None,
            help="Project name (omit inside a project directory).",
        ),
        click.Option(
            ["--port", "iPort"], default=None, type=int,
            help="Port of the vaibify session to drive.",
        ),
        click.Option(
            ["--workflow", "sWorkflowPath"], default=None,
            help="Container path of the project.json to connect.",
        ),
        click.Option(
            ["--json", "bJson"], is_flag=True, default=False,
            help="Emit one JSON object per line.",
        ),
        click.Option(
            ["--dry-run", "bDryRun"], is_flag=True, default=False,
            help="Print the call that would be made; send nothing.",
        ),
        click.Option(
            ["--timeout", "fTimeoutSeconds"], type=float,
            default=hubSession.F_DEFAULT_TIMEOUT_SECONDS,
            help="Seconds to wait for the response, or for the next "
                 "event of a run (0 disables the limit).",
        ),
    ])
    return listParameters


def _fnPrintDryRun(dictEntry, dictSession, dictParams):
    """Describe the call the command would make, without making it."""
    listPositional, dictFields = ftSplitFieldArguments(
        dictParams["tfields"],
    )
    if dictEntry["sMethod"] == "WS":
        dictTarget = {
            "sTransport": "WS",
            "sContainerId": dictSession["sContainerId"],
            "dictPayload": fdictBuildWebSocketPayload(
                dictEntry, listPositional, dictFields,
            ),
        }
    else:
        dictPathValues = {
            sPlaceholder: dictParams[_fsParameterName(sPlaceholder)]
            for sPlaceholder in flistArgumentPlaceholders(dictEntry)
        }
        dictQuery, dictBody = ftSplitQueryFromBodyFields(
            dictEntry, dictFields,
        )
        # The dry run shows the SPLIT, not the raw fields: its whole job
        # is to let a caller see the call that will be made, and a field
        # the route reads from the query string is a different call from
        # the same field in the body.
        dictTarget = {
            "sTransport": "HTTP",
            "sMethod": dictEntry["sMethod"],
            "sUrl": dictSession["sBaseUrl"] + fsAppendQueryString(
                fsBuildRequestPath(dictEntry, dictSession, dictPathValues),
                dictQuery,
            ),
            "dictFields": dictBody,
        }
    click.echo(json.dumps(dictTarget, indent=2, sort_keys=True))


def _fiDispatchAction(dictEntry, dictSession, dictParams):
    """Send one catalog action on its declared transport."""
    listPositional, dictFields = ftSplitFieldArguments(
        dictParams["tfields"],
    )
    fTimeoutSeconds = dictParams["fTimeoutSeconds"]
    if dictEntry["sMethod"] == "WS":
        return hubSession.fiStreamPipelineAction(
            dictSession,
            fdictBuildWebSocketPayload(
                dictEntry, listPositional, dictFields,
            ),
            dictParams["bJson"], fTimeoutSeconds,
        )
    if listPositional:
        raise hubSession.HubSessionError(
            "unexpected argument(s) %s; body fields use key=value"
            % ", ".join(listPositional)
        )
    dictPathValues = fdictResolvePathValues(
        dictSession, flistArgumentPlaceholders(dictEntry), dictParams,
    )
    dictQuery, dictBody = ftSplitQueryFromBodyFields(dictEntry, dictFields)
    return hubSession.fiSendHttpAction(
        dictSession, dictEntry["sMethod"],
        fsAppendQueryString(
            fsBuildRequestPath(dictEntry, dictSession, dictPathValues),
            dictQuery,
        ),
        dictBody, dictParams["bJson"], fTimeoutSeconds,
    )


def _fsResolveContainerName(sProjectName):
    """Return the container name for the requested (or resolved) project."""
    from .configLoader import fconfigResolveProject
    return fconfigResolveProject(sProjectName).sProjectName


def fnRunCatalogAction(dictEntry, dictParams):
    """Open a researcher session, dispatch one action, release the lease."""
    sContainerName = _fsResolveContainerName(dictParams["sProjectName"])
    try:
        if dictParams["bDryRun"]:
            _fnPrintDryRun(
                dictEntry,
                hubSession.fdictInspectHubSession(
                    sContainerName, dictParams["iPort"],
                ),
                dictParams,
            )
            return
        _fnDispatchUnderLease(dictEntry, sContainerName, dictParams)
    except hubSession.HubSessionError as error:
        click.echo("Error: %s" % error, err=True)
        sys.exit(4)


def _fnDispatchUnderLease(dictEntry, sContainerName, dictParams):
    """Claim the container, dispatch, and always release the lease."""
    dictSession = hubSession.fdictOpenResearcherSession(
        sContainerName, dictParams["iPort"],
        dictParams["sWorkflowPath"],
    )
    try:
        iExitCode = _fiDispatchAction(dictEntry, dictSession, dictParams)
    finally:
        hubSession.fnReleaseContainer(dictSession)
    sys.exit(iExitCode)


def _fsBuildCommandHelp(dictEntry):
    """Return the help text for one generated command.

    The researcher-only note records that the dashboard withholds this
    action from the in-container agent; it never withholds it here,
    because the person at the terminal IS the researcher.
    """
    if dictEntry["bAgentSafe"]:
        return dictEntry["sDescription"]
    return (
        "%s\n\nWithheld from the in-container agent (bAgentSafe=False); "
        "available here because the host CLI is the researcher lane."
        % dictEntry["sDescription"]
    )


def fcommandBuildActionCommand(dictEntry):
    """Return the click command for one catalog entry, or None.

    ``None`` means the generator cannot dispatch this entry's declared
    transport. It is never silently swallowed: the architectural
    invariant fails until the entry is dispatchable or exempted.
    """
    if dictEntry["sMethod"] not in _SET_DISPATCHABLE_METHODS:
        return None

    def _fnInvokeAction(**dictParams):
        fnRunCatalogAction(dictEntry, dictParams)

    return click.Command(
        name=dictEntry["sName"],
        params=_flistBuildCommandParameters(dictEntry),
        callback=_fnInvokeAction,
        short_help=dictEntry["sDescription"].split(". ")[0],
        help=_fsBuildCommandHelp(dictEntry),
    )


def fnPrintActionCatalog():
    """Print every generated action, grouped by catalog category."""
    dictByCategory = {}
    for dictEntry in LIST_AGENT_ACTIONS:
        dictByCategory.setdefault(
            dictEntry["sCategory"], [],
        ).append(dictEntry)
    for sCategory in sorted(dictByCategory):
        click.echo("\n%s" % sCategory)
        for dictEntry in dictByCategory[sCategory]:
            click.echo("  %-34s %s" % (
                dictEntry["sName"],
                dictEntry["sDescription"].split(". ")[0],
            ))
    click.echo(
        "\nRun 'vaibify do <action> --help' for one action's arguments."
    )


@click.group("do", invoke_without_command=True)
@click.pass_context
def do(ctx):
    """Run a dashboard action from the host, as the researcher.

    Every subcommand is generated from the agent-action catalog, so the
    names match the dashboard and the in-container 'vaibify-do' CLI.
    """
    if ctx.invoked_subcommand is None:
        fnPrintActionCatalog()


def fnRegisterGeneratedActions(groupParent):
    """Attach one generated command per catalog entry to the group."""
    for dictEntry in LIST_AGENT_ACTIONS:
        if dictEntry["sName"] in SET_ACTIONS_WITHOUT_CLI:
            continue
        command = fcommandBuildActionCommand(dictEntry)
        if command is not None:
            groupParent.add_command(command)


fnRegisterGeneratedActions(do)
