"""Reaching for a rung must not knock the project off a lower one.

A whole class of defect, found by a researcher walking the ladder
rather than by any test (2026-09-09). An action offered by the
dashboard *to satisfy a higher PROOF rung* writes to a file a LOWER
rung compares against GitHub and Zenodo, so following the dashboard
upward diverges a published copy and drops the project a level. Both
shipped instances were internally consistent everywhere -- each
component did exactly what it said -- and the only place the
contradiction existed was on the researcher's screen:

* depositing the environment image (Level 3) recorded ``archived``
  into the workflow, and the project definition is a file the Level 2
  verifies COMPARE. Nothing read the field; it cost a level, a push
  and an immutable Zenodo version to echo a form.
* every ``environment.json`` write stamped a fresh ``sTimestamp``, so
  re-capturing an UNCHANGED environment still moved the file's hash
  and diverged both remotes.

The rule this file enforces is the general form of both:

    An action taken to reach rung N must not move the bytes of a file
    compared at some other rung M, unless the write is content-guarded
    (identical input, identical bytes) or a human has recorded why it
    is acceptable -- and where the record says the researcher is
    warned, a confirmation must actually exist.

**What is verified, and what is asserted.** The write set of each
ladder action is read STATICALLY out of the route modules: a workflow
dict mutated in the handler (which reaches ``project.json`` on the
next save, which is exactly how the deposit instance worked -- it
called no save at all), plus calls that reach a named writer of a
compared artifact. That is a claim about the source, not about a
running hub, and it is deliberately the coarse direction: a route is
flagged for reaching a writer even on a branch that may not execute.
The content-guarantee half is not static -- it drives the real writers
against a real repository twice and compares the bytes.

The population is the dashboard's own ladder-action table, so a new
action with no ledger entry FAILS rather than passing unexamined.
"""

import ast
import os
import re
import subprocess

import pytest

from vaibify.reproducibility import publicationScope
from vaibify.reproducibility import environmentSnapshot, manifestWriter
from vaibify.reproducibility.reproduceScriptGenerator import (
    fsRenderReproduceScript,
)


_S_REPOSITORY_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)),
)
_S_ACTION_TABLE_SOURCE = "vaibify/gui/static/scriptApplication.js"
_S_ROUTE_DIRECTORY = "vaibify/gui/routes"
_S_WORKFLOW_ROUTE_PREFIX = "/api/workflow/{sContainerId}"

# One representative project definition. The real basename is the
# project's own, and `publicationScope` classifies every member of the
# `.vaibify/projects/` family identically, which
# `test_the_artifact_map_names_paths_the_publication_scope_knows`
# pins rather than assumes.
_S_PROJECT_DEFINITION = publicationScope.S_PROJECTS_DIRECTORY + "/any.json"
_S_ATTESTATION = ".vaibify/l3_attestation.json"
_S_ENVIRONMENT_JSON = ".vaibify/environment.json"
_S_MANIFEST = "MANIFEST.sha256"
_S_REPRODUCE_SCRIPT = "reproduce.sh"
_S_DOCKERFILE = "Dockerfile"
_S_REQUIREMENTS_LOCK = "requirements.lock"

# A workflow dict mutated inside a handler reaches the project
# definition whether or not the handler saves: the deposit instance
# wrote only in memory and relied on the researcher's next save, and a
# detector keyed on the save call would have called it clean.
_S_WORKFLOW_MUTATION = "workflow-mutation"

# Every named function that writes a file some level compares. A
# rename silently empties this map, which would make the whole static
# half vacuous, so
# `test_every_named_writer_still_defines_the_function_it_names`
# fails when one stops existing.
DICT_WRITER_ARTIFACTS = {
    "fdictCommitWorkflowSave": (_S_PROJECT_DEFINITION,),
    "fnSaveWorkflow": (_S_PROJECT_DEFINITION,),
    "fnWriteEnvironmentJson": (_S_ENVIRONMENT_JSON,),
    "fnWriteManifest": (_S_MANIFEST,),
    "fsGenerateReproduceScript": (_S_REPRODUCE_SCRIPT,),
    "fnWriteAttestation": (_S_ATTESTATION,),
    # The shared verification writer: the reproduced manifest, then the
    # attestation (or a reproduction record) naming it. The route calls
    # it instead of fnWriteAttestation directly, so the scan must know
    # it writes the attestation or the verify crossing goes invisible.
    "flistWriteVerificationOutcome": (_S_ATTESTATION,),
    "fnGenerateRequirementsLock": (_S_REQUIREMENTS_LOCK,),
    "fdictGenerateReproducibilityEnvelope": (
        _S_MANIFEST, _S_REQUIREMENTS_LOCK, _S_ENVIRONMENT_JSON,
    ),
}

# Adapter writes whose path the scan cannot read, and the artifact a
# human read out of the handler. Recorded rather than resolved,
# because resolving an imported constant through the import graph
# would be a second, weaker authority on what the handler writes; an
# unrecorded opaque write fails
# `test_every_opaque_write_names_the_artifact_it_writes`.
TUPLE_ADAPTER_WRITE_METHODS = ("fnWriteTextAtomic", "fnWriteJsonAtomic")
DICT_OPAQUE_WRITE_ARTIFACTS = {
    ("POST", _S_WORKFLOW_ROUTE_PREFIX + "/level3/dockerfile"): (
        _S_DOCKERFILE,
    ),
}

# The rung each ladder action is clicked to REACH. Zero means the
# action asks a question or withdraws a claim: a verification that
# writes nothing, or a deliberate step back down (clearing an answer,
# deleting a declaration), where a lowered level is the researcher's
# stated intent rather than a surprise.
DICT_LADDER_ACTION_RUNGS = {
    "capture-binary": 3,
    "declare-binary": 3,
    "declare-no-binaries": 3,
    "remove-binary": 3,
    "scan-determinism": 0,
    "verify-dependency-lock": 0,
    "regenerate-envelope": 3,
    "verify-manifest": 0,
    "copy-image-dockerfile": 3,
    "generate-reproduce-script": 3,
    "verify-l3": 3,
    "declare-determinism": 3,
    "delete-determinism": 0,
    "answer-environment-archive": 2,
    "clear-environment-archive-answer": 0,
    "deposit-environment-archive": 3,
    "remove-ai-model": 0,
}

# Artifacts whose writer does not move the bytes unless the content it
# describes genuinely moved. This is the disposition that makes a
# crossing harmless without asking the researcher anything, so it is
# not taken on trust: every member is driven twice against a real
# repository by
# `test_a_content_guarded_artifact_is_not_rewritten_when_nothing_changed`,
# and a member with no driver fails that test rather than passing.
SET_CONTENT_GUARDED_ARTIFACTS = frozenset({
    _S_ENVIRONMENT_JSON, _S_MANIFEST, _S_REPRODUCE_SCRIPT,
})

S_DISPOSITION_WARNED = "warns-first"
S_DISPOSITION_UNWARNED = "unwarned"

# Every crossing a human has read and accepted, with the reason. An
# action that crosses and is absent from here FAILS: the point is that
# a new one is classified deliberately, not that crossings are banned.
DICT_ACCEPTED_CROSSINGS = {
    "declare-no-binaries": (
        S_DISPOSITION_WARNED,
        "The waiver is a Level 3 claim recorded in the project "
        "definition, which Level 2 compares; the researcher confirms "
        "before it is written.",
    ),
    "remove-binary": (
        S_DISPOSITION_WARNED,
        "Removing a declared package rewrites the project "
        "definition, which Level 2 compares; the researcher confirms "
        "before it is written.",
    ),
    "verify-l3": (
        S_DISPOSITION_WARNED,
        "The rebuild attestation is vaibify's own artefact, committed "
        "by the verification that produced it, and it is compared "
        "against both remotes outside the envelope. The researcher is "
        "warned before the verification begins.",
    ),
    "declare-binary": (
        S_DISPOSITION_UNWARNED,
        "Declaring a package to satisfy Level 3 rewrites the project "
        "definition, which Level 2 compares, and says nothing about "
        "the push and Zenodo version that restores it. Surfaced by "
        "this test on 2026-09-09; the remedy is a confirmation naming "
        "the cost, and the decision is the researcher's.",
    ),
    "declare-determinism": (
        S_DISPOSITION_UNWARNED,
        "Declaring the reproducibility rules to satisfy Level 3 "
        "rewrites the project definition, which Level 2 compares, "
        "with no warning. Same shape as the deposit instance except "
        "that the written content is real rather than an echo, so the "
        "honest remedy is a warning rather than a removal. Surfaced "
        "by this test on 2026-09-09.",
    ),
}

# May only fall. Fixing one of these -- by warning, or by not writing
# -- means deleting its entry above and lowering this number in the
# same commit.
_I_UNWARNED_CROSSING_BUDGET = 2


# ----------------------------------------------------------------------
# Reading the dashboard's ladder-action table
# ----------------------------------------------------------------------


def _fsReadRepositoryFile(sRelativePath):
    """Return the text of one repository file."""
    with open(
        os.path.join(_S_REPOSITORY_ROOT, sRelativePath),
        encoding="utf-8",
    ) as fileSource:
        return fileSource.read()


def _fsReadActionTableBlock():
    """Return the source text of ``_DICT_PROJECT_ACTIONS``."""
    listLines = _fsReadRepositoryFile(_S_ACTION_TABLE_SOURCE).split("\n")
    iStart = next(
        iIndex for iIndex, sLine in enumerate(listLines)
        if "var _DICT_PROJECT_ACTIONS = {" in sLine
    )
    listBlock = []
    iDepth = 0
    for sLine in listLines[iStart:]:
        iDepth += sLine.count("{") - sLine.count("}")
        listBlock.append(sLine)
        if iDepth == 0:
            break
    return "\n".join(listBlock)


def _fdictReadLadderActions():
    """Return ``{sAction: {sMethod, sPath, bConfirm}}`` from the table."""
    sBlock = _fsReadActionTableBlock()
    listKeys = re.findall(r'^        "([a-z0-9-]+)": \{', sBlock, re.M)
    dictActions = {}
    for iIndex, sAction in enumerate(listKeys):
        sBody = _fsSelectActionBody(sBlock, listKeys, iIndex)
        matchPath = re.search(r'sPath: "([^"]+)"', sBody)
        matchMethod = re.search(r'sMethod: "(\w+)"', sBody)
        dictActions[sAction] = {
            "sPath": matchPath.group(1) if matchPath else "",
            "sMethod": (
                matchMethod.group(1) if matchMethod else "POST"
            ),
            "bConfirm": (
                "dictConfirm:" in sBody or "fnConfirm:" in sBody
            ),
        }
    return dictActions


def _fsSelectActionBody(sBlock, listKeys, iIndex):
    """Return one action's entry, from its key to the next key."""
    sOpening = '        "%s": {' % listKeys[iIndex]
    iStart = sBlock.index(sOpening)
    if iIndex + 1 < len(listKeys):
        iEnd = sBlock.index('        "%s": {' % listKeys[iIndex + 1])
    else:
        iEnd = len(sBlock)
    return sBlock[iStart:iEnd]


def _tRouteKeyOf(dictAction):
    """Return the ``(sMethod, sRouteTemplate)`` an action posts to."""
    return (
        dictAction["sMethod"].upper(),
        _S_WORKFLOW_ROUTE_PREFIX + dictAction["sPath"],
    )


# ----------------------------------------------------------------------
# Reading what each route writes
# ----------------------------------------------------------------------


def _fdictCollectRouteWrites():
    """Return ``{(sMethod, sRoute): frozenset(artifacts)}`` for every route."""
    dictWrites = {}
    sDirectory = os.path.join(_S_REPOSITORY_ROOT, _S_ROUTE_DIRECTORY)
    for sName in sorted(os.listdir(sDirectory)):
        if not sName.endswith(".py"):
            continue
        treeModule = ast.parse(
            _fsReadRepositoryFile(
                os.path.join(_S_ROUTE_DIRECTORY, sName),
            ),
        )
        listFunctions, dictFunctions = _tIndexFunctions(treeModule)
        for nodeFunction in listFunctions:
            tRoute = _tRouteDeclaredBy(nodeFunction)
            if tRoute:
                dictWrites[tRoute] = _fsetCollectWrites(
                    nodeFunction, dictFunctions,
                )
    return dictWrites


def _tIndexFunctions(treeModule):
    """Return ``(every function node, {sName: [nodes]})`` for a module.

    Nested definitions included: every handler in this package is a
    closure inside its own ``_fnRegister*``.
    """
    listFunctions = []
    dictFunctions = {}
    for nodeAny in ast.walk(treeModule):
        if isinstance(nodeAny, (ast.FunctionDef, ast.AsyncFunctionDef)):
            listFunctions.append(nodeAny)
            dictFunctions.setdefault(nodeAny.name, []).append(nodeAny)
    return listFunctions, dictFunctions


def _tRouteDeclaredBy(nodeFunction):
    """Return the ``(sMethod, sPath)`` a handler decorator declares."""
    for nodeDecorator in nodeFunction.decorator_list:
        if not isinstance(nodeDecorator, ast.Call):
            continue
        nodeCalled = nodeDecorator.func
        if (
            isinstance(nodeCalled, ast.Attribute)
            and nodeCalled.attr in ("post", "put", "delete", "get")
            and nodeDecorator.args
            and isinstance(nodeDecorator.args[0], ast.Constant)
        ):
            return (
                nodeCalled.attr.upper(), nodeDecorator.args[0].value,
            )
    return ()


def _fsetCollectWrites(nodeFunction, dictFunctions, setSeen=None,
                       iDepth=0):
    """Return every compared artifact the handler can reach."""
    setSeen = set() if setSeen is None else setSeen
    setWrites = set()
    if _fbMutatesTheWorkflow(nodeFunction):
        setWrites.add(_S_WORKFLOW_MUTATION)
    if iDepth > 6:
        return frozenset(setWrites)
    for sCalled in _fsetCalleeNames(nodeFunction):
        setWrites.update(DICT_WRITER_ARTIFACTS.get(sCalled, ()))
        if sCalled in TUPLE_ADAPTER_WRITE_METHODS:
            setWrites.add("opaque:" + sCalled)
        for nodeInner in _flistUnvisited(sCalled, dictFunctions, setSeen):
            setWrites.update(
                _fsetCollectWrites(
                    nodeInner, dictFunctions, setSeen, iDepth + 1,
                ),
            )
    return frozenset(setWrites)


def _flistUnvisited(sCalled, dictFunctions, setSeen):
    """Return the module-local definitions of a callee, visited once."""
    if sCalled in setSeen or sCalled not in dictFunctions:
        return []
    setSeen.add(sCalled)
    return dictFunctions[sCalled]


def _fsetCalleeNames(nodeFunction):
    """Return every name called anywhere inside a function."""
    setNames = set()
    for nodeAny in ast.walk(nodeFunction):
        if not isinstance(nodeAny, ast.Call):
            continue
        if isinstance(nodeAny.func, ast.Name):
            setNames.add(nodeAny.func.id)
        elif isinstance(nodeAny.func, ast.Attribute):
            setNames.add(nodeAny.func.attr)
    return setNames


def _fbMutatesTheWorkflow(nodeFunction):
    """Return True iff the function writes into a workflow dict.

    Assignment into ``dictWorkflow[...]`` or a mutating method on it.
    The deposit instance did exactly this and called no save: the
    field reached ``project.json`` on the researcher's next save, so a
    detector that watched only the save call would have reported the
    lane clean.

    Keyed on the BINDING NAME, which is the honest limit of a static
    read: a handler that mutates the definition under some other local
    name is invisible here. Every route module in this package spells
    it ``dictWorkflow``, and the check is coarse in the safe
    direction -- a name is matched by prefix, so a copy bound as
    ``dictWorkflowFresh`` still counts.
    """
    for nodeAny in ast.walk(nodeFunction):
        if isinstance(nodeAny, ast.Assign):
            if _fbAssignsIntoWorkflow(nodeAny):
                return True
        if _fbCallsWorkflowMutator(nodeAny):
            return True
    return False


def _fbAssignsIntoWorkflow(nodeAssign):
    """Return True iff an assignment target is ``dictWorkflow[...]``."""
    for nodeTarget in nodeAssign.targets:
        if (
            isinstance(nodeTarget, ast.Subscript)
            and isinstance(nodeTarget.value, ast.Name)
            and nodeTarget.value.id.startswith("dictWorkflow")
        ):
            return True
    return False


def _fbCallsWorkflowMutator(nodeAny):
    """Return True iff the node mutates a workflow dict by method."""
    return (
        isinstance(nodeAny, ast.Call)
        and isinstance(nodeAny.func, ast.Attribute)
        and nodeAny.func.attr in ("setdefault", "pop", "update")
        and isinstance(nodeAny.func.value, ast.Name)
        and nodeAny.func.value.id.startswith("dictWorkflow")
    )


# ----------------------------------------------------------------------
# The crossings
# ----------------------------------------------------------------------


def _fiComparingLevelOf(sArtifact):
    """Return the level whose verify compares an artifact (0 = none).

    Answered by ``publicationScope`` itself rather than by a second
    list here: the whole defect class is about which level compares
    what, so a copy of that partition would be the next thing to
    drift out of agreement with it.
    """
    if sArtifact == _S_WORKFLOW_MUTATION:
        sArtifact = _S_PROJECT_DEFINITION
    if publicationScope.fsetSelectLevel3Paths([sArtifact]):
        return 3
    if publicationScope.fsetSelectLevel2Paths([sArtifact]):
        return 2
    return 0


def _flistFindCrossings():
    """Return ``(sAction, iRung, sArtifact, iComparedAt)`` for each crossing."""
    dictActions = _fdictReadLadderActions()
    dictWrites = _fdictCollectRouteWrites()
    listCrossings = []
    for sAction, dictAction in sorted(dictActions.items()):
        iRung = DICT_LADDER_ACTION_RUNGS.get(sAction, 0)
        if not iRung:
            continue
        for sArtifact in sorted(
            _fsetArtifactsWrittenBy(dictAction, dictWrites),
        ):
            iComparedAt = _fiComparingLevelOf(sArtifact)
            if iComparedAt in (0, iRung):
                continue
            if sArtifact in SET_CONTENT_GUARDED_ARTIFACTS:
                continue
            listCrossings.append(
                (sAction, iRung, sArtifact, iComparedAt),
            )
    return listCrossings


def _fsetArtifactsWrittenBy(dictAction, dictWrites):
    """Return the artifacts one action's route can write."""
    tRoute = _tRouteKeyOf(dictAction)
    setWritten = set(dictWrites.get(tRoute, ()))
    setWritten.update(DICT_OPAQUE_WRITE_ARTIFACTS.get(tRoute, ()))
    return {
        sArtifact for sArtifact in setWritten
        if not sArtifact.startswith("opaque:")
    }


# ----------------------------------------------------------------------
# The static half
# ----------------------------------------------------------------------


def test_the_rung_ledger_covers_every_ladder_action():
    """A new ladder action is classified, or nothing here can see it.

    Equality in both directions: an unlisted action would be exempt
    from every test below, and a listed action that no longer exists
    would keep a stale judgement alive. It also fails when the table
    parse collapses -- an empty read would otherwise make the whole
    static half vacuously green.
    """
    setParsed = set(_fdictReadLadderActions())
    assert setParsed == set(DICT_LADDER_ACTION_RUNGS), (
        "the dashboard's ladder actions and this file's rung ledger "
        "disagree; classify the new action's rung (0 for an action "
        "that asks a question or withdraws a claim): "
        + str(setParsed.symmetric_difference(DICT_LADDER_ACTION_RUNGS))
    )


def test_every_ladder_action_resolves_to_a_registered_route():
    """A renamed route must not silently empty an action's write set."""
    dictWrites = _fdictCollectRouteWrites()
    listUnresolved = [
        sAction for sAction, dictAction
        in sorted(_fdictReadLadderActions().items())
        if _tRouteKeyOf(dictAction) not in dictWrites
    ]
    assert listUnresolved == [], (
        "these dashboard actions post to a route no handler declares, "
        "so their writes are invisible here: " + str(listUnresolved)
    )


def test_every_named_writer_still_defines_the_function_it_names():
    """A renamed writer would empty the map without failing anything.

    The map is matched against the source by NAME, so a writer that
    has been renamed simply stops being found: every route reading as
    clean is the same output a genuinely clean repository gives. The
    definition is required to exist, not merely the spelling to appear
    somewhere.
    """
    setDefined = _fsetCollectDefinedFunctionNames()
    listMissing = [
        sWriter for sWriter in sorted(DICT_WRITER_ARTIFACTS)
        if sWriter not in setDefined
    ]
    assert listMissing == [], (
        "these writers are named here but defined nowhere in the "
        "package, so the scan they belong to proves nothing: "
        + str(listMissing)
    )


def _fsetCollectDefinedFunctionNames():
    """Return every function name defined anywhere in the package."""
    setDefined = set()
    for sRoot, _listDirectories, listFiles in os.walk(
        os.path.join(_S_REPOSITORY_ROOT, "vaibify"),
    ):
        for sName in sorted(listFiles):
            if not sName.endswith(".py"):
                continue
            with open(
                os.path.join(sRoot, sName), encoding="utf-8",
            ) as fileModule:
                setDefined.update(
                    re.findall(
                        r"^\s*(?:async def|def) (\w+)\(",
                        fileModule.read(), re.M,
                    ),
                )
    return setDefined


def test_the_artifact_map_names_paths_the_publication_scope_knows():
    """The artifacts are the scope module's paths, not lookalikes.

    ``_fiComparingLevelOf`` answers Level 2 for any path that is
    compared and not envelope, so a typo in an artifact name would be
    classified confidently and wrongly. The two families are pinned to
    the scope module instead.
    """
    setEnvelope = set(publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS)
    for sArtifact in (
        _S_ENVIRONMENT_JSON, _S_MANIFEST, _S_REPRODUCE_SCRIPT,
        _S_DOCKERFILE, _S_REQUIREMENTS_LOCK,
    ):
        assert sArtifact in setEnvelope, sArtifact
    assert _S_PROJECT_DEFINITION.startswith(
        publicationScope.S_PROJECTS_DIRECTORY + "/",
    )
    assert _S_ATTESTATION in (
        publicationScope.TUPLE_COMPARED_NOT_REQUIRED_PATHS
    )


@pytest.mark.falsification
def test_no_ladder_action_crosses_a_level_without_a_recorded_disposition():
    """The guard the two shipped instances needed.

    An action clicked to reach rung N that writes a file compared at
    another rung must be in ``DICT_ACCEPTED_CROSSINGS`` with a reason.
    Not "must not happen": some crossings are inherent, and a rule
    nobody can satisfy gets deleted. What must not happen is a
    crossing nobody looked at.

    Kills: restoring the deposit's ``archived`` echo into the workflow
    (the write that dropped a researcher from Level 2 to Level 1 while
    they were reaching for Level 3).
    """
    listUnrecorded = [
        tCrossing for tCrossing in _flistFindCrossings()
        if tCrossing[0] not in DICT_ACCEPTED_CROSSINGS
    ]
    assert listUnrecorded == [], (
        "these actions advance one rung by writing a file another "
        "rung compares against GitHub and Zenodo, with no recorded "
        "judgement — either stop writing it, warn the researcher, or "
        "record why it is acceptable: " + str(listUnrecorded)
    )


@pytest.mark.falsification
def test_a_crossing_recorded_as_warned_really_warns():
    """The ledger's strongest disposition is checked, not believed.

    ``warns-first`` is the reason several crossings are accepted, so
    it is worth exactly as much as the confirmation it claims. The
    check is presence, not wording: the wording will change and the
    guarantee will not.

    Kills: deleting the confirmation from an action recorded here as
    warning first.
    """
    dictActions = _fdictReadLadderActions()
    listSilent = [
        sAction for sAction, (sDisposition, _sReason)
        in sorted(DICT_ACCEPTED_CROSSINGS.items())
        if sDisposition == S_DISPOSITION_WARNED
        and not dictActions.get(sAction, {}).get("bConfirm")
    ]
    assert listSilent == [], (
        "recorded as warning the researcher first, but the dashboard "
        "action carries no confirmation: " + str(listSilent)
    )


def test_no_accepted_crossing_entry_is_stale():
    """An entry outlives its crossing only by being deleted.

    A judgement kept after the code it judged has changed is worse
    than none: it reads as review of the current lane.
    """
    setCrossing = {tCrossing[0] for tCrossing in _flistFindCrossings()}
    listStale = sorted(set(DICT_ACCEPTED_CROSSINGS) - setCrossing)
    assert listStale == [], (
        "these actions no longer cross a level, so their recorded "
        "judgements must be deleted: " + str(listStale)
    )


def test_the_unwarned_crossing_budget_only_falls():
    """The silent crossings are counted, and the count may only fall.

    Equality, not a ceiling: fixing one means deleting its entry and
    lowering ``_I_UNWARNED_CROSSING_BUDGET`` in the same commit, so
    the number here cannot quietly stop describing the repository.
    """
    listUnwarned = sorted(
        sAction for sAction, (sDisposition, _sReason)
        in DICT_ACCEPTED_CROSSINGS.items()
        if sDisposition == S_DISPOSITION_UNWARNED
    )
    assert len(listUnwarned) <= _I_UNWARNED_CROSSING_BUDGET, (
        "a new silent level-crossing was accepted: " + str(listUnwarned)
    )
    assert len(listUnwarned) == _I_UNWARNED_CROSSING_BUDGET, (
        "one was fixed without lowering the budget in the same "
        "commit: " + str(listUnwarned)
    )


def test_every_opaque_write_names_the_artifact_it_writes():
    """A write the scan cannot read is recorded, never ignored.

    The Dockerfile is written through the repo adapter with an
    imported constant, so the scan sees a write and not a path.
    Resolving it would be a second authority on what the handler
    writes; recording it keeps the gap visible and bounded.
    """
    dictWrites = _fdictCollectRouteWrites()
    dictActions = _fdictReadLadderActions()
    listUnnamed = []
    for sAction, dictAction in sorted(dictActions.items()):
        tRoute = _tRouteKeyOf(dictAction)
        bOpaque = any(
            sArtifact.startswith("opaque:")
            for sArtifact in dictWrites.get(tRoute, ())
        )
        if bOpaque and tRoute not in DICT_OPAQUE_WRITE_ARTIFACTS:
            listUnnamed.append(sAction)
    assert listUnnamed == [], (
        "these actions write through the repo adapter to a path this "
        "scan cannot read; record what they write in "
        "DICT_OPAQUE_WRITE_ARTIFACTS: " + str(listUnnamed)
    )


# ----------------------------------------------------------------------
# The content-guarantee half: driven, not read
# ----------------------------------------------------------------------


@pytest.fixture
def sProjectRepo(tmp_path):
    """A real repository with one step's script and output on disk."""
    sRepo = str(tmp_path)
    subprocess.run(["git", "init", "-q", sRepo], check=True)
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    os.makedirs(os.path.join(sRepo, "StepOne"), exist_ok=True)
    for sName, sContent in (
        ("run.py", "print(1)\n"), ("out.csv", "a,b\n1,2\n"),
    ):
        with open(
            os.path.join(sRepo, "StepOne", sName), "w",
        ) as fileOut:
            fileOut.write(sContent)
    return sRepo


def _fdictBuildWorkflow(sProjectRepo):
    """Return a one-step workflow rooted on the fixture repository."""
    return {
        "sName": "Example",
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [{
            "sStepId": "step-one",
            "sName": "Step One",
            "sDirectory": "StepOne",
            "saDataCommands": ["python run.py"],
            "saOutputDataFiles": ["out.csv"],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


def _fbaCaptureManifest(sProjectRepo, dictWorkflow):
    """Write the manifest and return the bytes that landed."""
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    return _fbaReadArtifact(sProjectRepo, _S_MANIFEST)


def _fbaCaptureEnvironmentJson(sProjectRepo, dictWorkflow):
    """Capture the same environment again and return the file's bytes."""
    environmentSnapshot.fnWriteEnvironmentJson(
        sProjectRepo,
        {"dictContainer": {"sImageDigest": "sha256:" + "a" * 64}},
    )
    return _fbaReadArtifact(sProjectRepo, _S_ENVIRONMENT_JSON)


def _fbaCaptureReproduceScript(_sProjectRepo, dictWorkflow):
    """Return the bytes the reproduce-script route would write.

    The route's write needs a container; its CONTENT is this pure
    render, which is the half that can move a published hash.
    """
    return fsRenderReproduceScript(dictWorkflow).encode("utf-8")


DICT_CONTENT_GUARD_DRIVERS = {
    _S_MANIFEST: _fbaCaptureManifest,
    _S_ENVIRONMENT_JSON: _fbaCaptureEnvironmentJson,
    _S_REPRODUCE_SCRIPT: _fbaCaptureReproduceScript,
}


def _fbaReadArtifact(sProjectRepo, sArtifact):
    """Return one artifact's bytes from the fixture repository."""
    with open(
        os.path.join(sProjectRepo, *sArtifact.split("/")), "rb",
    ) as fileArtifact:
        return fileArtifact.read()


@pytest.mark.falsification
@pytest.mark.parametrize(
    "sArtifact", sorted(SET_CONTENT_GUARDED_ARTIFACTS),
)
def test_a_content_guarded_artifact_is_not_rewritten_when_nothing_changed(
    sArtifact, sProjectRepo,
):
    """Repeating the write must not move the bytes.

    This is what lets a crossing be dismissed without asking the
    researcher anything, so it is driven against the real writers
    rather than asserted. An artifact listed as guarded with no driver
    fails here too: the claim and its evidence live together.

    Kills: removing the unchanged-capture guard from
    ``fnWriteEnvironmentJson``, which is the write that diverged both
    remotes on every capture of an environment nobody had changed.
    """
    fbaCapture = DICT_CONTENT_GUARD_DRIVERS.get(sArtifact)
    assert fbaCapture is not None, (
        sArtifact + " is recorded as content-guarded with nothing "
        "driving it"
    )
    dictWorkflow = _fdictBuildWorkflow(sProjectRepo)
    baFirst = fbaCapture(sProjectRepo, dictWorkflow)
    baSecond = fbaCapture(sProjectRepo, dictWorkflow)
    assert baFirst == baSecond, (
        sArtifact + " was rewritten by a repeat of the same capture, "
        "so its published copies now differ for a change nobody made"
    )
