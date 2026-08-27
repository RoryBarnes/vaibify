"""Every site the scan cannot read is ruled on, and the ruling can expire.

The inventory declares its blind spot -- launches whose argv is assembled
where the AST scan cannot follow -- and identifies each site by two
hashes: the call expression and the whole enclosing function. That is
enough to prove "same site". It is NOT enough to preserve "somebody
reviewed this", and the gap is demonstrated rather than argued: for

    def f():
        command = fbuildGitCommand()
        subprocess.run(command)

the call expression is ``subprocess.run(command)`` both before AND after
the builder is swapped from assembling a git invocation to assembling
``docker rm``. Verified in
``testMutationInventory.py::testABlindSpotDispositionDiesWithItsCommandBuilder``:
identical hashes. The scope hash catches that particular swap because the
builder is NAMED inside the function -- but a builder DEFINED elsewhere,
a constant the argv is built from, or a helper that supplies the
executable all sit outside it.

So a manual disposition here must NAME the symbols its review relied on,
and those symbols are fingerprinted too. Change what the review depended
on and the ruling expires, whatever the call site looks like.
**A call-node hash is an identity, never a warrant for a semantic
review.**

Every opaque site under ``vaibify/gui/`` is ruled on below, and none is
left undisposed. The rulings fall into three shapes: one exceptional
authority with a tested constraint; the determined-in-source sites,
opaque to the SCANNER while entirely determined in the source -- an argv
whose executable is a literal, with only its arguments variable; and the
separate-authority sites of the Agent Council, Docker-SDK calls whose
client is a runtime parameter the scan cannot resolve, operating only on
council-created resources governed by the council registry rather than
the commit carrier. That distinction is the whole content of these
rulings, and it is why they are written by hand.
"""

import ast
import hashlib
import importlib.util
import pathlib

import pytest


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

# The terminal states a blind-spot site may end in. "Mechanically
# traceable" is absent on purpose: a site in this record is by
# construction one the scan could not read, so tracing it resolves it by
# REMOVING it from the blind spot, not by earning a disposition here.
S_DISPOSITION_EXCEPTIONAL_AUTHORITY = "exceptional-authority"
S_DISPOSITION_DETERMINED_IN_SOURCE = "determined-in-source"
S_DISPOSITION_SEPARATE_AUTHORITY = "separate-authority"

SET_DISPOSITIONS = frozenset({
    S_DISPOSITION_EXCEPTIONAL_AUTHORITY,
    S_DISPOSITION_DETERMINED_IN_SOURCE,
    S_DISPOSITION_SEPARATE_AUTHORITY,
})


_S_COUNCIL_RUNNER_RATIONALE = (
    "A Docker-SDK call inside agentCouncilDockerGateway — since "
    "remediation R4 the ONLY council module with SDK reach "
    "(testCouncilGatewayAuthority.py fails the build on any other) — "
    "operating only on a COUNCIL-created container, never the active "
    "project container. Every runner is stamped with the "
    "S_COUNCIL_LABEL at creation and its registry reservation is "
    "written BEFORE the create (fdictReserveAndCreateRunner), so its "
    "lifecycle governance by the council registry is now enforced in "
    "code, not asserted: an admission refusal creates nothing, a "
    "failure after the reserve settles or quarantines the reservation, "
    "and the handle-keyed destruction verifies the target's council "
    "label against the handle's reservation id BEFORE any removal "
    "(fdictDestroyAndSettle), refusing a container the gateway did not "
    "create. Reached only through an opaque gateway-minted handle or "
    "the label-filtered discovery sweep. Opaque to the scanner because "
    "the SDK client is a runtime object, not a literal it can resolve. "
    "Falsified live by testAgentCouncilRunnerLive.py (no mounts, no "
    "socket, 1000-owned, detached child dies with the namespace), "
    "testCouncilGatewayLive.py (failure-after-create settles; forced "
    "indeterminate teardown quarantines with budget held) and "
    "testAgentCouncilProvidersLive.py (shutdown drain and restart "
    "reconcile settle only labelled runners). "
    "Re-reviewed 2026-08-21 when the create and discovery fingerprints "
    "moved: every council container now also carries "
    "S_COUNCIL_RESOURCE_LABEL, the project container name that owns it, "
    "and the startup reconcile SPARES a survivor whose project is held "
    "by a live peer hub. Neither widens SDK reach. The label is inert "
    "metadata read only by the reconcile, and sparing strictly REDUCES "
    "what may be destroyed — the direction that cannot turn a "
    "council-scoped call into a project-scoped one. The failure mode "
    "worth naming is the opposite one, a survivor spared forever "
    "against a lock nobody really holds; fdictReadLockHolder answers "
    "only for a live foreign process and reaps a stale holder on the "
    "spot, and an unlabeled survivor is swept rather than spared, so "
    "both roads lead back to sweeping. "
    "Re-reviewed 2026-08-25 when the survivor fingerprint moved again: "
    "the flock read was extracted to _fbResourceHeldByALivePeerHub so "
    "the campaign-shaped twin (fbCampaignBelongsToALivePeerHub, used by "
    "the startup classifier and the egress sweep) asks the identical "
    "question. Behaviour is unchanged and the direction is unchanged — "
    "two more callers that DECLINE to act. Neither twin touches the "
    "SDK; both read a host lock file."
)
_LIST_COUNCIL_RUNNER_SYMBOLS = [
    "gui/agentCouncilRunner.py::S_COUNCIL_LABEL",
    "gui/agentCouncilRunner.py::S_COUNCIL_RESOURCE_LABEL",
    "gui/agentCouncilRunner.py::fdictComposeRunnerCreateSpecification",
    "gui/agentCouncilDockerGateway.py::fdictReserveAndCreateRunner",
    "gui/agentCouncilDockerGateway.py::fdictDestroyAndSettle",
    "gui/agentCouncilDockerGateway.py::flistDiscoverLabeledRunners",
    "gui/agentCouncilDockerGateway.py::fdictDestroyRunnerAndProveAbsence",
    "gui/agentCouncilRegistry.py::_fbSurvivorBelongsToALivePeerHub",
    "gui/agentCouncilRegistry.py::_fbResourceHeldByALivePeerHub",
]
_DICT_COUNCIL_RUNNER_DISPOSITION = {
    "sDisposition": S_DISPOSITION_SEPARATE_AUTHORITY,
    "sRationale": _S_COUNCIL_RUNNER_RATIONALE,
    "listSupportingSymbols": _LIST_COUNCIL_RUNNER_SYMBOLS,
}


_S_COUNCIL_EGRESS_RATIONALE = (
    "A Docker-SDK call inside agentCouncilDockerGateway — since "
    "remediation R4 the ONLY council module with SDK reach "
    "(testCouncilGatewayAuthority.py fails the build on any other) — "
    "operating only on a COUNCIL-created egress network and its "
    "CONNECT-proxy container, never the active project container. Both "
    "are named from the server-minted S_NETWORK_NAME_PREFIX / "
    "S_PROXY_NAME_PREFIX plus a campaign id that "
    "fnValidateCampaignIdOrRaise refuses unless it is 1-64 characters "
    "of [A-Za-z0-9-]; the proxy image is the DIGEST-PINNED "
    "S_PROXY_IMAGE, created with the runner's hardened posture "
    "(unprivileged user, all capabilities dropped, no-new-privileges, "
    "memory/CPU/PID bounds), and the proxy's argv and environment are "
    "composed from validated server-owned values, so no researcher, "
    "project or model text reaches the SDK command= or environment=. "
    "Its lifecycle authority is the council registry (a separate "
    "authority from the commit carrier, per the design's section "
    "10.3), not a lease: the network and proxy are created per "
    "campaign and settled only after fdictRemoveCampaignEgressResources "
    "proves each gone by a positive NotFound, an unanswered daemon "
    "reported indeterminate rather than swallowed. Opaque to the "
    "scanner because the SDK client is a runtime object, not a literal "
    "it can resolve. Falsified live by testAgentCouncilEgressLive.py "
    "(every escape refused -- allowlisted CONNECT tunnels while "
    "forbidden host, raw-IP, non-CONNECT, direct-dial, external-DNS, "
    "IPv6 and no-network are all refused -- and teardown proves "
    "absence idempotently) and testCouncilGatewayLive.py (the pinned, "
    "non-root, cap-dropped, bounded posture inspected live). "
    "Re-reviewed 2026-08-21 when the proxy-launch fingerprint moved: "
    "the proxy now also carries S_COUNCIL_RESOURCE_LABEL, the owning "
    "project container's name, so a peer hub's startup reconcile can "
    "recognise whose proxy it is instead of sweeping every "
    "council-labelled container on the daemon. It is one more label "
    "value composed from a server-owned name — no new SDK reach, no "
    "new caller text, and nothing about the proxy's posture or its "
    "prove-absence teardown changes."
)
_LIST_COUNCIL_EGRESS_SYMBOLS = [
    "gui/agentCouncilEgress.py::S_NETWORK_NAME_PREFIX",
    "gui/agentCouncilEgress.py::S_PROXY_NAME_PREFIX",
    "gui/agentCouncilEgress.py::S_PROXY_IMAGE",
    "gui/agentCouncilEgress.py::fnValidateCampaignIdOrRaise",
    "gui/agentCouncilRunner.py::S_COUNCIL_RESOURCE_LABEL",
]
_DICT_COUNCIL_EGRESS_DISPOSITION = {
    "sDisposition": S_DISPOSITION_SEPARATE_AUTHORITY,
    "sRationale": _S_COUNCIL_EGRESS_RATIONALE,
    "listSupportingSymbols": _LIST_COUNCIL_EGRESS_SYMBOLS,
}
DICT_BLIND_SPOT_DISPOSITIONS = {
    "gui/agentCouncilDockerGateway.py|_fnAttachToDefaultBridge|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fnAwaitProxyListening|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fnCopyProxyScriptIntoContainer|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fnEnsureProxyImageAvailable|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fnKillContainerQuietly|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fsReadProxyInternalAddress|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fsRemoveInternalNetwork|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fsRemoveInternalNetwork|"
    "untraceable-docker-sdk-root|1": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fsRemoveProxyContainer|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_fsRemoveProxyContainer|"
    "untraceable-docker-sdk-root|1": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_ftStartExecStream|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|_ftStartExecStream|"
    "untraceable-docker-sdk-root|1": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fdictDestroyRunnerAndProveAbsence|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fdictExecuteBoundedTurn|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fdictExecuteBoundedTurn|"
    "untraceable-docker-sdk-root|1": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fdictProbeRunnerAbsence|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fdictReserveAndCreateRunner|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|flistDiscoverLabeledRunners|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fnCopySnapshotIntoRunner|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fnCopySnapshotIntoRunner|"
    "untraceable-docker-sdk-root|1": _DICT_COUNCIL_RUNNER_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fsCreateCampaignInternalNetwork|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/agentCouncilDockerGateway.py|fsLaunchAllowlistProxy|"
    "untraceable-docker-sdk-root|0": _DICT_COUNCIL_EGRESS_DISPOSITION,
    "gui/commitCarrier.py|fdictLaunchGatedHelperProcess|"
    "opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_EXCEPTIONAL_AUTHORITY,
        "sRationale": (
            "The one generic command authority under vaibify/gui/: it "
            "spawns any argv a caller hands it, so no argument about "
            "what the command WILL be can constrain it. Its constraint "
            "is structural instead, and it is two things at once. The "
            "spawn is bracketed by the journal -- fsPrepareOperation "
            "before, fnPromoteOperationToInFlight with the child's real "
            "pid after -- so no phase of it can leave a writer the "
            "journal cannot name. And the promoted identity is then "
            "ASSERTED against the admission, so a helper whose identity "
            "the journal did not accept never reaches its release line. "
            "The child blocks on a stdin gate until that release, and a "
            "parent that dies first closes the pipe, so the helper exits "
            "without acting. Tested by "
            "testTheGatedHelperIsConstrainedByItsHolderIdentity."
        ),
        "listSupportingSymbols": [
            "gui/commitCarrier.py::S_GATED_HELPER_STUB",
            "gui/commitCarrier.py::fdictLaunchGatedHelperProcess",
            "config/mutationAdmission.py::fnAssertOperationAdmittedByIdentity",
        ],
    },
    "gui/gitStatus.py|fsRunGit|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "Opaque to the scanner, determined in the source: the "
            "executable is the literal \"git\" and the hardening flags "
            "are a module constant. Only the git SUBCOMMAND varies, so "
            "this can never become a Docker invocation however its "
            "callers change. It runs in a HOST checkout -- container-side "
            "git is containerGit, which goes through the gateway."
        ),
        "listSupportingSymbols": [
            "gui/gitStatus.py::fsRunGit",
            "gui/gitStatus.py::_fdictBaseEnv",
            "reproducibility/gitHardening.py::LIST_GIT_HARDENING_CONFIG",
        ],
    },
    "gui/routes/sessionRoutes.py|_fprocessLaunchDetachedHub|"
    "opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "Fully literal but for the port number: it re-launches "
            "vaibify itself as a detached host process. Opaque only "
            "because sys.executable is not a constant the scan can "
            "resolve. It spawns the hub, never a container command, and "
            "the agent lane is refused at the route."
        ),
        "listSupportingSymbols": [
            "gui/routes/sessionRoutes.py::_fprocessLaunchDetachedHub",
            "gui/routes/sessionRoutes.py::S_SUPPRESS_BROWSER_ENV",
            "gui/routes/sessionRoutes.py::_fnRejectContainerAgentCallers",
        ],
    },
    "gui/setupServer.py|ftResultRunBuild|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "`python -m vaibify build` in the project directory, every "
            "element of it a literal but the interpreter path. The "
            "first-run wizard's build lane: it runs before any container "
            "for the project exists, so there is nothing yet for a "
            "carrier to be admitted against."
        ),
        "listSupportingSymbols": [
            "gui/setupServer.py::ftResultRunBuild",
        ],
    },
    "gui/syncDispatcher.py|_ftRunHostLsRemote|opaque-subprocess-command|0": {
        "sDisposition": S_DISPOSITION_DETERMINED_IN_SOURCE,
        "sRationale": (
            "`git ls-remote` against a fixed host, to validate a token "
            "before it is used. Executable is the literal \"git\"; the "
            "only variable part is the project id inside a URL built "
            "from a module constant. Reaches a remote, never a "
            "container, and its stderr is redacted so a credential "
            "cannot ride out in an error message."
        ),
        "listSupportingSymbols": [
            "gui/syncDispatcher.py::_ftRunHostLsRemote",
            "gui/syncDispatcher.py::_S_OVERLEAF_HOST",
            "reproducibility/gitHardening.py::"
            "LIST_GIT_CREDENTIAL_ISOLATION_CONFIG",
        ],
    },
}


# The fingerprint of each supporting symbol at the time its disposition
# was written. Regenerate ONLY after re-reading the ruling it belongs to:
# a mismatch means the code a review depended on changed, which is the
# review expiring, not a hash needing a refresh.
# Print the current values by running
#   python -m pytest tests/testBlindSpotDispositions.py -q
# and reading testADispositionExpiresWhenItsSupportingSymbolsChange,
# whose failure names every symbol that moved and its new hash.
DICT_SUPPORTING_SYMBOL_FINGERPRINTS = {
    # Re-read 2026-08-12 and the ruling SURVIVES. The change was to the
    # quarantine refusal's SENTENCE — it now names the kind, target and
    # timestamp of the record that poisoned the container instead of a
    # bare hex id. The gated helper's disposition rests on the identity
    # ASSERTION (a helper whose promoted identity the journal did not
    # accept never reaches its release line), and every branch of that
    # assertion is unchanged: the same scan, the same own-record check,
    # the same holder comparison, the same exception type.
    "config/mutationAdmission.py::fnAssertOperationAdmittedByIdentity":
        "7e18213dca68a496",
    # Agent Council egress and runner: both separate-authority, governed
    # by the council registry, not the commit carrier — and since R4 that
    # governance is enforced by the gateway (reserve-before-create,
    # label-verified destruction), the only council module with SDK
    # reach. Re-read 2026-08-19 for the R4 migration: S_PROXY_IMAGE's
    # hash moved because the image is now digest-pinned, and the moved
    # symbols' hashes are their gateway/runner forms after the split.
    # Re-read 2026-08-20: the two name prefixes went PUBLIC so the
    # startup egress sweep can compose leftover names from stored
    # campaign ids; the values and every use are unchanged, so the
    # ruling stands and only its anchors moved.
    "gui/agentCouncilEgress.py::S_NETWORK_NAME_PREFIX":
        "557a6a74745678bf",
    "gui/agentCouncilEgress.py::S_PROXY_NAME_PREFIX": "b19a94061ae2d43f",
    "gui/agentCouncilEgress.py::S_PROXY_IMAGE": "3af607254cf1d87f",
    "gui/agentCouncilEgress.py::fnValidateCampaignIdOrRaise":
        "87db3f8231c827f6",
    "gui/agentCouncilRunner.py::S_COUNCIL_LABEL": "a5575a22df9e26bf",
    # Re-read 2026-08-21 and the ruling SURVIVES, for the three symbols
    # below plus the two new ones. What changed is ownership
    # ATTRIBUTION, not reach: creation stamps a second label naming the
    # project container, and discovery reads it back. The reconcile then
    # spares a survivor whose project a live peer hub holds — a
    # restriction on what may be destroyed, never an extension of what
    # may be touched. Two hubs on one daemon were destroying each
    # other's live runners because a daemon-wide label was the only
    # thing either could see.
    "gui/agentCouncilRunner.py::S_COUNCIL_RESOURCE_LABEL":
        "eba896746fc73000",
    "gui/agentCouncilRunner.py::fdictComposeRunnerCreateSpecification":
        "5e83fd78e4730736",
    "gui/agentCouncilRegistry.py::_fbSurvivorBelongsToALivePeerHub":
        "9a5b8eee881fa9c5",
    "gui/agentCouncilRegistry.py::_fbResourceHeldByALivePeerHub":
        "c0f215615af1f24c",
    "gui/agentCouncilDockerGateway.py::fdictReserveAndCreateRunner":
        "a24871a501bf8d6b",
    "gui/agentCouncilDockerGateway.py::fdictDestroyAndSettle":
        "51560bacb4682048",
    "gui/agentCouncilDockerGateway.py::flistDiscoverLabeledRunners":
        "be05651320b3051d",
    "gui/agentCouncilDockerGateway.py::fdictDestroyRunnerAndProveAbsence":
        "05d83409bc4a9f16",
    "gui/commitCarrier.py::S_GATED_HELPER_STUB": "2bd6936769eb618c",
    "gui/commitCarrier.py::fdictLaunchGatedHelperProcess":
        "58b0217540f74765",
    "gui/gitStatus.py::_fdictBaseEnv": "944845e8671a1808",
    "gui/gitStatus.py::fsRunGit": "fde0aaa94089d1cc",
    "gui/routes/sessionRoutes.py::S_SUPPRESS_BROWSER_ENV":
        "b0122d40b40d22c0",
    "gui/routes/sessionRoutes.py::_fprocessLaunchDetachedHub":
        "e02a6840322d9b13",
    "gui/routes/sessionRoutes.py::_fnRejectContainerAgentCallers":
        "7c0e85b8e21ddc5e",
    "gui/setupServer.py::ftResultRunBuild": "4820c8f27ce656a2",
    "gui/syncDispatcher.py::_S_OVERLEAF_HOST": "65054ecdea58cd8f",
    "gui/syncDispatcher.py::_ftRunHostLsRemote": "1416c2b66127daa3",
    "reproducibility/gitHardening.py::LIST_GIT_CREDENTIAL_ISOLATION_CONFIG":
        "f11e8b4053702d61",
    "reproducibility/gitHardening.py::LIST_GIT_HARDENING_CONFIG":
        "bbc7222727695adc",
}


def _fmoduleGenerator():
    """Import the inventory generator by path (tools/ is not a package)."""
    pathTool = PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    spec = importlib.util.spec_from_file_location(
        "generateMutationInventoryForBlindSpots", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


@pytest.fixture(scope="module")
def moduleGenerator():
    return _fmoduleGenerator()


def fsFingerprintSupportingSymbol(sSymbolReference):
    """Return the hash of one named symbol's own source text.

    ``<package-relative file>::<top-level name>``. The name may be a
    function, a class, or an assignment target -- a constant list of git
    flags is exactly the kind of thing a ruling rests on, and a record
    that could only fingerprint functions would leave it unwatched.

    Raises when the symbol cannot be found, because a disposition
    pointing at a symbol that no longer exists is the loudest possible
    signal that the review has expired.
    """
    sFile, sName = sSymbolReference.split("::")
    treeModule = ast.parse((PATH_PACKAGE / sFile).read_text())
    sSource = _fsSourceOfTopLevelName(treeModule, sName, sFile)
    return hashlib.sha256(sSource.encode("utf-8")).hexdigest()[:16]


def _fsSourceOfTopLevelName(treeModule, sName, sFile):
    """Return the source text defining one top-level name."""
    sModuleSource = (PATH_PACKAGE / sFile).read_text()
    for nodeTop in treeModule.body:
        if isinstance(
            nodeTop, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ) and nodeTop.name == sName:
            return ast.get_source_segment(sModuleSource, nodeTop)
        if isinstance(nodeTop, ast.Assign):
            for nodeTarget in nodeTop.targets:
                if isinstance(nodeTarget, ast.Name) and nodeTarget.id == sName:
                    return ast.get_source_segment(sModuleSource, nodeTop)
    raise LookupError(
        f"{sFile}::{sName} is named by a disposition and no longer "
        f"exists at module level; that ruling has expired"
    )


# ---------------------------------------------------------------------
# Every blind spot is ruled on, and no ruling is left dangling.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testEveryGuiBlindSpotCarriesADisposition(moduleGenerator):
    """No UNDISPOSED opaque site may remain under vaibify/gui/.

    Not "none remains" -- a mechanically opaque site may survive with an
    independent ruling. What may not survive is one nobody ruled on.

    Kills: adding an opaque subprocess launch under vaibify/gui/ without
    a disposition.
    """
    listUndisposed = sorted(
        moduleGenerator.fsBlindSpotKey(dictSite)
        for dictSite in moduleGenerator.flistUnresolvedSites()
        if dictSite["sFile"].startswith("gui/")
        and moduleGenerator.fsBlindSpotKey(dictSite)
        not in DICT_BLIND_SPOT_DISPOSITIONS
    )
    assert listUndisposed == [], (
        f"these launches under vaibify/gui/ have an argv the scan cannot "
        f"read and no ruling on what they run: {listUndisposed}. Trace "
        f"the site, or add a disposition naming the symbols your review "
        f"relied on."
    )


def testNoDispositionOutlivesTheSiteItRuledOn(moduleGenerator):
    """A ruling on a site that moved is a review aimed at fiction."""
    setPresent = {
        moduleGenerator.fsBlindSpotKey(dictSite)
        for dictSite in moduleGenerator.flistUnresolvedSites()
    }
    listStale = sorted(set(DICT_BLIND_SPOT_DISPOSITIONS) - setPresent)
    assert listStale == [], (
        f"these dispositions name blind spots that no longer exist -- "
        f"the site was traced, moved or deleted: {listStale}"
    )


def testEveryDispositionNamesSupportingSymbolsAndAReason():
    """A ruling with no named support is an opinion, not a review.

    The supporting symbols ARE the review: they say what a reader had to
    look at to believe the ruling. A disposition without them can only be
    re-checked by redoing the whole investigation, which is the thing
    nobody repeats before shipping.
    """
    listOffenders = []
    for sKey, dictEntry in DICT_BLIND_SPOT_DISPOSITIONS.items():
        if dictEntry["sDisposition"] not in SET_DISPOSITIONS:
            listOffenders.append((sKey, "undeclared disposition"))
        if not dictEntry["sRationale"].strip():
            listOffenders.append((sKey, "no rationale"))
        if not dictEntry["listSupportingSymbols"]:
            listOffenders.append((sKey, "no supporting symbols"))
    assert listOffenders == [], listOffenders


@pytest.mark.falsification
def testADispositionExpiresWhenItsSupportingSymbolsChange():
    """The ruling is bound to the code the review actually read.

    The site fingerprints prove "same call, same enclosing function".
    They say nothing about a constant the argv is assembled from or a
    helper defined in another module, and those are exactly what these
    rulings rest on -- "the executable is the literal git and the flags
    are a module constant" is a claim about a symbol two files away.

    Kills: editing LIST_GIT_HARDENING_CONFIG, or any other named
    supporting symbol, without re-reading the ruling that cites it.
    """
    dictMoved = {}
    for sSymbol, sRecorded in sorted(
        DICT_SUPPORTING_SYMBOL_FINGERPRINTS.items(),
    ):
        sActual = fsFingerprintSupportingSymbol(sSymbol)
        if sActual != sRecorded:
            dictMoved[sSymbol] = sActual
    assert dictMoved == {}, (
        f"the code these rulings depend on changed. Re-READ each "
        f"disposition that cites the symbol, decide whether it still "
        f"holds, and only then record the new fingerprint:\n"
        + "\n".join(
            f'    "{sSymbol}": "{sActual}",'
            for sSymbol, sActual in sorted(dictMoved.items())
        )
    )


def testEverySupportingSymbolIsFingerprinted():
    """The two records cannot drift apart.

    A symbol named by a ruling but absent from the fingerprint table
    would be cited support that nothing watches -- the failure mode this
    whole file exists to close, reintroduced through bookkeeping.
    """
    setNamed = {
        sSymbol
        for dictEntry in DICT_BLIND_SPOT_DISPOSITIONS.values()
        for sSymbol in dictEntry["listSupportingSymbols"]
    }
    assert setNamed == set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS), (
        f"named but unwatched: "
        f"{sorted(setNamed - set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS))}; "
        f"watched but cited by nobody: "
        f"{sorted(set(DICT_SUPPORTING_SYMBOL_FINGERPRINTS) - setNamed)}"
    )
