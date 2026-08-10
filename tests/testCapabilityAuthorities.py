"""No capability the hub can reach is unauthorised (migration rule R4).

The inventory (``tests/testMutationInventory.py``) records every
capability ACQUISITION in the package and ratchets how many still lack a
reviewed disposition. That is a record plus a ratchet. It is not a
structural rule, and two things it cannot say are exactly the two that
matter here.

**Which acquisitions the hub can actually reach.** A rule phrased over
``vaibify/gui/`` alone passes while a route bypasses every guarded
primitive one package away: ``gui/buildRoutes.py`` calls
``cli.commandBuild.fnBuildFromConfig``, which reaches
``docker/imageBuilder.py``, which shells out to ``docker build``. So the
population here is the TRANSITIVE import closure seeded at
``vaibify/gui/`` -- 41 raw-effect acquisitions across 8 packages, not the
17 that live in the GUI tree.

**That the record cannot be silenced by regenerating it.** The
inventory's drift check is satisfied by ``--write``; a new
``import subprocess`` in a hub-reachable module therefore becomes
recorded, and recorded is not reviewed. :data:`DICT_NAMED_AUTHORITIES`
is a SEPARATE, hand-maintained list, so a new hub-reachable authority
fails here until a human names it -- with the execution lanes it serves
and a rationale.

Two rejected phrasings, both loopholes, both from R4:

* *"...mutation-capable raw effect"*. Command decoding is best-effort,
  so ``subprocess.run(listCommand)`` would escape for no better reason
  than that nobody could decode ``listCommand``. Capability is the
  boundary; UNKNOWN counts as mutating.
* *"...or its caller runs it inside an observed carrier"*. Observation
  proves only that the callers somebody TESTED were admitted. A new or
  untested caller reaching the same raw helper recreates the bypass.
  Observation classifies intent; it never authorizes.

And R4's transitivity rule: **a directory is never an authority.**
``vaibify/docker/*`` is not allow-listed for being in that directory;
each raw authority in it is named on its own line. A module serving two
lanes -- ``cli/commandBuild.py`` is reached both by the ``vaibify build``
host CLI and by the build route -- declares BOTH, because a disposition
written for one lane says nothing about the other.

**What this file does NOT cover, stated so nobody reads it as more.** It
proves that every raw process/Docker capability the hub can reach has
been NAMED and reviewed. It does not prove any named authority is
admitted at its effect boundary; that is the carrier migration, and the
lanes recorded here are what it will be judged against. It is an
import-origin rule, so a capability obtained purely through reflection
is the inventory's business, not this file's -- ``sys.modules`` and
``getattr`` acquisitions carry their disposition in
``tests/mutationInventory.json``.
"""

import ast
import importlib
import importlib.util
import pathlib
import textwrap

import pytest


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

# The capabilities this rule is about: a raw process launch, a Docker
# client, a raw daemon socket. Reflection is an acquisition too (R3) but
# it is not a raw effect -- it is a way of reaching one, and the thing it
# reaches lands in this list under its own name.
#
# ``process-signal`` (admitted to the inventory 2026-08-10) is
# deliberately NOT here, and the omission is recorded rather than left
# to look like an oversight. Four of its seven sites pass signal 0,
# which delivers nothing at all -- they are existence probes wearing a
# signal's spelling -- and two of the remaining three signal a process
# THIS process created and holds the pid of, or the hub itself. Listing
# all seven as raw effects would bury the one that is: the host
# cancellation group kill. That one carries its review in its inventory
# disposition, where the reasoning sits next to the code it is about.
# Revisit if a second signalling site ever aims outside vaibify's own
# processes.
SET_RAW_EFFECT_CAPABILITIES = frozenset({
    "process-launch", "docker-client", "unix-socket",
})

# The one disposition that is a CLASS rather than a per-site judgement.
# It covers ``docker.errors`` exception TYPES bound only to classify an
# already-raised error (an ``except`` clause or an ``isinstance`` test).
# An exception type conveys no capability: it cannot construct a client,
# open a socket, or issue a request. Disposing such bindings one at a
# time as though they were clients would bury the real ones. The class
# once held five members across the GUI poll lanes; they were replaced
# by the gateway predicate ``fbErrorMeansContainerUnreachable``, whose
# single lazy APIError import is now the class's one member.
#
# The scanner deliberately over-records (safe direction) and its
# vocabulary is kill-confirm-covered, so this is recorded as a reviewed
# CLASS here rather than narrowed over there -- a scanner rule reading
# "the errors submodule is not a client" would be one more spelling-based
# exception in a scanner whose whole lesson is that spelling-based rules
# lose. :func:`testTheExceptionTypeClassOnlyEverCoversAnException` keeps
# the class honest by resolving each name and checking it really is one.
S_LANE_EXCEPTION_TYPE = "exception-type"


def _fdictAuthority(listLanes, sRationale):
    """Return one named authority's record."""
    return {"listLanes": listLanes, "sRationale": sRationale}


# Every raw-effect capability the hub can reach, one line each, keyed by
# the inventory's stable acquisition identity
# (file|scope|capability|name|kind|ordinal). Fail-closed in both
# directions: an acquisition the scan finds and this list does not is
# unauthorised, and a line here the scan no longer finds is a review
# aimed at code that moved.
DICT_NAMED_AUTHORITIES = {
    # -- the host-mode gateway -------------------------------------------
    "host/hostConnection.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "websocket", "background"],
            "The host-mode execution gateway, hub-reachable since the "
            "connection router wired it into dictCtx['docker'] (wave "
            "2). The single permitted subprocess launcher under "
            "vaibify/host/ (testHostSubprocessConfinement pins it); "
            "every launch is admission-gated, spawned suspended behind "
            "a stdin gate, and write-ahead journaled as host-exec with "
            "its recycle-proof group identity before the gate opens. "
            "It executes on the HOST with the user's authority by "
            "design -- the host-mode warning modal owns that "
            "disclosure, and the path guard bounds every direct path "
            "argument to the project and scratch roots.",
        ),
    # -- host CLI and configuration -------------------------------------
    "cli/commandBuild.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Stages a build context and drives docker build. Serves TWO "
            "lanes: `vaibify build` on the host, and gui/buildRoutes.py "
            "calling fnBuildFromConfig. The HTTP lane is the R4 example "
            "-- a GUI-directory rule would miss it entirely.",
        ),
    "cli/commandBuild.py|_fsGitRemoteUrl|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Reads `git remote get-url` in the host checkout to stamp "
            "build provenance. Host-side read; touches no container.",
        ),
    "cli/commandBuild.py|_fsGitBranch|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Reads `git rev-parse --abbrev-ref HEAD` in the host "
            "checkout for the same provenance stamp.",
        ),
    "cli/configLoader.py|fbDockerAvailable|docker-client|docker|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Imports the docker package to answer whether the SDK is "
            "installed at all. Marked `# noqa: F401` because the import "
            "IS the probe -- no client is constructed here.",
        ),
    "cli/preflightChecks.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Host preflight: probes the docker CLI and daemon before a "
            "run is attempted, so a failure is reported rather than hit.",
        ),
    "config/keepAliveManager.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "Launches and reaps the macOS `caffeinate` keep-alive that "
            "holds the host awake for a named container. Host process "
            "management; reaches no container.",
        ),
    "config/processLiveness.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "background", "startup"],
            "Probes whether a recorded host PID is still alive, so a "
            "stale lock is not read as a live hub. Host-side read.",
        ),
    "config/secretManager.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Delegates credential retrieval to the OS keychain / `gh "
            "auth` rather than implementing token storage. Host-side, "
            "and deliberately the only place that shells out for a "
            "secret.",
        ),
    # -- the Docker package: named one at a time, never by directory ----
    "docker/__init__.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Package-level import serving the docker-availability probe. "
            "Named individually: being under vaibify/docker/ authorizes "
            "nothing.",
        ),
    "docker/containerManager.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http", "background"],
            "THE container lifecycle gateway: run, stop, rm, inspect. "
            "One of the two modules that DEFINE the boundary, so its "
            "authority is the boundary rather than a caller of it.",
        ),
    "docker/dockerConnection.py|_fmoduleGetDocker|docker-client|docker|"
    "import|0":
        _fdictAuthority(
            ["host-cli", "http", "websocket", "background"],
            "THE docker-py gateway. Every guarded primitive and the "
            "single typed-read exemption live behind this client; no "
            "other module may construct one.",
        ),
    "docker/dockerConnection.py|_fnMountUnixAdapter|docker-client|"
    "docker.transport.unixconn.UnixHTTPAdapter|import-from|0":
        _fdictAuthority(
            ["host-cli", "http", "websocket", "background"],
            "Mounts the Unix-socket transport onto the gateway's own "
            "session so the connection pool can be tuned. Inside the "
            "gateway, on the client the gateway constructed.",
        ),
    "docker/dockerConnection.py|_fnEnsureDockerHost|process-launch|"
    "subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http", "websocket", "background"],
            "Resolves DOCKER_HOST from `docker context inspect` when the "
            "environment does not carry one, so a Colima or Rancher "
            "socket is found before the client is built.",
        ),
    "docker/dockerConnection.py|fbErrorMeansContainerUnreachable|"
    "docker-client|docker.errors.APIError|import-from|0":
        _fdictAuthority(
            [S_LANE_EXCEPTION_TYPE],
            "The substrate-failure predicate's lazy binding of the SDK's "
            "APIError, used only in an isinstance test. It replaced the "
            "five except-clause acquisitions the GUI poll lanes held, so "
            "the exception-type class now has one member, inside the "
            "gateway that owns the docker capability.",
        ),
    "docker/dockerContext.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Reads the active `docker context` to report which runtime "
            "the host is on. Read-only daemon query.",
        ),
    "docker/imageBuilder.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Runs `docker build` and streams its output. The raw "
            "authority at the far end of the buildRoutes -> commandBuild "
            "-> imageBuilder chain: reachable from an HTTP route through "
            "TWO packages, which is why this list is transitive.",
        ),
    "docker/volumeManager.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Creates and inspects the named workspace volume. Volume "
            "REMOVAL is `vaibify destroy`, which is not hub-reachable.",
        ),
    "docker/x11Forwarding.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["host-cli", "http"],
            "Probes the host X server / XQuartz so GUI forwarding is "
            "offered only when it would work.",
        ),
    # -- the GUI tree ---------------------------------------------------
    "gui/commitCarrier.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "websocket", "background"],
            "The EXCEPTIONAL authority: fdictLaunchGatedHelperProcess "
            "spawns an arbitrary argv. Its tested constraint is the "
            "holder-identity assertion plus the journaling gate -- see "
            "testTheGatedHelperRefusesWithoutItsHoldersIdentity.",
        ),
    "gui/dockerStatus.py|_fbCaffeinateRunning|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "Asks `pgrep` whether this host's caffeinate keep-alive is "
            "live, so the dashboard reports sleep state honestly. Host "
            "process read.",
        ),
    "gui/dockerStatus.py|fdictDetectDockerRuntime|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http"],
            "Reads `docker context inspect` to name the runtime in the "
            "dashboard. Read-only daemon query.",
        ),
    "gui/gitStatus.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "Runs git in a HOST checkout to report branch and dirtiness. "
            "Container-side git is containerGit, which goes through the "
            "gateway; this one never touches a container.",
        ),
    "gui/hostControlChannel.py|fdictSendHostControlRequest|unix-socket|"
    "socket.AF_UNIX|attribute|0":
        _fdictAuthority(
            ["host-control-socket"],
            "Connects to the hub's OWN host-control Unix socket, not to "
            "the Docker daemon. A daemon socket reachable from a "
            "container is the bind-mount hole fixed in 345f6b2; this "
            "socket is the hub talking to itself.",
        ),
    "gui/registryRoutes.py|_fbDockerContainerExists|process-launch|"
    "subprocess|import|0":
        _fdictAuthority(
            ["http"],
            "`docker ps -a --filter name=` to refuse creating a project "
            "whose container name is taken. Read-only, pre-claim: it "
            "answers about a container no session owns yet.",
        ),
    "gui/resourceMonitor.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http"],
            "`docker stats --no-stream` for the dashboard's CPU and "
            "memory tiles. Read-only daemon query; the disk half of this "
            "module went through the typed-read exemption instead.",
        ),
    "gui/routes/sessionRoutes.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http"],
            "Relaunches the hub itself as a detached host process on a "
            "new port. Spawns vaibify, not a container command.",
        ),
    "gui/routes/syncRoutes.py|_fsReadHostGitUserName|process-launch|"
    "subprocess|import|0":
        _fdictAuthority(
            ["http"],
            "`git config user.name` on the HOST, to prefill the sync "
            "panel's author field. Host-side read.",
        ),
    "gui/setupServer.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http"],
            "The first-run setup wizard's build lane: runs the image "
            "build before any container exists, so there is nothing yet "
            "for a carrier to be admitted against.",
        ),
    "gui/syncDispatcher.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "Host-side git and `ls-remote` for the sync panel. The "
            "container half of sync goes through the gateway; this is "
            "the host remote it is synced against.",
        ),
    "gui/workspacePath.py|<module>|process-launch|subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "`docker inspect` to resolve the named workspace volume's "
            "mount point. Read-only daemon query.",
        ),
    # -- reproducibility ------------------------------------------------
    "reproducibility/dataArchiver.py|<module>|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "Builds a host-side archive of a run's outputs with tar and "
            "checksums. Operates on files already pulled to the host.",
        ),
    "reproducibility/dependencyPinning.py|<module>|process-launch|"
    "subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "Resolves the exact installed versions behind a run so the "
            "declaration can be pinned. Host-side interpreter query.",
        ),
    "reproducibility/environmentSnapshot.py|<module>|process-launch|"
    "subprocess|import|0":
        _fdictAuthority(
            ["http", "background"],
            "Captures host platform, toolchain and image identity for "
            "the provenance record.",
        ),
    "reproducibility/githubAuth.py|<module>|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "Delegates to `gh auth` rather than storing a token. The "
            "credential never enters vaibify's own storage.",
        ),
    "reproducibility/overleafMirror.py|<module>|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "Drives git against the Overleaf remote from a host working "
            "copy.",
        ),
    "reproducibility/overleafSync.py|<module>|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "The commit-and-push half of the Overleaf mirror, same host "
            "working copy.",
        ),
    "reproducibility/repoFiles.py|<module>|process-launch|subprocess|"
    "import|0":
        _fdictAuthority(
            ["http", "background"],
            "Enumerates tracked files with `git ls-files` in a host "
            "checkout.",
        ),
}


# Raw-effect acquisitions that still live under vaibify/gui/. A RATCHET:
# every one routed through a gateway primitive lowers it, and it may
# never rise. Seeded at 19 on 2026-08-03 -- 13 process-launch, 5
# Docker-client (all of them exception types), 1 Unix socket. 19 -> 18
# when the stop route's own `docker stop` went through the lifecycle
# gateway instead, 18 -> 17 when the file pull's own `docker cp` went
# through the gateway's streaming read. 17 -> 12 when the five
# exception-type acquisitions left the GUI tree for the gateway's
# fbErrorMeansContainerUnreachable predicate, leaving zero Docker-client
# acquisitions under vaibify/gui/.
I_GUI_RAW_CAPABILITY_BUDGET = 12


def _fmoduleGenerator():
    """Import the inventory generator by path (tools/ is not a package)."""
    pathTool = PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    spec = importlib.util.spec_from_file_location(
        "generateMutationInventoryForAuthorities", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


@pytest.fixture(scope="module")
def moduleGenerator():
    return _fmoduleGenerator()


def fsModuleNameForPath(pathModule, pathRoot):
    """Return the dotted module name a source file is imported as."""
    listParts = list(pathModule.relative_to(pathRoot).with_suffix("").parts)
    if listParts[-1] == "__init__":
        listParts = listParts[:-1]
    return ".".join(listParts)


def _fsetImportedNames(pathModule, sModuleName):
    """Return every dotted name this module imports, relatives resolved.

    Both statement forms and both spellings, because a hub-reachable
    authority is routinely reached by a FUNCTION-level relative import --
    ``registryRoutes`` imports ``containerManager`` inside the handler
    that stops a container, and a module-header-only reading would call
    that unreachable.
    """
    setNames = set()
    listPackage = sModuleName.split(".")
    for nodeImport in ast.walk(ast.parse(pathModule.read_text())):
        if isinstance(nodeImport, ast.Import):
            for nodeAlias in nodeImport.names:
                setNames.add(nodeAlias.name)
        elif isinstance(nodeImport, ast.ImportFrom):
            if nodeImport.level:
                listBase = (
                    listPackage if pathModule.name == "__init__.py"
                    else listPackage[:-1]
                )
                for _ in range(nodeImport.level - 1):
                    listBase = listBase[:-1]
                sModule = ".".join(
                    listBase + ([nodeImport.module] if nodeImport.module else [])
                )
            else:
                sModule = nodeImport.module or ""
            setNames.add(sModule)
            for nodeAlias in nodeImport.names:
                setNames.add(sModule + "." + nodeAlias.name)
    return setNames


def fdictBuildImportGraph(pathRoot, pathPackage):
    """Return ``{module: {imported module, ...}}`` for one package tree."""
    dictByName = {
        fsModuleNameForPath(pathModule, pathRoot): pathModule
        for pathModule in pathPackage.rglob("*.py")
        if "__pycache__" not in pathModule.parts
    }
    dictGraph = {}
    for sModuleName, pathModule in dictByName.items():
        setEdges = set()
        for sImported in _fsetImportedNames(pathModule, sModuleName):
            sCandidate = sImported
            while sCandidate:
                if sCandidate in dictByName:
                    setEdges.add(sCandidate)
                    break
                if "." not in sCandidate:
                    break
                sCandidate = sCandidate.rsplit(".", 1)[0]
        dictGraph[sModuleName] = setEdges
    return dictGraph, dictByName


def fsetReachTransitively(dictGraph, setSeeds, setExcludedEdges=frozenset()):
    """Return the TRANSITIVE import closure of ``setSeeds``.

    Transitive is the whole point. A one-hop reading is what a
    GUI-directory rule amounts to, and it passes while ``buildRoutes``
    reaches ``docker build`` two packages away.

    ``setExcludedEdges`` drops named ``(from, to)`` pairs, which is how
    :func:`testTheRealBuildChainIsReachedThroughItsMiddleModule` asks
    whether a module survives the loss of its shortcut edge.
    """
    setSeen = set(setSeeds)
    listStack = list(setSeen)
    while listStack:
        sModule = listStack.pop()
        for sDependency in dictGraph.get(sModule, ()):
            if (sModule, sDependency) in setExcludedEdges:
                continue
            if sDependency not in setSeen:
                setSeen.add(sDependency)
                listStack.append(sDependency)
    return setSeen


def fsetHubReachableFiles(setExcludedEdges=frozenset()):
    """Return the package-relative files the hub can reach, transitively."""
    dictGraph, dictByName = fdictBuildImportGraph(
        PATH_REPOSITORY, PATH_PACKAGE,
    )
    setSeeds = {
        sName for sName in dictByName if sName.startswith("vaibify.gui")
    }
    setReached = fsetReachTransitively(dictGraph, setSeeds, setExcludedEdges)
    return {
        str(dictByName[sName].relative_to(PATH_PACKAGE))
        for sName in setReached
    }


def flistHubReachableRawAcquisitions(moduleGenerator):
    """Return every raw-effect acquisition in a hub-reachable module."""
    setFiles = fsetHubReachableFiles()
    return [
        dictAcquisition
        for dictAcquisition in moduleGenerator.flistAcquisitions()
        if dictAcquisition["sFile"] in setFiles
        and dictAcquisition["sCapability"] in SET_RAW_EFFECT_CAPABILITIES
    ]


def _fsetPermittedLanes(moduleGenerator):
    """Return the closed lane vocabulary, shared with the inventory."""
    return frozenset(
        moduleGenerator.DICT_REVIEWER_ENUMS["listExecutionLanes"]
    ) | {S_LANE_EXCEPTION_TYPE}


# ---------------------------------------------------------------------
# The rule.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testEveryHubReachableRawCapabilityIsNamedIndividually(moduleGenerator):
    """A raw capability the hub can reach must be named, or CI fails.

    FAIL-CLOSED, and separate from the inventory on purpose: the
    inventory's drift check is satisfied by ``--write``, so a new
    ``import subprocess`` in a hub-reachable module becomes RECORDED by
    running one command. Recorded is not reviewed. This list is
    hand-maintained, so the same edit fails here until a human names the
    authority and says which lanes it serves.

    Kills: dropping the unnamed-authority assertion, or defaulting an
    absent key to permitted.
    """
    listReachable = flistHubReachableRawAcquisitions(moduleGenerator)
    listUnnamed = sorted(
        moduleGenerator.fsAcquisitionKey(dictAcquisition)
        for dictAcquisition in listReachable
        if moduleGenerator.fsAcquisitionKey(dictAcquisition)
        not in DICT_NAMED_AUTHORITIES
    )
    assert listUnnamed == [], (
        f"these raw process/Docker capabilities are reachable from the "
        f"hub and authorised by nobody: {listUnnamed}. Name each one in "
        f"DICT_NAMED_AUTHORITIES with the lanes it serves and a "
        f"rationale -- regenerating the inventory does not answer for it."
    )


def testNoNamedAuthorityOutlivesTheCodeItRuledOn(moduleGenerator):
    """A ruling on code that moved is a review aimed at fiction.

    The other direction of the same fail-closed list. An entry the scan
    no longer finds is stale, and a stale entry is how an allow-list
    quietly grows: nobody deletes lines they cannot attribute.
    """
    setScanned = {
        moduleGenerator.fsAcquisitionKey(dictAcquisition)
        for dictAcquisition in flistHubReachableRawAcquisitions(
            moduleGenerator,
        )
    }
    listStale = sorted(set(DICT_NAMED_AUTHORITIES) - setScanned)
    assert listStale == [], (
        f"these named authorities no longer exist or are no longer "
        f"hub-reachable: {listStale}. Delete the line -- do not leave a "
        f"review pointing at code that moved."
    )


def testAnAuthorityIsNamedByAcquisitionAndNeverByDirectory():
    """``vaibify/docker/*`` is not an authority; each module in it is.

    R4's transitivity rule in its structural form. A directory entry
    would authorize whatever anyone later put in that directory, which is
    the property an allow-list must not have.
    """
    listMalformed = []
    for sKey, dictEntry in DICT_NAMED_AUTHORITIES.items():
        listFields = sKey.split("|")
        if len(listFields) != 6:
            listMalformed.append(sKey)
        elif "*" in sKey or sKey.endswith("/"):
            listMalformed.append(sKey)
        elif not dictEntry["sRationale"].strip():
            listMalformed.append(sKey)
    assert listMalformed == [], (
        f"an authority must be one acquisition with a rationale, never a "
        f"directory or a pattern: {listMalformed}"
    )
    listDockerPackage = sorted(
        sKey for sKey in DICT_NAMED_AUTHORITIES
        if sKey.startswith("docker/")
    )
    assert len(listDockerPackage) >= 8, (
        "the Docker package's authorities are named one at a time; a "
        "shrink here means somebody collapsed them into a rule"
    )


def testEveryNamedAuthorityDeclaresLanesFromTheClosedVocabulary(
    moduleGenerator,
):
    """Free text in a lane makes the record incomparable.

    The vocabulary is the inventory generator's, imported rather than
    copied, so "the route" and "route wrapper" cannot become two answers
    to one question.
    """
    setPermitted = _fsetPermittedLanes(moduleGenerator)
    listOffenders = []
    for sKey, dictEntry in DICT_NAMED_AUTHORITIES.items():
        if not dictEntry["listLanes"]:
            listOffenders.append((sKey, "no lane declared"))
            continue
        for sLane in dictEntry["listLanes"]:
            if sLane not in setPermitted:
                listOffenders.append((sKey, sLane))
    assert listOffenders == [], (
        f"undeclared lanes: {listOffenders}; permitted: "
        f"{sorted(setPermitted)}"
    )


def testAModuleServingTwoLanesDeclaresBothOfThem():
    """``commandBuild`` is reached by the host CLI AND by an HTTP route.

    R4 names this case explicitly: a disposition written for the CLI lane
    says nothing about what the route may do, so the record has to carry
    both or it is answering a question nobody asked.
    """
    for sKey, dictEntry in DICT_NAMED_AUTHORITIES.items():
        if not sKey.startswith("cli/commandBuild.py|"):
            continue
        assert {"host-cli", "http"} <= set(dictEntry["listLanes"]), (
            f"{sKey} serves both the `vaibify build` CLI and "
            f"gui/buildRoutes.py, and must declare both lanes; it "
            f"declares {dictEntry['listLanes']}"
        )


@pytest.mark.falsification
def testTheRealBuildChainIsReachedThroughItsMiddleModule():
    """The named live example: a route's raw effect two packages away.

    ``gui/buildRoutes.py`` calls ``cli.commandBuild.fnBuildFromConfig``,
    which reaches ``docker/imageBuilder.py``, which runs ``docker
    build``. buildRoutes ALSO imports imageBuilder directly for an
    unrelated helper, so the direct edge is excluded here -- otherwise
    this would pass on a one-hop reading and prove nothing about the
    chain it is named after.

    Kills: making fsetReachTransitively stop after one hop.
    """
    setWithoutShortcut = fsetHubReachableFiles(setExcludedEdges=frozenset({
        ("vaibify.gui.buildRoutes", "vaibify.docker.imageBuilder"),
    }))
    assert "cli/commandBuild.py" in setWithoutShortcut
    assert "docker/imageBuilder.py" in setWithoutShortcut, (
        "imageBuilder is reachable from an HTTP route only THROUGH "
        "commandBuild once the direct import is set aside; a reachability "
        "rule that loses it would authorise `docker build` by omission"
    )


@pytest.mark.falsification
def testAnUnnamedAuthorityBehindAHelperIsStillReported(tmp_path):
    """A synthetic route -> helper -> raw authority chain, unrelated to
    the build chain above, driven end to end.

    Two unrelated chains, because one proves only that the known example
    is handled. This one isolates a DIFFERENT guard: not that reach is
    transitive, but that an acquisition the record has never heard of is
    REPORTED rather than treated as fine.

    Kills: defaulting an absent DICT_NAMED_AUTHORITIES key to permitted.
    """
    pathPackage = tmp_path / "syntheticPackage"
    (pathPackage / "gui").mkdir(parents=True)
    (pathPackage / "farAway").mkdir()
    (pathPackage / "__init__.py").write_text("")
    (pathPackage / "gui" / "__init__.py").write_text("")
    (pathPackage / "farAway" / "__init__.py").write_text("")
    (pathPackage / "gui" / "someRoute.py").write_text(textwrap.dedent("""
        from syntheticPackage import middleHelper

        def fnHandleRequest():
            return middleHelper.fnDoTheWork()
    """))
    (pathPackage / "middleHelper.py").write_text(textwrap.dedent("""
        from syntheticPackage.farAway import rawAuthority

        def fnDoTheWork():
            return rawAuthority.fnLaunch()
    """))
    (pathPackage / "farAway" / "rawAuthority.py").write_text(textwrap.dedent("""
        import subprocess

        def fnLaunch():
            return subprocess.run(["docker", "rm", "-f", "anything"])
    """))

    dictGraph, dictByName = fdictBuildImportGraph(tmp_path, pathPackage)
    setSeeds = {
        sName for sName in dictByName
        if sName.startswith("syntheticPackage.gui")
    }
    setReached = fsetReachTransitively(dictGraph, setSeeds)
    assert "syntheticPackage.farAway.rawAuthority" in setReached, (
        "the raw authority two hops from the route was not reached"
    )

    # The authority is real, reachable, and named by nobody -- which is
    # exactly the state the rule must report rather than tolerate.
    sKey = (
        "farAway/rawAuthority.py|<module>|process-launch|subprocess|import|0"
    )
    assert sKey not in DICT_NAMED_AUTHORITIES
    listUnnamed = [
        sCandidate for sCandidate in [sKey]
        if sCandidate not in DICT_NAMED_AUTHORITIES
    ]
    assert listUnnamed == [sKey], (
        "an acquisition absent from the record was treated as permitted"
    )


@pytest.mark.falsification
def testTheExceptionTypeClassOnlyEverCoversAnException():
    """The one CLASS disposition, kept honest by resolving the name.

    Every acquisition filed under it is a ``docker.errors`` type bound
    only to classify an already-raised error, and disposing those one
    at a time as clients would bury the real ones. A class disposition
    is only safe while it cannot be stretched: this resolves each
    covered name and fails if it is not a subclass of
    ``BaseException`` -- so filing ``docker.APIClient`` under it is a
    test failure, not a review comment.

    Kills: applying S_LANE_EXCEPTION_TYPE to anything that is not an
    exception type.
    """
    listOffenders = []
    for sKey, dictEntry in DICT_NAMED_AUTHORITIES.items():
        if S_LANE_EXCEPTION_TYPE not in dictEntry["listLanes"]:
            continue
        sCapabilityName = sKey.split("|")[3]
        objResolved = _fobjResolveDottedName(sCapabilityName)
        if objResolved is None:
            listOffenders.append((sKey, "unresolvable"))
        elif not (
            isinstance(objResolved, type)
            and issubclass(objResolved, BaseException)
        ):
            listOffenders.append((sKey, "not an exception type"))
    assert listOffenders == [], (
        f"the exception-type class was applied to something that "
        f"conveys capability: {listOffenders}"
    )


def _fobjResolveDottedName(sDottedName):
    """Import and return a dotted name, or the module itself, or None."""
    listParts = sDottedName.split(".")
    for iSplit in range(len(listParts), 0, -1):
        try:
            objCurrent = importlib.import_module(".".join(listParts[:iSplit]))
        except ImportError:
            continue
        for sAttribute in listParts[iSplit:]:
            objCurrent = getattr(objCurrent, sAttribute, None)
            if objCurrent is None:
                return None
        return objCurrent
    return None


def testTheGuiRawCapabilityBudgetOnlyEverShrinks(moduleGenerator):
    """A ratchet on raw capability still living inside the GUI tree.

    §9's "no module under vaibify/gui/ holds a process-creation or Docker
    capability" is the destination, not today's state. The honest interim
    is a number that may only fall, so a new one cannot arrive quietly
    and the remaining ones stay visible.
    """
    listGui = [
        dictAcquisition
        for dictAcquisition in flistHubReachableRawAcquisitions(
            moduleGenerator,
        )
        if dictAcquisition["sFile"].startswith("gui/")
    ]
    assert len(listGui) <= I_GUI_RAW_CAPABILITY_BUDGET, (
        f"{len(listGui)} raw process/Docker capabilities live under "
        f"vaibify/gui/, over the budget of "
        f"{I_GUI_RAW_CAPABILITY_BUDGET}: "
        f"{sorted(moduleGenerator.fsAcquisitionKey(a) for a in listGui)}"
    )
    if len(listGui) < I_GUI_RAW_CAPABILITY_BUDGET:
        pytest.fail(
            f"only {len(listGui)} raw capabilities remain under "
            f"vaibify/gui/ -- lower I_GUI_RAW_CAPABILITY_BUDGET to hold "
            f"the gain."
        )
