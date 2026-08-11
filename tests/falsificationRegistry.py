"""Machine-applicable record of the mutation each falsification test kills.

A falsification test (pytest mark ``falsification``; see AGENTS.md
"Epistemics") is kill-confirmed: it was proven to FAIL when a specific
source mutation is applied. This registry stores that mutation in an
*applicable* form -- (source file, exact ``old`` text -> ``new`` text) --
so the kill can be RE-confirmed automatically as the code evolves, via
``tools/reconfirmFalsification.py``. A count of falsification tests means
little; "every one still kills its mutant" is the guarantee, and this
registry plus that harness is how it is kept honest.

INDEPENDENT-ORACLE RULE (load-bearing -- do not weaken): kill-confirmation
proves a test is SENSITIVE to change, NOT that its asserted value is
CORRECT. A falsification test is trustworthy only when its expected value
is derived INDEPENDENTLY of the code under test (a specification, an
analytic result, a conservation law, a published benchmark) AND it is
kill-confirmed; neither condition alone suffices. The danger zone is a
test written against freshly-authored, unverified code, whose oracle then
freezes the bug. (Mathews & Nagappan 2024; Konstantinou et al. 2024 --
see the vaibify-falsification-notes synthesis.)

Each entry:
- ``nodeid``: the pytest node id of the falsification test.
- ``source``: the source file the mutation is applied to.
- ``old``: the EXACT text to replace; must occur in ``source`` exactly
  ``iExpectedOccurrences`` times.
- ``new``: the replacement (``old != new``); realizes the break the test
  is meant to catch.
- ``iExpectedOccurrences``: how many copies of ``old`` the mutation must
  replace, defaulting to one.

DEFENCE IN DEPTH NEEDS MORE THAN ONE COPY MUTATED. Some guards exist
deliberately twice -- the ownership-transfer checks run once before
anything is minted and again at the synchronous commit point. Disabling
either copy alone changes NOTHING a caller can observe, because the other
still refuses, so a one-copy mutation reports SURVIVED and reads as an
undefended guard. Three did, for a week. The honest mutation is "this
guard is gone", which means every copy; the count is stated so that a
third copy appearing makes the entry fail loudly instead of silently
leaving one live.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Falsification:
    """One falsification test and the source mutation it is proven to kill."""

    nodeid: str
    source: str
    old: str
    new: str
    iExpectedOccurrences: int = 1


# Each entry below is confirmed by tools/reconfirmFalsification.py to
# actually kill its falsification test.
LIST_FALSIFICATIONS = [

    Falsification(
        nodeid=(
            'tests/testStyleInvariants.py::'
            'testScannerCatchesFnReturningValue'
        ),
        source='tools/generateStyleInventory.py',
        old=(
            '            if listValueReturns:\n'
            '                self.fnRecord(sIdentity, S_CLASS_FN_RETURN,\n'
        ),
        new=(
            '            if False:\n'
            '                self.fnRecord(sIdentity, S_CLASS_FN_RETURN,\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testStyleInvariants.py::'
            'testScannerCatchesLiteralReturnMismatch'
        ),
        source='tools/generateStyleInventory.py',
        old=(
            '    if sLiteralType == "NoneType":\n'
            '        return True\n'
        ),
        new=(
            '    if sLiteralType == "NoneType" or True:\n'
            '        return True\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testStyleInvariants.py::'
            'testScannerCatchesReturnAnnotationMismatch'
        ),
        source='tools/generateStyleInventory.py',
        old=(
            '        if not all(fbAnnotationPartAgrees(sPrefix, tPart)\n'
            '                   for tPart in listParts):\n'
            '            self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,\n'
        ),
        new=(
            '        if False and not all(fbAnnotationPartAgrees(sPrefix, tPart)\n'
            '                   for tPart in listParts):\n'
            '            self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testStyleInvariants.py::'
            'testScannerCatchesBarePrefixlessFunction'
        ),
        source='tools/generateStyleInventory.py',
        old=(
            '        else:\n'
            '            self.fnRecord(sIdentity, S_CLASS_NAME,\n'
            '                          "no valid prefix in the closed vocabulary")\n'
        ),
        new=(
            '        else:\n'
            '            pass\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testStyleInvariants.py::'
            'testScannerCatchesMisprefixedContextManager'
        ),
        source='tools/generateStyleInventory.py',
        old=(
            '        if bContextDecorated and sPrefix != "context":\n'
            '            self.fnRecord(sIdentity, S_CLASS_YIELD,\n'
        ),
        new=(
            '        if False:\n'
            '            self.fnRecord(sIdentity, S_CLASS_YIELD,\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testEntrypointAdvertisedPaths.py::'
            'testTheGuideDoesNotAdvertiseTheWithdrawnDirector'
        ),
        source='vaibify/containerImage/entrypoint.sh',
        old='- `/workspace/.vaibify/logs/` — Pipeline execution logs\n',
        new=(
            '- `/workspace/.vaibify/logs/` — Pipeline execution logs\n'
            '- `/workspace/.vaibify/director.py` — Standalone pipeline '
            'executor\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testEntrypointAdvertisedPaths.py::'
            'testEveryStagedModuleImportsAsAFlatName'
        ),
        # levelGates.py really does carry package-relative imports, so
        # staging it reproduces the director defect exactly: a module
        # installed at /usr/share/vaibify/ that raises ImportError the
        # first time anything runs it.
        source='vaibify/cli/commandBuild.py',
        old=(
            '    "overleafSync.py", "latexConnector.py", '
            '"zenodoClient.py",\n'
        ),
        new=(
            '    "overleafSync.py", "latexConnector.py", '
            '"zenodoClient.py",\n    "levelGates.py",\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testEntrypointAdvertisedPaths.py::'
            'testEveryStagedModuleActuallyImports'
        ),
        source='vaibify/cli/commandBuild.py',
        old='    "credentialRedactor.py",\n',
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_a_child_that_vanishes_mid_scan_refuses'
        ),
        source='vaibify/config/bindMountValidator.py',
        old='            _fnAssertChildIsNotSocket(entryChild, sResolved)\n',
        new='''            if _fbIsUnixSocket(entryChild.path):
                raise BindMountValidationError(
                    f"bindMounts host path '{sResolved}' contains the "
                    f"Unix socket '{entryChild.path}'"
                )
''',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_an_unreadable_subdirectory_refuses_rather_than_passes'
        ),
        source='vaibify/config/bindMountValidator.py',
        old="""        except OSError as errorScan:
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' contains "
                f"'{sDirectory}', which could not be read "
                f"({errorScan.strerror}), so the tree cannot be shown to "
                f"be free of daemon sockets. "
                + _S_UNREADABLE_REMEDY
            ) from errorScan""",
        new='        except OSError:\n            continue',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_an_unstattable_entry_refuses_rather_than_passes'
        ),
        source='vaibify/config/bindMountValidator.py',
        old="""    except FileNotFoundError:
        return False
    except OSError as errorStat:
        raise BindMountValidationError(
            f"bindMounts host path '{sPath}' could not be inspected "
            f"({errorStat.strerror}), so it cannot be shown not to be a "
            f"daemon socket"
        ) from errorStat""",
        new='    except OSError:\n        return False',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_relocated_docker_config_endpoint_is_rejected'
        ),
        source='vaibify/config/bindMountValidator.py',
        old=(
            '    sConfigured = os.environ.get("DOCKER_CONFIG")\n'
            '    if sConfigured:\n'
            '        return os.path.realpath(os.path.expanduser('
            'sConfigured))\n'
        ),
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_a_directory_containing_a_socket_is_rejected'
        ),
        source='vaibify/config/bindMountValidator.py',
        old='    _fnRejectContainedSocket(sResolved)\n',
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_configured_daemon_endpoint_in_home_is_rejected'
        ),
        source='vaibify/config/bindMountValidator.py',
        old=(
            '    _fnRejectDeniedPrefix(sResolved)\n'
            '    _fnRejectDaemonSocket(sResolved)\n'
        ),
        new='    _fnRejectDeniedPrefix(sResolved)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_docker_host_environment_endpoint_is_rejected'
        ),
        source='vaibify/config/bindMountValidator.py',
        old=(
            '    sFromEnvironment = _fsUnixPathFromEndpoint('
            'os.environ.get("DOCKER_HOST"))\n'
            '    if sFromEnvironment:\n'
            '        listEndpoints.append(sFromEnvironment)\n'
        ),
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testBindMountValidator.py::'
            'test_any_unix_socket_is_rejected_even_when_unconfigured'
        ),
        source='vaibify/config/bindMountValidator.py',
        old="""    if _fbIsUnixSocket(sResolved):
        raise BindMountValidationError(
            f"bindMounts host path '{sResolved}' is a Unix socket; "
            f"sockets are never mounted into a workflow container"
        )""",
        new='    return',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testANestedFunctionsLocalCannotAnswerForItsParent'
        ),
        source='tools/generateMutationInventory.py',
        old="""    listOwn = []
    listNested = []
    listPending = list(ast.iter_child_nodes(nodeScope))
    while listPending:
        nodeChild = listPending.pop()
        if isinstance(nodeChild, _T_SCOPE_BOUNDARY_NODES):
            listNested.append(nodeChild)
            continue
        listOwn.append(nodeChild)
        listPending.extend(ast.iter_child_nodes(nodeChild))
    return (listOwn, listNested)""",
        new="""    listOwn = []
    listNested = []
    for nodeChild in ast.walk(nodeScope):
        if nodeChild is nodeScope:
            continue
        if isinstance(nodeChild, _T_SCOPE_BOUNDARY_NODES):
            listNested.append(nodeChild)
            continue
        listOwn.append(nodeChild)
    return (listOwn, listNested)""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testASiblingScopesClientNameDoesNotEscapeIt'
        ),
        source='tools/generateMutationInventory.py',
        # The same mutation as the test above, and deliberately so: one
        # defect, two symptoms. Losing scope pruning breaks the command
        # lists AND the client origins, because both now read the same
        # model. Two entries record that both are actually observed to
        # die, rather than assuming the second from the first.
        old="""    listOwn = []
    listNested = []
    listPending = list(ast.iter_child_nodes(nodeScope))
    while listPending:
        nodeChild = listPending.pop()
        if isinstance(nodeChild, _T_SCOPE_BOUNDARY_NODES):
            listNested.append(nodeChild)
            continue
        listOwn.append(nodeChild)
        listPending.extend(ast.iter_child_nodes(nodeChild))
    return (listOwn, listNested)""",
        new="""    listOwn = []
    listNested = []
    for nodeChild in ast.walk(nodeScope):
        if nodeChild is nodeScope:
            continue
        if isinstance(nodeChild, _T_SCOPE_BOUNDARY_NODES):
            listNested.append(nodeChild)
            continue
        listOwn.append(nodeChild)
    return (listOwn, listNested)""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAParameterShadowsAModuleLevelClient'
        ),
        source='tools/generateMutationInventory.py',
        old=(
            '    setClientNames = dictInherited["setClientNames"] '
            '- setRebound'
        ),
        new='    setClientNames = set(dictInherited["setClientNames"])',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testADuplicatedBlindSpotEntryIsCaught'
        ),
        source='tools/generateMutationInventory.py',
        old="""    ] + [
        f"duplicated {sKey}"
        for sKey in _flistDuplicatedBlindSpotKeys(listRecordedSites)
    ]
    if len(listRecordedSites) != len(dictScanned):
        listDrift.append(
            f"recorded list holds {len(listRecordedSites)} sites, "
            f"scan found {len(dictScanned)}"
        )""",
        new="""    ]""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testABlindSpotDispositionDiesWithItsCommandBuilder'
        ),
        source='tools/generateMutationInventory.py',
        old="""            "sScopeFingerprint": _fsFingerprintNode(
                nodeScope if nodeScope is not None else nodeCall,
                self.tupleSourceLines,
            ),""",
        new="""            "sScopeFingerprint": _fsFingerprintNode(
                nodeCall,
                self.tupleSourceLines,
            ),""",
    ),

    # --- The capability record (plan rule R3) -------------------------
    #
    # Two of these mutate a vaibify module rather than the scanner, and
    # deliberately: the claim is that a capability arriving in a module
    # nobody was watching is caught. Planting it in a module the record
    # already lists as dangerous would be caught by that module's
    # existing entries while the real hole stayed open, so the fixture
    # is pipelineUtils.py -- a leaf holding no capability of any kind.

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testTheAcquisitionRecordMatchesAFreshScanOfTheSource'
        ),
        source='vaibify/gui/pipelineUtils.py',
        old='from datetime import datetime, timezone\n',
        new=(
            'from datetime import datetime, timezone\n\n'
            '_S_MEMBER_NAME = "sep"\n'
            '_S_SEPARATOR = getattr(posixpath, _S_MEMBER_NAME)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testEveryAcquisitionCarriesADisposition'
        ),
        source='vaibify/gui/pipelineUtils.py',
        old='import posixpath\nimport re\nimport time\n',
        new='import posixpath\nimport re\nimport subprocess\nimport time\n',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnUnreadableCommandIsARowAndNotOnlyABlindSpot'
        ),
        source='tools/generateMutationInventory.py',
        old="""            self.listRows.append(self._fdictBuildRow(
                nodeCall, S_PRIMITIVE_UNKNOWN_COMMAND,
                S_ACCESS_UNKNOWN_COMMAND, S_REFERENCE_UNKNOWN_COMMAND,
            ))
            self.listUnresolvedSubprocessSites.append(""",
        new="""            self.listUnresolvedSubprocessSites.append(""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnImportedProcessCapabilityIsRecordedWhateverIsDoneWithIt'
        ),
        source='tools/generateMutationInventory.py',
        old="""            if sCapability is not None:
                self._fnRecordAcquisition(
                    nodeImport, nodeAlias.name.split(".")[0], sCapability,
                    S_ACQUISITION_IMPORT,
                )""",
        new="""            del sCapability""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnAcquisitionInsideAClassOrANestedFunctionIsRecorded'
        ),
        source='tools/generateMutationInventory.py',
        old="""        nodeScope = (
            self._listScopeStack[-1] if self._listScopeStack else None
        )
        dictAcquisition = {""",
        new="""        if self._listScopeStack:
            return
        nodeScope = None
        dictAcquisition = {""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testReflectionThroughSysModulesIsAnAcquisition'
        ),
        source='tools/generateMutationInventory.py',
        old='    ("sys", "modules"): S_CAPABILITY_REFLECTION,\n',
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::testEvalAndExecAreAcquisitions'
        ),
        source='tools/generateMutationInventory.py',
        old='SET_REFLECTION_BUILTINS = frozenset({"eval", "exec", "__import__"})',
        new='SET_REFLECTION_BUILTINS = frozenset({"__import__"})',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testADynamicAttributeLookupIsAnAcquisition'
        ),
        source='tools/generateMutationInventory.py',
        old="""    return len(nodeCall.args) >= 2 and not isinstance(
        nodeCall.args[1], ast.Constant,
    )""",
        new="""    return False""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAMemberOfAnOrdinaryModuleIsAcquiredWhenItIsNamed'
        ),
        source='tools/generateMutationInventory.py',
        old="""        sCapability = _fsCapabilityForMember(sModule, sMember)""",
        new="""        sCapability = None""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnAliasedModuleDoesNotHideItsDangerousMember'
        ),
        source='tools/generateMutationInventory.py',
        old="""        sModule = _fsResolveModuleAlias(
            sModule, self._fdictBindingsHere()["dictModuleAliases"],
        )
""",
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAFromImportOfALauncherIsAcquiredAndItsAliasResolves'
        ),
        source='tools/generateMutationInventory.py',
        old="""        if isinstance(nodeChild, ast.ImportFrom) and not nodeChild.level:
            if (nodeChild.module or "") == "subprocess":
                for nodeAlias in nodeChild.names:
                    if nodeAlias.name in SET_SUBPROCESS_LAUNCHERS:
                        setNames.add(nodeAlias.asname or nodeAlias.name)
            continue""",
        new="""        if isinstance(nodeChild, ast.ImportFrom):
            continue""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testALauncherBoundToALocalNameStillResolves'
        ),
        source='tools/generateMutationInventory.py',
        old="""        listChain = _flistAttributeChain(nodeChild.value)
        if len(listChain) < 2 or listChain[0] not in setProcessModuleNames:""",
        new="""        listChain = []
        if len(listChain) < 2 or listChain[0] not in setProcessModuleNames:""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnUnrelatedRunMethodIsNotAProcessLaunch'
        ),
        source='tools/generateMutationInventory.py',
        old="""        listChain = _flistAttributeChain(nodeFunction)
        return bool(listChain) and listChain[0] in (
            dictBindings["setProcessModuleNames"]
        )""",
        new="""        return True""",
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAnUnrelatedFromEnvIsNotADockerClient'
        ),
        source='tools/generateMutationInventory.py',
        old='    return listChain[0] in setDockerModuleNames\n',
        new='    return True\n',
    ),

    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testAChainedDockerConstructorIsStillTheDockerSdk'
        ),
        source='tools/generateMutationInventory.py',
        old="""    return listChain[0] in setDockerModuleNames and any(
        sPart in _TUPLE_DOCKER_CLIENT_CONSTRUCTORS
        for sPart in listChain[1:]
    )""",
        new="""    return False""",
    ),

    Falsification(
        nodeid='tests/testMutationBoundary.py::testAnUnadmittedExecIsRefusedBeforeItRuns',
        source='vaibify/docker/dockerConnection.py',
        old="""        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunInContainerStreamed",
        )
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = self._fdictBuildExecKwargs(
            sCommand, sWorkdir, sUser)""",
        new="""        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = self._fdictBuildExecKwargs(
            sCommand, sWorkdir, sUser)""",
    ),

    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testATransferBeforeTheLaunchRefusesAndChangesNothing',
        source='vaibify/gui/sessionLifecycle.py',
        old="""        return (
            f"Container '{sName}' has an unsettled journal record "
            f"({sOperationId}, kind {dictRecord['sKind']}) that is "
            "neither the live durable task nor a drainable terminal; """,
        new="""        continue
        return (
            f"Container '{sName}' has an unsettled journal record "
            f"({sOperationId}, kind {dictRecord['sKind']}) that is "
            "neither the live durable task nor a drainable terminal; """,
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAFailedStartAfterATransferDoesNotFreeTheSuccessor',
        source='vaibify/gui/sessionLifecycle.py',
        old="""    return (
        identityOwnership.bEstablishedTheOwnership
        and containerOwnership.fbOwnershipIdentityStillHolds(
            recordOwner, identityOwnership,
        )
    )""",
        new="""    return identityOwnership.bEstablishedTheOwnership""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testTheStartTakesTheCardinalityLockNotJustTheIndex',
        source='vaibify/gui/sessionLifecycle.py',
        old="""    async with _flockObtainContainerMutation(dictLockStore, sName):
        async with _flockObtainSessionCardinality(dictLockStore):
            return _ftReserveForStartUnderLocks(""",
        new="""    async with _flockObtainContainerMutation(dictLockStore, sName):
        if True:
            return _ftReserveForStartUnderLocks(""",
    ),

    Falsification(
        nodeid='tests/testReconciliation.py::test_break_glass_refuses_when_the_stop_is_not_proven',
        source='vaibify/config/reconciliation.py',
        old="""    try:
        bSettled = fnStopContainerByName(sContainerName)
    except Exception as error:
        raise ReconciliationRefusedError(
            f"The break-glass could not stop container "
            f"'{sContainerName}' ({error}), so the process the malformed "
            "record describes may still be running; the quarantine "
            "stands. Stop or remove the container, then retry."
        )
    if not bSettled:
        raise ReconciliationRefusedError(
            f"The break-glass could not PROVE container "
            f"'{sContainerName}' stopped or absent, so the process the "
            "malformed record describes may still be running; the "
            "quarantine stands. Stop or remove the container, then retry."
        )""",
        new="""    try:
        fnStopContainerByName(sContainerName)
    except Exception as error:
        logger.warning(
            "Break-glass container stop for '%s' reported: %s",
            sContainerName, error,
        )""",
    ),

    Falsification(
        nodeid='tests/testArchitecturalInvariants.py::testScienceTermScanMatchesSeparatedSpellings',
        source='tests/testArchitecturalInvariants.py',
        old='    regexTerm = re.compile(\n        r"\\b" + _S_TERM_SEPARATOR_PATTERN.join(\n            re.escape(sCharacter) for sCharacter in sTerm\n        ),\n        re.IGNORECASE,\n    )',
        new='    regexTerm = re.compile(r"\\b" + re.escape(sTerm), re.IGNORECASE)',
    ),

    Falsification(
        nodeid='tests/testContainerOwnership.py::test_agent_token_with_empty_container_id_fails_closed',
        source='vaibify/gui/containerOwnership.py',
        old='if not sPresentedToken or not sContainerId:',
        new='if not sPresentedToken and not sContainerId:',
    ),
    Falsification(
        nodeid='tests/testContainerOwnership.py::test_same_lease_reclaim_refreshes_grace_clock',
        source='vaibify/gui/containerOwnership.py',
        old="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId and (
        recordOwner.sBrowserSessionId == ""
        or recordOwner.sBrowserSessionId == sBrowserSessionId
    ):
        recordOwner.fLastSeenMonotonic = time.monotonic()
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))""",
        new="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId and (
        recordOwner.sBrowserSessionId == ""
        or recordOwner.sBrowserSessionId == sBrowserSessionId
    ):
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))""",
    ),
    Falsification(
        nodeid='tests/testContainerOwnership.py::test_copied_lease_from_foreign_session_is_refused_without_refresh',
        source='vaibify/gui/containerOwnership.py',
        old="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId and (
        recordOwner.sBrowserSessionId == ""
        or recordOwner.sBrowserSessionId == sBrowserSessionId
    ):""",
        new="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId:""",
    ),
    # The HTTP-boundary counterpart of the copied-lease WebSocket gate:
    # ContainerAwareRoute short-circuits a container-owner request that
    # the strong predicate refuses. Neutralizing the short-circuit lets a
    # non-owning browser session mutate a container it never claimed.
    Falsification(
        nodeid='tests/testSecurityBoundaryInvariants.py::testContainerScopedHttpMutationRequiresOwningLease',
        source='vaibify/gui/routeScope.py',
        old='                if iCode:\n                    return _fresponseRefused(iCode)',
        new='                if False:\n                    return _fresponseRefused(iCode)',
    ),
    # The read half of the same boundary: dropping container-read from the
    # enforced-scope set reverts owned-container GETs to the old
    # declared-but-unenforced state while every mutation test stays green.
    Falsification(
        nodeid='tests/testSecurityBoundaryInvariants.py::testContainerScopedHttpReadRequiresOwningLease',
        source='vaibify/gui/routeScope.py',
        old='_SET_LEASE_ENFORCED_SCOPES = frozenset({\n    S_SCOPE_CONTAINER_OWNER,\n    S_SCOPE_CONTAINER_READ,\n})',
        new='_SET_LEASE_ENFORCED_SCOPES = frozenset({\n    S_SCOPE_CONTAINER_OWNER,\n})',
    ),
    Falsification(
        nodeid='tests/testContainerOwnership.py::test_release_stops_keep_alive',
        source='vaibify/gui/containerOwnership.py',
        old='    fnStopKeepAlive(sName)',
        new='    pass',
    ),
    Falsification(
        nodeid='tests/testContainerOwnership.py::test_fbOwnerIsReapable_is_true_at_exact_grace_boundary',
        source='vaibify/gui/containerOwnership.py',
        old='return fElapsedSeconds >= fGraceSeconds',
        new='return fElapsedSeconds > fGraceSeconds',
    ),
    Falsification(
        nodeid='tests/testWebSocketAuthorization.py::test_agent_lane_served_while_browser_session_live',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    if bBrowser and bExclusivePipelineLane and fbRefuseSecondLiveConnection(
        dictContainerOwners, sName,
    ):""",
        new="""    if bExclusivePipelineLane and fbRefuseSecondLiveConnection(
        dictContainerOwners, sName,
    ):""",
    ),
    Falsification(
        nodeid='tests/testContainerSessionResolution.py::test_terminal_plus_pipeline_ws_coexist_in_one_session',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    return (
        recordOwner is not None
        and recordOwner.iLivePipelineConnectionCount >= 1
    )""",
        new="""    return (
        recordOwner is not None
        and recordOwner.iLiveConnectionCount >= 1
    )""",
    ),
    Falsification(
        nodeid='tests/testPipelineServerTaskEviction.py::test_second_run_while_first_is_live_is_refused_not_started',
        source='vaibify/gui/pipelineServer.py',
        old="""    taskLive = dictPipelineTasks.get(sContainerId)
    return taskLive is not None and not taskLive.done()""",
        new="""    taskLive = dictPipelineTasks.get(sContainerId)
    return False""",
    ),
    Falsification(
        nodeid='tests/testWebSocketAuthorization.py::test_agent_lane_does_not_touch_per_container_counter',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    if bBrowser:
        containerOwnership.fnIncrementLiveConnection(""",
        new="""    if True:
        containerOwnership.fnIncrementLiveConnection(""",
    ),
    Falsification(
        nodeid='tests/testServerMiddlewareCoverage.py::testContainerIdFromPathRecognizesWebSocketPrefix',
        source='vaibify/gui/serverMiddleware.py',
        old="""("api", "ws")""",
        new="""("api",)""",
    ),
    Falsification(
        nodeid='tests/testServerMiddlewareCoverage.py::testContainerIdFromPathStillRecognizesApiPrefix',
        source='vaibify/gui/serverMiddleware.py',
        old="""("api", "ws")""",
        new="""("ws",)""",
    ),
    Falsification(
        nodeid='tests/testServerMiddlewareCoverage.py::testAgentPresentedTokenFallsBackToWebSocketQueryParam',
        source='vaibify/gui/serverMiddleware.py',
        old="""    if request.headers.get("upgrade", "").lower() == "websocket":
        return request.query_params.get("sToken", "")""",
        new='''    if request.headers.get("upgrade", "").lower() == "websocket":
        return ""''',
    ),
    Falsification(
        nodeid='tests/testServerMiddlewareCoverage.py::testAgentPresentedTokenHeaderWinsOverWebSocketQuery',
        source='vaibify/gui/serverMiddleware.py',
        old='if sHeader:',
        new='if not sHeader:',
    ),
    Falsification(
        nodeid='tests/testTruthDerivation.py::testMissingOutputOutranksDriftedOutput',
        source='vaibify/gui/truthDerivation.py',
        old='''    if bAnyMissing:
        return "outputs-missing"
    if bAnyChanged:
        return "outputs-changed"''',
        new='''    if bAnyChanged:
        return "outputs-changed"
    if bAnyMissing:
        return "outputs-missing"  # mutant''',
    ),
    Falsification(
        nodeid='tests/testTruthDerivation.py::testMarkerWithoutExitStatusDefaultsToCleanPass',
        source='vaibify/gui/truthDerivation.py',
        old="""iExitStatus = dictMarker.get("iExitStatus", 0)""",
        new="""iExitStatus = dictMarker.get("iExitStatus", 1)  # mutant""",
    ),
    Falsification(
        nodeid='tests/testTruthDerivation.py::testAggregateAllUnnecessaryAxesStaysUnnecessary',
        source='vaibify/gui/truthDerivation.py',
        old='''    if "passed" in listAxisValues:
        return "passed"
    return "unnecessary"''',
        new='''    if "passed" in listAxisValues:
        return "passed"
    return "passed"''',
    ),
    Falsification(
        nodeid='tests/testTruthDerivation.py::testChangedOutputsAreReportedInStableSortedOrder',
        source='vaibify/gui/truthDerivation.py',
        old='    return sorted(listResult)',
        new='    return listResult',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fnInvalidateStepFiles_demotes_passed_states_on_data_change',
        source='vaibify/gui/fileStatusManager.py',
        old="""_SET_PASSED_TEST_STATES = frozenset({"passed", "passed-from-marker"})""",
        new="""_SET_PASSED_TEST_STATES = frozenset({"passed"})""",
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fnInvalidateDownstreamStep_demotes_passed_states',
        source='vaibify/gui/fileStatusManager.py',
        old="""_SET_PASSED_TEST_STATES = frozenset({"passed", "passed-from-marker"})""",
        new="""_SET_PASSED_TEST_STATES = frozenset({"passed"})""",
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fnInvalidateStepFiles_plot_standards_goes_stale_on_plot_change',
        source='vaibify/gui/fileStatusManager.py',
        old='''dictVerification["sPlotStandards"] = "stale"''',
        new='''dictVerification["sPlotStandards"] = "passed"''',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fnInvalidateStepFiles_plot_standards_kept_on_non_plot_change',
        source='vaibify/gui/fileStatusManager.py',
        old='if _fbAnyPlotFileChanged(',
        new='if True or _fbAnyPlotFileChanged(',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fdictDetectChangedFiles_suppressed_while_running',
        source='vaibify/gui/fileStatusManager.py',
        old='if bPipelineRunning:',
        new='if False and bPipelineRunning:',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fdictDetectChangedFiles_detects_change_when_not_running',
        source='vaibify/gui/fileStatusManager.py',
        old='if bPipelineRunning:',
        new='if not bPipelineRunning:',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fbReconcileUpstreamFlags_clears_flag_when_downstream_fresh',
        source='vaibify/gui/fileStatusManager.py',
        old='elif iSignal == 0 and bHasFlag:',
        new='elif iSignal == 2 and bHasFlag:  # mutant',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fbReconcileUpstreamFlags_sets_flag_when_downstream_stale',
        source='vaibify/gui/fileStatusManager.py',
        old='if iSignal == 1 and not bHasFlag:',
        new='if iSignal == 2 and not bHasFlag:  # mutant',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fiMtimeStalenessSignal_equal_mtimes_is_fresh',
        source='vaibify/gui/fileStatusManager.py',
        old='iUpMtime > iMyMtime',
        new='iUpMtime >= iMyMtime',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fiMtimeStalenessSignal_older_downstream_is_stale',
        source='vaibify/gui/fileStatusManager.py',
        old='iUpMtime > iMyMtime',
        new='iUpMtime > iMyMtime + 1000',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_flistNewerPaths_excludes_equal_boundary',
        source='vaibify/gui/fileStatusManager.py',
        old='if iMtime > iThreshold:',
        new='if iMtime >= iThreshold:',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_flistNewerPaths_includes_strictly_newer',
        source='vaibify/gui/fileStatusManager.py',
        old='if iMtime > iThreshold:',
        new='if iMtime < iThreshold:  # mutant',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fbAnyMtimeNewerThan_excludes_equal_boundary',
        source='vaibify/gui/fileStatusManager.py',
        old='int(sMtime) > iThreshold',
        new='int(sMtime) >= iThreshold',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fbAnyMtimeNewerThan_includes_strictly_newer',
        source='vaibify/gui/fileStatusManager.py',
        old='int(sMtime) > iThreshold',
        new='int(sMtime) > iThreshold + 1000',
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fbReconcileUserVerificationTimestamps_retains_stale',
        source='vaibify/gui/fileStatusManager.py',
        # Anchored on the dictVerify accessor so it stays unique: the
        # cross-machine hash pass added a second `in ("passed",
        # "stale")` test elsewhere in this module.
        old="""dictVerify.get("sUser") in ("passed", "stale"):""",
        new="""dictVerify.get("sUser") in ("passed",):""",
    ),
    Falsification(
        nodeid='tests/testPathValidation.py::testRejectsRootEmbeddedAsInteriorSubstring',
        source='vaibify/gui/pipelineServer.py',
        old="""if not sNormalized.startswith(sRoot + "/") and sNormalized != sRoot:""",
        new="""if (sRoot + "/") not in sNormalized and sNormalized != sRoot:""",
    ),
    Falsification(
        nodeid='tests/testPathValidation.py::testNormalizesTrailingSlashRoot',
        source='vaibify/gui/pipelineServer.py',
        old='sRoot = posixpath.normpath(sAllowedRoot)',
        new='sRoot = sAllowedRoot',
    ),
    Falsification(
        nodeid='tests/testPathValidation.py::testNormalizesDotBearingRoot',
        source='vaibify/gui/pipelineServer.py',
        old='sRoot = posixpath.normpath(sAllowedRoot)',
        new='sRoot = sAllowedRoot',
    ),
    Falsification(
        nodeid='tests/testPathValidation.py::testReturnsNormalizedPathNotRawInput',
        source='vaibify/gui/pipelineServer.py',
        old="""            403, "Path traversal is not permitted"
        )
    return sNormalized""",
        new="""            403, "Path traversal is not permitted"
        )
    return sResolvedPath""",
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_blas_waiver_requires_literal_true',
        source='vaibify/reproducibility/determinismGate.py',
        old='if dictDeterminism.get(S_ACCEPT_BLAS_WAIVER_KEY) is True:',
        new='if dictDeterminism.get(S_ACCEPT_BLAS_WAIVER_KEY):',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_mkl_cbwr_alone_declares_determinism',
        source='vaibify/reproducibility/determinismGate.py',
        old='if dictDeterminism.get(S_MKL_CBWR_KEY):',
        new='if False and dictDeterminism.get(S_MKL_CBWR_KEY):',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_bare_imported_seed_with_clock_is_flagged',
        source='vaibify/reproducibility/determinismGate.py',
        old="""        return nodeFn.id.lower().endswith("seed")""",
        new='        return False',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_bare_os_urandom_outside_seed_is_flagged',
        source='vaibify/reproducibility/determinismGate.py',
        old='if _REGEX_OS_URANDOM.search(sLine):',
        new='if False and _REGEX_OS_URANDOM.search(sLine):',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_missing_determinism_block_is_an_issue',
        source='vaibify/reproducibility/determinismGate.py',
        old='if not fbWorkflowDeclaresDeterminism(dictWorkflow):',
        new='if False and not fbWorkflowDeclaresDeterminism(dictWorkflow):',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_from_secrets_import_is_flagged',
        source='vaibify/reproducibility/determinismGate.py',
        old='r"\\b(?:import\\s+secrets\\b|from\\s+secrets\\s+import\\b|secrets\\.[A-Za-z_])"',
        new='r"\\b(?:import\\s+secrets\\b|secrets\\.[A-Za-z_])"',
    ),
    Falsification(
        nodeid='tests/testDeterminismGate.py::test_unseeded_warning_requires_literal_true',
        source='vaibify/reproducibility/determinismGate.py',
        old="""if dictStep.get("bUnseededRandomnessWarning") is True:""",
        new="""if dictStep.get("bUnseededRandomnessWarning"):""",
    ),
    Falsification(
        nodeid='tests/testConftestManagerCoverage.py::test_buildCategoryResults_tallies_pass_and_fail_to_correct_keys',
        source='vaibify/gui/conftestManager.py',
        old="""        if item.rep_call.passed:
            dictCat["iPassed"] += 1
        elif item.rep_call.failed:
            dictCat["iFailed"] += 1""",
        new="""        if item.rep_call.passed:
            dictCat["iFailed"] += 1
        elif item.rep_call.failed:
            dictCat["iPassed"] += 1  # mutant""",
    ),
    Falsification(
        nodeid='tests/testConftestManagerCoverage.py::test_sessionfinish_marker_filename_uses_underscore_for_nested_dir',
        source='vaibify/gui/conftestManager.py',
        old='''sFilename = sStepDirRel.replace("/", "_") + ".json"''',
        new='''sFilename = sStepDirRel.replace("/", "-") + ".json"  # mutant''',
    ),
    Falsification(
        nodeid='tests/testConftestManagerCoverage.py::test_activeWorkflowSlug_falls_back_to_default_when_nothing_present',
        source='vaibify/gui/conftestManager.py',
        old='''        return pathJson.stem
    return "default"''',
        new='''        return pathJson.stem
    return ""''',
    ),
    Falsification(
        nodeid='tests/testConftestManagerCoverage.py::test_pathsWithinRoot_rejects_sibling_with_shared_name_prefix',
        source='vaibify/gui/conftestManager.py',
        old="""if sNorm == sNormRoot or sNorm.startswith(sNormRoot + "/"):""",
        new='if sNorm.startswith(sNormRoot):',
    ),
    Falsification(
        nodeid='tests/testConftestManagerCoverage.py::test_pathsWithinRoot_keeps_in_root_path',
        source='vaibify/gui/conftestManager.py',
        old="""if sNorm == sNormRoot or sNorm.startswith(sNormRoot + "/"):""",
        new="""if sNorm == sNormRoot and sNorm.startswith(sNormRoot + "/"):""",
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_fiExecuteAndRecord_failed_step_emits_stepFail_not_stepPass',
        source='vaibify/gui/pipelineRunner.py',
        old='await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)',
        new='await _fnEmitStepResult(fnStatusCallback, iStepNumber, 0)',
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_fiExecuteAndRecord_returns_real_exit_code',
        source='vaibify/gui/pipelineRunner.py',
        old="""    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode""",
        new="""    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return 0""",
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_ftRunStepCommands_full_returns_plot_exit_code',
        source='vaibify/gui/pipelineRunner.py',
        old='return (iPlotExit, fCpuTime + fPlotCpu)',
        new='return (iExitCode, fCpuTime + fPlotCpu)  # mutant',
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_fnVerifyOnly_missing_output_emits_stepFail_badge',
        source='vaibify/gui/pipelineRunner.py',
        old='fnStatusCallback, iIndex + 1, 0 if bStepOk else 1',
        new='fnStatusCallback, iIndex + 1, 0',
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_appendAndMaybeDrainBatch_flushes_at_exactly_fifty',
        source='vaibify/gui/pipelineRunner.py',
        old="""len(dictBatch["listLines"]) >= I_BATCH_MAX_LINES""",
        new="""len(dictBatch["listLines"]) > I_BATCH_MAX_LINES""",
    ),
    Falsification(
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_fsetSnapshotDirectory_empty_on_partial_with_error',
        source='vaibify/gui/pipelineRunner.py',
        old='if iExit != 0 or not sOutput.strip():',
        new='if iExit != 0 and not sOutput.strip():',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestKillRouteAuthGate::test_unauthorized_kill_rejected_before_count_exec',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""    async def fdictHandleKillRunningTasks(sContainerId: str, requestHttp: Request):
        dictCtx["require"](sContainerId)""",
        new=(
            '    async def fdictHandleKillRunningTasks('
            'sContainerId: str, requestHttp: Request):'
        ),
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestKillRouteActuallyKills::test_kill_exec_issued_when_count_positive',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='if iCountBefore > 0:',
        new='if False:',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestKillRouteActuallyKills::test_no_kill_exec_when_count_zero',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='if iCountBefore > 0:',
        new='if True:',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestPipelineWsRejectBeforeServe::test_rejected_session_closed_not_served',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='if iRejectCode:',
        new='if not iRejectCode:',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestPipelineWsRejectBeforeServe::test_authorized_session_served_not_closed',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='if iRejectCode:',
        new='if not iRejectCode:',
    ),
    Falsification(
        # The ETag stamp is derived from the whole response payload
        # (2026-07-25), so each signal is excluded by naming its key
        # in _SET_ETAG_VOLATILE_KEYS rather than by deleting a hand-
        # maintained list entry. Same break, expressed against the
        # mechanism that replaced the list.
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_max_mtime_by_step_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""_SET_ETAG_VOLATILE_KEYS = frozenset()""",
        new="""_SET_ETAG_VOLATILE_KEYS = frozenset({"dictMaxMtimeByStep"})""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_proof_level_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""_SET_ETAG_VOLATILE_KEYS = frozenset()""",
        new="""_SET_ETAG_VOLATILE_KEYS = frozenset({"iProofLevel"})""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_l2_blocker_count_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""_SET_ETAG_VOLATILE_KEYS = frozenset()""",
        new="""_SET_ETAG_VOLATILE_KEYS = frozenset({"iL2BlockerCount"})""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_l3_blocker_count_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""_SET_ETAG_VOLATILE_KEYS = frozenset()""",
        new="""_SET_ETAG_VOLATILE_KEYS = frozenset({"iL3BlockerCount"})""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestSplitCachedAndChanged::test_stale_mtime_forces_rehash',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""            and dictEntry.get("iMtime") == iMtime""",
        new='            and True',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestSplitCachedAndChanged::test_matching_mtime_reuses_cache',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='if bCacheValid:',
        new='if False:',
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestUpdateShaCacheSingleFieldChange::test_mtime_only_change_signals_persistence',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""                or dictExisting.get("iMtime") != iMtime""",
        new="""                and dictExisting.get("iMtime") != iMtime""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestUpdateShaCacheSingleFieldChange::test_sha_only_change_signals_persistence',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""                or dictExisting.get("iMtime") != iMtime""",
        new="""                and dictExisting.get("iMtime") != iMtime""",
    ),
    Falsification(
        nodeid='tests/testSyncDispatcherMutationCoverage.py::test_digest_script_matches_git_blob_sha',
        source='vaibify/gui/syncDispatcher.py',
        old="""b'blob '""",
        new="""b'xblob '""",
    ),
    Falsification(
        nodeid='tests/testSyncDispatcherMutationCoverage.py::test_marker_script_extracts_hex_template_hash',
        source='vaibify/gui/syncDispatcher.py',
        old='([0-9a-f]+)',
        new='([0-9]+)',
    ),
    Falsification(
        nodeid='tests/testSyncDispatcherMutationCoverage.py::test_compute_container_digests_path_with_space',
        source='vaibify/gui/syncDispatcher.py',
        old="""iSpace = sStripped.find(" ")""",
        new="""iSpace = sStripped.rfind(" ")""",
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_extractArrayValue_default_index_is_last_element',
        source='vaibify/gui/dataLoaders.py',
        old="""    listIndices = dictAccess.get("listIndices", [-1])
    if len(listIndices) == 1 and daData.ndim > 1:
        return float(daData.flat[listIndices[0]])""",
        new="""    listIndices = dictAccess.get("listIndices", [0])
    if len(listIndices) == 1 and daData.ndim > 1:
        return float(daData.flat[listIndices[0]])""",
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_splitHeaderAndData_mixed_first_line_treated_as_header',
        source='vaibify/gui/dataLoaders.py',
        old='bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)',
        new='bAllNumeric = any(_fbIsNumericToken(s) for s in listTokens)  # mutant',
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_loadCsvNegativeRow_index_minus_two_is_second_to_last',
        source='vaibify/gui/dataLoaders.py',
        old='    return float(dequeTail[0][iCol])',
        new='    return float(dequeTail[-1][iCol])',
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_loadCsvByRowIndex_index_zero_returns_first_row',
        source='vaibify/gui/dataLoaders.py',
        old="""    if iIndex < 0:
        return _ffLoadCsvNegativeRow(sFullPath, sColumn, iIndex)""",
        new="""    if iIndex <= 0:
        return _ffLoadCsvNegativeRow(sFullPath, sColumn, iIndex)""",
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_extractHdf5Value_negative_flat_index_maps_to_last',
        source='vaibify/gui/dataLoaders.py',
        old='            iFlat += int(np.prod(tShape))',
        new='            iFlat -= int(np.prod(tShape))  # mutant',
    ),
    Falsification(
        nodeid='tests/testDataLoadersMutationCoverage.py::test_loadFitsValue_two_component_index_selects_second',
        source='vaibify/gui/dataLoaders.py',
        old='    iDataIdx = listIndices[1] if len(listIndices) > 1 else 0',
        new='    iDataIdx = listIndices[1] if len(listIndices) > 2 else 0  # mutant',
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_output_entry_resolving_to_repo_parent_is_rejected',
        source='vaibify/gui/workflowManager.py',
        old="""if sJoined == ".." or sJoined.startswith("../"):""",
        new="""if sJoined.startswith("../"):""",
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_step_directory_equal_to_repo_parent_is_rejected',
        source='vaibify/gui/workflowManager.py',
        old="""    sNorm = posixpath.normpath(sDirectory)
    if sNorm == ".." or sNorm.startswith("../"):""",
        new="""    sNorm = posixpath.normpath(sDirectory)
    if sNorm.startswith("../"):""",
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_plot_directory_equal_to_repo_parent_is_rejected',
        source='vaibify/gui/workflowManager.py',
        old="""    sNorm = posixpath.normpath(sPlotDirectory)
    if sNorm == ".." or sNorm.startswith("../"):""",
        new="""    sNorm = posixpath.normpath(sPlotDirectory)
    if sNorm.startswith("../"):""",
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_dataset_destination_equal_to_repo_parent_is_rejected',
        source='vaibify/gui/workflowManager.py',
        old="""    sNorm = posixpath.normpath(sDestination)
    if sNorm == ".." or sNorm.startswith("../"):""",
        new="""    sNorm = posixpath.normpath(sDestination)
    if sNorm.startswith("../"):""",
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_dep_cache_key_tracks_sadependencies_edits',
        source='vaibify/gui/workflowManager.py',
        old="""        dictRelevant["saDependencies"] = sorted(
            [str(s) for s in listDeps if s is not None],
        )""",
        new="""        dictRelevant["saDependencies"] = []""",
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_self_referencing_step_flagged_as_circular',
        source='vaibify/gui/workflowManager.py',
        old='    if iRefNumber >= iNumber:',
        new='    if iRefNumber > iNumber:',
    ),
    Falsification(
        nodeid='tests/testWorkflowManagerMutationCoverage.py::test_reference_to_last_step_is_circular_not_beyond',
        source='vaibify/gui/workflowManager.py',
        old='    if iRefNumber > iStepCount:',
        new='    if iRefNumber >= iStepCount:',
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_github_full_count_with_nonempty_diverged_is_not_synced',
        source='vaibify/reproducibility/levelGates.py',
        old="""    if dictStatus.get("listDiverged"):
        return False""",
        new="""    if False and dictStatus.get("listDiverged"):
        return False""",
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_github_undercount_with_empty_diverged_is_not_synced',
        source='vaibify/reproducibility/levelGates.py',
        old="""    if dictStatus.get("iMatching") != iTotal:
        return False""",
        new="""    if False and dictStatus.get("iMatching") != iTotal:
        return False""",
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_github_verified_sha_empty_but_live_sha_present_is_not_synced',
        source='vaibify/reproducibility/levelGates.py',
        old='    if not sVerifiedSha and not sLiveSha:',
        new='    if not sVerifiedSha or not sLiveSha:',
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_github_verified_sha_present_but_live_sha_empty_is_not_synced',
        source='vaibify/reproducibility/levelGates.py',
        old='    if not sVerifiedSha and not sLiveSha:',
        new='    if not sVerifiedSha or not sLiveSha:',
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_github_full_match_without_timestamp_is_not_synced',
        source='vaibify/reproducibility/levelGates.py',
        old="""    sLastVerified = (dictStatus or {}).get("sLastVerified")
    if not sLastVerified:
        return False""",
        new="""    sLastVerified = (dictStatus or {}).get("sLastVerified")
    if False and not sLastVerified:
        return False""",
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_fdictLevel2Gaps_subset_failure_keeps_level2_false',
        source='vaibify/reproducibility/levelGates.py',
        old='            bL1 and bGithub and bZenodo and bArxiv and bDecl\n            and bModels and bPersonal,',
        new='            bL1 or bGithub and bZenodo and bArxiv and bDecl\n            and bModels and bPersonal,',
    ),
    Falsification(
        nodeid='tests/testLevelGatesMutationCoverage.py::test_blocker_cache_evicts_oldest_entry_first',
        source='vaibify/reproducibility/levelGates.py',
        old='        _DICT_BLOCKER_CACHE.popitem(last=False)',
        new='        _DICT_BLOCKER_CACHE.popitem(last=True)',
    ),
    Falsification(
        nodeid='tests/testL3AttestationMutationCoverage.py::test_empty_digest_attestation_not_current_without_manifest',
        source='vaibify/reproducibility/l3Attestation.py',
        old="""    if not sRecorded:
        return False""",
        new="""    if False:
        return False""",
    ),
    Falsification(
        nodeid='tests/testL3AttestationMutationCoverage.py::test_build_attestation_matched_distinct_from_total',
        source='vaibify/reproducibility/l3Attestation.py',
        old='''"iOutputHashesMatched": int(iOutputHashesMatched),''',
        new='''"iOutputHashesMatched": int(iOutputHashesTotal),''',
    ),
    # The builder guard above cannot see a CALLER performing the same
    # substitution one frame up; the CLI lane did exactly that. These
    # four sit at the shared derivation and at each lane's call site.
    Falsification(
        nodeid='tests/testRerunHashCompareMutationCoverage.py::test_shared_hash_compare_excludes_mismatches_from_matched',
        source='vaibify/reproducibility/rerunVerification.py',
        old='''        "iOutputHashesMatched": max(
            len(listEntries) - len(listMismatches), 0,
        ),''',
        new='''        "iOutputHashesMatched": len(listEntries),''',
    ),
    Falsification(
        nodeid='tests/testRerunHashCompareMutationCoverage.py::test_zero_exit_rerun_with_changed_bytes_does_not_pass',
        source='vaibify/reproducibility/rerunVerification.py',
        old='''        "bPassed": (
            bool(bRerunSucceeded)
            and not listMismatches
            and not bManifestMoved
        ),''',
        new='''        "bPassed": bool(bRerunSucceeded),''',
    ),
    Falsification(
        nodeid='tests/testRerunHashCompareMutationCoverage.py::test_cli_attestation_matched_count_comes_from_the_rehash',
        source='vaibify/cli/commandReproduce.py',
        old='''        iOutputHashesMatched=dictOutcome["iOutputHashesMatched"],''',
        new='''        iOutputHashesMatched=dictOutcome["iOutputHashesTotal"],''',
    ),
    Falsification(
        nodeid='tests/testRerunHashCompareMutationCoverage.py::test_route_attestation_matched_count_comes_from_the_rehash',
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old='''        iOutputHashesMatched=dictResult["iOutputHashesMatched"],''',
        new='''        iOutputHashesMatched=dictResult["iOutputHashesTotal"],''',
    ),
    # A rerun that silently skips steps (interactive, disabled, or a
    # step-less workflow) leaves pinned outputs untouched, so every hash
    # trivially matches and the attestation certifies a run that ran
    # nothing. These two guard the refusal scanner and the empty-manifest
    # fail-close; the tests patch the runner to fail loudly if invoked.
    Falsification(
        nodeid='tests/testRerunRefusesUnexecutedSteps.py::test_interactive_step_refuses_rerun_before_any_execution',
        source='vaibify/reproducibility/rerunVerification.py',
        old='    return listReasons',
        new='    return []',
    ),
    Falsification(
        nodeid='tests/testRerunRefusesUnexecutedSteps.py::test_manifest_pinning_no_files_fails_closed',
        source='vaibify/reproducibility/rerunVerification.py',
        old='    if not listEntries:',
        new='    if False and not listEntries:',
    ),
    # The publishing commit moves HEAD, so a rerun that re-derives the
    # epoch salts its figures differently from the pinned artefacts.
    # These two guard the override short-circuit and the rerun lane's
    # hand-off of the recorded epoch.
    Falsification(
        nodeid='tests/testRecordedEpochReplay.py::test_override_bypasses_the_head_derivation',
        source='vaibify/gui/determinismEnvironment.py',
        old="""    iEpoch = iSourceDateEpochOverride
    if iEpoch <= 0:
        iEpoch = await _fiQueryHeadCommitEpoch(
            connectionDocker, sContainerId, sProjectRepoPath,
        )""",
        new="""    iEpoch = await _fiQueryHeadCommitEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
    )""",
    ),
    Falsification(
        nodeid='tests/testRecordedEpochReplay.py::test_rerun_lane_passes_the_recorded_epoch_to_the_runner',
        source='vaibify/reproducibility/rerunVerification.py',
        old='        iSourceDateEpochOverride=fiRecordedSourceDateEpoch(filesRepo),',
        new='        iSourceDateEpochOverride=0,',
    ),
    # A bind-mount denylist that checks only the descendant direction is
    # bypassed by mounting the parent of a protected directory ($HOME
    # exposes ~/.ssh). The ancestor direction of the overlap check is
    # what closes it.
    Falsification(
        nodeid='tests/testBindMountValidator.py::test_mounting_home_itself_is_rejected',
        source='vaibify/config/bindMountValidator.py',
        old="""    return (
        sFirst.startswith(sSecond + os.sep)
        or sSecond.startswith(sFirst + os.sep)
    )""",
        new="""    return sFirst.startswith(sSecond + os.sep)""",
    ),
    # A repo destination becomes rm -rf "${WORKSPACE}/${destination}" in
    # the entrypoint; unvalidated, it deletes host data via '../' escape
    # or a bind-mount collision. These guard the host-side validator.
    Falsification(
        nodeid='tests/testProjectConfigExtended.py::test_repo_destination_traversal_is_rejected',
        source='vaibify/config/projectConfig.py',
        old='''    return not (
        posixpath.isabs(sDestination) or ".." in sDestination.split("/")
    )''',
        new='    return not posixpath.isabs(sDestination)',
    ),
    Falsification(
        nodeid='tests/testProjectConfigExtended.py::test_repo_destination_colliding_with_bind_mount_is_rejected',
        source='vaibify/config/projectConfig.py',
        old="""    listTargets = []
    for dictMount in dictConfig.get("bindMounts") or []:""",
        new="""    listTargets = []
    return listTargets
    for dictMount in dictConfig.get("bindMounts") or []:""",
    ),
    # The repo NAME derives the clone SOURCE path ${WORKSPACE}/${name};
    # dropping the source from the bind-overlap check lets a name that
    # equals a bind target clone into the mounted host directory.
    Falsification(
        nodeid='tests/testProjectConfigExtended.py::test_repo_name_colliding_with_bind_mount_is_rejected',
        source='vaibify/config/projectConfig.py',
        old="        for sPath in (sSourceAbs, sDestAbs):",
        new="        for sPath in (sDestAbs,):",
    ),
    # An unreachable Docker daemon yields no protected paths; proceeding
    # with an empty protected set deletes credential files a live
    # container still mounts. Enumeration failure must forbid the sweep.
    Falsification(
        nodeid='tests/testEphemeralStore.py::test_sweep_is_forbidden_when_the_daemon_is_unreachable',
        source='vaibify/gui/routes/syncRoutes.py',
        old="""        setMounted = _fsetMountedHostPaths(dictCtx)
        if setMounted is None:""",
        new="""        setMounted = _fsetMountedHostPaths(dictCtx)
        if False:""",
    ),
    # The host-log-tail endpoint returns the raw host-wide log and
    # free-text incidents; the agent lane must receive only an
    # allowlisted per-container view, never the raw log or free text.
    Falsification(
        nodeid='tests/testAgentLaneEnforcement.py::test_host_log_tail_agent_lane_is_sanitized',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="        if serverMiddleware.fbRequestRidesAgentLane(request):",
        new="        if False:",
    ),
    # The browser lane's streamed exec must be as fail-closed as the
    # blocking one; a catch-all iExitCode=0 lets a step whose real
    # command would fail look green through the streamed path.
    Falsification(
        nodeid='tests/testBrowserLaneContract.py::testTheStreamedExecAlsoRaisesRatherThanInventingAnAnswer',
        source='tests/browser/fakeDockerAdapter.py',
        old='        iExitCode, sStdout = self._ftAnswerModelledCommand(sCommand)',
        new='        iExitCode, sStdout = (0, "ok")',
    ),
    # fiApplyMigrations stamped the version DOWN to the current at the
    # end, silently downgrading a future-version project.json and dropping
    # fields this build does not understand on the next save.
    Falsification(
        nodeid='tests/testWorkflowSchemaForwardCompat.py::test_future_schema_version_is_refused_not_downgraded',
        source='vaibify/gui/workflowMigrations.py',
        old="    if iVersion > I_CURRENT_WORKFLOW_VERSION:",
        new="    if False:",
    ),
    # A mount at the workspace root is an ANCESTOR of the destination;
    # the destination is a descendant of the mount. Dropping the
    # descendant direction of the overlap check lets that rm -rf through.
    Falsification(
        nodeid='tests/testProjectConfigExtended.py::test_repo_destination_under_a_workspace_root_mount_is_rejected',
        source='vaibify/config/projectConfig.py',
        old="""    return (
        sFirst.startswith(sSecond + "/")
        or sSecond.startswith(sFirst + "/")
    )""",
        new="""    return sSecond.startswith(sFirst + "/")""",
    ),
    # repr() is Python escaping, not shell escaping; embedded in a
    # double-quoted bash -c string, a crafted container path executes on
    # preview/fetch. Shell-quoting the whole -c argument closes it.
    Falsification(
        nodeid='tests/testDataPreviewInjection.py::test_npy_preview_quotes_the_whole_program',
        source='vaibify/gui/dataPreview.py',
        old='    sCommand = "python3 -c " + fsShellQuote(sProgram)\n    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(\n        sContainerId, sCommand\n    )\n    return sOutput.strip() if iExitCode == 0 else "(unreadable)"\n\n\ndef _fsPreviewHdf5',
        new='    sCommand = "python3 -c \\"" + sProgram + "\\""\n    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(\n        sContainerId, sCommand\n    )\n    return sOutput.strip() if iExitCode == 0 else "(unreadable)"\n\n\ndef _fsPreviewHdf5',
    ),
    Falsification(
        nodeid='tests/testDataPreviewInjection.py::test_file_fetch_does_not_execute_injected_payload',
        source='vaibify/docker/dockerConnection.py',
        # Re-anchored 2026-08-05: the slot is filled by
        # _fsTypedReadPathLiteral rather than by a bare repr(), because
        # the exemption's path parameter widened to accept a collection
        # for the batched existence probe. The mutation is unchanged in
        # meaning -- drop the literal-ization and the shell quoting, so
        # a path becomes program text.
        old="""        sCommand = "python3 -c " + shlex.quote(
            sTemplate.replace(
                _S_TYPED_READ_PATH_SLOT,
                _fsTypedReadPathLiteral(objPaths),
            ),
        )""",
        new="""        sCommand = "python3 -c \\"" + sTemplate.replace(
            _S_TYPED_READ_PATH_SLOT, objPaths,
        ) + "\\"" """,
    ),
    # The ownerless-connect exception is the viewer's bootstrap; extending
    # it to the hub lets an unclaimed connect run its write path (empty
    # agent token clobbers the owner's) against a container another hub may
    # hold via the flock.
    Falsification(
        nodeid='tests/testConnectHubOwnershipGate.py::test_hub_refuses_connect_to_an_unclaimed_container',
        source='vaibify/gui/routes/workflowRoutes.py',
        old="""    if dictContainerOwners.get(sName) is None:
        if dictCtx.get("bIsHub"):
            raise HTTPException(
                409,
                "Claim this container before connecting to it.",
            )
        return""",
        new="""    if dictContainerOwners.get(sName) is None:
        return""",
    ),
    # Sweep C1: connect must consult the SESSION-BOUND lease, not the lease
    # VALUE. Reverting the connect gate to the value-only fbSessionOwnsContainer
    # admits a second browser session replaying the owner's copied lease.
    Falsification(
        nodeid='tests/testConnectHubOwnershipGate.py::test_hub_connect_refuses_second_session_with_a_copied_lease',
        source='vaibify/gui/routes/workflowRoutes.py',
        old="""    if containerOwnership.fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
    ):
        return""",
        new="""    if containerOwnership.fbSessionOwnsContainer(
        dictContainerOwners, sName, sLeaseId,
    ):
        return""",
    ),
    # Sweep C2: release must consult the SESSION-BOUND lease, not the lease
    # VALUE. Reverting the guard to a lease-value comparison lets a second
    # browser session drop the true owner's record with a copied lease.
    Falsification(
        nodeid='tests/testConnectHubOwnershipGate.py::test_release_refuses_second_session_presenting_a_copied_lease',
        source='vaibify/gui/containerOwnership.py',
        old="""    return bBoundOwner or bUnboundOwner""",
        new="""    return recordOwner.sLeaseId == sLeaseId""",
    ),
    # Sweep C8: the repo-URL check must reject a leading-dash argument
    # injection and non-vetted git transports (ext::, file://). Dropping the
    # guard readmits those remote-code primitives, which carry no shell
    # metacharacter and so clear the metacharacter filter.
    Falsification(
        nodeid='tests/testProjectConfigExtended.py::test_repo_url_argument_and_scheme_injection_is_rejected',
        source='vaibify/config/projectConfig.py',
        old="""    if sUrl.startswith("-"):
        return False
    if sUrl.startswith(_TUPLE_SAFE_URL_SCHEMES):
        return True
    return bool(_S_SCP_LIKE_URL.match(sUrl))""",
        new="""    return True""",
    ),
    # Without base-uri, an injected <base> tag (from a hostile filename
    # rendered into innerHTML) re-homes the dashboard's root-relative
    # API calls; base-uri does not fall back to default-src.
    Falsification(
        nodeid='tests/testContentSecurityPolicy.py::test_base_uri_is_locked_to_none',
        source='vaibify/gui/serverMiddleware.py',
        old='            "base-uri \'none\'; "\n            "form-action \'self\'; "',
        new='            "form-action \'self\'; "',
    ),
    # init swallowed the duplicate-name error and reported success, so a
    # second project reusing a name was scaffolded but unregistered and
    # --project resolved to the other directory. The outcome must
    # distinguish same-dir re-init from a cross-directory name conflict.
    Falsification(
        nodeid='tests/testInitRegistrationOutcome.py::test_name_conflict_with_a_different_dir_fails_loudly',
        source='vaibify/cli/commandInit.py',
        old="""    except ValueError:
        dictExisting = fdictGetProject(sName)
        sExistingDir = (dictExisting or {}).get("sDirectory", "")
        if sExistingDir and os.path.abspath(sExistingDir) == \\
                os.path.abspath(sCwd):
            return "already-registered"
        return "name-conflict\"""",
        new="""    except ValueError:
        return "registered\"""",
    ),
    # The build-progress record is read by every later build click (the
    # 409 duplicate refusal) and by re-attached tabs; these guard that a
    # dead build always closes its record and that no unredacted line
    # can reach the dashboard tail.
    Falsification(
        nodeid='tests/testBuildProgressRoutes.py::test_failed_build_closes_the_record_as_failed',
        source='vaibify/gui/buildRoutes.py',
        old="""    except BaseException:
        if dictProgress is not None:
            _fnCloseBuildProgress(dictProgress, "failed")
        raise""",
        new="""    except BaseException:
        raise""",
    ),
    Falsification(
        nodeid='tests/testBuildProgressRoutes.py::test_sink_lines_pass_credential_redaction',
        source='vaibify/docker/imageBuilder.py',
        old='        fnLineSink(fsRedactBuildOutputCredentials(sLine))',
        new='        fnLineSink(sLine)',
    ),
    Falsification(
        nodeid='tests/testL3AttestationMutationCoverage.py::test_non_dict_payload_reads_none_and_not_current',
        source='vaibify/reproducibility/l3Attestation.py',
        old="""    if not isinstance(dictPayload, dict):
        return None""",
        new="""    if False:
        return None""",
    ),
    Falsification(
        nodeid='tests/testL3AttestationMutationCoverage.py::test_invalidate_returns_false_when_no_file',
        source='vaibify/reproducibility/l3Attestation.py',
        old="""    return ffilesEnsureRepoFiles(filesRepo).fbRemoveFile(
        _fsAttestationRelativePath(),
    )""",
        new="""    ffilesEnsureRepoFiles(filesRepo).fbRemoveFile(
        _fsAttestationRelativePath(),
    )
    return True""",
    ),
    Falsification(
        nodeid='tests/testL3AttestationMutationCoverage.py::test_current_manifest_digest_has_sha256_prefix',
        source='vaibify/reproducibility/l3Attestation.py',
        old="""    return "sha256:" + sHash""",
        new='    return sHash',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_container_system_tools_capture_records_adapter_values',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='''"sPython": _fsFirstLine(sPython),''',
        new='''"sPython": None,''',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_container_system_tools_gcc_and_osrelease_failure_yield_none',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='''"sOsRelease": sOsRelease if iOsCode == 0 else None,''',
        new='''"sOsRelease": sOsRelease,''',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_non_dict_environment_json_returns_none_without_crash',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='if not isinstance(dictPayload, dict):',
        new='if isinstance(dictPayload, dict):',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_top_level_registry_digest_pins',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='''return dictPayload.get("sImageDigest") or ""''',
        new='''return ""''',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_top_level_local_image_id_pins',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='''return dictPayload.get("sImageDigest") or ""''',
        new='''return ""''',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_over_long_image_id_is_not_pinned',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='return len(sHexPart) == 64 and all(',
        new='return len(sHexPart) > 63 and all(',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_image_id_digest_length_boundary_is_exactly_64',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='return len(sHexPart) == 64 and all(',
        new='return len(sHexPart) > 64 and all(',
    ),
    Falsification(
        nodeid='tests/testEnvironmentSnapshotMutationCoverage.py::test_write_environment_json_does_not_mutate_caller_dict',
        source='vaibify/reproducibility/environmentSnapshot.py',
        old='dictPayload = dict(dictEnvironment)',
        new='dictPayload = dictEnvironment',
    ),
    Falsification(
        nodeid='tests/testCredentialRedactorMutationCoverage.py::test_token_keyword_redacts_whole_line_not_just_keyword_span',
        source='vaibify/reproducibility/credentialRedactor.py',
        old="""    "password", "token", "bearer", "authorization",""",
        new="""    "password", "bearer", "authorization",""",
    ),
    Falsification(
        nodeid='tests/testCredentialRedactorMutationCoverage.py::test_github_prefixes_scrubbed_in_keyword_free_message',
        source='vaibify/reproducibility/credentialRedactor.py',
        old='ghp|gho|ghu|ghs|ghr|github_pat',
        new='ghp|gho|ghu|ghs|github_pat',
    ),
    Falsification(
        nodeid='tests/testCredentialRedactorMutationCoverage.py::test_scheme_present_empty_netloc_short_circuits_unchanged',
        source='vaibify/reproducibility/credentialRedactor.py',
        old='if not result.scheme or not result.netloc:',
        new='if not result.scheme and not result.netloc:',
    ),
    Falsification(
        nodeid='tests/testDependencyPinningMutationCoverage.py::test_verify_flags_md5_only_lock_as_unhashed',
        source='vaibify/reproducibility/dependencyPinning.py',
        old="""if "--hash=sha256:" not in sJoined:""",
        new="""if "--hash=" not in sJoined:""",
    ),
    Falsification(
        nodeid='tests/testDependencyPinningMutationCoverage.py::test_verify_flags_empty_but_present_lockfile',
        source='vaibify/reproducibility/dependencyPinning.py',
        old='''    if not listEntries:
        return [
            "requirements.lock at '"''',
        new='''    if False:
        return [
            "requirements.lock at '"''',
    ),
    Falsification(
        nodeid='tests/testDependencyPinningMutationCoverage.py::test_verify_accepts_flush_left_hash_continuation',
        source='vaibify/reproducibility/dependencyPinning.py',
        old="""if sLine[:1].isspace() or sLine.lstrip().startswith("--hash"):""",
        new='if sLine[:1].isspace():',
    ),
    Falsification(
        nodeid='tests/testDependencyPinningMutationCoverage.py::test_resolve_prefers_uv_module_over_piptools',
        source='vaibify/reproducibility/dependencyPinning.py',
        old="""    if _fbModuleAvailable("uv"):
        return [sys.executable, "-m", "uv", "pip", "compile"]
    if _fbModuleAvailable("piptools"):
        return [sys.executable, "-m", "piptools", "compile"]""",
        new="""    if _fbModuleAvailable("piptools"):
        return [sys.executable, "-m", "piptools", "compile"]
    if _fbModuleAvailable("uv"):
        return [sys.executable, "-m", "uv", "pip", "compile"]  # mutant""",
    ),
    Falsification(
        nodeid='tests/testProvenanceTrackerMutationCoverage.py::test_fnUpdateProvenance_hashes_plot_files_not_input_files',
        source='vaibify/reproducibility/provenanceTracker.py',
        old="""    for sOutputPath in dictStep.get("saPlotFiles", []):
        if Path(sOutputPath).is_file():
            dictHashes[sOutputPath] = fsComputeFileHash(sOutputPath)""",
        new="""    for sOutputPath in dictStep.get("saInputFiles", []):
        if Path(sOutputPath).is_file():
            dictHashes[sOutputPath] = fsComputeFileHash(sOutputPath)""",
    ),
    Falsification(
        nodeid='tests/testProvenanceTrackerMutationCoverage.py::test_fnUpdateProvenance_stores_computed_hashes_not_empty',
        source='vaibify/reproducibility/provenanceTracker.py',
        old="""dictProvenance["dictFileHashes"] = dictHashes""",
        new="""dictProvenance["dictFileHashes"] = {}""",
    ),
    Falsification(
        nodeid='tests/testProvenanceTrackerMutationCoverage.py::test_fnUpdateProvenance_records_step_identity_by_sname',
        source='vaibify/reproducibility/provenanceTracker.py',
        old="""saSteps.append(dictStep.get("sName", "unknown"))""",
        new="""saSteps.append(dictStep.get("sId", "unknown"))""",
    ),
    Falsification(
        nodeid='tests/testProvenanceTrackerMutationCoverage.py::test_fnUpdateProvenance_stamps_real_timestamp',
        source='vaibify/reproducibility/provenanceTracker.py',
        old="""dictProvenance["sTimestamp"] = _fsCurrentTimestamp()""",
        new='''dictProvenance["sTimestamp"] = ""''',
    ),
    Falsification(
        nodeid='tests/testDockerfileLintMutationCoverage.py::test_truncated_sha256_digest_is_rejected',
        source='vaibify/reproducibility/dockerfileLint.py',
        old='r"@sha256:[0-9a-f]{64}\\b"',
        new='r"@sha256:[0-9a-f]{1,64}\\b"',
    ),
    Falsification(
        nodeid='tests/testDockerfileLintMutationCoverage.py::test_continued_apt_line_packages_are_inspected',
        source='vaibify/reproducibility/dockerfileLint.py',
        old='    return sLine.rstrip().endswith("\\\\")',
        new='    return False',
    ),
    Falsification(
        nodeid='tests/testDockerfileLintMutationCoverage.py::test_source_date_epoch_lookalike_is_not_accepted',
        source='vaibify/reproducibility/dockerfileLint.py',
        old='    r"^\\s*(?:ENV|ARG)\\s+SOURCE_DATE_EPOCH(?:\\s|=)",',
        new='    r"^\\s*(?:ENV|ARG)\\s+SOURCE_DATE_EPOCH",',
    ),
    Falsification(
        nodeid='tests/testDockerfileLintMutationCoverage.py::test_base_image_issue_cites_one_based_line_number',
        source='vaibify/reproducibility/dockerfileLint.py',
        old="""    listIssues = []
    for iIndex, sLine in enumerate(listLines, start=1):
        sStripped = _fsStripLineComment(sLine).strip()""",
        new="""    listIssues = []
    for iIndex, sLine in enumerate(listLines, start=0):
        sStripped = _fsStripLineComment(sLine).strip()  # mutant""",
    ),
    Falsification(
        nodeid='tests/testDataArchiverMutationCoverage.py::TestChecksumLineShape::test_fsGenerateChecksums_records_basename_only',
        source='vaibify/reproducibility/dataArchiver.py',
        old='sRelative = os.path.basename(sPath)',
        new='sRelative = sPath',
    ),
    Falsification(
        nodeid='tests/testDataArchiverMutationCoverage.py::TestChecksumLineShape::test_fsGenerateChecksums_does_not_leak_absolute_path',
        source='vaibify/reproducibility/dataArchiver.py',
        old='sRelative = os.path.basename(sPath)',
        new='sRelative = sPath',
    ),
    Falsification(
        nodeid='tests/testDataArchiverMutationCoverage.py::TestChecksumLineShape::test_fsGenerateChecksums_has_trailing_newline',
        source='vaibify/reproducibility/dataArchiver.py',
        old='return "\\n".join(listLines) + "\\n"',
        new='return "\\n".join(listLines)',
    ),
    Falsification(
        nodeid='tests/testDataArchiverMutationCoverage.py::TestTitlePrecedence::test_fdictBuildZenodoMetadata_prefers_project_title',
        source='vaibify/reproducibility/dataArchiver.py',
        old="""sTitle = dictWorkflow.get("sProjectTitle",
        dictWorkflow.get("sWorkflowName", "Dataset"))""",
        new="""sTitle = dictWorkflow.get("sWorkflowName",
        dictWorkflow.get("sProjectTitle", "Dataset"))  # mutant""",
    ),
    Falsification(
        nodeid='tests/testDataArchiverMutationCoverage.py::TestTitlePrecedence::test_fsGenerateArchiveReadme_prefers_project_title',
        source='vaibify/reproducibility/dataArchiver.py',
        old="""sTitle = dictWorkflow.get("sProjectTitle",
        dictWorkflow.get("sWorkflowName", "Vaibify Workflow"))""",
        new="""sTitle = dictWorkflow.get("sWorkflowName",
        dictWorkflow.get("sProjectTitle", "Vaibify Workflow"))  # mutant""",
    ),
    Falsification(
        nodeid='tests/testManifestWriterMutationCoverage.py::test_flag_token_ending_in_py_is_not_treated_as_test_script',
        source='vaibify/reproducibility/manifestWriter.py',
        old="""        and not sToken.startswith("-")""",
        new='        and True',
    ),
    Falsification(
        nodeid='tests/testManifestWriterMutationCoverage.py::test_manifest_header_is_exact_literal_first_line',
        source='vaibify/reproducibility/manifestWriter.py',
        old='_MANIFEST_HEADER = "# SHA-256 manifest of workflow artefacts\\n"',
        new='_MANIFEST_HEADER = "# anything\\n"',
    ),
    Falsification(
        nodeid='tests/testRepoFilesMutationCoverage.py::test_host_hash_refuses_sibling_dir_sharing_root_prefix',
        source='vaibify/reproducibility/repoFiles.py',
        old="""        return sCandidateReal != sRepoReal and not sCandidateReal.startswith(
            sRepoReal + os.sep,
        )""",
        new="""        return sCandidateReal != sRepoReal and not sCandidateReal.startswith(
            sRepoReal,
        )""",
    ),
    Falsification(
        nodeid='tests/testRepoFilesMutationCoverage.py::test_container_hash_refuses_sibling_dir_sharing_root_prefix',
        source='vaibify/reproducibility/repoFiles.py',
        old="""_S_HASH_SCRIPT = '''
import base64, hashlib, json, os, sys
dictArgs = json.loads(base64.b64decode(%(payload)s).decode())
sRoot = dictArgs["sRoot"]
dictOut = {}
def _fsHash(sAbs):
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFd = os.open(sAbs, iFlags)
    except OSError:
        return None
    h = hashlib.sha256()
    with os.fdopen(iFd, "rb") as f:
        for ba in iter(lambda: f.read(65536), b""):
            h.update(ba)
    return h.hexdigest()
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal + os.sep):""",
        new="""_S_HASH_SCRIPT = '''
import base64, hashlib, json, os, sys
dictArgs = json.loads(base64.b64decode(%(payload)s).decode())
sRoot = dictArgs["sRoot"]
dictOut = {}
def _fsHash(sAbs):
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFd = os.open(sAbs, iFlags)
    except OSError:
        return None
    h = hashlib.sha256()
    with os.fdopen(iFd, "rb") as f:
        for ba in iter(lambda: f.read(65536), b""):
            h.update(ba)
    return h.hexdigest()
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal):""",
    ),
    Falsification(
        nodeid='tests/testRepoFilesMutationCoverage.py::test_snapshot_hash_refuses_sibling_dir_sharing_root_prefix',
        source='vaibify/reproducibility/repoFiles.py',
        old="""    dictOut["dictFiles"][sRel] = dictEntry
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal + os.sep):""",
        new="""    dictOut["dictFiles"][sRel] = dictEntry
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal):""",
    ),

    # --- 2026-07-03: declaration/push cosmic-ray survivors (ux/dashboard-clarity) ---
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_generic_verify_failure_returns_the_generic_warning',
        source='vaibify/gui/routeContext.py',
        old='    return (\n        "Pushed, but the " + sService + " status check failed — "\n        "the Published (L2) cells stay unknown. See the hub log."\n    )',
        new='    return ""',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_manifest_warning_requires_both_filenotfound_and_manifest',
        source='vaibify/gui/routeContext.py',
        old='    if (isinstance(error, FileNotFoundError)\n            and "manifest" in str(error).lower()):',
        new='    if (isinstance(error, FileNotFoundError)\n            or "manifest" in str(error).lower()):',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_catalog_entry_is_user_only',
        source='vaibify/gui/actionCatalog.py',
        old='     "sPath": "/api/git/{sContainerId}/untrack-ai-declaration",\n     "bAgentSafe": False,',
        new='     "sPath": "/api/git/{sContainerId}/untrack-ai-declaration",\n     "bAgentSafe": True,',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_remove_cached_with_no_paths_issues_no_git_command',
        source='vaibify/gui/containerGit.py',
        old='    route-level filter must not be the only wall.\n    """\n    if not listFilePaths:\n        return (0, "")',
        new='    route-level filter must not be the only wall.\n    """\n    if listFilePaths is None:\n        return (0, "")',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_rm_failure_detail_carries_git_output',
        source='vaibify/gui/routes/gitRoutes.py',
        old='            detail="git rm --cached failed: " + (sOut or "").strip(),',
        new='            detail="git rm --cached failed: " + (sOut and "").strip(),',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_commit_failure_detail_carries_git_output',
        source='vaibify/gui/routes/gitRoutes.py',
        old='            docker, sContainerId, [sPath], sWorkspace=sRepo,\n        )\n        raise HTTPException(\n            status_code=500,\n            detail="git commit failed: " + (sOut or "").strip(),',
        new='            docker, sContainerId, [sPath], sWorkspace=sRepo,\n        )\n        raise HTTPException(\n            status_code=500,\n            detail="git commit failed: " + (sOut and "").strip(),',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_after_push_gate_is_exact_equality_not_ordering',
        source='vaibify/gui/routes/repoRoutes.py',
        old='    if sRepoPath != "/workspace/" + sRepoName:',
        new='    if sRepoPath > "/workspace/" + sRepoName:',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_push_files_response_carries_verify_warning',
        source='vaibify/gui/routes/repoRoutes.py',
        old='    fnBumpSyncEpoch(dictCtx, sContainerId)\n    if not dictResult.get("bSuccess"):\n        return dictResult',
        new='    fnBumpSyncEpoch(dictCtx, sContainerId)\n    if dictResult.get("bSuccess"):\n        return dictResult',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_unattested_blocker_requires_a_declaration_step',
        source='vaibify/reproducibility/levelGates.py',
        old='        if fbStepIsAiDeclaration(dictStep)\n        and not fbStepUserApproved(dictStep)',
        new='        if fbStepIsAiDeclaration(dictStep)\n        or not fbStepUserApproved(dictStep)',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_attested_check_fails_closed_on_non_dict_workflow',
        source='vaibify/reproducibility/levelGates.py',
        old='    if not isinstance(dictWorkflow, dict):\n        return False\n    bFound = False',
        new='    if not isinstance(dictWorkflow, dict):\n        return True\n    bFound = False',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_l3_projection_skips_workflow_scope_entries',
        source='vaibify/reproducibility/levelGates.py',
        old='        if not (isinstance(iStepIndex, int) and iStepIndex >= 0):\n            continue\n        listFailing = dictEntry.get("listFailingCriteria") or [',
        new='        if not (isinstance(iStepIndex, int) and iStepIndex >= 0):\n            break\n        listFailing = dictEntry.get("listFailingCriteria") or [',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_declaration_step_l2_counts_are_exact',
        source='vaibify/reproducibility/levelGates.py',
        old='            ("ai-declaration-attested",\n             "ai-declaration-unattested" not in setCriteria))',
        new='            ("ai-declaration-attested",\n             "ai-declaration-unattested" in setCriteria))',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_step_l3_counts_zero_without_repo',
        source='vaibify/reproducibility/levelGates.py',
        old='            (sCriterion, False)\n            for sCriterion in _T_STEP_LEVEL3_CRITERIA\n        ]',
        new='            (sCriterion, True)\n            for sCriterion in _T_STEP_LEVEL3_CRITERIA\n        ]',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_step_l3_satisfied_arithmetic_is_subtraction',
        source='vaibify/reproducibility/levelGates.py',
        old='        (sCriterion, sCriterion not in setFailing)',
        new='        (sCriterion, sCriterion in setFailing)',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_randomness_criterion_requires_literal_true',
        source='vaibify/reproducibility/levelGates.py',
        old='    if dictStep.get("bUnseededRandomnessWarning") is True:\n        setApplicable.add("nondeterminism-undeclared")',
        new='    if dictStep.get("bUnseededRandomnessWarning") == True:\n        setApplicable.add("nondeterminism-undeclared")',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_binary_reference_reads_the_declared_path',
        source='vaibify/reproducibility/levelGates.py',
        old='        _fbStepReferencesDeclaredBinary(\n            listCommands, dictEntry.get("sBinaryPath") or "",\n        )',
        new='        _fbStepReferencesDeclaredBinary(\n            listCommands, dictEntry.get("sBinaryPath") and "",\n        )',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_push_staged_pushes_an_already_committed_repo_real_git',
        source='vaibify/gui/syncDispatcher.py',
        old='        f"(git diff --cached --quiet || "\n        f"git {sHardening} commit -m {fsShellQuote(sCommitMessage)}) && "',
        new='        f"git {sHardening} commit -m {fsShellQuote(sCommitMessage)} && "',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_push_staged_commits_staged_changes_then_pushes_real_git',
        source='vaibify/gui/syncDispatcher.py',
        old='        f"git {sHardening} commit -m {fsShellQuote(sCommitMessage)}) && "\n        f"git {sHardening} push && "',
        new='        f"git {sHardening} commit -m {fsShellQuote(sCommitMessage)}) && "\n        f"git {sHardening} push --dry-run && "',
    ),

    # --- 2026-07-03: untrack real-git regressions (pathspec-commit bug, staged-index guard) ---
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_clean_declaration_really_untracks_real_git',
        source='vaibify/gui/routes/gitRoutes.py',
        old='    iExit, sOut = containerGit.ftResultGitCommitInContainer(\n        docker, sContainerId,\n        "[vaibify] remove AI declaration from the repo",\n        sWorkspace=sRepo,\n    )',
        new='    iExit, sOut = containerGit.ftResultGitCommitInContainer(\n        docker, sContainerId,\n        "[vaibify] remove AI declaration from the repo",\n        sWorkspace=sRepo, listFilePaths=[sPath],\n    )',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_modified_declaration_untracks_not_commits_real_git',
        source='vaibify/gui/routes/gitRoutes.py',
        old='    iExit, sOut = containerGit.ftResultGitCommitInContainer(\n        docker, sContainerId,\n        "[vaibify] remove AI declaration from the repo",\n        sWorkspace=sRepo,\n    )',
        new='    iExit, sOut = containerGit.ftResultGitCommitInContainer(\n        docker, sContainerId,\n        "[vaibify] remove AI declaration from the repo",\n        sWorkspace=sRepo, listFilePaths=[sPath],\n    )',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_refuses_when_other_changes_staged_real_git',
        source='vaibify/gui/routes/gitRoutes.py',
        old='    iExit, sOut = containerGit.ftResultGitDiffCachedQuietInContainer(\n        docker, sContainerId, sWorkspace=sRepo,\n    )\n    if iExit != 0:',
        new='    iExit, sOut = containerGit.ftResultGitDiffCachedQuietInContainer(\n        docker, sContainerId, sWorkspace=sRepo,\n    )\n    if False and iExit != 0:',
    ),

    # --- 2026-07-11: per-step falsification attestation honesty guards ---
    Falsification(
        nodeid='tests/testFalsificationAttestationMutationCoverage.py::test_na_step_never_presents_current_record',
        source='vaibify/reproducibility/falsificationAttestation.py',
        old='    bRecordCurrent = False\n    if dictApplicability["bApplicable"]:\n        bRecordCurrent = fbFalsificationRecordCurrent(',
        new='    bRecordCurrent = False\n    if True:\n        bRecordCurrent = fbFalsificationRecordCurrent(',
    ),
    Falsification(
        nodeid='tests/testFalsificationAttestationMutationCoverage.py::test_empty_digest_record_is_never_current',
        source='vaibify/reproducibility/falsificationAttestation.py',
        old='    sRecorded = dictRecord.get("sScriptDigest") or ""\n    if not sRecorded:\n        return False',
        new='    sRecorded = dictRecord.get("sScriptDigest") or ""',
    ),
    Falsification(
        nodeid='tests/testFalsificationAttestationMutationCoverage.py::test_kill_rate_numerator_is_the_killed_count',
        source='vaibify/reproducibility/falsificationAttestation.py',
        old='    fKillRate = float(int(iMutantsKilled)) / iTotal if iTotal > 0 else 0.0',
        new='    fKillRate = float(int(iMutantsSurvived)) / iTotal if iTotal > 0 else 0.0',
    ),
    Falsification(
        nodeid='tests/testFalsificationAttestationMutationCoverage.py::test_digest_collapses_when_any_covered_file_is_missing',
        source='vaibify/reproducibility/falsificationAttestation.py',
        old='        sHash = (dictHashed.get(sRelPath) or {}).get("sSha256")\n        if not sHash:\n            return ""',
        new='        sHash = (dictHashed.get(sRelPath) or {}).get("sSha256")\n        if not sHash:\n            continue',
    ),
    Falsification(
        nodeid='tests/testRemoteDataOverwriteGate.py::test_gated_run_is_refused_and_never_dispatched',
        source='vaibify/gui/pipelineServer.py',
        old='    if dictRequest.get("bConfirmRemoteOverwrite"):\n        return None',
        new='    if True:\n        return None',
    ),
    Falsification(
        nodeid='tests/testFalsificationRoutesMutationCoverage.py::test_exec_failure_record_reports_exact_zero_counts',
        source='vaibify/gui/routes/falsificationRoutes.py',
        old='            S_STATUS_ERROR, sDigest, sClassification, 0, 0, 0,\n            sCosmicRayVersion=sCosmicRayVersion,\n            fDurationSeconds=time.monotonic() - fStarted,\n            sReason="cosmic-ray exited "',
        new='            S_STATUS_ERROR, sDigest, sClassification, 1, 0, 0,\n            sCosmicRayVersion=sCosmicRayVersion,\n            fDurationSeconds=time.monotonic() - fStarted,\n            sReason="cosmic-ray exited "',
    ),
    Falsification(
        nodeid='tests/testFalsificationRoutesMutationCoverage.py::test_unparseable_summary_reason_carries_the_output_tail',
        source='vaibify/gui/routes/falsificationRoutes.py',
        old='            sReason="could not summarize the mutation session: "\n            + _fsTailOfOutput(resultSummary),',
        new='            sReason="could not summarize the mutation session: "\n            % _fsTailOfOutput(resultSummary),',
    ),
    Falsification(
        nodeid='tests/testFalsificationRoutesMutationCoverage.py::test_graded_summary_builds_an_attained_record',
        source='vaibify/gui/routes/falsificationRoutes.py',
        old='        dictSummary["iMutantsTotal"], dictSummary["iMutantsKilled"],\n        dictSummary["iMutantsSurvived"],',
        new='        dictSummary["iMutantsTotal"], dictSummary["iMutantsSurvived"],\n        dictSummary["iMutantsKilled"],',
    ),
    Falsification(
        nodeid='tests/testFalsificationRoutesMutationCoverage.py::test_zero_graded_mutants_is_an_error_not_an_attainment',
        source='vaibify/gui/routes/falsificationRoutes.py',
        old='    if dictSummary["iMutantsTotal"] == 0:',
        new='    if dictSummary["iMutantsTotal"] < 0:',
    ),
    Falsification(
        nodeid='tests/testFalsificationRoutesMutationCoverage.py::test_tail_of_output_keeps_the_last_characters',
        source='vaibify/gui/routes/falsificationRoutes.py',
        old='    return sCombined[-iMaxCharacters:]',
        new='    return sCombined[not iMaxCharacters:]',
    ),
    Falsification(
        nodeid='tests/testFalsificationAttestationMutationCoverage.py::test_record_defaults_report_zero_duration',
        source='vaibify/reproducibility/falsificationAttestation.py',
        old='    fDurationSeconds=0.0, sReason="",',
        new='    fDurationSeconds=1.0, sReason="",',
    ),
    # --- Step name <-> directory slug contract (2026-07-18) ---
    Falsification(
        nodeid='tests/testStepSlugContract.py::test_slug_enforces_camelcase_on_lowercase_words',
        source='vaibify/gui/pipelineUtils.py',
        old='        sWord[0].upper() + sWord[1:] for sWord in listWords if sWord',
        new='        sWord[0] + sWord[1:] for sWord in listWords if sWord',
    ),
    Falsification(
        # basename IN slug instead of == : "systems/GJ1132" passes
        # against "GJ1132XUV" — the guardrail goes blind to exactly
        # the truncated legacy directories it exists to catch.
        nodeid='tests/testStepSlugContract.py::test_conformance_governs_only_the_final_component',
        source='vaibify/gui/pipelineUtils.py',
        old='    return posixpath.basename(sDirectory) == fsSlugFromStepName(',
        new='    return posixpath.basename(sDirectory) in fsSlugFromStepName(',
    ),
    Falsification(
        # Dropping .lower() lets 'NEW STEP' coexist with 'New Step' —
        # one directory on a macOS clone.
        nodeid='tests/testStepSlugContract.py::test_unique_slug_rejects_a_case_variant',
        source='vaibify/gui/pipelineUtils.py',
        old='        ).lower() == sSlugLower:',
        new='        ) == sSlugLower:',
    ),
    Falsification(
        # Inverting the guard lets a rename slip through the generic
        # edit path, desynchronizing name from directory/marker/manifest.
        nodeid='tests/testPipelineServerRoutes.py::test_update_step_rejects_name_changes',
        source='vaibify/gui/routes/stepRoutes.py',
        old='    if "sName" in dictUpdates \\\n            and dictUpdates["sName"] != sCurrentName:',
        new='    if "sName" in dictUpdates \\\n            and dictUpdates["sName"] == sCurrentName:',
    ),
    Falsification(
        # Honoring a typed basename over the derived slug reopens
        # name/directory divergence at creation.
        nodeid='tests/testStepSlugContract.py::test_derive_ignores_a_nonconforming_provided_basename',
        source='vaibify/gui/pipelineServer.py',
        old='    return posixpath.join(sParent, sSlug) if sParent else sSlug',
        new='    return posixpath.join(sParent, sSlug) if sParent else (sDirectoryRaw or sSlug)',
    ),
    Falsification(
        # THE SHIPPED BUG (live 2026-07-18): the align route read the
        # workflow path from a key the workflow dict never carries, so
        # the marker namespace slug was empty and every verification
        # marker was silently orphaned. Killed only by the real-wiring
        # route test — the unit fixtures had encoded the same wrong key.
        nodeid='tests/testStepRoutes.py::testAlignRouteMovesTheMarkerThroughRealWiring',
        source='vaibify/gui/routes/stepRoutes.py',
        # Re-anchored 2026-08-05: the alignment loop moved out of the
        # handler into the mode-(b) worker, so the line dedented, and
        # the path expression alone now occurs twice (the rename cascade
        # passes it too). The preceding argument line disambiguates.
        old=(
            '                dictWorkflow, iIndex, dictPlan,\n'
            '                dictCtx["paths"].get(sContainerId, ""),'
        ),
        new=(
            '                dictWorkflow, iIndex, dictPlan,\n'
            '                dictWorkflow.get("sPath", ""),'
        ),
    ),
    Falsification(
        # A short-circuited warnings builder makes a manual
        # project.json edit visible in the GUI only — the CLI and the
        # in-container agent would never see the violation.
        nodeid='tests/testStepSlugContract.py::test_backend_reports_directory_contract_warnings',
        source='vaibify/gui/workflowManager.py',
        old='        if fbStepDirectoryConforms(dictStep):\n            continue',
        new='        if fbStepDirectoryConforms(dictStep) or True:\n            continue',
    ),
    # --- Personal instruction layer (Replay axis, 2026-07-19) ---
    Falsification(
        # Dropping the agent-lane rejection hands a compromised
        # in-container agent a hash oracle over host files.
        nodeid='tests/testReplayRoutes.py::test_hash_route_rejects_agent_token_lane',
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '        fnRejectAgentTokenLane(requestHttp)\n'
            '        dictCtx["require"](sContainerId)\n'
            '        fdictRequireWorkflow(dictCtx["workflows"], sContainerId)'
        ),
        new=(
            '        dictCtx["require"](sContainerId)\n'
            '        fdictRequireWorkflow(dictCtx["workflows"], sContainerId)'
        ),
    ),
    Falsification(
        # Removing the personal-layer conjunct lets a project reach
        # Level 2 with the instruction stack's fourth layer
        # unaccounted for.
        nodeid='tests/testLevelGatesMutationCoverage.py::test_l2_gate_requires_personal_layer_answer',
        source='vaibify/reproducibility/levelGates.py',
        old='    if not replayGate.fbWorkflowDeclaresPersonalLayer(dictWorkflow):\n        return False',
        new='    if False:\n        return False',
    ),
    Falsification(
        # A constant digest would turn the commitment into theater:
        # it could never prove a later release matches the governing
        # files.
        nodeid='tests/testReplayRoutes.py::test_hash_route_digest_tracks_file_content',
        source='vaibify/gui/personalLayerManager.py',
        old='        "sSha256": hasher.hexdigest(),',
        new='        "sSha256": "0" * 64,',
    ),
    Falsification(
        # Dropping the personal-layer conjunct would let the axis
        # report "Declared" while the instruction stack's fourth
        # layer is unaccounted for.
        nodeid='tests/testReplayGate.py::test_axis_declared_requires_personal_layer_answer',
        source='vaibify/reproducibility/replayGate.py',
        old='    if not fbWorkflowDeclaresPersonalLayer(dictWorkflow):\n        return "untracked"',
        new='    if False:\n        return "untracked"',
    ),
    # --- Config values that survive being written but not being read
    #     back (resource limits, wizard save; 2026-07-25) ---
    Falsification(
        # The range check also rejects NaN (it fails every
        # comparison), so what the finiteness check defends is the
        # diagnostic: without it the refusal quotes a bound the value
        # was never in.
        nodeid='tests/testResourceLimitRoundTrip.py::test_non_finite_memory_limit_never_reaches_the_yaml',
        source='vaibify/gui/registryRoutes.py',
        old='    if isinstance(numberValue, float) and not math.isfinite(\n        numberValue\n    ):',
        new='    if False:',
    ),
    Falsification(
        # Without the ceiling, %g renders a fat-fingered cap as
        # 1e+06 and the project stops loading.
        nodeid='tests/testResourceLimitRoundTrip.py::test_oversized_cpu_limit_never_reaches_the_yaml',
        source='vaibify/gui/registryRoutes.py',
        old='    if not numberMinimum <= numberValue <= numberMaximum:',
        new='    if not numberMinimum <= numberValue:',
    ),
    # --- Host GitHub credential resolution (Phase 1, 2026-07-25) ---
    Falsification(
        # The empty secret name is rejected by fsRetrieveSecret before
        # it dispatches on the method, so the gh-auth fallback becomes
        # dead code and every dashboard push is refused on a host
        # whose `gh auth login` works.
        nodeid='tests/testGithubTokenResolution.py::test_resolve_token_reaches_gh_auth_fallback_with_real_validation',
        source='vaibify/reproducibility/githubAuth.py',
        old='_S_GH_AUTH_SLOT_NAME = "gh_token"',
        new='_S_GH_AUTH_SLOT_NAME = ""',
    ),
    Falsification(
        # The same dead fallback embedded in the generated askpass
        # helper makes host-side git authentication silently anonymous.
        nodeid='tests/testGithubTokenResolution.py::test_askpass_helper_passes_a_valid_secret_name_to_gh_auth',
        source='vaibify/reproducibility/githubAuth.py',
        old='        sGhAuthNameRepr=repr(_S_GH_AUTH_SLOT_NAME),',
        new='        sGhAuthNameRepr=repr(""),',
    ),
    Falsification(
        # Grading connectivity on the container probe alone reports
        # "Connected" right before the host-side push is refused.
        nodeid='tests/testGithubTokenResolution.py::test_github_check_is_not_connected_without_a_host_credential',
        source='vaibify/gui/syncDispatcher.py',
        old='        "bConnected": bContainerReaches and bHostCredential,',
        new='        "bConnected": bContainerReaches,',
    ),
    Falsification(
        # A no-op sweep leaves live tokens readable on disk for months
        # while reporting success.
        nodeid='tests/testEphemeralStore.py::test_sweep_removes_stale_credential_files',
        source='vaibify/config/ephemeralStore.py',
        old='            os.remove(sPath)',
        new='            pass',
    ),
    Falsification(
        # githubAuth._PATTERN_SEGMENT allows dots in owner and repo
        # names; this alphabet did not, so every dotted repository
        # raised out of the push route as a bare HTTP 500.
        nodeid='tests/testGithubTokenResolution.py::test_dotted_repository_slot_passes_real_secret_name_validation',
        source='vaibify/config/secretManager.py',
        old='r"^[a-zA-Z0-9_:./-]{1," + str(_I_MAXIMUM_SECRET_NAME_LENGTH) + r"}$"',
        new='r"^[a-zA-Z0-9_:/-]{1," + str(_I_MAXIMUM_SECRET_NAME_LENGTH) + r"}$"',
    ),
    Falsification(
        # The old 64-character cap was shorter than a real
        # "github_token:<owner>/<repo>" slot, which runs to 153.
        nodeid='tests/testGithubTokenResolution.py::test_widest_real_keyring_slot_fits_the_length_cap',
        source='vaibify/config/secretManager.py',
        old='_I_MAXIMUM_SECRET_NAME_LENGTH = 160',
        new='_I_MAXIMUM_SECRET_NAME_LENGTH = 64',
    ),
    Falsification(
        # Widening the alphabet to admit "." must not admit "." as a
        # path SEGMENT: sName reaches /run/secrets/{sName}.
        nodeid='tests/testGithubTokenResolution.py::test_widened_alphabet_still_refuses_path_traversal',
        source='vaibify/config/secretManager.py',
        old='if "" in listParts or "." in listParts or ".." in listParts:',
        new='if "" in listParts or ".." in listParts:',
    ),
    # --- Step identity: labels and the rename cascade (2026-07-25) ---
    Falsification(
        # The shipped bug, restored on the RESOLVER side: comparing the
        # raw field against a bool made a hand-edited
        # "bInteractive": null resolve A01 to the SECOND step and A02
        # to nothing at all.
        nodeid='tests/testStepLabels.py::test_label_round_trip_is_total_over_every_flag_shape',
        source='vaibify/gui/pipelineUtils.py',
        old='        if fbStepIsInteractive(dictStep) == bWantInteractive:',
        new='        if dictStep.get("bInteractive", False) == bWantInteractive:',
    ),
    Falsification(
        # The same disagreement from the LABELLER side: raw truthiness
        # reads a quoted "false" as interactive, so the step is
        # labelled I01 and resolved as automated.
        nodeid='tests/testStepLabels.py::TestFbStepIsInteractive::test_the_string_false_is_not_read_as_a_non_empty_object',
        source='vaibify/gui/pipelineUtils.py',
        old='        if fbStepIsInteractive(dictStep):\n            iInteractive += 1',
        new='        if dictStep.get("bInteractive", False):\n            iInteractive += 1',
    ),
    Falsification(
        # Without the undo, a failed marker or manifest stage leaves
        # the directory at the new slug while project.json still names
        # the old one -- and fbStepDirectoryConforms then reports the
        # step healthy, so no warning ever appears.
        nodeid='tests/testStepRename.py::test_apply_undoes_the_directory_move_when_a_later_stage_fails',
        source='vaibify/gui/stepRename.py',
        old="""            _fnUndoOrRecordSplit(
                connectionDocker, sContainerId, sRepo, dictWorkflow,
                iStepIndex, dictPlan, dictReport, errorCascade,
            )
            raise""",
        new='            raise',
    ),
    Falsification(
        # When the undo itself fails the pair has genuinely split;
        # pointing the step at the directory that holds its bytes is
        # what makes the split visible as a nonconforming step.
        nodeid='tests/testStepRename.py::test_apply_makes_an_unrecoverable_split_visible_not_conforming',
        source='vaibify/gui/stepRename.py',
        old="""        _fnApplyWorkflowRewrites(dictWorkflow, iStepIndex, dictPlan)
        dictWorkflow["listSteps"][iStepIndex]["sName"] = \\
            dictPlan["sOldName"]
        raise StepRenameSplitError(""",
        new='        raise StepRenameSplitError(',
    ),
    Falsification(
        # Swallowing the parse error renames the step while its
        # verification record stays behind under a name nothing will
        # look for again.
        nodeid='tests/testStepRename.py::test_apply_refuses_a_rename_when_the_marker_is_unreadable',
        source='vaibify/gui/stepRename.py',
        old="""    except (ValueError, OSError) as error:
        # Loud, not silent: a marker that exists but cannot be read is
        # a verification record about to be orphaned under a name
        # nothing will look for again.
        raise ValueError(
            f"The step's verification marker '{sOldRelative}' could "
            f"not be read ({error}) — refusing a rename that would "
            "orphan the step's test record. Re-run the step's tests "
            "or delete the marker, then rename.",
        ) from error""",
        new="""    except (ValueError, OSError):
        return False""",
    ),
    Falsification(
        # Gating uniqueness on the directory move lets a step with no
        # directory yet be renamed onto another step's slug; the
        # collision then surfaces at directory creation instead.
        nodeid='tests/testStepRename.py::test_plan_rejects_a_slug_collision_with_no_directory_to_move',
        source='vaibify/gui/stepRename.py',
        old='    fnRequireUniqueStepSlug(dictWorkflow, iStepIndex, sNewName)',
        new='    if bDirectoryRenamed:\n        fnRequireUniqueStepSlug(dictWorkflow, iStepIndex, sNewName)',
    ),
    Falsification(
        # Falling through to the generic RuntimeError branch raises
        # 500 without saving, so the nonconforming directory the
        # cascade recorded is lost on the next load.
        nodeid='tests/testStepRoutes.py::testRenameRoutePersistsAnUnrecoverableSplit',
        source='vaibify/gui/routes/stepRoutes.py',
        # Re-anchored 2026-08-05: the branch moved into the mode-(b)
        # worker and its comment moved up into the wrapper's docstring,
        # where the save-then-poison ordering is now explained in full.
        # The mutation is unchanged in meaning -- delete the branch and
        # StepRenameSplitError falls through to the generic RuntimeError
        # clause, which 500s without ever saving.
        old="""        except stepRename.StepRenameSplitError as error:
            dictCtx["save"](sContainerId, dictWorkflow)
            raise HTTPException(500, str(error)) from error
""",
        new='',
    ),
    # --- Dashboard honesty (Opus 5 review, phase 3, 2026-07-25) ---
    Falsification(
        # Reading a timezone-less stamp as the host's LOCAL time
        # relocates a recorded cause by the host's UTC offset, so a
        # legitimate event falls outside the window and ordinary work
        # is flagged permanently.
        nodeid='tests/testAttributionLog.py::test_naive_event_timestamp_is_read_as_utc_not_local',
        source='vaibify/gui/attributionLog.py',
        old=(
            '    if dtParsed.tzinfo is None:\n'
            '        return dtParsed.replace(tzinfo=timezone.utc)\n'
            '    return dtParsed.astimezone(timezone.utc)'
        ),
        new='    return dtParsed',
    ),
    Falsification(
        # Without a lower bound on event age, one forward-dated line
        # appended from the container's shell sits inside the window
        # of every later change and blinds the watchdog forever.
        nodeid='tests/testAttributionLog.py::test_future_dated_event_never_attributes_a_change',
        source='vaibify/gui/attributionLog.py',
        old='        if fEpoch > fNowEpoch:\n            continue',
        new='        if False:\n            continue',
    ),
    Falsification(
        # Judging a terminal as two instants instead of an interval
        # false-flags every ordinary edit made minutes into a session.
        nodeid='tests/testAttributionLog.py::test_open_terminal_session_attributes_a_later_change',
        source='vaibify/gui/attributionLog.py',
        old=(
            '    return iOpenCount > 0 and fSpanStart <= fAnchorEpoch '
            '<= fNowEpoch'
        ),
        new='    return False',
    ),
    Falsification(
        # Anchoring only on "now" reinstates the 60-vs-90 second gap:
        # a change judged by a late tick is flagged even though its
        # explaining event is exactly as old as the change.
        nodeid='tests/testSupervisionWatchdog.py::test_delayed_tick_does_not_flag_an_explained_change',
        source='vaibify/gui/attributionLog.py',
        old=(
            '    if fChangeEpoch is None:\n'
            '        return [fNowEpoch]\n'
            '    try:\n'
            '        return [fNowEpoch, float(fChangeEpoch)]\n'
            '    except (TypeError, ValueError):\n'
            '        return [fNowEpoch]'
        ),
        new='    return [fNowEpoch]',
    ),
    Falsification(
        # dictRunState drives the run marker for agent-initiated runs
        # and fell outside the freshness stamp for a month; excluding
        # it again lets a revalidating cache serve a stale body that
        # clears a live run's lights.
        nodeid='tests/testPipelineRoutesEtag.py::test_file_status_run_state_change_changes_etag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='_SET_ETAG_VOLATILE_KEYS = frozenset()',
        new='_SET_ETAG_VOLATILE_KEYS = frozenset({"dictRunState"})',
    ),
    Falsification(
        # Grading supervision on the persisted count makes the PROOF
        # row gradeable on self-report: the supervised agent edits
        # project.json and the row turns green.
        nodeid='tests/testReplayGate.py::test_supervision_is_not_gradeable_on_the_persisted_count',
        source='vaibify/reproducibility/replayGate.py',
        old=(
            '    if not isinstance(dictEvidence, dict):\n'
            '        return False\n'
            '    if dictEvidence.get("iFlagCount") != 0:\n'
            '        return False\n'
            '    return (\n'
            '        dictEvidence.get("bFlagChainIntact") is True\n'
            '        and dictEvidence.get("bEventChainIntact") is True\n'
            '        and dictEvidence.get("bPersistedFlagCountMatches") '
            'is True\n'
            '        and dictEvidence.get("bHostAnchorConsistent") '
            'is True\n'
            '    )'
        ),
        new=(
            '    return int(dictSupervision.get('
            '"iUnattributedFlagCount") or 0) == 0'
        ),
    ),
    Falsification(
        # Comparing only the declared model list leaves five of the
        # six captured stamp fields hand-editable forever, and every
        # one is folded into the L3 attestation.
        nodeid='tests/testAiProvenanceStamp.py::test_edited_stamp_fields_are_detected_as_stale',
        source='vaibify/reproducibility/aiProvenanceStamp.py',
        old='    if not _fbStampShapeIntact(dictStamp):\n        return False',
        new='    if False:\n        return False',
    ),
    Falsification(
        # The import route reads an arbitrary HOST file into a
        # repo-tracked, agent-readable, agent-pushable location; its
        # docstring promised unreachability that nothing enforced.
        nodeid='tests/testReplayRoutes.py::test_context_import_rejects_agent_token_lane',
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '        fnRejectAgentTokenLane(requestHttp)\n'
            '        dictCtx["require"](sContainerId)\n'
            '        dictWorkflow = fdictRequireWorkflow('
        ),
        new=(
            '        dictCtx["require"](sContainerId)\n'
            '        dictWorkflow = fdictRequireWorkflow('
        ),
    ),
    Falsification(
        # The version field is machine-recorded provenance, so accepting a
        # made-up provider name turns arbitrary unvalidated data into an
        # attestation claim.
        nodeid='tests/testAiProvenanceStamp.py::test_agent_version_stamp_rejects_unexpected_provider_name',
        source='vaibify/reproducibility/aiProvenanceStamp.py',
        old=(
            '        sAgent not in {\n'
            '            "claude", "codex", "gemini", "opencode", "cline",\n'
            '            "openhands", "pi",\n'
            '        }\n'
        ),
        new='        False\n',
    ),
    Falsification(
        # A disabled update choice is an explicit user preference. If this
        # mapping vanishes, deserialization silently restores the default
        # true and the dashboard misrepresents what will run at startup.
        nodeid='tests/testProjectConfigExtended.py::test_pi_auto_update_yaml_mapping_cannot_be_dropped',
        source='vaibify/config/projectConfig.py',
        old='    "piAutoUpdate": "bPiAutoUpdate",\n',
        new='',
    ),
    # ---- Phase 4 security remediation (2026-07-25) ----
    Falsification(
        # Without the catalog check the agent lane returns to an
        # unconditional pass-through, and every user-only action --
        # kill-pipeline, delete-step, supervision/configure -- is
        # reachable by a compromised in-container agent with curl.
        nodeid='tests/testAgentLaneEnforcement.py::testAgentLaneRefusesEveryUserOnlyRoute',
        source='vaibify/gui/serverMiddleware.py',
        old='            if not _fbAgentLanePermitsRequest(request):',
        new='            if False:',
    ),
    Falsification(
        # Fail-open on an unregistered route would make the enforcement
        # point decay: every route added later becomes agent-reachable
        # by omission rather than by decision.
        nodeid='tests/testAgentLaneEnforcement.py::testAgentLaneFailsClosedForUnregisteredMutatingRoute',
        source='vaibify/gui/actionCatalog.py',
        old='    return sMethod not in SET_STATE_MUTATING_METHODS',
        new='    return True',
    ),
    Falsification(
        # Connect with no ownership check lets a second browser tab
        # bypass the claim route's 409 and take the workflow, the
        # project-repo path, and the container's agent session.
        nodeid='tests/testAgentLaneEnforcement.py::testConnectRefusesASessionWithoutTheOwningLease',
        source='vaibify/gui/routes/workflowRoutes.py',
        old='    raise HTTPException(409, "In use in another browser session")',
        new='    return',
    ),
    Falsification(
        # docker cp runs on the HOST, so an unconfined agent-lane pull
        # writes agent-authored bytes into a shell profile or an
        # authorized-keys file -- code execution out of the sandbox.
        nodeid='tests/testAgentLaneEnforcement.py::testAgentPullMustLandInTheExportDirectory',
        source='vaibify/gui/routes/fileRoutes.py',
        old='        if fbRequestRidesAgentLane(requestHttp):',
        new='        if False:',
    ),
    Falsification(
        # Writing .git/hooks/pre-commit is code execution on the next
        # commit; writing .vaibify/ defeats the metadata-integrity
        # contract the PROOF truth system rests on.
        nodeid='tests/testAgentLaneEnforcement.py::testSaveAndRunTestRefusesDenylistedPaths',
        source='vaibify/gui/routes/testRoutes.py',
        old='    fnRejectWriteDenylistedPath(sNormalized, sRoot)',
        new='    pass',
    ),
    Falsification(
        # An unvalidated fallback turns the HEAD probe's `test -f` into
        # an existence oracle over arbitrary container paths.
        nodeid='tests/testAgentLaneEnforcement.py::testFigureProbeValidatesTheWorkdirFallback',
        source='vaibify/gui/routes/figureRoutes.py',
        # Re-pinned 2026-08-08: the jail's root became the resource's
        # own (host-mode wave 4), so the anchor names sProjectRoot.
        # The kill was hand-replayed against the new spelling.
        old='            fsValidatePathWithinRoot(sFallback, sProjectRoot))',
        new='            sFallback)',
    ),
    Falsification(
        # Without the control-character rejection a path carrying a
        # newline plus the heredoc terminator escapes into
        # /bin/bash -c in the batched existence check.
        nodeid='tests/testInjectionGuards.py::testPathValidationRejectsControlCharacters',
        source='vaibify/gui/pipelineServer.py',
        old='    _fnRejectControlCharactersInPath(sResolvedPath)\n',
        new='',
    ),
    Falsification(
        # A prefix compare accepts http://localhost.evil.example, the
        # same prefix-attack class fsValidatePathWithinRoot defends
        # against.
        nodeid='tests/testInjectionGuards.py::testLoopbackOriginRejectsASuffixDomain',
        source='vaibify/gui/pipelineServer.py',
        old='    return (tParsed.hostname or "") in _SET_LOOPBACK_ORIGIN_HOSTS',
        new='    return sOrigin.startswith("http://localhost")',
    ),
    Falsification(
        # Without containment, a traversing template name copies an
        # arbitrary host directory into a project that is then mounted
        # into a container.
        nodeid='tests/testInjectionGuards.py::testTemplateNameCannotEscapeTheTemplateRoot',
        source='vaibify/config/templateManager.py',
        old='    if pathRoot not in pathTemplate.parents:',
        new='    if False:',
    ),
    Falsification(
        # saTestCommands is persisted and re-executed, so an unquoted
        # path is a stored, repeatedly-executed injection.
        nodeid='tests/testInjectionGuards.py::testPersistedTestCommandQuotesItsPath',
        source='vaibify/gui/testStatusManager.py',
        old='    sRunCmd = f"python -m pytest {fsShellQuote(sFilePath)} -v"',
        new='    sRunCmd = f"python -m pytest {sFilePath} -v"',
    ),
    Falsification(
        # sProjectRepoPath comes from the workflow, so an unquoted
        # `mv {a} {b}` is command injection through a repo path.
        nodeid='tests/testInjectionGuards.py::testContainerCacheRenameQuotesBothPaths',
        source='vaibify/gui/mtimeCache.py',
        old='            f"mv {fsShellQuote(sPathTemp)} {fsShellQuote(sPath)}",',
        new='            f"mv {sPathTemp} {sPath}",',
    ),
    Falsification(
        # Treating an undeclared expected port as "check disabled"
        # silently drops the DNS-rebinding defence for every request an
        # incorrectly wired app serves.
        nodeid='tests/testInjectionGuards.py::testUndeclaredExpectedPortFailsTheHostCheckClosed',
        source='vaibify/gui/serverMiddleware.py',
        old='    if iExpectedPort is None:\n        return False',
        new='    if iExpectedPort is None:\n        return True',
    ),
    Falsification(
        # Recording the boolean helper's fail-open False turns "docker
        # inspect could not answer" into the asserted fact "this
        # container had network access", inside an L3 attestation.
        nodeid='tests/testAiProvenanceStamp.py::test_unanswerable_isolation_probe_is_recorded_as_unknown',
        source='vaibify/gui/aiProvenanceCapture.py',
        old='        bNetworkIsolatedAtCapture=bIsolated if bAnswered else None,',
        new='        bNetworkIsolatedAtCapture=bIsolated,',
    ),
    Falsification(
        # flags.jsonl and project.json are both container-writable, so
        # editing both leaves them agreeing. Only the host anchor, out
        # of the container's reach, still remembers the erased flags.
        nodeid='tests/testSupervisionAnchor.py::test_truncating_the_log_and_the_count_together_still_fails_the_gate',
        source='vaibify/gui/attributionLog.py',
        old="""    if supervisionAnchor.fbAnchorContradictedBy(
        dictAnchor, listFlags, sHead,
    ):
        return False""",
        new="""    if False:
        return False""",
    ),
    Falsification(
        # A count-only anchor misses an in-place rewrite of a flag's
        # detail, so the anchor pins the chain head digest too.
        nodeid='tests/testSupervisionAnchor.py::test_rewriting_records_in_place_is_caught_by_the_head_digest',
        source='vaibify/gui/supervisionAnchor.py',
        old="""    if len(listFlags) == iAnchored:
        return bool(dictAnchor.get(_S_HEAD_KEY)) and (
            dictAnchor.get(_S_HEAD_KEY) != sHeadSha256
        )""",
        new="""    if len(listFlags) == iAnchored:
        return False""",
    ),
    Falsification(
        # Without monotonicity a truncation writes its own smaller
        # count back and launders itself on the next observation.
        nodeid='tests/testSupervisionAnchor.py::test_anchor_never_lowers_itself',
        source='vaibify/gui/supervisionAnchor.py',
        old="""    if int(dictExisting.get(_S_COUNT_KEY) or 0) > int(iFlagCount):
        return""",
        new="""    if False:
        return""",
    ),
    Falsification(
        # Anchoring every polled project accumulates one host file per
        # repository forever, makes a recreated repo read as tampered,
        # and writes into the developer's home during test runs.
        nodeid='tests/testSupervisionAnchor.py::test_supervision_disabled_writes_no_host_anchor',
        source='vaibify/gui/attributionLog.py',
        old="""    if not fbSupervisionEnabled(dictWorkflow or {}):
        return True""",
        new="""    if False:
        return True""",
    ),
    Falsification(
        # The one reconcile action that left the screen un-repainted.
        nodeid='tests/testSyncEpoch.py::test_verify_remote_bumps_sync_epoch',
        source='vaibify/gui/routes/syncRoutes.py',
        old="""        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictCarried["dictStatus"]""",
        new="""        return dictCarried["dictStatus"]""",
    ),
    Falsification(
        # An out-of-band push produces no HTTP traffic; this route is
        # the only thing that can invalidate an open tab.
        nodeid='tests/testSyncEpoch.py::test_reconcile_remote_state_bumps_sync_epoch',
        source='vaibify/gui/routes/gitRoutes.py',
        # Disambiguated 2026-08-05: the migrated untrack route grew an
        # identical three-line tail, so the snippet now names the
        # verify-status assignment this route alone performs.
        old="""        dictResponse["dictVerifyStatus"] = (
            _fdictReconcileSyncStatusFromVerify(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
            )
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)""",
        new="""        dictResponse["dictVerifyStatus"] = (
            _fdictReconcileSyncStatusFromVerify(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
            )
        )""",
    ),
    Falsification(
        # A file the verify never looked at must not be recorded as
        # synced; equality on the coverage count is what enforces it.
        nodeid='tests/testSyncEpoch.py::test_reconcile_marks_only_paths_the_verify_actually_covered',
        source='vaibify/gui/routes/gitRoutes.py',
        old='    if dictStatus.get("iTotalFiles") != len(listCanonical):',
        new='    if dictStatus.get("iTotalFiles") > len(listCanonical):',
    ),
    Falsification(
        # Sleeping a full cadence first is a first pass the 30-minute
        # idle shutdown guarantees never happens.
        nodeid='tests/testScheduledReverify.py::test_first_reverify_pass_does_not_wait_a_full_cadence',
        source='vaibify/reproducibility/scheduledReverify.py',
        old="""    if fElapsed is None:
        return fStartupDelay""",
        new="""    if fElapsed is None:
        return max(float(fHoursCadence), 0.0) * 3600.0""",
    ),
    Falsification(
        # Without the remaining-cadence term every restart re-verifies
        # every remote within minutes.
        nodeid='tests/testScheduledReverify.py::test_restart_resumes_the_cadence_instead_of_restarting_it',
        source='vaibify/reproducibility/scheduledReverify.py',
        old='    return max(fRemaining, fStartupDelay)',
        new='    return fStartupDelay',
    ),
    Falsification(
        # Inverting the guard skips every real workflow entry, so the
        # scheduled pass invalidates nothing.
        nodeid='tests/testScheduledReverify.py::test_completed_pass_bumps_every_touched_container_sync_epoch',
        source='vaibify/reproducibility/scheduledReverify.py',
        old="""        if isinstance(entryWorkflow, dict):
            continue
        fnBumpSyncEpoch(dictCtx, entryWorkflow[0])""",
        new="""        if not isinstance(entryWorkflow, dict):
            continue
        fnBumpSyncEpoch(dictCtx, entryWorkflow[0])""",
    ),
    Falsification(
        # No stamp means the cadence restarts on every hub start and
        # the dashboard can never say "never run".
        nodeid='tests/testScheduledReverify.py::test_a_completed_pass_persists_its_stamp',
        source='vaibify/reproducibility/scheduledReverify.py',
        old="""        fnRecordLastReverifyIso(_fsBuildIsoTimestamp())
        _fnBumpSyncEpochForVerifiedContainers(dictCtx, listWorkflows)""",
        new="""        _fnBumpSyncEpochForVerifiedContainers(dictCtx, listWorkflows)""",
    ),
    Falsification(
        # The owner map is name-keyed and every URL carries the docker
        # id. Dropping the resolution is the historical fatal bug that
        # a name == id fixture hid behind a green suite.
        nodeid='tests/testLiveSessionBoundary.py::testConnectMintsTheLeaseThatOpensThePipelineWebSocket',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""        sName = fsContainerNameForId(
            dictCtx.get("docker"), sContainerId,
        )""",
        new="""        sName = sContainerId""",
    ),
    Falsification(
        # Without the lease branch, any tab holding the shared token
        # reaches the pipeline of a container another session owns.
        nodeid='tests/testLiveSessionBoundary.py::testPipelineWebSocketRefusesALeaseConnectNeverMinted',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    if not fbCheckBoundLeaseOwnership(
        connection, dictContainerOwners, sName, sBrowserSessionId,
    ):
        return I_REJECT_FOREIGN_LEASE""",
        new="""    if False:
        return I_REJECT_FOREIGN_LEASE""",
    ),
    Falsification(
        # Without the lane budget, a duplicate tab that copied the
        # lease drives runs into the container concurrently.
        nodeid='tests/testLiveSessionBoundary.py::testDuplicateTabPipelineWebSocketIsRefusedOnTheServedApplication',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    if bBrowser and bExclusivePipelineLane and fbRefuseSecondLiveConnection(
        dictContainerOwners, sName,
    ):""",
        new="""    if False:""",
    ),
    Falsification(
        # Budgeting the terminal lane too is the Run-Step-always-refused
        # regression: the terminal strip holds the only slot.
        nodeid='tests/testLiveSessionBoundary.py::testTerminalAndPipelineWebSocketsCoexistOnTheServedApplication',
        source='vaibify/gui/webSocketAuthorization.py',
        old="""    fnIncrementGlobal, fnDecrementGlobal, bExclusivePipelineLane=False,""",
        new="""    fnIncrementGlobal, fnDecrementGlobal, bExclusivePipelineLane=True,""",
    ),
    Falsification(
        # A git checkout stamps every file with the checkout time, so
        # without the restore every attestation dies on a machine hop.
        nodeid='tests/testCrossMachineUserVerification.py::test_fresh_clone_does_not_discard_the_researchers_attestation',
        source='vaibify/gui/fileStatusManager.py',
        old=(
            '        if dictRecorded and dictRecorded == dictCurrent:\n'
            '            dictVerification["sUser"] = "passed"'
        ),
        new=(
            '        if False:\n'
            '            dictVerification["sUser"] = "passed"'
        ),
    ),
    Falsification(
        # Restoring on the mere presence of a recorded hash would
        # launder a real change into a verified state.
        nodeid='tests/testCrossMachineUserVerification.py::test_a_genuinely_changed_plot_stays_stale',
        source='vaibify/gui/fileStatusManager.py',
        old='        if dictRecorded and dictRecorded == dictCurrent:',
        new='        if dictRecorded:',
    ),
    Falsification(
        # Recording while stale would let a changed plot adopt its own
        # new hash and verify itself on the following poll.
        nodeid='tests/testCrossMachineUserVerification.py::test_a_stale_step_never_adopts_the_current_hash_as_verified',
        source='vaibify/gui/fileStatusManager.py',
        old='        if sUser == "passed":',
        new='        if sUser in ("passed", "stale"):',
    ),
    Falsification(
        # Observed on a real machine: sweeping an April-dated token
        # broke a container that had bind-mounted it. Docker then
        # creates a directory stub where the file was.
        nodeid='tests/testEphemeralStore.py::test_sweep_spares_a_stale_file_a_container_still_mounts',
        source='vaibify/config/ephemeralStore.py',
        old=(
            '            if sPath in setProtected:\n'
            '                continue'
        ),
        new=(
            '            if False:\n'
            '                continue'
        ),
    ),

    # ── Documented hard rules that no test could previously falsify ──
    # (2026-07-26 audit). Mutation testing cannot reach this class: it
    # finds weak tests over EXISTING code, and there is no mutant for an
    # enforcement point that was never written.

    Falsification(
        # AGENTS.md "Ask first" names five sensitive categories; the
        # credential manager is the one whose loss is unrecoverable
        # (rotation is the only remediation).
        nodeid='tests/testHarnessHookMutationCoverage.py::testSensitiveEditHookAsksForEveryDocumentedPath',
        source='.claude/hooks/askSensitiveEdit.py',
        old=(
            '    (\n'
            '        r"/vaibify/config/secretManager\\.py$",\n'
            '        "secretManager.py handles credentials. A wrong line '
            'can leak "\n'
            '        "tokens into git history; rotation is the only '
            'remediation. "\n'
            '        "Pausing to confirm.",\n'
            '    ),\n'
        ),
        new='',
    ),
    Falsification(
        # NotebookEdit carries notebook_path, never file_path, so
        # dropping the fallback silently exempts every notebook edit.
        nodeid='tests/testHarnessHookMutationCoverage.py::testSensitiveEditHookReadsTheNotebookPathField',
        source='.claude/hooks/askSensitiveEdit.py',
        old=(
            '    return dictToolInput.get("file_path") or '
            'dictToolInput.get("notebook_path", "")'
        ),
        new='    return dictToolInput.get("file_path", "")',
    ),
    Falsification(
        # A hook that asks for everything is a hook the researcher
        # learns to click through.
        nodeid='tests/testHarnessHookMutationCoverage.py::testSensitiveEditHookLeavesOrdinarySourceFilesAlone',
        source='.claude/hooks/askSensitiveEdit.py',
        old='    return False, ""',
        new='    return True, ""',
    ),
    Falsification(
        nodeid='tests/testHarnessHookMutationCoverage.py::testSensitiveEditHookEmitsTheAskDecisionPayload',
        source='.claude/hooks/askSensitiveEdit.py',
        old='            "permissionDecision": "ask",',
        new='            "permissionDecision": "allow",',
    ),
    Falsification(
        nodeid='tests/testHarnessHookMutationCoverage.py::testDestructiveGitHookDeniesTheDocumentedCommands',
        source='.claude/hooks/blockDestructiveGit.py',
        old=(
            '    (\n'
            '        r"\\bgit\\s+rebase\\s+(?:-i\\b|--interactive\\b)",\n'
            '        "Interactive rebase requires a TTY editor and is not "\n'
            '        "appropriate in an agent session. Run manually.",\n'
            '    ),\n'
        ),
        new='',
    ),
    Falsification(
        # --force-with-lease is the documented escape hatch; denying it
        # leaves the block with no legitimate way past.
        nodeid='tests/testHarnessHookMutationCoverage.py::testDestructiveGitHookPermitsForceWithLease',
        source='.claude/hooks/blockDestructiveGit.py',
        old=r'(?:--force(?!-with-lease)|-f|\+\S+)(?!\S)',
        new=r'(?:--force|-f|\+\S+)',
    ),
    Falsification(
        # AGENTS.md calls these "hard-blocked": an ask-decision turns
        # the block into a prompt an agent can talk its way through.
        nodeid='tests/testHarnessHookMutationCoverage.py::testDestructiveGitHookEmitsTheDenyDecisionPayload',
        source='.claude/hooks/blockDestructiveGit.py',
        old='            "permissionDecision": "deny",',
        new='            "permissionDecision": "ask",',
    ),
    Falsification(
        # Write recreates a file wholesale; a matcher narrowed to Edit
        # lets the highest-risk operation past a hook that still looks
        # installed.
        nodeid='tests/testHarnessHookMutationCoverage.py::testHookSettingsRegisterBothPreToolUseHooks',
        source='.claude/settings.json',
        old='"matcher": "Edit|Write|NotebookEdit",',
        new='"matcher": "Edit",',
    ),
    Falsification(
        # docs/reproducibility.md: sSourceUrl is inert metadata, never
        # fetched. It arrives from a project.json the in-container agent
        # can write, so any dereference is agent-driven SSRF.
        nodeid='tests/testProvenanceContractMutationCoverage.py::testRemoteSourceUrlIsNeverDereferencedByVaibifySource',
        source='vaibify/gui/pipelineRunner.py',
        old='        sSha = dictShaByPath.get(dictRemote.get("sPath", ""))',
        new=(
            '        sSha = dictShaByPath.get('
            'dictRemote.get("sSourceUrl", ""))'
        ),
    ),
    Falsification(
        # docs/reproducibility.md: vaibify never stores tokens in
        # environment variables (readable via /proc, docker inspect).
        nodeid='tests/testProvenanceContractMutationCoverage.py::testCredentialsAreNeverWrittenIntoEnvironmentVariables',
        source='vaibify/docker/dockerConnection.py',
        old='            os.environ["DOCKER_HOST"] = sHost',
        new='            os.environ["GITHUB_TOKEN"] = sHost',
    ),
    Falsification(
        # The guard had zero coverage before this entry: dropping the
        # call renames the step while its directory, test marker and
        # manifest rows stay under the old slug.
        nodeid='tests/testStepRenameCascadeMutationCoverage.py::testGenericStepUpdateRefusesARename',
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '        _fnRejectContractBreakingUpdates(\n'
            '            dictWorkflow, iStepIndex, dictUpdates,\n'
            '        )\n'
        ),
        new='',
    ),
    Falsification(
        # Containment instead of equality: "analysis/Corner" would pass
        # against the slug "CornerPlot".
        nodeid='tests/testStepRenameCascadeMutationCoverage.py::testGenericStepUpdateRefusesADirectoryOffTheSlug',
        source='vaibify/gui/routes/stepRoutes.py',
        old='                and posixpath.basename(sDirectory) != sSlug:',
        new='                and posixpath.basename(sDirectory) not in sSlug:',
    ),
    Falsification(
        # The contract frees the PARENT path; a guard that refuses every
        # sDirectory edit blocks legitimate reorganisation while looking
        # like correct enforcement.
        nodeid='tests/testStepRenameCascadeMutationCoverage.py::testGenericStepUpdateStillMovesTheParentPath',
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '        if sDirectory and "{" not in sDirectory \\\n'
            '                and posixpath.basename(sDirectory) != sSlug:'
        ),
        new='        if sDirectory:',
    ),
    Falsification(
        # git accepts the flag after the remote and refspec, so the
        # positional pattern never blocked the ordinary invocation.
        nodeid='tests/testForcePushArgumentOrder.py::testForcePushIsBlockedAnywhereInTheArgumentList',
        source='.claude/hooks/blockDestructiveGit.py',
        old=r'r"\bgit\s+push\b[^;&|]*?(?<!\S)"',
        new=r'r"\bgit\s+push\s+"',
    ),
    Falsification(
        # Two guards protect --force-with-lease and each is
        # sufficient alone: the (?!-with-lease) lookahead AND the
        # trailing (?!\S), which fails because a "-" follows --force
        # in the lease form. Removing either one alone therefore
        # SURVIVES -- verified, not assumed. The mutation recorded
        # here removes both, which is the only change that actually
        # blocks the documented escape hatch.
        nodeid='tests/testForcePushArgumentOrder.py::testLeaseExemptionSurvivesTheWidenedScan',
        source='.claude/hooks/blockDestructiveGit.py',
        old=r'r"(?:--force(?!-with-lease)|-f|\+\S+)(?!\S)",',
        new=r'r"(?:--force|-f|\+\S+)",',
    ),
    Falsification(
        # The mirror painting a directory name the backend would never
        # create is the exact drift AGENTS.md's "never write a second
        # derivation" exists to prevent, and the pre-existing guard
        # (source contains toUpperCase and slice(1)) survives it.
        nodeid='tests/testStepSlugMirrorEquivalence.py::testJavascriptMirrorBodyMatchesItsPin',
        source='vaibify/gui/static/scriptUtilities.js',
        old='sWord.slice(1)',
        new='sWord.slice(1).toLowerCase()',
    ),
    Falsification(
        # The decision is asserted as a pure value rather than through
        # pytest.skip/pytest.fail on purpose: a guard broken the
        # obvious way turns the run into a SKIP, and a skipped test is
        # not a failed one, so an outcome-only test would score this
        # mutant as surviving.
        nodeid='tests/testDockerLiveDaemonRequirement.py::test_demanded_but_unreachable_daemon_resolves_to_failure',
        source='tests/testDockerConnectionLive.py',
        old="""    if bDemanded:
        return S_OUTCOME_FAIL""",
        new="""    if bDemanded:
        return S_OUTCOME_SKIP""",
    ),
    Falsification(
        # Reintroduces the exact shell guard that made a job
        # advertised as live-Docker coverage report success for
        # having run nothing.
        nodeid='tests/testDockerLiveDaemonRequirement.py::test_no_workflow_swallows_an_unreachable_docker_daemon',
        source='.github/workflows/tests-linux.yml',
        old='          python -m pytest tests/ -m docker_live --tb=short -v',
        new='          docker info >/dev/null 2>&1 || { echo "skipping"; exit 0; }\n          python -m pytest tests/ -m docker_live --tb=short -v',
    ),
    Falsification(
        # The route accepted the list, wrote it to vaibify.yml, and the
        # build dropped it silently. Removing the guard restores that.
        nodeid='tests/testCreationWizardRoutes.py::testCreateProjectRejectsCondaPackages',
        source='vaibify/gui/registryRoutes.py',
        old="""        _fnRejectUninstallablePackages(request.listCondaPackages)
        _fnRejectDuplicateProjectName(request.sProjectName)""",
        new="""        _fnRejectDuplicateProjectName(request.sProjectName)""",
    ),
    Falsification(
        # Every L2 composition fixture writes the same sha on both
        # sides of the comparison, so removing the GitHub conjunct
        # entirely leaves them all green. Only a drifting sha sees it.
        nodeid='tests/testLevelGates.py::test_fbAtLeastLevel2_committed_sha_drift_blocks_l2',
        source='vaibify/reproducibility/levelGates.py',
        old="""    if not fbWorkflowFullySyncedWithGithub(
        dictWorkflow, filesRepo,
    ):
        return False
    if not fbWorkflowFullySyncedWithZenodo(""",
        new="""    if not fbWorkflowFullySyncedWithZenodo(""",
    ),
    Falsification(
        # Restores the claim the docs carried for months: that the
        # mutation gate grades every pull request. It does not.
        nodeid='tests/testDocsMatchWorkflowTriggers.py::test_documented_mutation_trigger_matches_the_workflow',
        source='docs/testing.md',
        old="| `mutation.yml` | the cosmic-ray gate on a branch's changed lines (warn-only) | manual (`workflow_dispatch`) |",
        new="| `mutation.yml` | the cosmic-ray gate on a PR's changed lines (warn-only) | on pull requests |",
    ),
    Falsification(
        # Strips the UNREACHABLE note while the module still has no
        # product caller -- the state the docs were in for months.
        nodeid='tests/testOrphanedPublishMachinery.py::testUnreachableGeneratorSaysSoOrGainsACaller',
        source='vaibify/reproducibility/githubWorkflow.py',
        old='UNREACHABLE -- no product code imports this module.',
        new='Reachable from the GUI publish pane.',
    ),
    Falsification(
        # Declares a gitignored generated copy as a build input. The
        # artifact is absent on any clean checkout, so a test that only
        # inspected RESOLVED paths scored this mutant as surviving --
        # which is exactly what the harness reported the first time.
        nodeid='tests/testBuildInputHash.py::testGeneratedBuildContextCopiesAreNotKeyed',
        source='tools/computeBuildInputHash.py',
        # Was director.py until 2026-08-08. That module was deleted in
        # 6e55cbf, so the mutation added a path listGenerated does not
        # contain and the assertion could not see it -- the entry named
        # a file that no longer exists and scored SURVIVED for that
        # reason alone. zenodoClient.py is what the test's own docstring
        # names, and it is a real generated copy.
        old='    "vaibify/containerImage/vaibifyDo.py",',
        new='    "vaibify/containerImage/vaibifyDo.py",\n    "vaibify/containerImage/zenodoClient.py",',
    ),
    Falsification(
        # Turns the browser lane's fail-closed adapter into the
        # permissive mock it exists not to become.
        nodeid='tests/testBrowserLaneContract.py::testTheFakeRaisesRatherThanInventingAnAnswer',
        source='tests/browser/fakeDockerAdapter.py',
        # Anchored on the line BEFORE the raise, not on the raise's
        # first two lines: splitting a multi-line string literal left a
        # dangling quote, so the mutation could not compile and the
        # harness scored it ERROR -- never KILLED -- for two commits.
        old="""            return (0, "{}")
        raise UnmodelledContainerCall(""",
        new="""            return (0, "{}")
        return (0, "")
        raise UnmodelledContainerCall(""",
    ),
    Falsification(
        # A verb-only match answers 0 for any path at all -- the
        # permissive behaviour this fake exists not to have.
        nodeid='tests/testBrowserLaneContract.py::testModelledCommandsValidateTheirArgumentsNotJustTheVerb',
        source='tests/browser/fakeDockerAdapter.py',
        old='        if S_WORKSPACE_ROOT not in sCommand:',
        new='        if False:',
    ),
    Falsification(
        # Drops a build input from the fresh-build trigger while the
        # hash still covers it -- the drift that let a script COPYed
        # into the image merge with no fresh build.
        nodeid='tests/testBuildInputHash.py::testFreshBuildTriggerCoversEveryHashInput',
        source='.github/workflows/freshImageBuild.yml',
        old="      - 'vaibify/reproducibility/credentialRedactor.py'\n",
        new="",
    ),
    Falsification(
        # Moves the host CLI onto the in-container agent's credential —
        # the wrong principal, and one the catalog deliberately fences.
        nodeid='tests/testCliHubSessionMutationCoverage.py::test_the_cli_authenticates_as_the_researcher_not_as_the_agent',
        source='vaibify/cli/hubSession.py',
        old='S_BROWSER_TOKEN_HEADER = "X-Session-Token"',
        new='S_BROWSER_TOKEN_HEADER = "X-Vaibify-Session"',
    ),
    Falsification(
        # Sends the lease where the route does not read it, so the
        # release succeeds with bReleased false and the container stays
        # held. Observed live before this test existed.
        nodeid='tests/testCliHubSessionMutationCoverage.py::test_release_sends_the_lease_where_the_route_reads_it',
        source='vaibify/cli/hubSession.py',
        old="""            None, F_BOOTSTRAP_TIMEOUT_SECONDS,
            sLeaseId=dictSession["sLeaseId"],
        )
    except HubSessionError as error:""",
        new="""            {"sLeaseId": dictSession["sLeaseId"]},
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        )
    except HubSessionError as error:""",
    ),
    Falsification(
        # Paths a route by the lease's key (container name) instead of
        # the docker id the exec behind it needs.
        nodeid='tests/testCliHubSessionMutationCoverage.py::test_generated_paths_carry_the_container_id_not_the_name',
        source='vaibify/cli/actionCommands.py',
        old='    dictValues["sContainerId"] = dictSession["sContainerId"]',
        new='    dictValues["sContainerId"] = dictSession["sContainerName"]',
    ),
    Falsification(
        # An agent that gets skills installed but no path to the
        # project's guidance starts with no instructions at all --
        # silently. Exactly how Cline shipped.
        nodeid='tests/testEntrypointAgentDocLinks.py::testEveryAgentWithSkillsAlsoHasADocPath',
        source='vaibify/containerImage/entrypoint.sh',
        old='    for sAgent in claude codex gemini opencode cline openhands pi; do',
        new='    for sAgent in claude codex gemini opencode cline openhands pi newagent; do',
    ),
    # ORPHANED_SESSION slice 4 (design §9, falsification case 19 claim
    # half): neutralizing the cardinality read-check lets one browser
    # session accumulate two owner records.
    Falsification(
        nodeid='tests/testSessionCardinality.py::test_second_claim_by_the_same_session_is_refused',
        source='vaibify/gui/containerOwnership.py',
        old='    if sHeldElsewhereName:\n        return (409, _fdictCardinalityRefused(sName, sHeldElsewhereName))',
        new='    if False:\n        return (409, _fdictCardinalityRefused(sName, sHeldElsewhereName))',
    ),
    Falsification(
        # The concurrent half of case 19: with the read-check gone, the
        # same-session race on two different containers grants both.
        nodeid='tests/testSessionCardinality.py::test_concurrent_claims_on_two_containers_resolve_to_one_record',
        source='vaibify/gui/containerOwnership.py',
        old='    if sHeldElsewhereName:\n        return (409, _fdictCardinalityRefused(sName, sHeldElsewhereName))',
        new='    if False:\n        return (409, _fdictCardinalityRefused(sName, sHeldElsewhereName))',
    ),
    Falsification(
        # The viewer first-connect creation path has its own guard; the
        # claim-route check cannot cover it.
        nodeid='tests/testSessionCardinality.py::test_viewer_first_connect_refuses_a_session_holding_another_container',
        source='vaibify/gui/pipelineServer.py',
        old='    if sHeldElsewhereName:\n        raise HTTPException(',
        new='    if False:\n        raise HTTPException(',
    ),
    # ORPHANED_SESSION slice 3 sub-step 3a (design §8/§13): the
    # write-ahead operation journal and its quarantine. Case 27 —
    # acquisition must consult the journal atomically with the fresh
    # flock; bypassing the consult reverts to the silent dead-PID reap.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_quarantine_survives_hub_sigkill_and_blocks_the_next_claim',
        source='vaibify/config/containerLock.py',
        old='        if fileHandle is not None:\n            return _ffileRefuseUnsettledJournal(\n                fileHandle, sProjectName, connectionDocker,\n            )',
        new='        if fileHandle is not None:\n            return fileHandle',
    ),
    # Case 36, auto-clear half: a provably-dead, provably-settled
    # leftover must clear automatically, or every interrupted multi-hour
    # run demands a human and the gate becomes a rubber stamp.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_auto_tier_clears_a_provably_dead_leftover_with_a_logged_note',
        source='vaibify/config/operationJournal.py',
        old='    if dictProbe["bSettled"]:\n        return ("settled", dictProbe["sDetail"])',
        new='    if dictProbe["bSettled"]:\n        return ("quarantinePermanent", dictProbe["sDetail"])',
    ),
    # Case 36, busy half: a live IN_FLIGHT holder is in use, never
    # quarantined; quarantining it would poison a working run.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_live_in_flight_holder_reads_busy_never_quarantined',
        source='vaibify/config/operationJournal.py',
        old='    if dictProbe["bHolderAlive"]:\n        return ("busy", dictProbe["sDetail"])',
        new='    if dictProbe["bHolderAlive"]:\n        return ("quarantinePermanent", dictProbe["sDetail"])',
    ),
    # Case 37, fail-closed half: with the malformed branch disabled, a
    # damaged journal falls through to the valid-records path with zero
    # records and resolves SETTLED — damage reading as clean.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_malformed_unreadable_and_newer_journals_read_quarantined',
        source='vaibify/config/operationJournal.py',
        old='    if dictOutcomeRead["sReadState"] == "malformed":',
        new='    if False and dictOutcomeRead["sReadState"] == "malformed":',
    ),
    # Case 37, durability half: writing in place instead of staging into
    # a temp file lets a crash mid-write destroy the previous journal.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_journal_survives_a_torn_write',
        source='vaibify/config/operationJournal.py',
        old='    sTemporaryPath = f"{sPath}{_S_TEMPORARY_WRITE_SUFFIX}.{os.getpid()}"',
        new='    sTemporaryPath = sPath',
    ),
    # Case 42, semantics half: an ordinary write that treats a damaged
    # journal as fresh silently replaces (and so clears) the quarantine
    # marker — the acknowledge-shaped clear the design forbids.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_newer_version_requires_upgrade_and_malformed_refuses_writes',
        source='vaibify/config/operationJournal.py',
        old='''    if dictOutcomeRead["sReadState"] != "valid":
        raise OperationJournalUnreadableError(
            f"The operation journal for container '{sContainerName}' is "
            f"{dictOutcomeRead['sReadState']} and refuses ordinary writes "
            f"({dictOutcomeRead['sDetail']}); reconciliation is required."
        )''',
        new='''    if dictOutcomeRead["sReadState"] != "valid":
        return _fdictBuildEmptyPayload(sContainerName)''',
    ),
    # Case 45, journal half (the carrier half lands with sub-step 3b):
    # replacing the record SET on every prepare loses coexistence, so a
    # pipeline task, a terminal exec, and a file write cannot be
    # journaled at once.
    Falsification(
        nodeid='tests/testOperationJournalMutationCoverage.py::test_journal_is_a_set_and_claimable_only_when_every_record_settles',
        source='vaibify/config/operationJournal.py',
        old='        dictPayload["dictOperations"][sOperationId] = dictRecord',
        new='        dictPayload["dictOperations"] = {sOperationId: dictRecord}',
    ),
    # Case 39: without the journal entry in the home-relative denylist,
    # a mount of ~/.vaibify (or the journal itself) reaches the
    # quarantine markers from inside a container.
    Falsification(
        nodeid='tests/testBindMountValidator.py::test_journal_directory_mount_is_rejected_in_every_direction',
        source='vaibify/config/bindMountValidator.py',
        old='''    ".kube",
    # The operation-journal quarantine markers (design §8): a same-UID
    # agent inside a container that could mount this directory could
    # delete a quarantine marker and un-quarantine a container whose
    # past operations were never proven settled.
    ".vaibify/journal",''',
        new='''    ".kube",''',
    ),

    # ORPHANED_SESSION slice 3b — the commit-guard carrier (design §8).
    # Case 16, mode (a): with the write-funnel gate removed, a dummy
    # route's direct container write reaches put_archive unadmitted.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_route_write_without_carrier_admission_is_refused_mode_a',
        source='vaibify/docker/dockerConnection.py',
        old='''        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileViaTar",
        )''',
        new='''        pass''',
    ),
    # Case 16, mode (b): inverting the enforced-lane check makes the
    # funnel a no-op exactly where it must fail closed, so a write
    # laundered across asyncio.to_thread lands unadmitted.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_route_write_without_carrier_admission_is_refused_mode_b',
        source='vaibify/config/mutationAdmission.py',
        old='''    if not fbLaneEnforced():
        return
    admission = fadmissionActiveForContainerId(sContainerId)''',
        new='''    if fbLaneEnforced():
        return
    admission = fadmissionActiveForContainerId(sContainerId)''',
    ),
    # Case 16c: with the durable-launch gate removed, a route reaches
    # exec_create without a carrier-minted mode-(c) guard.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_route_durable_exec_without_mode_c_guard_is_refused',
        source='vaibify/docker/dockerConnection.py',
        old='''        mutationAdmission.fnAssertDurableExecAdmitted(
            sContainerId, "ftRunInContainerStreamedWithChunks",
        )''',
        new='''        pass''',
    ),
    # Case 16b: without the shield, cancelling the requesting coroutine
    # cancels the supervisor, the drain frees while the worker thread
    # keeps running, and a competitor acquires mid-effect.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_cancelled_requester_leaves_drain_held_until_worker_ends',
        source='vaibify/gui/commitCarrier.py',
        old='    return await asyncio.shield(taskSupervisor)',
        new='    return await taskSupervisor',
    ),
    # Case 26: without the retained-names skip, hub shutdown frees the
    # flock of a container whose guarded worker is still live, handing
    # it to the next hub mid-commit.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_hub_shutdown_retains_flock_while_guarded_worker_lives',
        source='vaibify/gui/appFactory.py',
        old='''        for sName, recordOwner in list(dictContainerOwners.items()):
            if sName in setRetainedNames:
                continue''',
        new='''        for sName, recordOwner in list(dictContainerOwners.items()):''',
    ),
    # Case 31 (carrier half): a cancel plane that settles the journal
    # record itself clears the write-ahead record while the worker can
    # still commit — the supervisor must be the single settler.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_out_of_band_cancel_leaves_supervisor_as_single_releaser',
        source='vaibify/gui/commitCarrier.py',
        old='''        supervisor.eventCancelRequested.set()
        _fnMarkSupervisorCancelRequested(supervisor)''',
        new='''        supervisor.eventCancelRequested.set()
        _fnMarkSupervisorCancelRequested(supervisor)
        operationJournal.fnSettleOperation(sName, supervisor.sOperationId)''',
    ),
    # Case 32 (carrier half): dropping the carrier veto lets the idle
    # reaper force-release an owner whose guarded worker is live.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_reaper_never_releases_owner_with_live_guarded_work',
        source='vaibify/gui/serverLifespan.py',
        old='''    def fbGuardedWorkLive(sName):
        return (
            commitCarrier.fbContainerHasLiveMutationWork(app.state, sName)
            or _fbOwnedNamePipelineRunning(app, dictCtx, sName)
        )''',
        new='''    def fbGuardedWorkLive(sName):
        return _fbOwnedNamePipelineRunning(app, dictCtx, sName)''',
    ),
    # Case 38 (holder half): neutralizing the holder comparison admits
    # any holder under a merely-present record.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_identity_gate_admits_own_record_and_refuses_foreign_holder',
        source='vaibify/config/mutationAdmission.py',
        old='''    for sIdentityKey, valueExpected in (dictHolderIdentity or {}).items():
        if dictOwnRecord.get(sIdentityKey) != valueExpected:''',
        new='''    for sIdentityKey, valueExpected in (dictHolderIdentity or {}).items():
        if False and dictOwnRecord.get(sIdentityKey) != valueExpected:''',
    ),
    # Case 38 (quarantine half): neutralizing the NEEDS_RECONCILIATION
    # scan lets a sitting owner resume mid-quarantine.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_identity_gate_refuses_sitting_owner_mid_quarantine',
        source='vaibify/config/mutationAdmission.py',
        old='''    for sRecordId, dictRecord in dictOperations.items():
        if dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
        ):''',
        new='''    for sRecordId, dictRecord in dictOperations.items():
        if False and dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
        ):''',
    ),
    # Case 45 (carrier half): an added presence-based refusal denies an
    # operation its own record whenever any other record coexists.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_carrier_admits_each_operation_against_its_own_record',
        source='vaibify/config/mutationAdmission.py',
        old='    dictOwnRecord = dictOperations.get(sOperationId)',
        new='''    if len(dictOperations) > 1:
        raise MutationNotAdmittedError(
            "presence-based refusal (mutant)"
        )
    dictOwnRecord = dictOperations.get(sOperationId)''',
    ),
    # Case 41 (gate part): reordering the helper stub to act BEFORE
    # reading the stdin gate lets a killed parent leave a landed effect
    # with no identity-persisted releasing gate.
    Falsification(
        nodeid='tests/testCommitCarrier.py::test_parent_kill_at_each_two_phase_transition_leaves_no_actor',
        source='vaibify/gui/commitCarrier.py',
        old='''S_GATED_HELPER_STUB = (
    "import os, sys, subprocess\\n"
    "sGateLine = sys.stdin.readline()\\n"
    "if sGateLine.strip() != 'GO':\\n"
    "    sys.exit(3)\\n"
    "sys.exit(subprocess.call(sys.argv[1:]))\\n"
)''',
        new='''S_GATED_HELPER_STUB = (
    "import os, sys, subprocess\\n"
    "iExitCode = subprocess.call(sys.argv[1:])\\n"
    "sGateLine = sys.stdin.readline()\\n"
    "if sGateLine.strip() != 'GO':\\n"
    "    sys.exit(3)\\n"
    "sys.exit(iExitCode)\\n"
)''',
    ),

    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_reconcile_cli_clears_a_sigkill_quarantine_and_restores_claim',
        source='vaibify/config/reconciliation.py',
        old='''    operationJournal.fnClearOperationsReconciled(
        sContainerName, listClearableOperationIds,
    )''',
        new='''    del sContainerName, listClearableOperationIds''',
    ),
    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_reconciliation_refuses_while_the_recorded_writer_lives',
        source='vaibify/config/reconciliation.py',
        old='''    if dictProbe["bHolderAlive"]:
        return (
            False,
            f"the recorded writer is still alive ({dictProbe['sDetail']}); "
            "reconciliation cannot clear a quarantine over a live writer",
        )''',
        new='''    if dictProbe["bHolderAlive"]:
        return (True, dictProbe["sDetail"])''',
    ),
    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_reconcile_versus_claim_is_atomic_on_the_container_flock',
        source='vaibify/config/containerLock.py',
        old='''    for _ in range(_I_MAX_ACQUIRE_ATTEMPTS):
        fileHandle = _ffileTryAcquireFlock(sPath, sProjectName, iPort)
        if fileHandle is not None:
            return fileHandle
    raise ContainerLockedError(sProjectName, 0, 0)''',
        new='''    return _ffileOpenLockFileNoFollow(sPath)''',
    ),
    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_a_stale_reconciliation_cannot_clear_a_successor_record',
        source='vaibify/config/reconciliation.py',
        old='    if set(dictOperations) != set(setExpectedOperationIds or ()):',
        new='    if False:',
    ),
    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_a_newer_version_journal_requires_upgrade_never_a_blind_clear',
        source='vaibify/config/operationJournal.py',
        old='''    if sReadState == "requiresUpgrade":
        raise OperationJournalUnreadableError(''',
        new='''    if False:
        raise OperationJournalUnreadableError(''',
    ),
    Falsification(
        nodeid='tests/testReconciliationMutationCoverage.py::test_break_glass_clears_only_the_malformed_record_it_names',
        source='vaibify/config/operationJournal.py',
        old='''    sActualSha256 = fsComputeJournalFileSha256(sContainerName)
    if sActualSha256 != sExpectedSha256:''',
        new='''    sActualSha256 = fsComputeJournalFileSha256(sContainerName)
    if False:''',
    ),

    # --- Slice 3d: terminal-exec containment (design v13 §6.1/§7/§10). ---
    # The terminal exec id must be durable BEFORE exec_start (the
    # create -> journal -> start split applied to terminals); the
    # mutation starts the exec first, leaving a crash window with an
    # unidentified writer.
    Falsification(
        nodeid='tests/testTerminalContainment.py::test_start_journals_the_exec_id_before_exec_start',
        source='vaibify/gui/terminalSession.py',
        old='''        terminalContainment.fnPromoteTerminalOperation(
            sContainerName, sOperationId, self._sExecId,
            self._sContainerId, self._dictContainment["iOwnerGeneration"],
        )
        self._socketExec = (
            self._connectionDocker.fsocketExecStart(self._sExecId)
        )''',
        new='''        self._socketExec = (
            self._connectionDocker.fsocketExecStart(self._sExecId)
        )
        terminalContainment.fnPromoteTerminalOperation(
            sContainerName, sOperationId, self._sExecId,
            self._sContainerId, self._dictContainment["iOwnerGeneration"],
        )''',
    ),
    # Terminate-and-prove must PROVE the group empty; the mutation is
    # the optimistic proceed the design forbids (v13: "never an
    # optimistic proceed").
    Falsification(
        nodeid='tests/testTerminalContainment.py::test_surviving_group_member_quarantines_never_settles',
        source='vaibify/gui/terminalContainment.py',
        old='''def _fbProbeProvesEmpty(dictProbe):
    """Return True only for a conclusive zero-member probe."""
    return bool(dictProbe.get("bConclusive")) and (
        dictProbe.get("iMemberCount") == 0
    )''',
        new='''def _fbProbeProvesEmpty(dictProbe):
    """Return True only for a conclusive zero-member probe."""
    return True''',
    ),
    # The codex-round-12 hole, unit half (case 43): settling a terminal
    # record on ``exec_inspect Running == false`` alone lets a detached
    # signal-trapping descendant write after the record clears. The
    # real-container half is tests/testTerminalContainmentLive.py.
    Falsification(
        nodeid='tests/testTerminalContainment.py::test_terminal_probe_refuses_to_settle_on_exec_dead_alone',
        source='vaibify/config/operationJournal.py',
        old='''    if not dictExecProbe["bSettled"]:
        return dictExecProbe
    return _fdictProbeTerminalGroupEmptiness(dictRecord, connectionDocker)''',
        new='''    if not dictExecProbe["bSettled"]:
        return dictExecProbe
    return dictExecProbe''',
    ),
    # Shutdown half of case 44 (unit): the flock-release hook must skip
    # a container whose terminal group may still write, exactly as it
    # skips live mutation work.
    Falsification(
        nodeid='tests/testTerminalContainment.py::test_shutdown_retains_the_flock_of_a_live_terminal_container',
        source='vaibify/gui/appFactory.py',
        old='''        setRetainedNames = commitCarrier.fsetNamesWithLiveMutationWork(
            app.state,
        ) | terminalContainment.fsetNamesWithLiveTerminalRecords(
            app.state,
        )''',
        new='''        setRetainedNames = commitCarrier.fsetNamesWithLiveMutationWork(
            app.state,
        )''',
    ),
    # A closed socket is not a dead terminal (design §7): the run
    # loop's teardown must terminate-and-prove, not merely send exit
    # keystrokes and close the socket.
    Falsification(
        nodeid='tests/testTerminalContainment.py::test_socket_close_drains_the_containment_record',
        source='vaibify/gui/pipelineServer.py',
        old='''        taskReader.cancel()
        await asyncio.to_thread(
            terminalContainment.fdictDrainSessionRecord, session,
        )
        session.fnClose()''',
        new='''        taskReader.cancel()
        session.fnClose()''',
    ),

    # --- Slice 3d, real-container halves (cases 43, 44, 45). These
    # five tests are docker_live-marked: they SKIP without a reachable
    # daemon (VAIBIFY_REQUIRE_DOCKER_DAEMON turns the skip into a
    # failure in the opt-in CI job), so kill-confirming them REQUIRES
    # a live daemon — under a daemon-less reconfirm run the mutant
    # survives vacuously via the skip, which is a limit of the
    # harness, not of the tests. Each was kill-confirmed by hand
    # against Docker 28.x. Case 43's transfer-commit half and case
    # 44's transfer/expiry halves land with slices 5 and 6.
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_prover_reports_survivors_after_exec_inspect_says_dead',
        source='vaibify/docker/dockerConnection.py',
        old='''        return {
            "bConclusive": True, "iMemberCount": iMemberCount,
            "sDetail": f"{iMemberCount} live member(s)",
        }''',
        new='''        return {
            "bConclusive": True, "iMemberCount": 0,
            "sDetail": f"{iMemberCount} live member(s)",
        }''',
    ),
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_release_kills_the_detached_descendant_or_quarantines',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    await asyncio.to_thread(
        terminalContainment.fdictDrainTerminalRecordsForContainer,
        appState, sName,
    )
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)''',
        new='''    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)''',
    ),
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_reaper_kills_the_detached_descendant_or_quarantines',
        source='vaibify/gui/serverLifespan.py',
        old='''    _fnDrainTerminalsOfReapableOwners(
        app, dictContainerOwners, fbGuardedWorkLive,
    )
    containerOwnership.flistReapIdleOwnerships(
        dictContainerOwners,
        lambda sName: (
            fbGuardedWorkLive(sName)
            or terminalContainment.fbContainerHasLiveTerminalRecords(
                app.state, sName,
            )
            or _fbOrphanedOwnerJournalUnsettled(dictContainerOwners, sName)
        ),
        dictSessionOwner=getattr(app.state, "dictSessionOwner", None),
    )''',
        new='''    containerOwnership.flistReapIdleOwnerships(
        dictContainerOwners,
        lambda sName: fbGuardedWorkLive(sName),
        dictSessionOwner=getattr(app.state, "dictSessionOwner", None),
    )''',
    ),
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_shutdown_drain_kills_the_detached_descendant_or_quarantines',
        source='vaibify/gui/appFactory.py',
        old='''    async def fnDrainGuardedMutations(app):
        await commitCarrier.fdictDrainMutationSupervisors(app.state)
        await asyncio.to_thread(
            terminalContainment.fdictDrainAllTerminalRecords, app.state,
        )''',
        new='''    async def fnDrainGuardedMutations(app):
        await commitCarrier.fdictDrainMutationSupervisors(app.state)''',
    ),
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_two_real_terminals_and_a_pipeline_record_settle_independently',
        source='vaibify/config/operationJournal.py',
        old='''                "reconciliation transaction instead"
            )
        del dictPayload["dictOperations"][sOperationId]
        _fnStoreJournalPayload(sContainerName, dictPayload)''',
        new='''                "reconciliation transaction instead"
            )
        dictPayload["dictOperations"] = {}
        _fnStoreJournalPayload(sContainerName, dictPayload)''',
    ),

    # --- Slice 5: the host-authorized transfer transaction (design
    # §6.1/§6.2). Cases 2/3/4/5/6/8/12/14/15/23/26b/31/44/46, transfer
    # halves. Three of these conditions are checked at BOTH the pre-mint
    # layer and the commit point, so disabling one copy changes nothing
    # observable and reads as SURVIVED.
    #
    # These entries originally mutated the pre-mint copy alone and
    # detected it through a DRAINING side effect: a doomed transfer must
    # not drain the sitting owner's terminals, so the backstop's refusal
    # arrived with the terminals already gone. That phase was later
    # removed -- a transfer now refuses a busy container at once and
    # never waits for a drain (AGENTS.md, "A busy container refuses a
    # hand-over at once") -- which silently made the discriminator
    # vacuous and the three entries unkillable. They survived for a week
    # while reading as three undefended ownership guards.
    #
    # So the mutation is now "the guard is gone", every copy, with the
    # count stated. Where two guards can still produce the same refusal,
    # the TEST discriminates on the message that names the cause.
    # Case 2 (stale-generation refusal, the ABA guard):
    Falsification(
        nodeid='tests/testHostTransfer.py::testStaleGenerationTransferIsRefused',
        source='vaibify/gui/sessionLifecycle.py',
        old='    if recordOwner.iOwnerGeneration != iExpectedGen:',
        new='    if False:',
        iExpectedOccurrences=2,
    ),
    # Case 2/15 (ACTIVE transfer revokes the old session in-commit):
    Falsification(
        nodeid='tests/testHostTransfer.py::testCorrectGenerationActiveTransferSucceedsAndRevokes',
        source='vaibify/gui/sessionLifecycle.py',
        old='    browserSession.fbRevokeSessionById(dictStore, sOldSessionId)',
        new='    pass',
    ),
    # Case 3 (bounded replay returns the STORED tuple):
    Falsification(
        nodeid='tests/testHostTransfer.py::testLostTransferResponseReplaysTheStoredTuple',
        source='vaibify/gui/browserSession.py',
        old='        recordCap.sIssuedLease = sLeaseId',
        new='        recordCap.sIssuedLease = ""',
    ),
    # Case 14 (reaped record answers "claim normally", never retry):
    Falsification(
        nodeid='tests/testHostTransfer.py::testReapedRecordYieldsClaimNormally',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None:
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_UNOWNED, {''',
        new='''    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None:
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_BUSY_RETRY, {''',
    ),
    # Case 26b (a poisoned record refuses every transfer):
    Falsification(
        nodeid='tests/testHostTransfer.py::testPoisonedRecordRefusesTransfer',
        source='vaibify/gui/sessionLifecycle.py',
        old='    if getattr(recordOwner, "poison", None) is not None:',
        new='    if False:',
        iExpectedOccurrences=2,
    ),
    # Case 31 (a cancel that won the lock blocks the transfer):
    Falsification(
        nodeid='tests/testHostTransfer.py::testCancelRequestedDurableTaskRefusesTransfer',
        source='vaibify/gui/sessionLifecycle.py',
        old='    if recordTask is not None and recordTask.sState != "running":',
        new='    if recordTask is not None and False:',
        iExpectedOccurrences=2,
    ),
    # Case 23 (the barrier test: adoption, not a blanket live-task
    # refusal — the exact "different operation ⇒ refuse" mistake the
    # §8 adoption exception exists to prevent):
    Falsification(
        nodeid='tests/testHostTransfer.py::testBarrierTransferAdoptsAStillRunningDurableTask',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    recordTask = _frecordLiveDurableTask(appState, sName)
    if recordTask is not None and recordTask.sState != "running":
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a durable task whose "
                        "cancellation is in progress; retry once it has "
                        "settled.",
        })
    sJournalReason = _fsUnadoptableJournalReason(appState, sName, recordTask)''',
        new='''    recordTask = _frecordLiveDurableTask(appState, sName)
    if recordTask is not None:
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a durable task whose "
                        "cancellation is in progress; retry once it has "
                        "settled.",
        })
    sJournalReason = _fsUnadoptableJournalReason(appState, sName, recordTask)''',
    ),
    # Case 5 (the preserved task is retagged in place, not left stale):
    Falsification(
        nodeid='tests/testHostTransfer.py::testPreservedTaskCompletesAttributedToNewGeneration',
        source='vaibify/gui/sessionLifecycle.py',
        old='    _fnRetagLiveDurableTask(appState, sName, iNewGeneration)',
        new='    pass',
    ),
    # Case 4 (an old-generation ordinary mutation fails at the
    # carrier's linearization check after a REAL transfer):
    Falsification(
        nodeid='tests/testHostTransfer.py::testOldLaneTupleCannotCommitAfterTransfer',
        source='vaibify/gui/commitCarrier.py',
        old='''    if not dictLaneTuple:
        return False
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(dictLaneTuple["sContainerName"])''',
        new='''    if not dictLaneTuple:
        return False
    return True
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(dictLaneTuple["sContainerName"])''',
    ),
    # Case 6 (old sockets are detached, closed, and inert afterwards):
    Falsification(
        nodeid='tests/testHostTransfer.py::testOldGenerationCleanupCannotTouchNewGenerationState',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    dictSessionSockets = getattr(appState, "dictSessionSockets", None)
    if dictSessionSockets is None or not sOldSessionId:
        return []
    return list(dictSessionSockets.pop(sOldSessionId, set()))''',
        new='''    dictSessionSockets = getattr(appState, "dictSessionSockets", None)
    if dictSessionSockets is None or not sOldSessionId:
        return []
    return []''',
    ),
    # Case 8 (the agent token rides through the transfer untouched):
    Falsification(
        nodeid='tests/testHostTransfer.py::testAgentAuthorizationSurvivesTransfer',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    recordOwner.sLeaseId = sNewLease
    recordOwner.sBrowserSessionId = sNewSessionId''',
        new='''    recordOwner.sLeaseId = sNewLease
    recordOwner.sAgentToken = containerOwnership.fsMintAgentToken()
    recordOwner.sBrowserSessionId = sNewSessionId''',
    ),
    # Case 12 (a half-implemented transfer that bumps the generation
    # without rotating the browser principals lets the displaced
    # session release the successor's record):
    Falsification(
        nodeid='tests/testHostTransfer.py::testStaleGenerationReleaseIsRefusedAfterTransfer',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    recordOwner.sLeaseId = sNewLease
    recordOwner.sBrowserSessionId = sNewSessionId
    recordOwner.iOwnerGeneration = iNewGeneration''',
        new='''    recordOwner.iOwnerGeneration = iNewGeneration''',
    ),
    # Case 44, transfer half. The DRAINING phase is DELETED (wave 2.4):
    # a hand-over must not carry a terminal execution nobody has proven
    # dead, and draining one would have settled it on the strength of a
    # probe rather than a stop -- while forcing a wait inside the held
    # lock. The refusal replaces it.
    Falsification(
        nodeid='tests/testHostTransfer.py::testTransferRefusesOverALiveTerminalRecordAndSignalsNothing',
        source='vaibify/gui/sessionLifecycle.py',
        old='''        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
        new='''        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_BUSY_RETRY, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
    ),
    # Case 46, transfer half: a refused transfer rolls back only what it
    # minted, and leaves the record it did not create standing.
    Falsification(
        nodeid='tests/testHostTransfer.py::testARefusedTransferRollsBackOnlyWhatItMinted',
        source='vaibify/gui/sessionLifecycle.py',
        old='''        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
        new='''        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
    ),
    # Wave 2.4: a busy container refuses AT ONCE and names the holder.
    Falsification(
        nodeid='tests/testHostTransfer.py::testABusyContainerRefusesTheTransferAtOnceAndNamesTheOperation',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    if lockMutation.locked():''',
        new='''    if False and lockMutation.locked():''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 5, checkpoint 2 — the mint-transfer socket
    # operation and the vaibify open client (design §6b).
    # ------------------------------------------------------------------
    # Case 2, mint half (the socket handshake: a stale CLI can never
    # mint against a successor generation without seeing it):
    Falsification(
        nodeid='tests/testHostControlChannel.py::test_mint_transfer_refuses_a_generation_the_hub_no_longer_serves',
        source='vaibify/gui/hostControlChannel.py',
        old='    if valueExpectedGeneration != recordOwner.iOwnerGeneration:',
        new='    if False and valueExpectedGeneration != recordOwner.iOwnerGeneration:',
    ),
    # Case 15, end-to-end half (real Unix-socket mint -> real HTTP
    # redemption; a client that skips the peer-authenticated mint and
    # presents an unminted token must be refused, and the command must
    # report the failure instead of opening a browser):
    Falsification(
        nodeid='tests/testVaibifyOpen.py::test_open_transfers_over_real_socket_and_http',
        source='vaibify/cli/commandOpen.py',
        old='    return dictMinted["sTransferCapability"]',
        new='    return "an-unminted-transfer-token"',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 5, checkpoint 3 — the safe reaper's
    # ORPHANED->RELEASED conditions and the agent-liveness stamps
    # (design §7; cases 7, 20, and the orphaned half of 32).
    # ------------------------------------------------------------------
    # Case 7, orphan-grace half (the reap grace measures from the
    # orphan stamp, never from the last socket):
    Falsification(
        nodeid='tests/testSafeReaper.py::testOrphanedReapGraceRunsFromTheOrphanStampNotTheLastSocket',
        source='vaibify/gui/containerOwnership.py',
        old='''    fOrphanedElapsedSeconds = (
        time.monotonic() - recordOwner.fOrphanedSinceMonotonic
    )''',
        new='''    fOrphanedElapsedSeconds = (
        time.monotonic() - recordOwner.fLastSeenMonotonic
    )''',
    ),
    # Case 7, agent-stamp half (reap succeeds only with a STALE agent
    # activity stamp):
    Falsification(
        nodeid='tests/testSafeReaper.py::testOrphanedReapRequiresAStaleAgentActivityStamp',
        source='vaibify/gui/containerOwnership.py',
        old='''    return recordOwner.fLastAgentActivityMonotonic > 0.0 and (
        time.monotonic() - recordOwner.fLastAgentActivityMonotonic
        < fGraceSeconds
    )''',
        new='''    return False''',
    ),
    # Case 7, journal half (ORPHANED->RELEASED requires every journal
    # record settled; the veto lives in the reaper loop):
    Falsification(
        nodeid='tests/testSafeReaper.py::testOrphanedReapVetoedWhileAJournalRecordIsUnsettled',
        source='vaibify/gui/serverLifespan.py',
        old='            or _fbOrphanedOwnerJournalUnsettled(dictContainerOwners, sName)',
        new='            or False',
    ),
    # Case 32, orphaned half (the reaper and a cancelling durable task
    # cannot both release the record; same busy-veto mutation as the
    # carrier half, killed here through an ORPHANED_SESSION record):
    Falsification(
        nodeid='tests/testSafeReaper.py::testOrphanedReapAndCancelCannotBothReleaseTheRecord',
        source='vaibify/gui/serverLifespan.py',
        old='''    def fbGuardedWorkLive(sName):
        return (
            commitCarrier.fbContainerHasLiveMutationWork(app.state, sName)
            or _fbOwnedNamePipelineRunning(app, dictCtx, sName)
        )''',
        new='''    def fbGuardedWorkLive(sName):
        return _fbOwnedNamePipelineRunning(app, dictCtx, sName)''',
    ),
    # Case 20, slice-5 half (an admitted agent request pins the record
    # for its FULL duration through the in-flight bracket):
    Falsification(
        nodeid='tests/testSafeReaper.py::testLongRunningAgentRequestHoldsTheRecordLiveFullDuration',
        source='vaibify/gui/serverMiddleware.py',
        old='    recordOwner.iInFlightAgentRequests += 1',
        new='    recordOwner.iInFlightAgentRequests += 0',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 5, checkpoint 4 — the remaining transfer
    # halves (cases 16b, 4's 3b half, 26b's lifecycle, 43's and 46's
    # real-container transfer halves).
    # ------------------------------------------------------------------
    # Case 16b, slice-5 half (the shielded supervisor keeps the drain
    # held across a requester cancel, so a transfer stays busy-refused
    # until the worker truly dies):
    Falsification(
        nodeid='tests/testHostTransfer.py::testCancelledRequesterKeepsTransferBlockedUntilWorkerDies',
        source='vaibify/gui/commitCarrier.py',
        old='    return await asyncio.shield(taskSupervisor)',
        new='    return await taskSupervisor',
    ),
    # Case 4, 3b half (the mode-(b) supervisor's own lane-tuple
    # revalidation under the drain refuses a pre-transfer tuple):
    Falsification(
        nodeid='tests/testHostTransfer.py::testOldTupleLockHeldMutationIsRefusedAfterTransfer',
        source='vaibify/gui/commitCarrier.py',
        old='''        if not fbLaneTupleStillCurrent(appState, supervisor.dictLaneTuple):
            raise CommitRefusedError(''',
        new='''        if False:
            raise CommitRefusedError(''',
    ),
    # Case 26b, full lifecycle (force-abandon must actually SET the
    # poison — an acknowledge-without-poison leaves the transfer
    # refusal misattributed to the journal and the claim un-poisoned):
    Falsification(
        nodeid='tests/testHostControlChannel.py::test_force_abandon_lifecycle_poisons_refuses_and_reconciles',
        source='vaibify/gui/hostControlChannel.py',
        old='''    listFenced = containerOwnership.flistPoisonAndFenceConnections(
        recordOwner,
        containerOwnership.PoisonRecord(
            sGuardedOperationId=sExpectedOperationId,
            sContainerId=recordOwner.sContainerId,
            sTaskHandleId=sTaskHandleId,
            fAbandonedMonotonic=time.monotonic(),
        ),
        getattr(app.state, "dictSessionSockets", None),
    )
    sessionLifecycle.fnScheduleConnectionFencing(listFenced)''',
        new='''    listFenced = []
    sessionLifecycle.fnScheduleConnectionFencing(listFenced)''',
    ),
    # Case 43, transfer-commit half (real container; also drives case
    # 44's transfer path): under the agree-with-exec_inspect prover
    # mutant, the DRAINING phase "proves" a live group empty, the
    # transfer commits over it, and the surviving signal-trapping
    # descendant writes its marker under the successor's ownership.
    # Kill-confirmation requires a reachable Docker daemon; without
    # one the test skips and the mutant survives vacuously.
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_transfer_refuses_over_a_live_terminal_against_a_real_container',
        source='vaibify/gui/sessionLifecycle.py',
        old='''        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
        new='''        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_BUSY_RETRY, {
            "sMessage": f"Container '{sName}' has a terminal execution "''',
    ),
    # Case 46, real-container half (a PAUSED container makes every
    # drain probe indeterminate; the optimistic-commit mutant settles
    # whatever the final probe says, so the transfer commits instead
    # of refusing retained-and-quarantined). Kill-confirmation
    # requires a reachable Docker daemon; without one the test skips
    # and the mutant survives vacuously.
    Falsification(
        nodeid='tests/testTerminalContainmentLive.py::test_an_indeterminate_drain_quarantines_rather_than_settling',
        source='vaibify/gui/terminalContainment.py',
        old='''    if _fbProbeProvesEmpty(dictProbe):
        return _fdictSettleProvenRecord(recordTerminal, dictProbe)
    return _fdictQuarantineRecord(
        recordTerminal,
        "the terminal process group could not be proven empty: "
        f"{dictProbe.get('sDetail', '')}",
    )''',
        new='''    return _fdictSettleProvenRecord(recordTerminal, dictProbe)''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 6, checkpoint 1 — the orphan transition,
    # the §4 zero-sockets trigger, and the §5 per-frame backstop
    # (cases 10, 18, the orphan-transition half of 7, the real-orphan
    # halves of 13 and 20).
    # ------------------------------------------------------------------
    # Case 10 (reload/pagehide during a live task retains ownership):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testReloadReconnectWithinWindowRetainsOwnership',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    return (
        time.monotonic() - recordOwner.fLastSeenMonotonic
        >= F_RECONNECT_WINDOW_SECONDS
    )''',
        new='''    return (
        time.monotonic() - recordOwner.fLastSeenMonotonic
        >= 0.0
    )''',
    ),
    # Case 18 (closing one terminal socket doesn't orphan; per-lane
    # counting — every browser lane vetoes the trigger):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testTerminalLaneSocketVetoesTheOrphanTrigger',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    if recordOwner.iLiveConnectionCount > 0:
        return False''',
        new='''    if recordOwner.iLivePipelineConnectionCount > 0:
        return False''',
    ),
    # Case 7, orphan-transition half (the reap grace measures from the
    # REAL orphan commit's stamp, not the long-dead last socket — the
    # mutant stamps the commit with the last-socket time, the exact
    # bug shape the case forbids):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testReapGraceMeasuresFromTheRealOrphanTransition',
        source='vaibify/gui/sessionLifecycle.py',
        old='    recordOwner.fOrphanedSinceMonotonic = time.monotonic()',
        new='    recordOwner.fOrphanedSinceMonotonic = recordOwner.fLastSeenMonotonic',
    ),
    # Case 13, real-orphan half (a live agent's REST activity pins a
    # record orphaned through the real transition; same predicate
    # mutation as the slice-5 hand-set-state entry, killed here
    # end-to-end through fnOrphanSession + the real middleware):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testOrphanedRecordWithLiveAgentRestActivityIsNotReaped',
        source='vaibify/gui/containerOwnership.py',
        old='''    return recordOwner.fLastAgentActivityMonotonic > 0.0 and (
        time.monotonic() - recordOwner.fLastAgentActivityMonotonic
        < fGraceSeconds
    )''',
        new='''    return False''',
    ),
    # Case 20, real-orphan half (mid-dispatch, with the admission
    # stamp aged stale, ONLY the in-flight bracket pins the record):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testInFlightAgentRequestPinsARealOrphanedRecordInTheReaperLoop',
        source='vaibify/gui/serverMiddleware.py',
        old='    recordOwner.iInFlightAgentRequests += 1',
        new='    recordOwner.iInFlightAgentRequests += 0',
    ),
    # The §5 per-frame backstop, pipeline lane (a frame in flight at
    # revocation is refused, never dispatched):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testPipelineFrameFromARevokedSessionIsRefusedNotDispatched',
        source='vaibify/gui/pipelineServer.py',
        old='''            if fbFrameCredentialStillActive is not None and (
                not fbFrameCredentialStillActive()
            ):
                await websocket.close(code=4401)
                return''',
        new='''            if False:
                await websocket.close(code=4401)
                return''',
    ),
    # The §5 per-frame backstop, terminal lane (a revoked session's
    # keystroke never reaches the container):
    Falsification(
        nodeid='tests/testOrphanTransition.py::testTerminalKeystrokeFromARevokedSessionIsRefused',
        source='vaibify/gui/pipelineServer.py',
        old='''        if fbFrameCredentialStillActive is not None and (
            not fbFrameCredentialStillActive()
        ):
            await websocket.close(code=4401)
            break''',
        new='''        if False:
            await websocket.close(code=4401)
            break''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 6, checkpoint 2 — the owner-aware session
    # sweep and the live-socket veto on sliding idle (design §11).
    # ------------------------------------------------------------------
    # The owner-aware rule: an expired OWNING session must be committed
    # through the orphan transition, never a bare credential revoke that
    # would strand an ACTIVE record no reaper condition can release.
    Falsification(
        nodeid='tests/testSessionLifecycleEvaluator.py::testExpiredOwningSessionIsOrphanedNotBareRevoked',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    await fnOrphanSession(
        appState, sName, fbStillWarranted=fbStillOwnedByThisSession,
    )''',
        new='''    browserSession.fbRevokeSessionById(dictStore, sSessionId)''',
    ),
    # A live WebSocket vetoes sliding idle: the socket layer never
    # refreshes the credential stamp, so without the veto a streaming
    # dashboard is revoked under the researcher.
    Falsification(
        nodeid='tests/testSessionLifecycleEvaluator.py::testLiveWebSocketVetoesSlidingIdle',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    if recordOwner is not None and recordOwner.iLiveConnectionCount > 0:
        return False
    return dictLifetime["fIdleSeconds"] >= F_SLIDING_IDLE_SECONDS''',
        new='''    return dictLifetime["fIdleSeconds"] >= F_SLIDING_IDLE_SECONDS''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 7 — the absolute cap and the pre-expiry
    # warning's backend truth (design §11).
    # ------------------------------------------------------------------
    # The socket veto is scoped to sliding idle ALONE: a forgotten-open
    # tab holds a live socket by definition, so generalizing the veto
    # makes the cap unreachable in exactly its target case.
    Falsification(
        nodeid='tests/testSessionLifecycleEvaluator.py::testAbsoluteCapFiresDespiteALiveWebSocket',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    if dictLifetime["fAgeSeconds"] >= F_ABSOLUTE_SESSION_CAP_SECONDS:
        return True
    if recordOwner is not None and recordOwner.iLiveConnectionCount > 0:
        return False''',
        new='''    if recordOwner is not None and recordOwner.iLiveConnectionCount > 0:
        return False
    if dictLifetime["fAgeSeconds"] >= F_ABSOLUTE_SESSION_CAP_SECONDS:
        return True''',
    ),
    # The warning counts down the CAP, the deadline with no veto — not
    # the sliding-idle clock a live socket forbids from ever firing.
    Falsification(
        nodeid='tests/testSessionLifecycleEvaluator.py::testExpiryViewCountsDownTheCapForThePresentingSessionOnly',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    fRemainingSeconds = max(
        0.0,
        F_ABSOLUTE_SESSION_CAP_SECONDS - dictLifetime["fAgeSeconds"],
    )''',
        new='''    fRemainingSeconds = max(
        0.0,
        F_SLIDING_IDLE_SECONDS - dictLifetime["fIdleSeconds"],
    )''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 6, checkpoint 4 — the remaining normative
    # cases: 1 (expiry orphans, never releases) and 11/17 (the §10
    # explicit-release authority).
    # ------------------------------------------------------------------
    # Case 1: an expired session holding a live run must be ORPHANED.
    # Releasing would free the flock over work that can still commit.
    Falsification(
        nodeid='tests/testSessionLifecycleEvaluator.py::testCapDuringALiveRunOrphansAndNeverReleases',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    await fnOrphanSession(
        appState, sName, fbStillWarranted=fbStillOwnedByThisSession,
    )''',
        new='''    containerOwnership._fnForceReleaseOwnership(
        appState.dictContainerOwners, sName,
        getattr(appState, "dictSessionOwner", None),
    )''',
    ),
    # Case 11: the agent refusal is about a LIVE agent; a stale stamp
    # must not lock a researcher out of releasing an idle container.
    Falsification(
        nodeid='tests/testExplicitReleaseAuthority.py::testIdleReleaseWithAStaleAgentStampSucceeds',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    if recordOwner is not None and containerOwnership.fbAgentIsLiveOnRecord(
        recordOwner,
    ):''',
        new='''    if recordOwner is not None:''',
    ),
    # Case 17, force half: force overrides the agent refusal and ONLY
    # that one — never a live durable task.
    Falsification(
        nodeid='tests/testExplicitReleaseAuthority.py::testReleaseUnderALiveAgentNeedsForceAndForceNeverBeatsALiveRun',
        source='vaibify/gui/sessionLifecycle.py',
        old='''    from . import commitCarrier
    if _frecordLiveDurableTask(appState, sName) is not None:''',
        new='''    from . import commitCarrier
    if bForce:
        return ""
    if _frecordLiveDurableTask(appState, sName) is not None:''',
    ),
    # Case 17, ordering half: the channels close while the flock is
    # still held, never after the container has been handed back.
    Falsification(
        nodeid='tests/testExplicitReleaseAuthority.py::testPermittedReleaseClosesChannelsBeforeFreeingTheFlock',
        source='vaibify/gui/sessionLifecycle.py',
        old='''        await _fnDrainAndCloseBeforeRelease(appState, sName)
        async with _flockObtainSessionCardinality(dictLockStore):
            bReleased = containerOwnership.fbReleaseOwnership(
                dictContainerOwners, sName, sLeaseId,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
            )''',
        new='''        async with _flockObtainSessionCardinality(dictLockStore):
            bReleased = containerOwnership.fbReleaseOwnership(
                dictContainerOwners, sName, sLeaseId,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
            )
        await _fnDrainAndCloseBeforeRelease(appState, sName)''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 8 — the `vaibify do` headless bootstrap and
    # the lease attachment (design §6b). The omission mutated here is
    # the one that shipped: fiSendHttpAction sent the credential alone,
    # so every owner-scoped call was refused while the CLI's own mocked
    # tests stayed green. Under the mutation the flow dies at connect
    # ("In use in another browser session") and the command exits 4.
    # ------------------------------------------------------------------
    Falsification(
        nodeid='tests/testVaibifyDoHeadless.py::test_do_bootstraps_over_the_socket_and_acts_under_its_lease',
        source='vaibify/cli/hubSession.py',
        old='''        sPath, dictFields, fTimeoutSeconds, dictQuery=dictQuery,
        sLeaseId=dictSession["sLeaseId"],
    )''',
        new='''        sPath, dictFields, fTimeoutSeconds, dictQuery=dictQuery,
    )''',
    ),

    # ------------------------------------------------------------------
    # ORPHANED_SESSION slice 9 — lifecycle owner-gating and the
    # server-owned start reservation (design §10b / §12 slice 9).
    # ------------------------------------------------------------------
    # The residual this slice closes: stop was browser-hub, so any
    # same-hub tab could tear down the container another session was
    # working in.
    # The hung-start kill path, the label-keyed cleanup, and the two
    # result-delivery paths (design §10b, cases 19/21/22/24/25/28/29/
    # 33/40/41). Each mutation is the shortcut that would have been
    # taken if the rule were prose instead of code.
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testInitiatorCancelOfAStaleStartKillsAndFreesTheContainer',
        source='vaibify/docker/containerManager.py',
        old="""    except subprocess.TimeoutExpired:
        processDocker.kill()
    processDocker.wait()
    return _fdictTerminationOutcome(processDocker, True, True)""",
        new="""    except subprocess.TimeoutExpired:
        pass
    return _fdictTerminationOutcome(processDocker, True, False)""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testCancelFromANonOwningSessionIsRefused',
        source='vaibify/gui/startReservation.py',
        old="""    if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
        return (403, {
            "sName": sName,""",
        new="""    if False:
        return (403, {
            "sName": sName,""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAFailedStartNeverReleasesOwnershipItDidNotCreate',
        source='vaibify/gui/sessionLifecycle.py',
        old="""            if not bMayRelease:
                return
""",
        new="",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testCancelAfterPartialCreationRemovesItBeforeClearingTheRecord',
        source='vaibify/gui/startReservation.py',
        old="""    dictSettlement = await asyncio.to_thread(
        containerManager.fdictSettleReservationContainers,
        reservation.sReservationId,
        reservation.recordStartTask.bProcessWasSignalled,
    )
    await sessionLifecycle.ftSettleFailedStartOwnership(""",
        new="""    dictSettlement = {
        "bConclusive": True, "listRemovedContainerIds": [],
        "sDetail": "cleanup deferred",
    }
    await sessionLifecycle.ftSettleFailedStartOwnership(""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAStaleSettlementCannotDeleteANewerReservation',
        source='vaibify/gui/startReservation.py',
        old="""    bReservationStillOurs = recordOwner is not None and (
        recordOwner.reservation is reservation
    )""",
        new="""    bReservationStillOurs = recordOwner is not None and (
        recordOwner.reservation is not None
    )""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAnInconclusiveSettlementQuarantinesInsteadOfReleasing',
        source='vaibify/gui/startReservation.py',
        old="""    if dictSettlement["bConclusive"]:
        _fnSettleJournalQuietly(
            sName, reservation.recordStartTask.sJournalOperationId,
        )
    else:""",
        new="""    if True:
        _fnSettleJournalQuietly(
            sName, reservation.recordStartTask.sJournalOperationId,
        )
    else:""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAFailedStartIsRetrievableAfterOwnershipIsReleased',
        source='vaibify/gui/startResultStore.py',
        old="""    recordResult = _fdictStoreFor(appState).get(sReservationId)
    if recordResult is None:
        return
    recordResult.sState = sState""",
        new="""    recordResult = _fdictStoreFor(appState).get(sReservationId)
    if recordResult is not None:
        return
    recordResult.sState = sState""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testALostSuccessResponseStillYieldsTheOwnerDerivedLease',
        source='vaibify/gui/startReservation.py',
        old='            "sLeaseId": recordOwner.sLeaseId,',
        new='            "sLeaseId": recordResult.sReservationId,',
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAConcurrentClaimAndStartResolveToOneOwnerRecord',
        source='vaibify/gui/sessionLifecycle.py',
        old="""            sBrowserSessionId=sBrowserSessionId,
            dictSessionOwner=getattr(appState, "dictSessionOwner", None),
            connectionDocker=connectionDocker,
        )
        dictPayload.pop("sLeaseId", None)""",
        new="""            sBrowserSessionId=sBrowserSessionId,
            dictSessionOwner=None,
            connectionDocker=connectionDocker,
        )
        dictPayload.pop("sLeaseId", None)""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testAKilledStartNeverAutoClearsIntoAClaimableContainer',
        source='vaibify/config/operationJournal.py',
        old="""    if not sDockerContainerId:
        return _fdictProbeOutcome(
            False, False, False,
            "label-only start probing is not supported; the verifier is "
            "unsupported and reconciliation is required",
        )""",
        new="""    if not sDockerContainerId:
        return _fdictProbeOutcome(
            False, True, False,
            "label-only start probing is assumed clean",
        )""",
    ),
    Falsification(
        nodeid='tests/testStartReservationFalsification.py::testTheJournalDirectoryIsIsolatedForTheseTests',
        source='tests/conftest.py',
        old="""    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "operationJournalIsolated"),
    )""",
        new="""    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        operationJournal._S_JOURNAL_DIRECTORY,
    )""",
    ),
    Falsification(
        nodeid='tests/testHostTransfer.py::testBarrierTransferAdoptsAStillRunningStart',
        source='vaibify/gui/startReservation.py',
        old="""    recordTask.admission.dictLiveState["sActiveExecOperationId"] = (
        reservation.recordStartTask.sJournalOperationId
    )""",
        new="""    recordTask.admission.dictLiveState["sActiveExecOperationId"] = (
        ""
    )""",
    ),
    Falsification(
        nodeid='tests/testHostTransfer.py::testTransferRebindsTheStartResultEntitlementToTheSuccessor',
        source='vaibify/gui/sessionLifecycle.py',
        old="""    from . import startResultStore
    startResultStore.fnRebindStartResultsForTransfer(
        appState, sName, sNewSessionId,
    )""",
        new="""    del appState, sName, sNewSessionId""",
    ),

    Falsification(
        nodeid='tests/testContainerLifecycleGating.py::test_stop_by_a_session_that_does_not_hold_the_lease_is_refused',
        source='vaibify/gui/routeScope.py',
        old='    ("POST", "/api/containers/{sName}/stop"): S_SCOPE_CONTAINER_LIFECYCLE,',
        new='    ("POST", "/api/containers/{sName}/stop"): S_SCOPE_BROWSER_HUB,',
    ),

    # Migration plan phase 1b (R5): runtime attribution of a container
    # mutation back to ONE inventory row, or an explicit refusal that
    # routes the row to manual tracing. Each mutant below was applied by
    # hand, watched to fail its own test, reverted, and the source
    # confirmed byte-identical with `shasum -a 256`.
    Falsification(
        nodeid='tests/testMutationAttribution.py::testTheIndexCoversExactlyTheCheckedInInventoryRows',
        source='tools/mutationAttribution.py',
        old='for pathModule in sorted(PATH_PACKAGE.rglob("*.py"))',
        new='for pathModule in sorted(PATH_PACKAGE.rglob("*.py"))[:20]',
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testAnAliasedPrimitiveIsAttributedToItsBindingRow',
        source='tools/mutationAttribution.py',
        old="""    if len(listMatched) == 1:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_ALIAS_SINGLE_HOP,
        )""",
        new="""    if len(listMatched) == 99:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_ALIAS_SINGLE_HOP,
        )""",
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testTwoCallsInOneFunctionAttributeToDistinctRows',
        source='tools/mutationAttribution.py',
        old="""    listMatched = [
        dictRow for dictRow in listCandidates
        if dictRow["sFingerprint"] in setFingerprintsHere
    ]""",
        new='    listMatched = list(listCandidates)',
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testTwoIdenticalExpressionsRefuseAttributionInsteadOfGuessing',
        source='tools/mutationAttribution.py',
        old="""    if len(listMatched) == 1:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_FINGERPRINT_EXACT,
        )""",
        new="""    if len(listMatched) >= 1:
        return _fdictAcceptAttribution(
            listMatched[0], S_EVIDENCE_FINGERPRINT_EXACT,
        )""",
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testASharedHelperUnderTwoCarrierModesKeepsBothModes',
        source='tools/mutationAttribution.py',
        old='            dictEntry["setCarrierModes"].add(sCarrierMode)',
        new='            dictEntry["setCarrierModes"] = {sCarrierMode}',
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testAPrimitivePassedIntoAThreadKeepsItsModeButLosesItsRow',
        source='tools/mutationAttribution.py',
        old='        "sAttributionEvidence": S_EVIDENCE_UNATTRIBUTED,',
        new='        "sAttributionEvidence": S_EVIDENCE_FINGERPRINT_EXACT,',
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testAnUnobservedRowIsRoutedToManualTracingBesideObservedSiblings',
        source='tools/mutationAttribution.py',
        old="""        if sRowKey in setAttributed:
            continue""",
        new="""        if dictRow["sPrimitive"] in {
            sKey.split("|")[2] for sKey in setAttributed
        }:
            continue""",
    ),
    Falsification(
        nodeid='tests/testMutationAttribution.py::testTheObservationArtifactCarriesEveryFactR5Names',
        source='tools/mutationAttribution.py',
        old='        "sCarrierInvocation": sCarrierInvocation,',
        new='        "sCarrierInvocationDropped": sCarrierInvocation,',
    ),
    Falsification(
        nodeid='tests/testSkillIntegrity.py::testThePathCheckerRefusesToPassOnAnEmptyScan',
        source='tools/checkAgentDocsPaths.py',
        old='    if not listDocs:',
        new='    if False:',
    ),
    Falsification(
        nodeid='tests/testSkillIntegrity.py::testTheTreeExclusionIsRelativeToTheRepositoryRoot',
        source='tools/checkAgentDocsPaths.py',
        old='for sPart in pathRelative.parts',
        new='for sPart in pathCandidate.parts',
    ),

    # --- R4: no unauthorised capability anywhere the hub can reach ---
    #
    # The unnamed-authority mutation is applied to the SOURCE, not to the
    # record: an authority arriving in a hub-reachable module is the
    # event the rule exists for, and mutating the record instead would
    # only prove the record can be edited. pipelineUtils is the leaf
    # module -- it holds no capability of any kind, so a subprocess
    # import there is unambiguously new.
    Falsification(
        nodeid=(
            'tests/testCapabilityAuthorities.py::'
            'testEveryHubReachableRawCapabilityIsNamedIndividually'
        ),
        source='vaibify/gui/pipelineUtils.py',
        old='"""Pure utility functions for pipeline execution (leaf module).',
        new=(
            'import subprocess\n\n'
            '"""Pure utility functions for pipeline execution (leaf module).'
        ),
    ),
    # The transitive half. buildRoutes imports imageBuilder directly for
    # an unrelated helper, so the real chain is only visible with that
    # shortcut edge set aside -- which is why a one-hop reading of
    # reachability passes every other check here and still authorises
    # `docker build` by omission.
    Falsification(
        nodeid=(
            'tests/testCapabilityAuthorities.py::'
            'testTheRealBuildChainIsReachedThroughItsMiddleModule'
        ),
        source='tests/testCapabilityAuthorities.py',
        old='    while listStack:\n        sModule = listStack.pop()',
        new='    while False and listStack:\n        sModule = listStack.pop()',
    ),
    # The synthetic chain, unrelated to the build chain, driven route ->
    # helper -> raw authority through the same closure.
    Falsification(
        nodeid=(
            'tests/testCapabilityAuthorities.py::'
            'testAnUnnamedAuthorityBehindAHelperIsStillReported'
        ),
        source='tests/testCapabilityAuthorities.py',
        old='    setSeen = set(setSeeds)\n    listStack = list(setSeen)',
        new='    setSeen = set(setSeeds)\n    listStack = []',
    ),
    # The one CLASS disposition, kept from stretching. The mutation is on
    # the record because the record IS the artifact this guard polices:
    # filing a real client under the exception-type class is the failure
    # mode, and it can only be written there.
    Falsification(
        nodeid=(
            'tests/testCapabilityAuthorities.py::'
            'testTheExceptionTypeClassOnlyEverCoversAnException'
        ),
        source='tests/testCapabilityAuthorities.py',
        old=(
            '    "cli/configLoader.py|fbDockerAvailable|docker-client|'
            'docker|import|0":\n        _fdictAuthority(\n'
            '            ["host-cli", "http"],'
        ),
        new=(
            '    "cli/configLoader.py|fbDockerAvailable|docker-client|'
            'docker|import|0":\n        _fdictAuthority(\n'
            '            [S_LANE_EXCEPTION_TYPE],'
        ),
    ),

    # --- Blind-spot dispositions: a ruling bound to what it read ---
    #
    # The gated helper is the one generic command authority under
    # vaibify/gui/. It is disposed of as an EXCEPTIONAL authority on the
    # strength of two structural constraints, and these are the mutants
    # that prove each constraint is real rather than described.
    Falsification(
        nodeid=(
            'tests/testCommitCarrier.py::'
            'testTheGatedHelperNeverActsWhenTheJournalRefusesIt'
        ),
        source='vaibify/gui/commitCarrier.py',
        old=(
            '    "import os, sys, subprocess\\n"\n'
            '    "sGateLine = sys.stdin.readline()\\n"\n'
        ),
        new=(
            '    "import os, sys, subprocess\\n"\n'
            '    "subprocess.call(sys.argv[1:])\\n"\n'
            '    "sGateLine = sys.stdin.readline()\\n"\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCommitCarrier.py::'
            'testTheGatedHelperIsConstrainedByItsHolderIdentity'
        ),
        source='vaibify/config/mutationAdmission.py',
        old='        if dictOwnRecord.get(sIdentityKey) != valueExpected:',
        new=(
            '        if False and dictOwnRecord.get(sIdentityKey) != '
            'valueExpected:'
        ),
    ),
    # A fingerprint proves "same site". It does not preserve "somebody
    # reviewed this", and the gap is a constant two files away from the
    # call: "the executable is git and the flags are a module constant"
    # is a claim about THAT symbol, which the site hashes never see.
    Falsification(
        nodeid=(
            'tests/testBlindSpotDispositions.py::'
            'testADispositionExpiresWhenItsSupportingSymbolsChange'
        ),
        source='vaibify/reproducibility/gitHardening.py',
        old='LIST_GIT_CREDENTIAL_ISOLATION_CONFIG = [',
        new=(
            'LIST_GIT_CREDENTIAL_ISOLATION_CONFIG = [\n'
            '    "-c", "credential.helper=osxkeychain",'
        ),
    ),
    # --- The lifecycle audit: findings, not a family declaration ---
    #
    # The population is resolved from the live application, so the mutant
    # is a route JOINING the family rather than a list somebody forgot to
    # extend -- which is the way an unaudited lifecycle route would
    # actually arrive.
    Falsification(
        nodeid=(
            'tests/testLifecycleRouteAuthority.py::'
            'testEveryLifecycleRouteHasBeenAuditedIndividually'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '    ("POST", "/api/containers/{sName}/build"): '
            'S_SCOPE_BROWSER_HUB,'
        ),
        new=(
            '    ("POST", "/api/containers/{sName}/build"): '
            'S_SCOPE_CONTAINER_LIFECYCLE,'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testLifecycleRouteAuthority.py::'
            'testTheRecordedLockAndJournalFactsMatchTheSource'
        ),
        source='vaibify/gui/registryRoutes.py',
        old=(
            '    from vaibify.docker import containerManager\n'
            '    from vaibify.config.keepAliveManager import fnStopKeepAlive'
        ),
        new=(
            '    from vaibify.gui.sessionLifecycle import '
            'flockContainerMutationForAppState  # noqa: F401\n'
            '    from vaibify.docker import containerManager\n'
            '    from vaibify.config.keepAliveManager import fnStopKeepAlive'
        ),
    ),
    # The pin on a documented refusal that no state transition reaches.
    # It fires the moment somebody makes it reachable, which is exactly
    # when the cancel route's recorded transfer behaviour needs re-reading.
    Falsification(
        nodeid=(
            'tests/testLifecycleRouteAuthority.py::'
            'testTheTransferRefusalForACancellingTaskCannotFire'
        ),
        source='vaibify/gui/commitCarrier.py',
        old='def _fbDurableTaskStillCurrent(appState, recordTask):',
        new=(
            'def _fnCancelDurableTask(recordTask):\n'
            '    recordTask.sState = "cancelling"\n\n\n'
            'def _fbDurableTaskStillCurrent(appState, recordTask):'
        ),
    ),

    # ------------------------------------------------------------------
    # Phase 1c: the carrier-mode declaration mechanism.
    #
    # The two audit mutants below are deliberately separate branches of
    # _ftJudgeOneObservation, and each was confirmed to kill ONLY its own
    # test: a shape protected by two guards survives every single
    # mutation and proves nothing about either.
    # ------------------------------------------------------------------
    Falsification(
        nodeid=(
            'tests/testCarrierModeDeclaration.py::'
            'testDeclaringMintsNoAdmission'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '    if ftResolveCarrierDeclaration(route.endpoint):\n'
            '        return False\n'
            '    return fbRouteAwaitsCarrierMode(route.methods, route.path)\n'
        ),
        new='    del route\n    return True\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierModeDeclaration.py::'
            'testARouteNeitherDeclaredNorAwaitingFailsClosed'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '    setKeys = {(sMethod, sPath) for sMethod in '
            '(setMethods or ())}\n'
            '    if not setKeys:\n'
            '        return False\n'
            '    return setKeys <= SET_ROUTES_AWAITING_CARRIER_MODE\n'
        ),
        new='    del setMethods, sPath\n    return True\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierModeDeclaration.py::'
            'testEveryContainerScopedRouteEitherDeclaresOrIsRecordedAsAwaiting'
        ),
        source='vaibify/gui/routeScope.py',
        # Anchored on two adjacent CONTAINER-READ entries, which are
        # the members that stay awaiting by decision (2026-08-05). The
        # entry named the pipeline kill route until phase 2 migrated
        # it, and would have gone stale again on every migration; the
        # read pair cannot. Two lines rather than one because every
        # awaiting GET also appears in SET_CONTAINER_READ_ROUTES, and
        # only the ADJACENCY of these two is unique to the allow-list
        # (the read set separates them with plot-standards).
        #
        # RECORDED COLLATERAL, intrinsic rather than drift: also fails
        # testTheDeclaringPopulationPartitionsWithNothingLeftOver and
        # testTheDeclarationIndexReadsTheLiveApplication. Dropping a
        # route from the allow-list without declaring it is exactly the
        # state those two exist to detect, so no mutation can produce
        # this one's failure without producing theirs. The entry's
        # previous form had the same collateral, unrecorded.
        old=(
            '    ("GET", "/api/steps/{sContainerId}/{iStepIndex}'
            '/falsification"),\n'
            '    ("GET", "/api/sync/{sContainerId}/check/{sService}"),\n'
        ),
        new=(
            '    ("GET", "/api/steps/{sContainerId}/{iStepIndex}'
            '/falsification"),\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierModeDeclaration.py::'
            'testTheAwaitingAllowListMayOnlyShrink'
        ),
        source='vaibify/gui/routeScope.py',
        old='SET_ROUTES_AWAITING_CARRIER_MODE = frozenset({\n',
        new=(
            'SET_ROUTES_AWAITING_CARRIER_MODE = frozenset({\n'
            '    ("POST", "/api/newly-invented/{sContainerId}/action"),\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierIntentAudit.py::'
            'testATypedReadDeclarationThatMutatesIsAViolation'
        ),
        source='tools/carrierIntentAudit.py',
        old=(
            '        return ("listViolations", _fdictRecordJudgement(\n'
            '            dictObservation, S_VIOLATION_TYPED_READ_MUTATED,\n'
            '        ))\n'
        ),
        new=(
            '        return ("listConfirmed", _fdictRecordJudgement(\n'
            '            dictObservation, "",\n'
            '        ))\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierIntentAudit.py::'
            'testADeclaredModeObservedOnTheAmbientAdmissionIsAViolation'
        ),
        source='tools/carrierIntentAudit.py',
        old=(
            '    return ("listViolations", _fdictRecordJudgement(\n'
            '        dictObservation, S_VIOLATION_MODE_UNDECLARED,\n'
            '    ))\n'
        ),
        new=(
            '    return ("listConfirmed", _fdictRecordJudgement(\n'
            '        dictObservation, "",\n'
            '    ))\n'
        ),
    ),

    # ------------------------------------------------------------------
    # Phase 2 group 1: the routes migrated onto the enforced branch.
    #
    # Each mutant deletes ONE route's carrier call, and each was
    # confirmed to kill ONLY its own test. The first attempt at these
    # kills failed to kill at all: it was run against
    # tests/testDraftRoutes.py, whose Docker mock answers a write by
    # storing bytes and never calls the admission gate, so deleting the
    # carrier outright left 17 tests passing. The double in
    # testCarrierMigratedRoutes.py calls the same gates the real
    # DockerConnection calls, which is why these kills are kills.
    # ------------------------------------------------------------------
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDraftSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/draftRoutes.py',
        old=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sDraftPath,\n'
            '        fnWriteTheDraft,\n'
        ),
        new=(
            '    fnWriteTheDraft()\n'
            '    _tupleUncarriedArguments = (\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sDraftPath,\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDraftDeleteCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/draftRoutes.py',
        old=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sDraftPath,\n'
            '        fnRemoveTheDraft,\n'
        ),
        new=(
            '    fnRemoveTheDraft()\n'
            '    _tupleUncarriedArguments = (\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sDraftPath,\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheFileSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        old=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sNormalized,'
            '\n'
            '        fnWriteTheFile,\n'
        ),
        new=(
            '    fnWriteTheFile()\n'
            '    _tupleUncarriedArguments = (\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sNormalized,'
            '\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheSettingsSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/settingsRoutes.py',
        old=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        appState, dictLaneTuple["sContainerName"], '
            'sContainerId,\n'
            '        dictLaneTuple, "file-write",\n'
        ),
        new=(
            '    dictCtx["save"](sContainerId, dictWorkflow)\n'
            '    _tupleUncarriedArguments = (\n'
            '        appState, dictLaneTuple["sContainerName"], '
            'sContainerId,\n'
            '        dictLaneTuple, "file-write",\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnUnmigratedRouteStillReachesThePrimitiveOnTheAmbientMint'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '    if ftResolveCarrierDeclaration(route.endpoint):\n'
            '        return False\n'
            '    return fbRouteAwaitsCarrierMode(route.methods, route.path)\n'
        ),
        new=(
            '    if ftResolveCarrierDeclaration(route.endpoint):\n'
            '        return False\n'
            '    return False\n'
        ),
    ),

    # ------------------------------------------------------------------
    # Phase 2 group 2: the lock-held migration of the clean route.
    #
    # The recorded mutation for the transfer test restores the EXACT
    # pre-migration code -- a bare asyncio.to_thread holding no lock --
    # so the kill re-creates the named live exploit rather than an
    # approximation of it. It kills the mode test too, and correctly:
    # dropping the drain breaks both the refusal and the observed mode.
    # Each test also has an isolating mutant, checked by hand: the save
    # bypass below fails only the mode test, and making
    # fsDescribeLiveMutationWork return a bare "a guarded operation"
    # fails only the transfer test (its naming assertion).
    # ------------------------------------------------------------------
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testATransferArrivingMidCleanIsRefusedAndNamesTheClean'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '    return await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"clean-outputs",\n'
            '        ftDeleteTheOutputs,\n'
            '    )\n'
        ),
        new='    return await asyncio.to_thread(ftDeleteTheOutputs, None)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheCleanDeletesUnderTheDrainAndSavesSynchronously'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "Recording the cleaned outputs",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePlotConversionRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/plotRoutes.py',
        old=(
            '    dictOutcome = await commitCarrier.fdictRunLockHeldMutation('
            '\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"standardize-plots",\n'
            '        flistConvertThePlots,\n'
            '    )\n'
        ),
        new=(
            '    dictOutcome = commitCarrier.fdictCommitSynchronousMutation('
            '\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"standardize-plots",\n'
            '        flistConvertThePlots, {"sDockerContainerId": '
            'sContainerId},\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePlotStandardizationSavesSynchronously'
        ),
        source='vaibify/gui/routes/plotRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n            dictCtx, sContainerId, dictWorkflow, request,\n            "Recording the standardized plots",\n        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRepoSidecarRewriteRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        old=(
            '    return await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            'sOperationTarget,\n'
            '        fgenericRewriteTheSidecar,\n'
            '    )\n'
        ),
        new=(
            '    return await asyncio.to_thread(fgenericRewriteTheSidecar, None)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePlotStandardsCheckReachesNoMutatingPrimitive'
        ),
        source='vaibify/gui/routes/plotRoutes.py',
        old=(
            '    listExists = await asyncio.gather(*[\n'
            '        asyncio.to_thread(\n'
            '            dictCtx["docker"].fbContainerPathIsFile,\n'
            '            sContainerId, _fsStandardPathForPlot('
            'sResolved, sBasename),\n'
            '        )\n'
            '        for sResolved, sBasename in listPlots\n'
            '    ])\n'
            '    return dict(zip(listBasenames, listExists))\n'
        ),
        new=(
            '    sCheckCommand = " && ".join(\n'
            '        f\'test -f {fsShellQuote('
            '_fsStandardPathForPlot(s, b))}\'\n'
            '        f\' && echo "Y" || echo "N"\'\n'
            '        for s, b in listPlots\n'
            '    )\n'
            '    tResult = await asyncio.to_thread(\n'
            '        dictCtx["docker"].ftResultExecuteCommand,\n'
            '        sContainerId, sCheckCommand,\n'
            '    )\n'
            '    listLines = (tResult[1] if tResult else "").strip()'
            '.split("\\n")\n'
            '    return {\n'
            '        sBasename: (\n'
            '            listLines[iIdx].strip() == "Y" '
            'if iIdx < len(listLines)\n'
            '            else False\n'
            '        )\n'
            '        for iIdx, sBasename in enumerate(listBasenames)\n'
            '    }\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testTypeAloneStopsTheSwallow.py::'
            'testARefusalIsNotCaughtByABareExceptOsError'
        ),
        source='vaibify/config/mutationAdmission.py',
        old='class ControlPlaneRefusalError(Exception):',
        new='class ControlPlaneRefusalError(PermissionError):',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRepoTrackRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        # Retargeted 2026-08-05: the settle-then-raise ordering moved to
        # routeContext.fgenericRunWorkerUnderTheDrain on its fourth caller,
        # so the drain invocation this used to mutate no longer lives in
        # repoRoutes. The DELEGATION to it does, and is this module's
        # own call site. It swaps the MODE rather than dropping the
        # admission, for the reason the test's own docstring gives:
        # reaching the exec unadmitted 500s the route before its refusal
        # can be observed, so that mutant fails
        # testAnExpectedRefusalLeavesTheContainerUsable too and isolates
        # neither. Verified: this one fails that test alone.
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n        sContainerId, fdictHandleRunTheEffect, sOperationTarget, requestHttp,\n    )\n'
        ),
        new=(
            '    from .. import commitCarrier\n    dictLaneTuple = fdictRequireLaneTupleForCommit(\n        requestHttp, sContainerId, sOperationTarget,\n    )\n    dictCarried = commitCarrier.fdictCommitSynchronousMutation(\n        requestHttp.app.state, dictLaneTuple["sContainerName"],\n        sContainerId, dictLaneTuple, "helper", sOperationTarget,\n        fdictHandleRunTheEffect, {"sDockerContainerId": sContainerId},\n    )["result"]\n    if dictCarried["errorRefused"] is not None:\n        raise dictCarried["errorRefused"]\n    return dictCarried["objResult"]\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRepositoryPushRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        old=(
            '    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper",\n'
            '        _fsDescribePushTarget(sRepoName, ""), '
            'fdictPushUnderTheSupervisor,\n'
            '    )\n'
        ),
        new=(
            '    dictOutcome = commitCarrier.fdictCommitSynchronousMutation('
            '\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper",\n'
            '        _fsDescribePushTarget(sRepoName, ""), '
            'fdictPushUnderTheSupervisor,\n'
            '        {"sDockerContainerId": sContainerId},\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePostPushVerifyRewritesTheSyncCacheUnderItsOwnDrain'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        old=(
            '    return await fsRefreshVerifyCacheAfterPush(\n'
            '        dictCtx, sContainerId, dictWorkflow, "github",\n'
            '        requestHttp=requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return await fsRefreshVerifyCacheAfterPush(\n'
            '        dictCtx, sContainerId, dictWorkflow, "github",\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testALivePushNamesItsRemoteWithoutLeakingItsToken'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        old=(
            '    return (\n'
            '        "github-push " + sRepoName + " -> "\n'
            '        + fsRedactCredentials(sRemoteUrl)\n'
            '    )\n'
        ),
        new=(
            '    return (\n'
            '        "github-push " + sRepoName + " -> "\n'
            '        + sRemoteUrl\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnExpectedRefusalLeavesTheContainerUsable'
        ),
        source='vaibify/gui/routes/repoRoutes.py',
        # Retargeted 2026-08-05: the 4xx/5xx split moved to
        # routeContext.fdictCarryARefusalBackInsteadOfRaising when
        # gitRoutes became its third caller. Mutating the shared helper
        # would kill this test AND the workflow-creation and git ones,
        # isolating nothing, so each caller's entry now names its OWN
        # call site: bypassing the capture here re-raises the 409 inside
        # the worker and quarantines the container.
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising('
            'fnEffect)\n'
        ),
        new=(
            '        return {"errorRefused": None, '
            '"objResult": fnEffect()}\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testMutationBoundary.py::'
            'testAnExistenceProbeSurvivesAnEnforcedLane'
        ),
        source='vaibify/reproducibility/repoFiles.py',
        old=(
            '        return self.connectionDocker.fbContainerPathIsFile(\n'
            '            self.sContainerId, self._fsAbsolute(sRelPath),\n'
            '        )\n'
        ),
        new=(
            '        iExitCode, _s = self._ftExec(\n'
            '            "test -f " + fsShellQuotePosix('
            'self._fsAbsolute(sRelPath)),\n'
            '        )\n'
            '        return iExitCode == 0\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testLevelGatesRefusalPropagation.py::'
            'testAGateNeverSwallowsAnAdmissionRefusal'
        ),
        source='vaibify/reproducibility/levelGates.py',
        old=(
            '        dictEntries = filesRepo.fdictHashFiles(listRelPaths)\n'
            '    except (OSError, ValueError) as error:\n'
            '        fnReRaiseControlPlaneRefusal(error)\n'
            '        return None\n'
        ),
        # The mutation BROADENS the handler rather than deleting the
        # re-raise. Deleting it stopped being a defect on 2026-08-07:
        # reparenting ControlPlaneRefusalError off PermissionError means
        # `except (OSError, ValueError)` can no longer see a refusal, so
        # the twelve hand-guards became belt-and-braces and removing one
        # changes nothing -- the type enforces the property now. What
        # still swallows a refusal, and is the realistic future edit, is
        # somebody widening a handler to catch Exception.
        new=(
            '        dictEntries = filesRepo.fdictHashFiles(listRelPaths)\n'
            '    except Exception:\n'
            '        return None\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testBlindSpotDispositions.py::'
            'testEveryGuiBlindSpotCarriesADisposition'
        ),
        source='vaibify/gui/workspacePath.py',
        old='import subprocess',
        new=(
            'import subprocess\n\n\n'
            'def fnLaunchAnythingAtAll(listCommand):\n'
            '    """A launch whose argv nobody can read."""\n'
            '    return subprocess.run(listCommand, capture_output=True)'
        ),
    ),

    # The three test-execution routes. Each mutant below was confirmed
    # to kill EXACTLY ONE of the three tests -- a clean diagonal. The
    # obvious mutants (delete the carrier call) were tried first and
    # rejected: an unadmitted mutation refuses, and a refusal empties
    # the hash ledger every later test reads, so one defect killed two
    # or three tests and none of them was isolated. All three are mode
    # SWAPS for that reason, which is also the sharper claim -- the
    # route reached the container under a real admission, just the
    # wrong one, so "it did not raise" would not catch any of them.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheLevelProbeAndTheTestRunShareOneLockHeldAdmission'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        old=(
            '    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "helper", sTarget, '
            'ftProbeThenRun,\n'
            '    )\n'
        ),
        new=(
            '    dictOutcome = commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "helper", sTarget, '
            'ftProbeThenRun,\n'
            '        {"iHolderPid": __import__("os").getpid(),\n'
            '         "iHolderProcessGroup": __import__("os").getpgrp()},\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheAutoArchiveProbeRunsUnderItsOwnDrain'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        old=(
            '    await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "helper", "auto-archive", '
            'fbArchive,\n'
            '    )\n'
        ),
        new=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "helper", "auto-archive", '
            'fbArchive,\n'
            '        {"iHolderPid": __import__("os").getpid(),\n'
            '         "iHolderProcessGroup": __import__("os").getpgrp()},\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheTestResultSaveCommitsSynchronously'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "Recording the test results",\n'
            '        )\n'
        ),
        new=(
            '        await _ftProbeLevelThenRunUnderTheDrain(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "run-tests-save",\n'
            '            lambda: dictCtx["save"](sContainerId, dictWorkflow),\n'
            '        )\n'
        ),
    ),

    # The run-dispatch gate over the carrier's live-work registry. The
    # two mutants are deliberately opposite in direction -- one makes
    # the refusal uninformative, the other makes it fire when it must
    # not -- because a gate like this has two ways to be wrong and only
    # one of them looks like a failure. A third mutant (degrade to
    # fbContainerHasLiveMutationWork plus a generic string) was tried
    # and lands on the naming test, not the false-refusal one; it is
    # recorded in that test's docstring rather than here, since a
    # second entry for the same kill would double-count one guard.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testARunArrivingUnderALiveCarrierWorkerIsRefusedAndNamesIt'
        ),
        source='vaibify/gui/pipelineServer.py',
        old=(
            '                await fnCallback(\n'
            '                    _fdictBusyRefusalEvent(\n'
            '                        sAction, dictRequest, sBusyWork,\n'
            '                    ),\n'
            '                )\n'
        ),
        new=(
            '                await fnCallback(\n'
            '                    _fdictBusyRefusalEvent(\n'
            '                        sAction, dictRequest,\n'
            '                    ),\n'
            '                )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testASynchronousSaveNeverMakesTheRunGateRefuse'
        ),
        source='vaibify/gui/pipelineServer.py',
        old=(
            '    return commitCarrier.fsDescribeLiveMutationWork(\n'
            '        dictDurableContext["appState"], '
            'dictDurableContext["sName"],\n'
            '    )\n'
        ),
        new=(
            '    from vaibify.config import mutationAdmission\n'
            '    if mutationAdmission.fbLaneEnforced():\n'
            '        return "a guarded operation"\n'
            '    return commitCarrier.fsDescribeLiveMutationWork(\n'
            '        dictDurableContext["appState"], '
            'dictDurableContext["sName"],\n'
            '    )\n'
        ),
    ),

    Falsification(
        # has-credential reads the researcher's HOST keyring and
        # ignores the container id in its own path. It is a GET, so
        # the catalog's agent-lane gate never sees it; without the
        # handler's own refusal an in-container agent learns whether
        # the researcher stores an Overleaf token.
        nodeid=(
            'tests/testAgentLaneEnforcement.py::'
            'test_has_credential_refuses_the_agent_lane'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        fnRejectAgentTokenLane(requestHttp)\n'
            '        dictCtx["require"](sContainerId)\n'
            '        syncDispatcher.fnValidateServiceName(sService)\n'
            '        return {\n'
            '            "bHasCredential": '
            '_fbServiceHasStoredCredential(sService),\n'
        ),
        new=(
            '        dictCtx["require"](sContainerId)\n'
            '        syncDispatcher.fnValidateServiceName(sService)\n'
            '        return {\n'
            '            "bHasCredential": '
            '_fbServiceHasStoredCredential(sService),\n'
        ),
    ),

    # --- The seven declaration saves, carrier mode (a) (2026-08-05) ---
    # One entry for a parametrized family, matching the convention the
    # two push families already use: the invariant requires exactly one
    # entry per marked FUNCTION. The registered mutant reverts the
    # ai-models/declare call site; the other six call sites were each
    # kill-confirmed by hand on 2026-08-05 and each killed only its own
    # parameter case, which is what establishes that sharing
    # fdictCommitWorkflowSave did not collapse seven guards into one
    # untested claim.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDeclarationSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The AI-model declaration",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    # --- The six step-CRUD saves, carrier mode (a) (2026-08-05) ---
    # One entry for the parametrized family, same convention as above.
    # The registered mutant reverts the create call site. All six were
    # kill-confirmed by hand on 2026-08-05 and each killed only its own
    # parameter case (create additionally kills the warning-flag test
    # below, which must drive create to reach the flag at all).
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheStepEditCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The step creation",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    # The create route's SECOND save. Registered separately because it
    # is a separate call site, and because this mutant is what proves
    # the family above does NOT cover it: reverting the warning-flag
    # save kills ONLY this test and none of the six parameter cases.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheHundredStepWarningSaveIsCarriedToo'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '            fdictCommitWorkflowSave(\n'
            '                dictCtx, sContainerId, dictWorkflow, '
            'requestHttp,\n'
            '                "The hundred-step warning flag",\n'
            '            )\n'
            '        return {\n'
            '            "iIndex": iIndex,\n'
        ),
        new=(
            '            dictCtx["save"](sContainerId, dictWorkflow)\n'
            '        return {\n'
            '            "iIndex": iIndex,\n'
        ),
    ),

    # --- The file upload, carrier mode (a) (2026-08-05) ---
    # Measured while confirming this: with the carrier call removed the
    # twelve upload tests in tests/testFileEndpointsAndMiddleware.py
    # ALL still passed, and only the test below failed. That is the
    # finding tests/testCarrierMigratedRoutes.py exists for, reproduced
    # on a fresh route rather than taken on trust.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheFileUploadCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        old=(
            '    commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, '
            'dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "file-write", '
            'sNormalized,\n'
            '        fnWriteTheUpload,\n'
            '        {\n'
            '            **fdictStampDockerIdForJournal(sContainerId),\n'
            '            "sExpectedSha256": '
            'hashlib.sha256(baContent).hexdigest(),\n'
            '            "sPriorSha256": sPriorSha256,\n'
            '        },\n'
            '    )\n'
        ),
        new='    fnWriteTheUpload()\n',
    ),

    # --- Project creation, carrier mode (b) (2026-08-05) ---
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheProjectCreationRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/workflowRoutes.py',
        old=(
            '    dictOutcome = await '
            'commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, '
            'dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"create-project",\n'
            '        fdictProbeThenCreate,\n'
            '    )\n'
            '    return dictOutcome["result"]\n'
        ),
        new='    return fdictProbeThenCreate()\n',
    ),

    # The separate guard inside that route: an expected 4xx must be
    # RETURNED from the worker, never raised out of it. Raising poisons
    # the journal record and quarantines the container, so a researcher
    # who picked a filename already in use is told to reconcile. This
    # mutant kills ONLY the refusal test, which is what establishes the
    # two guards are separately proven rather than jointly assumed.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testARefusedProjectCreationLeavesTheContainerUsable'
        ),
        source='vaibify/gui/routes/workflowRoutes.py',
        # Retargeted 2026-08-05 for the same reason as the repoRoutes
        # entry above: the split now lives in the shared
        # routeContext helper, so this names THIS route's call of it.
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising(\n'
            '            lambda: _fsProbeThenWriteNewWorkflow(\n'
            '                dictCtx["docker"], sContainerId, request, '
            'sFileName,\n'
            '            ),\n'
            '        )\n'
        ),
        new=(
            '        return {"errorRefused": None, "objResult":\n'
            '            _fsProbeThenWriteNewWorkflow(\n'
            '                dictCtx["docker"], sContainerId, request, '
            'sFileName,\n'
            '            )}\n'
        ),
    ),

    # --- Five Sync-panel routes, carrier modes (a) and (b)
    # (2026-08-05) ---
    # Each mutant below was applied by hand, the whole
    # testCarrierMigratedRoutes.py file run, the source restored from an
    # in-memory copy and re-hashed byte-identical. Every one killed
    # EXACTLY its own test, with one stated exception recorded on the
    # arXiv save's entry.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheSyncTrackingToggleCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The sync-tracking change",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGitIdentityWriteRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        iExit, sOut = await _ftWriteGitIdentityUnderTheDrain(\n'
            '            dictCtx, sContainerId, sWorkdir,\n'
            '            request.sName.strip(), request.sEmail.strip(), '
            'requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        iExit, sOut = await asyncio.to_thread(\n'
            '            _ftWriteGitIdentity,\n'
            '            dictCtx["docker"], sContainerId, sWorkdir,\n'
            '            request.sName.strip(), request.sEmail.strip(),\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheSingleFileGithubPushRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        dictResult = await _fdictRunAddFileUnderTheDrain(\n'
            '            dictCtx, sContainerId, sWorkdir, request, '
            'requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResult = await asyncio.to_thread(\n'
            '            _fdictRunGithubAddFileBlocking,\n'
            '            dictCtx, sContainerId, sWorkdir, request,\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRemoteVerifyRewritesItsCacheUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        dictCarried = await _fdictVerifyRemoteUnderTheDrain(\n'
            '            dictWorkflow, sService, filesRepo, sContainerId, '
            'requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictCarried = {\n'
            '            "dictStatus": await asyncio.to_thread(\n'
            '                fdictRunRemoteVerifyBlocking, dictWorkflow, '
            'sService,\n'
            '                filesRepo,\n'
            '            ),\n'
            '            "errorRemote": None,\n'
            '        }\n'
        ),
    ),

    # The arXiv handler's mode-(a) half. This mutant kills BOTH arXiv
    # tests, not one, and the reason is sequencing rather than a weak
    # assertion: the save runs first, so an unadmitted write 500s the
    # handler before the verify that would rewrite the cache can run.
    # Recorded here so a re-confirmation run does not read the second
    # failure as drift. The reverse direction DOES isolate -- see the
    # entry below.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheArxivConfigureSaveCommitsSynchronously'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        dictRemotes["arxiv"] = dictConfig\n'
            '    fdictCommitWorkflowSave(\n'
            '        dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '        "The arXiv configuration",\n'
            '    )\n'
        ),
        new=(
            '        dictRemotes["arxiv"] = dictConfig\n'
            '    dictCtx["save"](sContainerId, dictWorkflow)\n'
        ),
    ),

    # The arXiv handler's mode-(b) half, and the entry that proves the
    # two are separately guarded: removing this carrier failed ONLY this
    # test while the save's test still passed. It isolates because
    # _fdictRunArxivVerifyAfterConfig catches the refusal and reports it
    # as sVerifyError, so the response stays 200 and only the cache
    # write goes missing from the ledger.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheArxivCacheRewriteRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        dictVerify = await _fgenericRunArxivCacheWorkUnderTheDrain(\n'
            '            sContainerId, requestHttp, "arxiv-verify",\n'
            '            lambda: _fdictRunArxivVerifyAfterConfig(\n'
            '                dictWorkflow,\n'
            '                ffilesForWorkflow(dictCtx, sContainerId, '
            'dictWorkflow),\n'
            '            ),\n'
            '        )\n'
        ),
        new=(
            '        dictVerify = await asyncio.to_thread(\n'
            '            _fdictRunArxivVerifyAfterConfig, dictWorkflow,\n'
            '            ffilesForWorkflow(dictCtx, sContainerId, '
            'dictWorkflow),\n'
            '        )\n'
        ),
    ),

    # --- The two routes ruled WRITES governed elsewhere (2026-08-05) ---
    # Neither is a carrier migration, so neither mutant removes a
    # carrier. What each proves is that the route's separate-authority
    # claim is checkable: reach a mutation-capable container primitive
    # and the enforced branch refuses, which is what makes an empty
    # gated ledger evidence rather than an assumption.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheHostFilePullReachesNoMutatingContainerPrimitive'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        old=(
            '    with open(sTargetPath, "wb") as fileTarget:\n'
            '        for baChunk in connectionDocker.fiterStreamFile(\n'
            '            sContainerId, sContainerPath,\n'
            '        ):\n'
            '            fileTarget.write(baChunk)\n'
        ),
        new=(
            '    iExit, sOut = connectionDocker.ftResultExecuteCommand(\n'
            '        sContainerId, "cat " + sContainerPath,\n'
            '    )\n'
            '    with open(sTargetPath, "wb") as fileTarget:\n'
            '        fileTarget.write(sOut.encode("utf-8"))\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheProjectCreationRequestMutatesOnlyHubState'
        ),
        source='vaibify/gui/routes/workflowRoutes.py',
        old=(
            '        dictCtx["dictProjectCreationRequests"]'
            '[sContainerId] = {\n'
        ),
        new=(
            '        dictCtx["docker"].fnWriteFile(\n'
            '            sContainerId, "/workspace/project.json", b"{}",\n'
            '        )\n'
            '        dictCtx["dictProjectCreationRequests"]'
            '[sContainerId] = {\n'
        ),
    ),

    # --- The git panel's six mutating routes, carrier modes (a) and
    # (b) (2026-08-05). Every mutant is that ROUTE's own call of
    # _fgenericRunGitWorkerUnderTheDrain reverted to a direct call of its
    # worker, so each kills exactly its own test. Mutating the shared
    # wrapper -- or the shared
    # routeContext.fdictCarryARefusalBackInsteadOfRaising it calls --
    # legitimately kills all of them at once, which is one guard
    # reported once per route that depends on it, not six guards none
    # of which is proven.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheProjectRepoFetchRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        return await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictFetchThenReadStatus(\n'
            '                dictCtx, sContainerId, sRepo, bCacheUsed,\n'
            '            ),\n'
            '            "git-fetch", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        return _fdictFetchThenReadStatus(\n'
            '            dictCtx, sContainerId, sRepo, bCacheUsed,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheProjectRepoPullRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        return await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictCheckCleanThenFastForward(\n'
            '                dictCtx, sContainerId, sRepo,\n'
            '            ),\n'
            '            "git-pull", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        return _fdictCheckCleanThenFastForward(\n'
            '            dictCtx, sContainerId, sRepo,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRemoteRefreshRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictFetchThenCollectRemotes(\n'
            '                dictCtx["docker"], sContainerId, sRepo, '
            'bCacheUsed,\n'
            '            ),\n'
            '            "git-fetch", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResponse = _fdictFetchThenCollectRemotes(\n'
            '            dictCtx["docker"], sContainerId, sRepo, '
            'bCacheUsed,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheCanonicalCommitRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictScanThenCommitCanonical(\n'
            '                dictCtx["docker"], sContainerId, '
            'dictWorkflow, sRepo,\n'
            '                request,\n'
            '            ),\n'
            '            "commit-canonical", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResponse = _fdictScanThenCommitCanonical(\n'
            '            dictCtx["docker"], sContainerId, dictWorkflow, '
            'sRepo,\n'
            '            request,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDeclarationUntrackRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictRemoveDeclarationFromTheIndex(\n'
            '                dictCtx["docker"], sContainerId, sRepo, '
            'request.sPath,\n'
            '            ),\n'
            '            "untrack-ai-declaration", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResponse = _fdictRemoveDeclarationFromTheIndex(\n'
            '            dictCtx["docker"], sContainerId, sRepo, '
            'request.sPath,\n'
            '        )\n'
        ),
    ),
    # This mutant kills BOTH reconcile tests, and that is straight-line
    # sequencing rather than drift: the fetch runs first, so an
    # unadmitted exec 500s the handler before the bookkeeping save the
    # sibling test asserts on can run. The isolation is one-directional
    # -- removing the SAVE's carrier below fails only its own test.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRemoteReconcileFetchesUnderTheDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda: _fdictFetchThenCollectRemotes(\n'
            '                dictCtx["docker"], sContainerId, sRepo, '
            'False,\n'
            '            ),\n'
            '            "git-fetch", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResponse = _fdictFetchThenCollectRemotes(\n'
            '            dictCtx["docker"], sContainerId, sRepo, False,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheReconcileBookkeepingSaveCommitsSynchronously'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, '
            'requestHttp,\n'
            '            "The reconcile bookkeeping save",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),
    # The panel's own 5xx carry-back. Dropping 502 from the carried set
    # sends a failed git fetch back through the default 4xx/5xx split,
    # which re-raises it inside the worker -- poisoning the journal and
    # quarantining the container over an unreachable remote.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnUnreachableRemoteLeavesTheContainerUsable'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old='_SET_GIT_REMOTE_REFUSAL_STATUSES = frozenset({502})\n',
        new='_SET_GIT_REMOTE_REFUSAL_STATUSES = frozenset()\n',
    ),
    # --- The badge refresh: the first AUTOMATIC read to migrate ---
    #
    # (host mode wave 3, 2026-08-08). An automatic read must do two
    # things a clicked mutation need not: run under a carrier like any
    # exec, and never QUEUE behind live work. The five levers below are
    # one per branch that can be lost on its own -- the carrier itself,
    # then each of the three states that make a container busy, then
    # the self-exclusion that keeps an idle container from reporting
    # itself busy forever.
    #
    # Measured isolation, not assumed. The first mutant fails all six
    # badge tests: with the carrier gone the exec is refused at the
    # primitive and the handler 500s before any of them can assert.
    # The three BRANCH levers each fail only their own test. The
    # self-exclusion lever fails four -- its own, the payload shape,
    # the drain-read, and the durable one, whose reason then names the
    # asking read instead of the run -- which is the same statement as
    # "an always-busy container stops answering", seen four ways.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheBadgeRefreshReadsUnderOneHeldDrain'
        ),
        source='vaibify/gui/routes/gitRoutes.py',
        old=(
            '        dictRead = await fdictRunAutomaticReadUnderTheDrain(\n'
            '            sContainerId,\n'
            '            lambda supervisor=None: _ftCollectGitBadgeInputs(\n'
            '                docker, sContainerId, dictWorkflow, sRepo, '
            'filesRepo,\n'
            '            ),\n'
            '            "git-badges", requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictRead = {\n'
            '            "bPaused": False,\n'
            '            "objResult": _ftCollectGitBadgeInputs(\n'
            '                docker, sContainerId, dictWorkflow, sRepo, '
            'filesRepo,\n'
            '            ),\n'
            '        }\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testABadgeRefreshOverALiveDeleteIsPausedRatherThanQueued'
        ),
        source='vaibify/gui/commitCarrier.py',
        old=(
            '    for supervisor in _fdictSupervisorRegistry('
            'appState).values():\n'
            '        if supervisor is supervisorSelf:\n'
            '            continue\n'
        ),
        new=(
            '    for supervisor in []:\n'
            '        if supervisor is supervisorSelf:\n'
            '            continue\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testABadgeRefreshUnderAHeldDrainIsPausedRatherThanQueued'
        ),
        source='vaibify/gui/commitCarrier.py',
        old=(
            '    if lockMutation.locked():\n'
            '        return S_DESCRIBED_GUARDED_OPERATION\n'
            '    return ""\n'
        ),
        new='    return ""\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testABadgeRefreshOverALiveDurableRunIsPaused'
        ),
        source='vaibify/gui/commitCarrier.py',
        old=(
            '    recordTask = _fdictDurableTaskRegistry(appState).get(\n'
            '        supervisorSelf.sName,\n'
            '    )\n'
            '    if recordTask is not None and (\n'
            '        recordTask.taskAsync is None or not '
            'recordTask.taskAsync.done()\n'
            '    ):\n'
            '        return S_DESCRIBED_DURABLE_TASK\n'
        ),
        new='    recordTask = None\n',
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAQuietContainerIsNeverReportedAsBusy'
        ),
        source='vaibify/gui/commitCarrier.py',
        old=(
            '        if supervisor is supervisorSelf:\n'
            '            continue\n'
            '        if supervisor.sName != supervisorSelf.sName:\n'
        ),
        new=(
            '        if supervisor.sName != supervisorSelf.sName:\n'
        ),
    ),
    # The stat batch's OUTPUT SHAPE, which nothing else in the suite
    # executes. Reverting it to line-oriented text is the plausible
    # regression -- it is what the program replaced -- and it brings
    # back the parse that a path containing a space used to defeat.
    Falsification(
        nodeid=(
            'tests/testDockerConnection.py::'
            'test_the_mtime_program_answers_json_and_skips_absent_paths'
        ),
        source='vaibify/docker/dockerConnection.py',
        old='        "sys.stdout.write(json.dumps(dictMtimes))\\n"\n',
        new=(
            '        "sys.stdout.write(chr(10).join('
            'k+chr(32)+v for k,v in dictMtimes.items()))\\n"\n'
        ),
    ),
    # A host project a researcher can actually create: until the mode
    # rode the registration route, one could be made only from Python.
    Falsification(
        nodeid=(
            'tests/testHostModeContainerOnlyRefusals.py::'
            'testRegisteringAHostProjectRecordsItsMode'
        ),
        source='vaibify/gui/registryRoutes.py',
        old=(
            '            fnAddProject(request.sDirectory, '
            'sMode=request.sMode)\n'
        ),
        new='            fnAddProject(request.sDirectory)\n',
    ),
    # --- The two claims host mode gives up (host mode wave 3) ---
    #
    # Level 3 is DEFINED by a pinned container image, and Supervised
    # mode's claim holds only while vaibify mediates every path to the
    # files. Neither survives on the host, and both fail SILENTLY if
    # the gate is lost: the researcher is handed a to-do list that
    # cannot be completed, or an attribution log that goes on claiming
    # a supervised period while their editor rewrites the files beside
    # it. Each lever is one gate; each has a container-direction test
    # beside it that the lever must NOT kill.
    Falsification(
        nodeid=(
            'tests/testHostModeHonestyGates.py::'
            'testAHostProjectGetsOneL3BlockerNamingTheReason'
        ),
        source='vaibify/reproducibility/levelGates.py',
        old=(
            '    if bHostProject:\n'
            '        return [_fdictBuildL3WorkflowBlocker('
            'S_L3_HOST_MODE_CRITERION)]\n'
        ),
        new='    if False:\n        return []\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeHonestyGates.py::'
            'testEnteringSupervisedModeIsRefusedForAHostProject'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '    if not bEnabled or not fbIsHostProject(sContainerId):\n'
            '        return\n'
        ),
        new='    if True:\n        return\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeHonestyGates.py::'
            'testASupervisedHostWorkflowRefusesAnOrdinaryMutation'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '            if _fbRefuseSupervisedHostMutation(\n'
            '                self.methods, self.path, request,\n'
            '            ):\n'
            '                return _fresponseSupervisedHostRefusal()\n'
        ),
        new='            pass\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeHonestyGates.py::'
            'testTheRecordedExitIsTheOneMutationPermitted'
        ),
        source='vaibify/gui/routeScope.py',
        old=(
            '    if sPath == S_END_SUPERVISION_ON_HOST_PATH:\n'
            '        return False\n'
        ),
        new='    if False:\n        return False\n',
    ),
    # --- The Repos panel's write, deleted (host mode wave 3) ---
    #
    # Both directions of one change. Auto-tracking every discovered
    # repository is the product behaviour and is unchanged; PERSISTING
    # it from a GET is what stopped, because the panel polls that GET
    # on a timer. The second lever guards the regression the first one
    # creates: with the read no longer writing, a mutation that fell
    # back to an empty state would persist a sidecar holding only the
    # repository the researcher just touched.
    Falsification(
        nodeid=(
            'tests/testTrackedReposManager.py::'
            'test_fdictReadOrSeedSidecar_seeds_in_memory_without_writing'
        ),
        source='vaibify/gui/trackedReposManager.py',
        old=(
            '    return fdictBuildInitialState(\n'
            '        flistBuildSeedEntries(\n'
            '            flistDiscoverGitDirs(connectionDocker, '
            'sContainerId),\n'
            '        ),\n'
            '    )\n'
        ),
        new=(
            '    dictSeeded = fdictBuildInitialState(\n'
            '        flistBuildSeedEntries(\n'
            '            flistDiscoverGitDirs(connectionDocker, '
            'sContainerId),\n'
            '        ),\n'
            '    )\n'
            '    fnWriteSidecar(connectionDocker, sContainerId, '
            'dictSeeded)\n'
            '    return dictSeeded\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testTrackedReposManager.py::'
            'test_a_first_mutation_persists_the_whole_seed'
        ),
        source='vaibify/gui/trackedReposManager.py',
        old=(
            '    if dictSidecar is None:\n'
            '        return _fdictSeedSidecarInMemory('
            'connectionDocker, sContainerId)\n'
        ),
        new=(
            '    if dictSidecar is None:\n'
            '        return fdictBuildInitialState([])\n'
        ),
    ),
    # --- The poll's write, deleted (host mode wave 3) ---
    #
    # The mutant restores the pathfile push while leaving the typed
    # read in place, so the poll still answers correctly and the only
    # thing that changes is that it writes into the container again on
    # a five-second timer. That is the whole point: the defect this
    # guards against is invisible in every functional assertion, which
    # is why it survived as long as it did.
    Falsification(
        nodeid=(
            'tests/testFileStatusManager.py::'
            'testTheStatBatchWritesNothingIntoTheContainer'
        ),
        source='vaibify/gui/fileStatusManager.py',
        old=(
            '    try:\n'
            '        dictModTimes = connectionDocker.fdictStatPathMtimes(\n'
        ),
        new=(
            '    try:\n'
            '        connectionDocker.fnWriteFileViaTar(\n'
            '            sContainerId, "/tmp/vaibifyPoll.list",\n'
            '            ("\\n".join(listPaths) + "\\n").encode("utf-8"),\n'
            '        )\n'
            '        dictModTimes = connectionDocker.fdictStatPathMtimes(\n'
        ),
    ),
    # --- The rest of the activation surface (host mode wave 3) ---
    #
    # The settings read declares typed-read in its strongest form -- it
    # reaches NO container primitive -- so the only way to break it is
    # to add a reach, exactly as the compare-plot entry does.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheSettingsReadOpensNoContainerConnectionAtAll'
        ),
        source='vaibify/gui/routes/settingsRoutes.py',
        old=(
            '    async def fdictGetSettings(sContainerId: str):\n'
            '        return fdictExtractSettings(\n'
        ),
        new=(
            '    async def fdictGetSettings(sContainerId: str):\n'
            '        dictCtx["docker"].ftResultExecuteCommand(\n'
            '            sContainerId, "echo settings",\n'
            '        )\n'
            '        return fdictExtractSettings(\n'
        ),
    ),
    # The state poll's two directions. Its carrier opens on a branch it
    # usually does not take, so "carried" and "not carried on the
    # ordinary path" are separate guarantees: hoisting the carrier out
    # of the branch makes every tenth second hold the drain, and
    # dropping the persister returns a real container write to the
    # background lane where the gate is a documented no-op.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheOrdinaryStatePollHoldsNoDrain'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '        dictState = await fdictReadReconciledState(\n'
            '            dictCtx, sContainerId,\n'
            '            fnPersistReconciled=_ffnBuildCarriedStatePersister(\n'
            '                dictCtx, sContainerId, requestHttp,\n'
            '            ),\n'
            '        )\n'
        ),
        new=(
            '        fnPersistReconciledAlways = (\n'
            '            _ffnBuildCarriedStatePersister(\n'
            '                dictCtx, sContainerId, requestHttp,\n'
            '            )\n'
            '        )\n'
            '        await fnPersistReconciledAlways({"bRunning": False})\n'
            '        dictState = await fdictReadReconciledState(\n'
            '            dictCtx, sContainerId,\n'
            '            fnPersistReconciled=fnPersistReconciledAlways,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheStaleHeartbeatReconcileWritesUnderTheDrain'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '        dictState = await fdictReadReconciledState(\n'
            '            dictCtx, sContainerId,\n'
            '            fnPersistReconciled=_ffnBuildCarriedStatePersister(\n'
            '                dictCtx, sContainerId, requestHttp,\n'
            '            ),\n'
            '        )\n'
        ),
        new=(
            '        dictState = await fdictReadReconciledState(\n'
            '            dictCtx, sContainerId,\n'
            '        )\n'
        ),
    ),
    # --- Container-only capabilities, refused for host projects ---
    #
    # (host mode wave 3, 2026-08-08). Both directions of one branch,
    # plus its position. The two levers share the same `old` text and
    # differ only in what they replace it with, because "never refuses"
    # and "always refuses" ARE the two ways one predicate can be wrong,
    # and they are indistinguishable in a report while being opposite
    # in the product: one drives Docker at a project with no container,
    # the other makes every containerized project unstartable.
    Falsification(
        nodeid=(
            'tests/testHostModeContainerOnlyRefusals.py::'
            'testEveryContainerOnlyLifecycleRouteRefusesAHostProject'
        ),
        source='vaibify/gui/routeContext.py',
        old=(
            '    if not fbIsHostProject(sName):\n'
            '        return\n'
        ),
        new=(
            '    if True:\n'
            '        return\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeContainerOnlyRefusals.py::'
            'testTheSameRoutesNeverRefuseAContainerProject'
        ),
        source='vaibify/gui/routeContext.py',
        old=(
            '    if not fbIsHostProject(sName):\n'
            '        return\n'
        ),
        new=(
            '    if False:\n'
            '        return\n'
        ),
    ),
    # Position, not presence: the refusal above the daemon check is
    # what keeps a host-only hub from being told to install Docker.
    Falsification(
        nodeid=(
            'tests/testHostModeContainerOnlyRefusals.py::'
            'testAHostProjectIsRefusedEvenWhenDockerIsUnreachable'
        ),
        source='vaibify/gui/registryRoutes.py',
        old=(
            '        fnRefuseContainerOnlyForHostProject(sName, '
            '"Stopping a container")\n'
            '        dictCtx["require"]()\n'
        ),
        new=(
            '        dictCtx["require"]()\n'
            '        fnRefuseContainerOnlyForHostProject(sName, '
            '"Stopping a container")\n'
        ),
    ),
    # --- Step routes that are not a plain save (phase 2, 2026-08-05) ---
    #
    # Each entry mutates the route's OWN call site. The rename has three
    # separable guarantees -- the cascade's drain, the save sharing it,
    # and the refusal being carried -- so each has its own lever; a
    # single mutant covering all three would isolate none of them.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheStepRenameCascadeRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        # Swaps the MODE rather than dropping the admission. Running the
        # worker inline was tried first: the cascade then reaches the
        # exec unadmitted and 500s, which fails the carried-refusal and
        # split tests too, so one mutant landed on four shapes and
        # isolated none.
        #
        # RECORDED COLLATERAL, and it is intrinsic rather than drift:
        # this also fails testTheRenameCascadeSaveSharesTheCascadesDrain.
        # The cascade and its save are ONE worker by design -- that IS
        # the guarantee -- so no mutation can change the cascade's
        # admission without changing the save's.
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictApplyTheRename, "rename-step", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    from .. import commitCarrier\n'
            '    dictLaneTuple = fdictRequireLaneTupleForCommit(\n'
            '        requestHttp, sContainerId, "rename-step",\n'
            '    )\n'
            '    dictCarried = commitCarrier.fdictCommitSynchronousMutation('
            '\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", "rename-step",\n'
            '        fdictApplyTheRename, {"sDockerContainerId": '
            'sContainerId},\n'
            '    )["result"]\n'
            '    if dictCarried["errorRefused"] is not None:\n'
            '        raise dictCarried["errorRefused"]\n'
            '    return dictCarried["objResult"]\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRenameCascadeSaveSharesTheCascadesDrain'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        # The save leaves the worker and becomes its OWN mode-(a)
        # commit after the drain is released -- which is exactly what a
        # reviewer would write if they read the cascade as "container
        # work, then bookkeeping", and is a design that looks correct:
        # the save is still carried, still journaled with its hash
        # postcondition, and the route still answers 200. What it loses
        # is the only thing that matters here, the drain held ACROSS
        # both. A bare uncarried save was tried first and 500s the
        # route, which fails the cascade's own test too.
        old=(
            '        dictCtx["save"](sContainerId, dictWorkflow)\n'
            '        return dictReport\n'
            '\n'
            '    def fdictApplyTheRename(supervisor=None):\n'
            '        del supervisor\n'
            '        return fdictCarryARefusalBackInsteadOfRaising('
            'fdictRenameThenSave)\n'
            '\n'
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictApplyTheRename, "rename-step", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '        return dictReport\n'
            '\n'
            '    def fdictApplyTheRename(supervisor=None):\n'
            '        del supervisor\n'
            '        return fdictCarryARefusalBackInsteadOfRaising('
            'fdictRenameThenSave)\n'
            '\n'
            '    dictReportDone = await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictApplyTheRename, "rename-step", '
            'requestHttp,\n'
            '    )\n'
            '    fdictCommitWorkflowSave(\n'
            '        dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '        "The rename save",\n'
            '    )\n'
            '    return dictReportDone\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheRenamePreviewScanRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictScanTheScripts, '
            '"rename-step-preview", requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    del requestHttp\n'
            '    return stepRename.flistScanScriptsForOldName(\n'
            '        dictCtx["docker"], sContainerId, dictWorkflow, '
            'dictPlan,\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDirectoryAlignmentBatchRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"align-step-directories",\n'
            '        fdictAlignEveryStep,\n'
            '    )\n'
            '    return dictOutcome["result"]\n'
        ),
        new=(
            '    return fdictAlignEveryStep()\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testATakenStepNameIsRefusedWithoutQuarantiningTheContainer'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        # Bypassing the capture re-raises the 409 inside the worker,
        # which settles through the failure path and quarantines the
        # container over a name that was already taken.
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising('
            'fdictRenameThenSave)\n'
        ),
        new=(
            '        return {"errorRefused": None, '
            '"objResult": fdictRenameThenSave()}\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnUnrecoverableSplitSavesUnderTheDrainAndThenPoisons'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        # Raise first, save second: the poison still happens, but the
        # save never does, so the only pointer to the split is lost.
        #
        # RECORDED COLLATERAL, not drift: this also fails
        # testStepRoutes.py::testRenameRoutePersistsAnUnrecoverableSplit,
        # which has defended the same save since before the migration.
        # The two are complementary rather than redundant -- that one
        # proves the save happens, this one proves it happens BEFORE the
        # record poisons -- but a single deletion of the save is
        # correctly visible to both.
        old=(
            '        except stepRename.StepRenameSplitError as error:\n'
            '            dictCtx["save"](sContainerId, dictWorkflow)\n'
            '            raise HTTPException(500, str(error)) from error\n'
        ),
        new=(
            '        except stepRename.StepRenameSplitError as error:\n'
            '            raise HTTPException(500, str(error)) from error\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheStepUpdateSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The step update",\n'
            '        )\n'
        ),
        new=(
            '        dictCtx["save"](sContainerId, dictWorkflow)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheStepUpdateHoldsTheDrainAcrossItsLevelReadings'
        ),
        source='vaibify/gui/routes/stepRoutes.py',
        old=(
            '    await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictRunTheUpdate, "update-step", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    dictCarried = fdictRunTheUpdate()\n'
            '    if dictCarried["errorRefused"] is not None:\n'
            '        raise dictCarried["errorRefused"]\n'
        ),
    ),
    # --- Replay-axis routes (phase 2, 2026-08-05) ---
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheProjectContextUpdateCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '    return commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "file-write", sAbsPath,\n'
            '        lambda: _fnWriteContextFile(\n'
            '            dictCtx, sContainerId, sAbsPath, sContent,\n'
            '        ),\n'
            '        {\n'
            '            **fdictStampDockerIdForJournal(sContainerId),\n'
            '            "sExpectedSha256": hashlib.sha256(\n'
            '                sContent.encode("utf-8"),\n'
            '            ).hexdigest(),\n'
            '            "sPriorSha256": fsHashContainerFileOrEmpty(\n'
            '                dictCtx, sContainerId, sAbsPath,\n'
            '            ),\n'
            '        },\n'
            '    )\n'
        ),
        new=(
            '    del dictLaneTuple\n'
            '    return _fnWriteContextFile(\n'
            '        dictCtx, sContainerId, sAbsPath, sContent,\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheContextTemplateProbeAndWriteShareOneDrain'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '    await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictWriteTheTemplate, '
            '"project-context-template",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    del requestHttp\n'
            '    dictCarried = fdictWriteTheTemplate()\n'
            '    if dictCarried["errorRefused"] is not None:\n'
            '        raise dictCarried["errorRefused"]\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheContextImportRePointsTheRootUnderTheSameDrain'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        # The symlink replacement leaves the worker and runs after the
        # drain is released, which is the plausible reading of it as a
        # tidy-up rather than part of the transaction. The write still
        # commits under the drain, so the refusal test is untouched.
        old=(
            '        _fnReplaceRootWithSymlink(\n'
            '            dictCtx, sContainerId, dictWorkflow, dictBody,\n'
            '        )\n'
            '\n'
            '    def fdictRunTheImport(supervisor=None):\n'
        ),
        new=(
            '\n'
            '    def fdictRunTheImport(supervisor=None):\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnUnadoptableRootFileIsRefusedWithoutQuarantining'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising('
            'fnImportTheContent)\n'
        ),
        new=(
            '        return {"errorRefused": None, '
            '"objResult": fnImportTheContent()}\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePromptRecordCaptureRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/replayRoutes.py',
        old=(
            '    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"prompt-record-capture",\n'
            '        fdictRunTheCapturePass,\n'
            '    )\n'
            '    return dictOutcome["result"]\n'
        ),
        new=(
            '    del dictLaneTuple\n'
            '    return fdictRunTheCapturePass()\n'
        ),
    ),
    # --- The batched existence probe (ruling 3, 2026-08-05) ---
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheExistenceBatchIsATypedReadAndNotAnExec'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        # Reverts the route to the shell heredoc the typed read
        # replaced: caller-derived paths interpolated raw, run through
        # the general exec primitive, which the enforced branch refuses.
        #
        # RECORDED COLLATERAL, not drift: this also fails
        # testFileEndpointsAndMiddleware.py::
        # test_files_exist_returns_dict_keyed_on_input, which stubs the
        # batched adapter the mutant stops calling. That is the same
        # guarantee seen from the route's side, and there is no way to
        # remove the adapter without both noticing.
        old=(
            '    listExists = connectionDocker.flistContainerPathsExist(\n'
            '        sContainerId, listAbsPaths,\n'
            '    )\n'
            '    return dict(zip(listAbsPaths, listExists))\n'
        ),
        new=(
            '    sScript = (\n'
            '        "while IFS= read -r p; do "\n'
            '        "if [ -e \\"$p\\" ]; then echo \\"$p\\"; fi; "\n'
            '        "done <<\'__VAIBIFY_EOF__\'\\n"\n'
            '        + "\\n".join(listAbsPaths) + "\\n__VAIBIFY_EOF__"\n'
            '    )\n'
            '    _iExitCode, sOutput = '
            'connectionDocker.ftResultExecuteCommand(\n'
            '        sContainerId, sScript,\n'
            '    )\n'
            '    setExisting = set(sOutput.splitlines())\n'
            '    return {s: (s in setExisting) for s in listAbsPaths}\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testMutationBoundary.py::'
            'testTheExemptionRefusesAnythingButPathStrings'
        ),
        source='vaibify/docker/dockerConnection.py',
        # The widened parameter accepts whatever it is handed and calls
        # repr on it -- which is what the single-path form did, and is
        # exactly what stops being safe once a COLLECTION may arrive:
        # repr of an arbitrary object is whatever its class prints.
        old=(
            '    if isinstance(objPaths, str):\n'
            '        return repr(objPaths)\n'
        ),
        new=(
            '    if True:\n'
            '        return repr(objPaths)\n'
        ),
    ),

    # --- Group 6 (2026-08-06): the step panel's probe-and-record
    # routes, and the three POSTs that turn out to be reads. ---
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheAcknowledgeStepSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "Recording the acknowledged step",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheKillProcessSweepRunsUnderOneHeldDrain'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        # Back to the bare to_thread the sweep used before, which is
        # the exploit shape: a hand-over arriving between the count and
        # the kill sees an unlocked container and commits, while the
        # former owner's `kill -9` carries on into it.
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictCountThenKill, "kill-pipeline", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    dictCarried = await asyncio.to_thread(fdictCountThenKill)\n'
            '    return dictCarried["objResult"]\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheKillStoppedStateWriteRunsUnderItsOwnDrain'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '    await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictWriteTheStoppedState,\n'
            '        "pipeline-state-kill", requestHttp,\n'
            '    )\n'
        ),
        new='    await asyncio.to_thread(fdictWriteTheStoppedState)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheKillReconcileWriteKeepsTheRunnersRealCauseOfDeath'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        # Drop the injected persister and the reconciling write falls
        # back to the reader's own background lane, which holds no
        # admission -- so on the enforced branch the write that records
        # the runner's real exit code is refused at the primitive.
        old=(
            '    dictState = await pipelineState.fdictReadReconciledState(\n'
            '        dictCtx, sContainerId,\n'
            '        fnPersistReconciled=_ffnBuildCarriedStatePersister(\n'
            '            dictCtx, sContainerId, requestHttp,\n'
            '        ),\n'
            '    )\n'
        ),
        new=(
            '    dictState = await pipelineState.fdictReadReconciledState(\n'
            '        dictCtx, sContainerId,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGeneratedTestRemovalCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        # RECORDED COLLATERAL, intrinsic: also fails
        # testTheGeneratedTestRemovalSaveCommitsThroughItsOwnCarrier for
        # the same straight-line reason as the acknowledge-step pair --
        # the rm runs first and its refusal 500s the handler.
        old=(
            '    return commitCarrier.fdictCommitSynchronousMutation(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],'
            '\n'
            '        sContainerId, dictLaneTuple, "helper", '
            '"delete-generated-tests",\n'
            '        lambda: _fnRemoveTestDirectory(\n'
            '            dictCtx["docker"], sContainerId, dictStep,\n'
            '            sProjectRepoPath=dictWorkflow.get('
            '"sProjectRepoPath", ""),\n'
            '        ),\n'
            '        {\n'
            '            "iHolderPid": os.getpid(),\n'
            '            "iHolderProcessGroup": os.getpgrp(),\n'
            '        },\n'
            '    )\n'
        ),
        new=(
            '    return _fnRemoveTestDirectory(\n'
            '        dictCtx["docker"], sContainerId, dictStep,\n'
            '        sProjectRepoPath=dictWorkflow.get('
            '"sProjectRepoPath", ""),\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGeneratedTestRemovalSaveCommitsThroughItsOwnCarrier'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "Recording the deleted tests",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheTestGenerationRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        # Back to the bare to_thread the generator used before: the
        # tests directory, the conftest marker and three test files are
        # then written with nothing holding the container.
        #
        # RECORDED COLLATERAL, intrinsic rather than drift: also fails
        # testTheGeneratedTestsAreRecordedSynchronously, because the
        # generation runs FIRST and its refusal 500s the handler before
        # the save. No mutation can separate a downstream carrier from
        # an upstream refusal in a straight-line handler; the reverse
        # direction DOES isolate.
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictGenerateTheTests, "generate-tests", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    dictCarried = await asyncio.to_thread(fdictGenerateTheTests)\n'
            '    return dictCarried["objResult"]\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGeneratedTestsAreRecordedSynchronously'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        old=(
            '    fdictCommitWorkflowSave(\n'
            '        dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '        "Recording the generated tests",\n'
            '    )\n'
        ),
        new='    dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnOutOfRangeStepIsRefusedBeforeTheContainerIsTouched'
        ),
        source='vaibify/gui/routes/testRoutes.py',
        # Ruling 6's separation, deleted: the bad index then travels
        # into the carrier, where _ftExtractStepInfo's IndexError
        # settles as a FAILED worker and quarantines the container over
        # a typo in the URL.
        old=(
            '        _fnRequireStepIndexBeforeGenerating('
            'dictWorkflow, iStepIndex)\n'
        ),
        new='',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheScriptScanIsATypedReadAndNotAnExec'
        ),
        source='vaibify/gui/routes/scriptRoutes.py',
        # A directory listing written as command text again. It is not
        # merely refused: it is INDISTINGUISHABLE at the primitive from
        # an rm, which is why the typed-read adapter exists.
        old=(
            '    try:\n'
            '        listEntries = connectionDocker.flistDirectoryEntries(\n'
            '            sContainerId, sDirectory,\n'
            '        )\n'
            '    except FileNotFoundError:\n'
            '        return []\n'
        ),
        new=(
            '    _iExitCode, sOutput = '
            'connectionDocker.ftResultExecuteCommand(\n'
            '        sContainerId, "ls -1 " + sDirectory,\n'
            '    )\n'
            '    listEntries = [s for s in sOutput.split("\\n") if s]\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDependencyScanReachesNoMutatingPrimitive'
        ),
        source='vaibify/gui/routes/scriptRoutes.py',
        # Reading a script with `cat` through the general exec, which is
        # how a file read becomes an arbitrary command. Note what this
        # mutant also exposes: _fsReadContainerFile's bare
        # `except Exception` SWALLOWS the resulting refusal and answers
        # None, so the route reports "no dependencies found" rather than
        # failing. The response assertion is what catches it.
        #
        # RECORDED COLLATERAL, intrinsic: also fails
        # testDependencyScanRoutes' test_scan_dependencies_finds_upstream_match
        # and test_scan_dependencies_reports_unmatched. Both assert on
        # what the scan DETECTED, and the mutant stops it reading the
        # script at all, so no mutation can break the read without
        # breaking them.
        old=(
            '        baContent = await asyncio.to_thread(\n'
            '            dictCtx["docker"].fbaFetchFile,\n'
            '            sContainerId, sFilePath,\n'
            '        )\n'
            '        return baContent.decode("utf-8")\n'
        ),
        new=(
            '        _iExitCode, sOutput = await asyncio.to_thread(\n'
            '            dictCtx["docker"].ftResultExecuteCommand,\n'
            '            sContainerId, "cat " + sFilePath,\n'
            '        )\n'
            '        return sOutput\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheComparePlotRouteOpensNoContainerConnectionAtAll'
        ),
        source='vaibify/gui/routes/plotRoutes.py',
        # The route grows a container touch. Its typed-read declaration
        # takes it off the ambient mint, so the touch has no admission
        # to make and the enforced branch refuses -- which is what makes
        # the empty gated ledger a standing guard, not a snapshot.
        #
        # RECORDED COLLATERAL, intrinsic: also fails testPlotRoutes'
        # test_compare_success and test_compare_no_standard_raises_404.
        # Adding a container call to a handler those tests drive with a
        # context that has no docker connection cannot leave them
        # standing, so the collateral is the mutation's shape rather
        # than a weakness in the isolation.
        old=(
            '        listPlots = _flistResolvePlotPaths(dictStep, dictVars)\n'
            '        sPlotPath = _fsFindPlotPath(listPlots, sFileName)\n'
            '        sStandardPath = _fsFindStandardForFile(\n'
            '            listPlots, sFileName)\n'
        ),
        new=(
            '        listPlots = _flistResolvePlotPaths(dictStep, dictVars)\n'
            '        dictCtx["docker"].ftResultExecuteCommand(\n'
            '            sContainerId, "test -f " + sFileName,\n'
            '        )\n'
            '        sPlotPath = _fsFindPlotPath(listPlots, sFileName)\n'
            '        sStandardPath = _fsFindStandardForFile(\n'
            '            listPlots, sFileName)\n'
        ),
    ),

    # --- The Sync panel's remaining Overleaf and Zenodo routes ---
    # (2026-08-06). Each mode-(b) mutant reverts the route to the bare
    # ``asyncio.to_thread`` it used before migration, which is the
    # state the migration exists to leave behind: no mutation lock, no
    # journal record, so a transfer arriving mid-flight sees an idle
    # container. Each mode-(a) mutant reverts a ``fdictCommitWorkflowSave``
    # to the raw ``dictCtx["save"]``.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheOverleafDiffDigestsItsFilesUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        return await _fdictBuildDiffUnderTheDrain(\n'
            '            dictCtx, sContainerId, sProjectId, request, '
            'requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        return await asyncio.to_thread(\n'
            '            _fdictBuildDiffResult,\n'
            '            dictCtx, sContainerId, sProjectId, request,\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheManuscriptPullWritesItsIgnoreUnderTheSameDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    return await _fdictPullManuscriptUnderTheDrain(\n'
            '        syncDispatcher, dictCtx, sContainerId, sProjectId,\n'
            '        listPullPaths, sTargetDirectory, requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return await asyncio.to_thread(\n'
            '        _fdictPullManuscriptBlocking,\n'
            '        syncDispatcher, dictCtx, sContainerId, sProjectId,\n'
            '        listPullPaths, sTargetDirectory,\n'
            '    )\n'
        ),
    ),

    # The two mirror mutants are not carrier removals -- these routes
    # HAVE no carrier, which is the claim. Each gives the route a
    # container touch, and the enforced branch refuses it, which is
    # what makes an empty gated ledger evidence rather than an
    # assumption.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheMirrorRefreshReachesNoContainerPrimitive'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        bSuccess, result = await asyncio.to_thread(\n'
            '            syncDispatcher.ftRefreshOverleafMirror, '
            'sProjectId,\n'
            '        )\n'
        ),
        new=(
            '        dictCtx["docker"].ftResultExecuteCommand(\n'
            '            sContainerId, "git fetch --all",\n'
            '        )\n'
            '        bSuccess, result = await asyncio.to_thread(\n'
            '            syncDispatcher.ftRefreshOverleafMirror, '
            'sProjectId,\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheMirrorDeleteReachesNoContainerPrimitive'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        await asyncio.to_thread(\n'
            '            overleafMirror.fnDeleteMirror, sProjectId,\n'
            '        )\n'
        ),
        new=(
            '        dictCtx["docker"].ftResultExecuteCommand(\n'
            '            sContainerId, "rm -rf /tmp/mirror",\n'
            '        )\n'
            '        await asyncio.to_thread(\n'
            '            overleafMirror.fnDeleteMirror, sProjectId,\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheZenodoMetadataSaveCommitsThroughTheSynchronousCarrier'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, '
            'requestHttp,\n'
            '            "The Zenodo metadata save",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheCredentialSetupProbesTheServiceUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        dictResult = await _fdictRunSetupUnderTheDrain(\n'
            '            dictCtx, sContainerId, request, requestHttp,\n'
            '        )\n'
        ),
        new=(
            '        dictResult = await asyncio.to_thread(\n'
            '            _fdictRunSetupBlocking, dictCtx, sContainerId, '
            'request,\n'
            '        )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheCredentialSetupSavesItsBindingSynchronously'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '            fdictCommitWorkflowSave(\n'
            '                dictCtx, sContainerId, dictWorkflow, '
            'requestHttp,\n'
            '                "The Overleaf project binding",\n'
            '            )\n'
        ),
        new='            dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    # The archive handler carries THREE mutations and the three mutants
    # below cascade in one direction, which is the diagnosis to keep:
    # the upload's mutant fails all three tests, the digests' fails two,
    # and the save's fails one. That is sequencing -- an unadmitted exec
    # 500s the handler before the next carrier is reached -- and no test
    # can separate a downstream carrier from an upstream refusal in a
    # straight-line handler.
    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheZenodoUploadRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        # RECORDED COLLATERAL, intrinsic: also fails
        # testTheZenodoDigestPassRunsUnderItsOwnDrain and
        # testTheZenodoArchiveRecordCommitsSynchronously, both of which
        # need the upload to have succeeded before their own carrier
        # runs at all.
        old=(
            '    iExit, sOut = await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictArchiveToZenodo, "zenodo-archive", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    iExit, sOut = await asyncio.to_thread(\n'
            '        syncDispatcher.ftResultArchiveToZenodo,\n'
            '        dictCtx["docker"], sContainerId, sZenodoService,\n'
            '        request.listFilePaths, dictMetadata, '
            'iParentDepositId,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheZenodoDigestPassRunsUnderItsOwnDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        # RECORDED COLLATERAL, intrinsic: also fails
        # testTheZenodoArchiveRecordCommitsSynchronously -- the refused
        # digest exec 500s the handler before the save is reached.
        old=(
            '    dictDigests = await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictComputeTheDigests, '
            '"zenodo-archive-digests",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    dictDigests = await asyncio.to_thread(\n'
            '        _fdictComputePostArchiveZenodoDigests,\n'
            '        dictCtx, sContainerId, dictWorkflow, '
            'request.listFilePaths,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheZenodoArchiveRecordCommitsSynchronously'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    fdictCommitWorkflowSave(\n'
            '        dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '        "The Zenodo archive record",\n'
            '    )\n'
        ),
        new='    dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    # ---------------- phase 2 group 1: the two repository pushes -----

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGithubPushRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n        sContainerId, fdictHandlePushToGithub, "github-push", requestHttp,\n    )\n'
        ),
        new=(
            '    return (await asyncio.to_thread(fdictHandlePushToGithub))["objResult"]\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheGithubPushBookkeepingSaveCommitsSynchronously'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The GitHub push bookkeeping save",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheBookkeepingSaveRefusalIsNotAbsorbedIntoAWarning'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    except Exception as error:\n'
            '        fnReRaiseControlPlaneRefusal(error)\n'
            '        logger.error(\n'
            '            "GitHub push bookkeeping failed for container %s",\n'
        ),
        new=(
            '    except Exception as error:\n'
            '        del error\n'
            '        logger.error(\n'
            '            "GitHub push bookkeeping failed for container %s",\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePushedTokenReachesNoJournalRecordOrResponse'
        ),
        # INTRINSIC COLLATERAL, recorded so a re-confirmation run does
        # not read it as drift: routing container-derived text into the
        # journal target necessarily ADDS a container read to the push
        # path, and the ~18 other push tests drive doubles that do not
        # answer it. There is no lower-collateral realization -- a
        # constant cannot carry a runtime token, so the mutant must
        # make the target container-derived, which is the defect.
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '        sContainerId, fdictHandlePushToGithub, "github-push", requestHttp,\n'
        ),
        new=(
            '        sContainerId, fdictHandlePushToGithub,\n        containerGit.fsRemoteUrlInContainer(\n            dictCtx["docker"], sContainerId, sWorkdir,\n        ), requestHttp,\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testThePushedTokenReachesNoLogLine'
        ),
        # Mutated where the URL is ALREADY in scope, so the mutant
        # adds no container call and breaks nothing: it is a pure
        # logging change, and it kills this test alone.
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sRemoteUrl)\n'
        ),
        new=(
            '    logger.info("binding token to remote %s", sRemoteUrl)\n'
            '    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sRemoteUrl)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheOverleafPushRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    dictCommit = await commitCarrier.fdictRunLockHeldMutation(\n'
            '        appState, dictLaneTuple["sContainerName"], '
            'sContainerId,\n'
            '        dictLaneTuple, "helper", "overleaf-push", '
            'ftPushWorker,\n'
            '    )\n'
            '    return dictCommit["result"]\n'
        ),
        new='    return await asyncio.to_thread(ftPushWorker)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheOverleafPushProvenanceRunsUnderItsOwnDrain'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictRecordTheBookkeeping, '
            '"overleaf-push-provenance",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new='    await asyncio.to_thread(fdictRecordTheBookkeeping, None)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheOverleafPushBookkeepingSaveCommitsSynchronously'
        ),
        source='vaibify/gui/routes/syncRoutes.py',
        old=(
            '    fdictCommitWorkflowSave(\n'
            '        dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '        "The Overleaf push bookkeeping save",\n'
            '    )\n'
        ),
        new='    dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    # ------- phase 2 group 2: the AICS Level 3 reproducibility surface

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDeterminismDeletionCommitsSynchronously'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '        dictWorkflow["dictDeterminism"] = {}\n'
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The determinism deletion",\n'
            '        )\n'
        ),
        new=(
            '        dictWorkflow["dictDeterminism"] = {}\n'
            '        dictCtx["save"](sContainerId, dictWorkflow)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDeterminismDeclarationCommitsSynchronously'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The determinism declaration",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheBinaryDeclarationCommitsSynchronously'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '        fdictCommitWorkflowSave(\n'
            '            dictCtx, sContainerId, dictWorkflow, requestHttp,\n'
            '            "The standalone-binary declaration",\n'
            '        )\n'
        ),
        new='        dictCtx["save"](sContainerId, dictWorkflow)\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheBinaryCaptureRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictCaptureTheBinary, "binary-capture", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _fdictCaptureAndRecordBinary(filesRepo, '
            'sBinaryPath)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheReproduceScriptAndItsManifestRepinShareOneDrain'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictGenerateTheScript, "reproduce-script", '
            'requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _fdictWriteScriptThenRepinManifest(\n'
            '        dictCtx, sContainerId, dictWorkflow, sProjectRepo,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAReproduceScriptRepinRefusalIsNotAbsorbedIntoAFlag'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    except Exception as errorCaught:\n        fnReRaiseControlPlaneRefusal(errorCaught)\n        logging.getLogger("vaibify").warning(\n'
        ),
        new=(
            '    except Exception as errorCaught:\n        logging.getLogger("vaibify").warning(\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheEnvelopeRegenerationRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictRegenerateTheEnvelope, '
            '"level3-envelope",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _fdictGenerateEnvelopeThenReadGaps(\n'
            '        filesRepo, dictWorkflow, sContainerId,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheEnvelopeReadinessReReadJoinsTheSameCarrier'
        ),
        # The re-read offloaded to a BARE thread, which is the
        # realistic way it leaves the carrier: a fresh thread inherits
        # no contextvars, so the admission is absent and the hash exec
        # is refused. Chosen over simply deleting the call because this
        # mutant kills on the MODE -- the guarantee -- rather than on
        # the re-read merely being absent.
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    return {\n'
            '        "dictL3ReadinessGaps": fdictL3ReadinessGaps(\n'
            '            dictWorkflow, filesRepo,\n'
            '        ),\n'
            '    }\n'
        ),
        new=(
            '    import concurrent.futures\n'
            '    with concurrent.futures.ThreadPoolExecutor(1) as pool:\n'
            '        return {\n'
            '            "dictL3ReadinessGaps": pool.submit(\n'
            '                fdictL3ReadinessGaps, dictWorkflow, filesRepo,\n'
            '            ).result(),\n'
            '        }\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDependencyLockVerifyReachesNoMutatingPrimitive'
        ),
        # The typed-read claim broken the way a maintainer would break
        # it: an existence check reimplemented as a shell test through
        # the GENERAL exec primitive, which the gate must treat as
        # mutating because command text cannot be told apart from a
        # delete. INTRINSIC COLLATERAL, recorded so a re-confirmation
        # run does not read it as drift: the L3 readiness gate reads
        # the same lock through the same helper, so
        # test_l3_verify_returns_202_with_handle_when_ready fails too.
        source='vaibify/reproducibility/dependencyPinning.py',
        old='    if not filesRepo.fbIsFile(_S_LOCK_FILENAME):\n',
        new=(
            '    if filesRepo.ftRunCommand(\n'
            '        ["test", "-f", _S_LOCK_FILENAME], 5.0,\n'
            '    )[0] != 0:\n'
        ),
    ),

    # -------- phase 2: the two routes left over from the group edges --

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheDeclarationTemplateProbeAndWriteShareOneDrain'
        ),
        source='vaibify/gui/routes/levelRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictGenerateTheTemplate, '
            '"ai-declaration-template",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _fdictProbeThenWriteTemplate(filesRepo, sRelative)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAnExistingDeclarationIsRefusedWithoutQuarantining'
        ),
        source='vaibify/gui/routes/levelRoutes.py',
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising(\n'
            '            lambda: _fdictProbeThenWriteTemplate('
            'filesRepo, sRelative),\n'
            '            setAlsoCarriedStatusCodes=frozenset({500}),\n'
            '        )\n'
        ),
        new=(
            '        return {\n'
            '            "errorRefused": None,\n'
            '            "objResult": _fdictProbeThenWriteTemplate(\n'
            '                filesRepo, sRelative,\n'
            '            ),\n'
            '        }\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheManifestVerifyRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fdictVerifyTheManifest, '
            '"manifest-verify",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return await asyncio.to_thread(\n'
            '        _fdictVerifyManifestBlocking,\n'
            '        manifestWriter, dictWorkflow, filesRepo,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testAMissingManifestIsRefusedWithoutQuarantining'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old=(
            '        return fdictCarryARefusalBackInsteadOfRaising(\n'
            '            lambda: _fdictVerifyManifestBlocking(\n'
            '                manifestWriter, dictWorkflow, filesRepo,\n'
            '            ),\n'
            '        )\n'
        ),
        new=(
            '        return {\n'
            '            "errorRefused": None,\n'
            '            "objResult": _fdictVerifyManifestBlocking(\n'
            '                manifestWriter, dictWorkflow, filesRepo,\n'
            '            ),\n'
            '        }\n'
        ),
    ),

    # -------------- phase 2 group 3: the two durable launches --------

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheFalsificationPreflightRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/falsificationRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, ftClassifyThenProbe, '
            '"falsification-preflight",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _ftRequireApplicableAndInstalled(\n'
            '        dictCtx, sContainerId, dictStep, filesRepo,\n'
            '    )\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheLaunchedFalsificationRunIsVisibleAsLiveWork'
        ),
        # The bare launch this replaced: a create_task recorded only in
        # a module-global dict, invisible to every authority that asks
        # whether the container is busy.
        source='vaibify/gui/routes/falsificationRoutes.py',
        old=(
            '    dictLaunched = await commitCarrier.fdictLaunchDurableTask(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, ftaskStartFalsification,\n'
            '    )\n'
            '    if not dictLaunched["bLaunched"]:\n'
            '        raise HTTPException(\n'
            '            409,\n'
            '            "This container is busy: " '
            '+ dictLaunched["sReason"] + ".",\n'
            '        )\n'
        ),
        new='    ftaskStartFalsification()\n',
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheVerifyReadinessGateRunsUnderTheDrain'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    return await fgenericRunWorkerUnderTheDrain(\n'
            '        sContainerId, fsGateThenSnapshot, '
            '"level3-verify-readiness",\n'
            '        requestHttp,\n'
            '    )\n'
        ),
        new=(
            '    return _fsRequireReadinessThenDigest('
            'dictWorkflow, filesRepo)\n'
        ),
    ),

    Falsification(
        nodeid=(
            'tests/testCarrierMigratedRoutes.py::'
            'testTheLaunchedVerificationIsVisibleAsLiveWork'
        ),
        source='vaibify/gui/routes/reproducibilityRoutes.py',
        old=(
            '    dictLaunched = await commitCarrier.fdictLaunchDurableTask(\n'
            '        requestHttp.app.state, dictLaneTuple["sContainerName"],\n'
            '        sContainerId, dictLaneTuple, ftaskStartVerification,\n'
            '    )\n'
            '    if not dictLaunched["bLaunched"]:\n'
            '        raise HTTPException(\n'
            '            409,\n'
            '            "This container is busy: " '
            '+ dictLaunched["sReason"] + ".",\n'
            '        )\n'
        ),
        new='    ftaskStartVerification()\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostControlChannel.py::'
            'test_a_reset_by_the_hub_reads_as_an_unreadable_response'
        ),
        source='vaibify/gui/hostControlChannel.py',
        old='        except (ConnectionResetError, BrokenPipeError):',
        new='        except (BrokenPipeError,):',
    ),
    Falsification(
        nodeid=(
            'tests/testDockerLiveDaemonRequirement.py::'
            'testDemandingADaemonRefusesRatherThanQuietlyDeferring'
        ),
        source='tools/reconfirmFalsification.py',
        # The refusal branch becomes unreachable -- the PROCEED case is
        # already returned above -- so a lane that demanded a daemon and
        # has none quietly partitions instead, and reports a clean sweep
        # of a denominator nobody was told had shrunk.
        old='    if sOutcome == S_OUTCOME_FAIL:',
        new='    if sOutcome == S_OUTCOME_PROCEED:',
    ),
    Falsification(
        nodeid=(
            'tests/testDockerLiveDaemonRequirement.py::'
            'testDeferredEntriesAreNamedAndLeftOutOfTheDenominator'
        ),
        source='tools/reconfirmFalsification.py',
        # Re-pinned 2026-08-09: the deferral now names WHICH facility
        # was missing, so the printed line is composed. Re-pinned again
        # 2026-08-10, when the article moved out of the phrase and into
        # the sentence ("no a live Docker daemon"). Both kills were
        # hand-replayed against the new spelling.
        old=(
            '    for entry, sPhrase in listDeferred:\n'
            "        print(f\"{'NOT EVALUATED: needs a ' + sPhrase:48}  \"\n"
            '              f"{entry.nodeid}")\n'
        ),
        new='',
    ),

    # --- Host-mode Phase A (2026-08-08). The mode-aware guarantees are
    # registered in symmetric pairs where a pair exists: one mutant
    # proves the host branch works, its twin proves the container
    # branch still does. A green suite over only one direction would
    # hide a mode detector stuck at a constant.
    Falsification(
        nodeid=(
            'tests/testRegistryManager.py::'
            'testMutateRegistryLockedReadsUnderTheLock'
        ),
        source='vaibify/config/registryManager.py',
        # The pre-fix ordering: reading before the flock lets two
        # concurrent registrations pass their duplicate checks against
        # the same stale snapshot.
        old=(
            '    os.makedirs(_S_REGISTRY_DIRECTORY, exist_ok=True)\n'
            '    with _ffileOpenRegistryLock():\n'
            '        dictRegistry = fdictLoadRegistry()\n'
            '        fnMutateRegistry(dictRegistry)\n'
        ),
        new=(
            '    os.makedirs(_S_REGISTRY_DIRECTORY, exist_ok=True)\n'
            '    dictRegistry = fdictLoadRegistry()\n'
            '    with _ffileOpenRegistryLock():\n'
            '        fnMutateRegistry(dictRegistry)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testRegistryManager.py::'
            'testAddProjectRejectsSameDirectoryUnderNewName'
        ),
        source='vaibify/config/registryManager.py',
        # Deleting the physical-directory check restores the pre-fix
        # behavior: one directory, two names, two "exclusive" sessions.
        old=(
            '        if _fbSamePhysicalDirectory(\n'
            '            dictExisting["sDirectory"], sDirectory,\n'
            '        ):\n'
            '            raise ValueError(\n'
            '                f"Directory \'{sDirectory}\' is already '
            'registered "\n'
            '                f"as \'{dictExisting[\'sName\']}\'"\n'
            '            )\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testRouteContextCoverage.py::'
            'TestStampDockerIdForJournal::'
            'test_host_resource_omits_the_key_entirely'
        ),
        source='vaibify/config/registryManager.py',
        # Mode detector stuck at "container": a host save would stamp
        # the Docker id and journal a probe against a container that
        # does not exist.
        old='    return dictProject.get("sMode") == "host"\n',
        new='    return False\n',
    ),
    Falsification(
        nodeid=(
            'tests/testRouteContextCoverage.py::'
            'TestStampDockerIdForJournal::'
            'test_registered_container_project_still_stamps'
        ),
        source='vaibify/config/registryManager.py',
        # The symmetric twin: stuck at "host", a registered container
        # project loses its stamp and its writes stop being verified
        # in the container.
        old='    return dictProject.get("sMode") == "host"\n',
        new='    return True\n',
    ),
    Falsification(
        nodeid=(
            'tests/testOperationJournal.py::'
            'testFileWriteProbeWithoutDockerIdUsesTheHostHashBranch'
        ),
        source='vaibify/config/operationJournal.py',
        # Probe stuck on the container branch: the poison connection in
        # the test raises on any use, so this cannot pass quietly.
        old=(
            '    if dictRecord.get("sDockerContainerId"):\n'
            '        return _fdictProbeContainerFileHash(dictRecord, '
            'connectionDocker)\n'
            '    return _fdictCompareHostFileHash(dictRecord)\n'
        ),
        new=(
            '    return _fdictProbeContainerFileHash(dictRecord, '
            'connectionDocker)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testOperationJournal.py::'
            'testFileWriteProbeWithDockerIdRequiresTheConnection'
        ),
        source='vaibify/config/operationJournal.py',
        # The symmetric twin: stuck on the host branch, a container
        # write would be "verified" against the host filesystem, where
        # the file legitimately may not exist.
        old=(
            '    if dictRecord.get("sDockerContainerId"):\n'
            '        return _fdictProbeContainerFileHash(dictRecord, '
            'connectionDocker)\n'
            '    return _fdictCompareHostFileHash(dictRecord)\n'
        ),
        new='    return _fdictCompareHostFileHash(dictRecord)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCapabilityInventory.py::'
            'testInventoryMatchesTheSource'
        ),
        source='tools/generateHostCapabilityInventory.py',
        # Dropping the passed-callable decode blinds the scanner to the
        # asyncio.to_thread shape -- the eight rows (including the main
        # step launch) degrade to UNKNOWN and the committed inventory
        # no longer matches.
        old=(
            '        else:\n'
            '            self._fnRecordPassedCallableArguments'
            '(nodeCall)\n'
        ),
        new=(
            '        else:\n'
            '            pass\n'
        ),
    ),

    # --- Host-mode Phase B: the HostConnection's contract (2026-08-08).
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestHostPathGuard::'
            'test_prefix_collision_is_refused'
        ),
        source='vaibify/host/hostConnection.py',
        # Dropping the os.sep suffix admits any sibling directory whose
        # name extends the root's (projectX vs projectXY).
        old=(
            '            if sRealPath == sAllowedRoot or '
            'sRealPath.startswith(\n'
            '                sAllowedRoot + os.sep,\n'
            '            ):\n'
        ),
        new=(
            '            if sRealPath == sAllowedRoot or '
            'sRealPath.startswith(\n'
            '                sAllowedRoot,\n'
            '            ):\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestHostPathGuard::'
            'test_symlink_escape_is_refused'
        ),
        source='vaibify/host/hostConnection.py',
        # abspath does not resolve symlinks, so a link inside the
        # project pointing outside it would pass containment.
        old='        sRealPath = os.path.realpath(sPath)\n',
        new='        sRealPath = os.path.abspath(sPath)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestGatedExec::'
            'test_identity_is_journaled_before_the_gate_opens'
        ),
        source='vaibify/host/hostConnection.py',
        # Releasing the gate before the promotion reopens the launch
        # race the gate exists to close: the command's first
        # instruction can run before any record names the process.
        old=(
            '        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, '
            '"spawned")\n'
            '        mutationAdmission.fnPromoteJournaledHostExec(\n'
            '            dictHostExecHandle, processChild.pid, '
            'processChild.pid,\n'
            '        )\n'
            '        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, '
            '"promoted")\n'
            '        processChild.stdin.write(b"GO\\n")\n'
            '        processChild.stdin.flush()\n'
            '        processChild.stdin.close()\n'
        ),
        new=(
            '        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, '
            '"spawned")\n'
            '        processChild.stdin.write(b"GO\\n")\n'
            '        processChild.stdin.flush()\n'
            '        processChild.stdin.close()\n'
            '        mutationAdmission.fnPromoteJournaledHostExec(\n'
            '            dictHostExecHandle, processChild.pid, '
            'processChild.pid,\n'
            '        )\n'
            '        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, '
            '"promoted")\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestGatedExec::'
            'test_timeout_terminates_the_whole_recorded_group'
        ),
        # Re-pinned when the termination primitives moved to
        # hostCancellation.py, which Cancel and the host connection's
        # own timeout now share. The guarantee and the mutation are
        # unchanged; only the file the line lives in moved.
        source='vaibify/host/hostCancellation.py',
        # Narrowing the group kill to a single PID leaves a backgrounded
        # sibling alive -- the unrelated-process-safety cuts both ways.
        old=(
            '            os.killpg(iProcessGroup, iSignalNumber)\n'
        ),
        new=(
            '            os.kill(iProcessGroup, iSignalNumber)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestAtomicWrites::'
            'test_replaced_executable_keeps_its_bit'
        ),
        source='vaibify/host/hostConnection.py',
        # Without the pre-rename fchmod the temp file's 0600 lands as
        # the target's mode and a replaced script loses its
        # executable bit.
        old='            os.fchmod(iDescriptor, iEffectiveMode)\n',
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostSubprocessConfinement.py::'
            'testHostConfinementScannerDetectsEachAcquisitionShape'
        ),
        source='tests/testHostSubprocessConfinement.py',
        # Every module reads as the exempt gateway, so the walk skips
        # the whole host tree and the confinement invariant passes over
        # any acquisition anybody writes — indistinguishable from the
        # vacuous pass the invariant legitimately has while
        # vaibify/host/ does not exist.
        old=(
            '        if pathModule.name == S_HOST_GATEWAY_MODULE:\n'
            '            continue\n'
        ),
        new=(
            '        if True:\n'
            '            continue\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testFileStatusManager.py::'
            'testTheStatBatchPropagatesNonSubstrateErrors'
        ),
        source='vaibify/gui/fileStatusManager.py',
        # The migrated poll net decays into a blanket pass: every
        # exception — a coding bug included — reads as "container
        # vanished mid-poll" and the poll reports a healthy-looking
        # empty answer over a real defect.
        old=(
            '    except Exception as error:\n'
            '        if not fbErrorMeansContainerUnreachable(error):\n'
            '            raise\n'
        ),
        new=(
            '    except Exception as error:\n'
            '        if False:\n'
            '            raise\n'
        ),
    ),

    # --- Host-mode wave 2: the connection router, in symmetric pairs
    # (2026-08-08). A dispatcher stuck at either leg is invisible to a
    # suite that only proves one direction.
    Falsification(
        nodeid=(
            'tests/testConnectionRouter.py::TestRouterDispatch::'
            'test_host_project_routes_to_the_host_leg'
        ),
        source='vaibify/gui/connectionRouter.py',
        # Stuck at Docker: the host branch vanishes and a host
        # project's calls land on the Docker leg (the poison leg in
        # the test raises on any touch).
        old=(
            '        if fbIsHostProject(sResourceId):\n'
            '            return self.connectionHostLeg\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testConnectionRouter.py::TestRouterDispatch::'
            'test_container_id_routes_to_the_docker_leg'
        ),
        source='vaibify/gui/connectionRouter.py',
        # The symmetric twin, stuck at host: every resource reads as a
        # host project and container calls land on the host leg.
        old='        if fbIsHostProject(sResourceId):\n',
        new='        if True:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testConnectionRouter.py::TestDockerReachablePredicate::'
            'test_router_answers_for_its_docker_leg'
        ),
        source='vaibify/config/connectionAvailability.py',
        # A predicate that stops asking the marker reports a leg-less
        # router as reachable, and every daemon-down branch it guards
        # silently dies -- the exact hazard the predicate exists for.
        old='    return fnProbeLeg()\n',
        new='    return True\n',
    ),
    Falsification(
        nodeid=(
            'tests/testConnectionRouter.py::TestRequireDockerHostBypass::'
            'test_host_resource_needs_no_daemon'
        ),
        source='vaibify/gui/dockerStatus.py',
        # Dropping the host bypass 503s every host-project route the
        # moment the daemon is down, though host projects need none.
        old=(
            '    if sResourceId is not None and '
            'fbIsHostProject(sResourceId):\n'
            '        return\n'
        ),
        new='',
    ),

    # --- Host-mode wave 2 chunk B: registry status, claim path, busy
    # oracle (2026-08-08). Symmetric pairs throughout: for every
    # mode-aware branch, one mutant proves the host direction works and
    # its twin proves the container direction still does.
    Falsification(
        nodeid=(
            'tests/testRegistryManager.py::'
            'testHostEntryStatusIsReadyWithoutConsultingDocker'
        ),
        source='vaibify/config/registryManager.py',
        # Enrichment stuck at container: a host entry's status walks
        # the Docker probes (patched to raise in the test), for a
        # project no daemon knows.
        old=(
            '    if dictProject.get("sMode") == "host":\n'
            '        return _fdictEnrichHostProjectStatus(dictProject)\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testRegistryManager.py::'
            'testContainerEntryStatusStillConsultsDocker'
        ),
        source='vaibify/config/registryManager.py',
        # The symmetric twin, stuck at host: a running container reads
        # as a red host tile and its Docker truth is never asked.
        old=(
            '    if dictProject.get("sMode") == "host":\n'
        ),
        new=(
            '    if True:\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestResolveContainerId::'
            'test_host_name_is_its_own_resource_id_without_docker'
        ),
        source='vaibify/gui/registryRoutes.py',
        # Dropping the host branch stores '' as the resource id, so
        # every downstream lookup keyed by the id goes blind for host
        # projects (the poison leg in the test raises on any touch).
        old=(
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if fbIsHostProject(sName):\n'
            '        return sName\n'
            '    connectionDocker = dictCtx.get("docker")\n'
        ),
        new=(
            '    connectionDocker = dictCtx.get("docker")\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestResolveContainerId::'
            'test_container_name_still_resolves_to_its_docker_id'
        ),
        source='vaibify/gui/registryRoutes.py',
        # The symmetric twin, stuck at host: a container claim stores
        # the NAME where its Docker id belongs, and the per-container
        # agent token is scoped to a container that never presents it.
        old=(
            '    if fbIsHostProject(sName):\n'
            '        return sName\n'
        ),
        new=(
            '    if True:\n'
            '        return sName\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestAgentTokenMint::'
            'test_host_claim_mints_no_agent_token'
        ),
        source='vaibify/gui/containerOwnership.py',
        # Dropping the mode branch mints a live agent credential for a
        # project whose agent lane must not exist (decision 6): the
        # credential is supposed to be UNMINTED, not undelivered.
        old=(
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if sResourceName and fbIsHostProject(sResourceName):\n'
            '        return ""\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestAgentTokenMint::'
            'test_container_claim_still_mints_a_real_token'
        ),
        source='vaibify/gui/containerOwnership.py',
        # The symmetric twin, stuck at unminted: every container claim
        # would carry an empty agent token and the in-container agent
        # lane dies hub-wide.
        old=(
            '    if sResourceName and fbIsHostProject(sResourceName):\n'
        ),
        new=(
            '    if sResourceName:\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestAuthorizeContainerHostBranch::'
            'test_host_connect_probes_nothing_and_pushes_nothing'
        ),
        source='vaibify/gui/pipelineServer.py',
        # Dropping the host branch execs a user probe against the host
        # leg and pushes a session file no container exists to receive;
        # both container touches swallow their own errors, so the test
        # asserts the recorded push and the resolved user instead.
        old=(
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if fbIsHostProject(sContainerId):\n'
            '        dictCtx["containerUsers"][sContainerId] = '
            'getpass.getuser()\n'
            '        return\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestAuthorizeContainerHostBranch::'
            'test_container_connect_still_probes_and_pushes'
        ),
        source='vaibify/gui/pipelineServer.py',
        # The symmetric twin, stuck at host: no container ever receives
        # its agent session again, and every container's user reads as
        # the HOST user.
        old=(
            '    if fbIsHostProject(sContainerId):\n'
            '        dictCtx["containerUsers"][sContainerId] = '
            'getpass.getuser()\n'
        ),
        new=(
            '    if True:\n'
            '        dictCtx["containerUsers"][sContainerId] = '
            'getpass.getuser()\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestReadinessRoute::'
            'test_host_project_is_ready_at_once_with_no_daemon'
        ),
        source='vaibify/gui/routes/systemRoutes.py',
        # Dropping the route's host branch probes an entrypoint marker
        # in a container that does not exist, so the post-claim
        # readiness poll answers an error shape instead of ready.
        old=(
            '        from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '        dictCtx["require"](sContainerId)\n'
            '        if fbIsHostProject(sContainerId):\n'
            '            return _fdictHostReadyResponse()\n'
        ),
        new=(
            '        dictCtx["require"](sContainerId)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostClaimPath.py::TestReadinessRoute::'
            'test_container_readiness_still_waits_for_the_entrypoint'
        ),
        source='vaibify/gui/routes/systemRoutes.py',
        # The symmetric twin, stuck at host: every container's boot
        # poll gets an instant false yes and the dashboard connects to
        # a container whose entrypoint has not finished.
        old=(
            '        if fbIsHostProject(sContainerId):\n'
            '            return _fdictHostReadyResponse()\n'
        ),
        new=(
            '        if True:\n'
            '            return _fdictHostReadyResponse()\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestClaimTakeOverVeto::'
            'test_host_run_vetoes_a_take_over_without_docker'
        ),
        source='vaibify/gui/registryRoutes.py',
        # Dropping the host branch sends a host name down the Docker
        # walk (the poison leg raises on the reachability probe), so a
        # live host run reads as idle and a foreign claim evicts the
        # owner mid-run.
        old=(
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if fbIsHostProject(sName):\n'
            '        from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '        return fbHostProjectHasLiveRun(appState, sName)\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestClaimTakeOverVeto::'
            'test_container_run_still_vetoes_through_docker'
        ),
        source='vaibify/gui/registryRoutes.py',
        # The symmetric twin, stuck at host: a container name is asked
        # for a journal it never writes, reads idle, and the take-over
        # veto stops protecting every container run.
        old=(
            '    if fbIsHostProject(sName):\n'
            '        from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '        return fbHostProjectHasLiveRun(appState, sName)\n'
        ),
        new=(
            '    if True:\n'
            '        from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '        return fbHostProjectHasLiveRun(appState, sName)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestReaperVeto::'
            'test_idle_host_project_permits_the_reap'
        ),
        source='vaibify/gui/serverLifespan.py',
        # Dropping the reaper's host branch routes host names to the
        # Docker walk, whose fail-safe reads every error as busy — an
        # idle host owner then can never be reaped. The IDLE-direction
        # test is the killer here precisely because the fail-safe
        # swallows the live-direction difference.
        old=(
            '        from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '        if fbIsHostProject(sName):\n'
            '            from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '            return fbHostProjectHasLiveRun(app.state, '
            'sName)\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestReaperVeto::'
            'test_container_run_still_vetoes_through_docker'
        ),
        source='vaibify/gui/serverLifespan.py',
        # The symmetric twin, stuck at host: a container mid-run reads
        # idle to the reaper, which then frees a flock over live work.
        old=(
            '        if fbIsHostProject(sName):\n'
            '            from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '            return fbHostProjectHasLiveRun(app.state, '
            'sName)\n'
        ),
        new=(
            '        if True:\n'
            '            from .hostBusyOracle import '
            'fbHostProjectHasLiveRun\n'
            '            return fbHostProjectHasLiveRun(app.state, '
            'sName)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestIdleWatchdogVeto::'
            'test_host_run_vetoes_self_exit_without_docker'
        ),
        source='vaibify/gui/serverLifespan.py',
        # Dropping the watchdog's host check makes a hub whose only
        # live work is a host run read as idle — it would self-SIGTERM
        # mid-run, which is the exact hazard the busy veto exists for.
        old=(
            '        if _fbAnyHeldHostProjectBusy(app):\n'
            '            return True\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestIdleWatchdogVeto::'
            'test_idle_host_only_hub_may_exit_with_no_daemon_at_all'
        ),
        source='vaibify/gui/serverLifespan.py',
        # The symmetric twin: leaving host names in the Docker
        # id-resolution walk turns every daemon error into the
        # fail-safe busy verdict, so a daemon-less host-only hub can
        # never retire itself.
        old=(
            '    setHeldNames = {\n'
            '        sName for sName in dictContainerOwners\n'
            '        if not fbIsHostProject(sName)\n'
            '    }\n'
        ),
        new=(
            '    setHeldNames = set(dictContainerOwners.keys())\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestComposedOracle::'
            'test_a_live_durable_task_reads_as_running'
        ),
        source='vaibify/gui/hostBusyOracle.py',
        # Dropping the in-process half blinds the oracle to a run whose
        # journal record has not landed yet or whose work is a guarded
        # mutation rather than a subprocess.
        old=(
            '    if commitCarrier.fbContainerHasLiveMutationWork('
            'appState, sName):\n'
            '        return True\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestReaperVeto::'
            'test_host_run_vetoes_the_reap_without_docker'
        ),
        source='vaibify/gui/hostBusyOracle.py',
        # Dropping the journal half blinds the oracle to a run that
        # outlived its hub (the crash-recovery case the write-ahead
        # journal exists for); the reaper's live-direction test is the
        # observation point because it feeds the oracle a real
        # journaled identity and no in-process durable task.
        old=(
            '    return operationJournal.fbAnyHostExecHolderLive'
            '(sName)\n'
        ),
        new=(
            '    return False\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestJournalHalf::'
            'test_a_live_recorded_group_reads_as_running'
        ),
        source='vaibify/config/operationJournal.py',
        # A kind filter that skips every record makes the journal half
        # vacuously idle — indistinguishable, to every veto, from a
        # project that has never run anything.
        #
        # The probe line is carried along only to disambiguate: the
        # holder LISTING grew an identical filter, and an `old` that
        # matches both would mutate a guarantee this test does not
        # observe.
        old=(
            '        if dictRecord.get("sKind") != "host-exec":\n'
            '            continue\n'
            '        dictProbe = _fdictProbeHelperOperation('
            'dictRecord, None)\n'
        ),
        new=(
            '        if True:\n'
            '            continue\n'
            '        dictProbe = _fdictProbeHelperOperation('
            'dictRecord, None)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostBusyOracle.py::TestJournalHalf::'
            'test_a_dead_holder_with_an_empty_group_reads_as_idle'
        ),
        source='vaibify/config/operationJournal.py',
        # The inverted twin, stuck at busy: a run that provably ended
        # (dead holder, empty group) keeps its project vetoed forever —
        # no reap, no take-over, no idle shutdown.
        old=(
            '        dictProbe = _fdictProbeHelperOperation(dictRecord, '
            'None)\n'
            '        if dictProbe["bHolderAlive"] or not '
            'dictProbe["bSettled"]:\n'
            '            return True\n'
            '    return False\n'
        ),
        new=(
            '        dictProbe = _fdictProbeHelperOperation(dictRecord, '
            'None)\n'
            '        if dictProbe["bHolderAlive"] or not '
            'dictProbe["bSettled"]:\n'
            '            return True\n'
            '    return True\n'
        ),
    ),
    # --- The root a project's files live under (host mode wave 4) ---
    #
    # One resolver, two directions. Stuck on the container volume, a
    # host project finds no Projects (an empty list, not an error) and
    # every connect is refused 403 "path traversal". Stuck on the
    # registry directory, every containerized project searches the
    # researcher's config folder from INSIDE the container, where it
    # does not exist. The two read identically in a report.
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testAHostProjectResolvesToItsRegisteredDirectory'
        ),
        source='vaibify/gui/projectRoots.py',
        old=(
            '    if not registryManager.fbIsHostProject(sResourceId):\n'
            '        return sContainerRoot\n'
        ),
        new='    if True:\n        return sContainerRoot\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testAContainerProjectResolvesToTheContainerRoot'
        ),
        source='vaibify/gui/projectRoots.py',
        old=(
            '    if not registryManager.fbIsHostProject(sResourceId):\n'
            '        return sContainerRoot\n'
        ),
        new='    if False:\n        return sContainerRoot\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testWorkflowDiscoverySearchesTheHostProjectDirectory'
        ),
        source='vaibify/gui/routes/workflowRoutes.py',
        # The route stops asking and takes the module default back.
        old=(
            '                dictCtx["docker"], sContainerId, '
            'sSearchRoot,\n'
        ),
        new='                dictCtx["docker"], sContainerId,\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testWorkflowDiscoveryStillSearchesTheVolumeForAContainer'
        ),
        source='vaibify/gui/routes/workflowRoutes.py',
        # The route reads the directory out of the registry itself
        # instead of going through the resolver -- the plausible
        # shortcut, and wrong because it never consults the MODE. Every
        # container project would search its own config folder.
        old=(
            '        sSearchRoot = projectRoots.fsResolveProjectRoot(\n'
            '            sContainerId, '
            'workflowManager.DEFAULT_SEARCH_ROOT,\n'
            '        )\n'
        ),
        new=(
            '        from vaibify.config import registryManager\n'
            '        sSearchRoot = (registryManager.fdictGetProject('
            'sContainerId) or {}).get(\n'
            '            "sDirectory", '
            'workflowManager.DEFAULT_SEARCH_ROOT)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testAHostWorkflowPathIsMeasuredAgainstItsProjectDirectory'
        ),
        source='vaibify/gui/pipelineServer.py',
        # The connect guard takes the container volume back as its
        # boundary, refusing every legitimate host workflow path with
        # the code that means "you tried to escape".
        old=(
            '    fsValidatePathWithinRoot(sNormalized, sProjectRoot)\n'
        ),
        new=(
            '    fsValidatePathWithinRoot(sNormalized, WORKSPACE_ROOT)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testTheExistenceProbeAcceptsPathsInsideTheHostProject'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        old=(
            '        sProjectRoot = projectRoots.fsResolveProjectRoot(\n'
            '            sContainerId, sWorkspaceRoot,\n'
            '        )\n'
        ),
        new='        sProjectRoot = sWorkspaceRoot\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testTheExistenceProbeStillJailsAContainerToTheVolume'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        # The registry read that skips the mode -- the shortcut that
        # looks like it does the same thing and jails every
        # containerized project inside the researcher's config folder.
        old=(
            '        sProjectRoot = projectRoots.fsResolveProjectRoot(\n'
            '            sContainerId, sWorkspaceRoot,\n'
            '        )\n'
        ),
        new=(
            '        from vaibify.config import registryManager\n'
            '        sProjectRoot = (registryManager.fdictGetProject(\n'
            '            sContainerId) or {}).get('
            '"sDirectory", sWorkspaceRoot)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testAHostProjectsFigureIsServedFromItsOwnDirectory'
        ),
        source='vaibify/gui/routes/figureRoutes.py',
        # Both the HEAD probe and the GET serve resolve the root; the
        # honest mutation is "the figure lane stopped asking", which
        # is both of them.
        old=(
            '        sProjectRoot = projectRoots.fsResolveProjectRoot(\n'
            '            sContainerId, WORKSPACE_ROOT,\n'
            '        )\n'
        ),
        new='        sProjectRoot = WORKSPACE_ROOT\n',
        iExpectedOccurrences=2,
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeProjectRoots.py::'
            'testAContainerFigureIsStillJailedToTheVolume'
        ),
        source='vaibify/gui/routes/figureRoutes.py',
        old=(
            '        sProjectRoot = projectRoots.fsResolveProjectRoot(\n'
            '            sContainerId, WORKSPACE_ROOT,\n'
            '        )\n'
        ),
        new=(
            '        from vaibify.config import registryManager\n'
            '        sProjectRoot = (registryManager.fdictGetProject(\n'
            '            sContainerId) or {}).get('
            '"sDirectory", WORKSPACE_ROOT)\n'
        ),
        iExpectedOccurrences=2,
    ),
    # --- The daemon gate names its resource (host mode wave 4) ---
    #
    # Host mode exists for the researcher who has no Docker, so a hub
    # serving a host project must never answer "install Docker". The
    # inverse is just as bad: a gate that stops asking lets a
    # container request reach code holding None for a connection.
    Falsification(
        nodeid=(
            'tests/testHostModeDaemonGate.py::'
            'testAHostProjectIsServedWhenNoDaemonIsReachable'
        ),
        source='vaibify/gui/routes/fileRoutes.py',
        # The bare call every one of these sites had before the sweep.
        old=(
            '        import asyncio\n'
            '        dictCtx["require"](sContainerId)\n'
            '        listInput = request.saRelativePaths or []\n'
        ),
        new=(
            '        import asyncio\n'
            '        dictCtx["require"]()\n'
            '        listInput = request.saRelativePaths or []\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostModeDaemonGate.py::'
            'testAContainerProjectStillGetsTheDaemonDiagnosis'
        ),
        source='vaibify/gui/dockerStatus.py',
        old=(
            '    if sResourceId is not None and '
            'fbIsHostProject(sResourceId):\n'
            '        return\n'
        ),
        new='    if True:\n        return\n',
    ),
    # --- The picker's host tile (host mode wave 4) ---
    #
    # Frontend mutations, observable only to a test that loads the
    # page; the harness defers them by the `browser` marker on a host
    # with no Playwright rather than scoring them blind.
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testAHostTileCarriesItsNameAsTheResourceId'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        # A host registry entry has no sContainerId, so this renders an
        # empty resource id: the click path resolves nothing and
        # returns in silence -- no error, no toast, no diagnosis.
        old=(
            '        var sId = bHost\n'
            '            ? (dictContainer.sName || "")\n'
            '            : (dictContainer.sContainerId || "");\n'
        ),
        new='        var sId = dictContainer.sContainerId || "";\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testAHostTileOffersNoContainerLifecycleAction'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        old=(
            '           researcher from being offered them. */\n'
            '        if (bHost) return "";\n'
        ),
        new=(
            '           researcher from being offered them. */\n'
            '        if (false) return "";\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testTheContainerTileKeepsEveryLifecycleAction'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        old=(
            '           researcher from being offered them. */\n'
            '        if (bHost) return "";\n'
        ),
        new=(
            '           researcher from being offered them. */\n'
            '        if (true) return "";\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testClickingAMissingHostProjectRefusesAndClaimsNothing'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        # Without the branch a host tile falls through to the container
        # click path, which tries to START a container it has none of.
        old='        if (elTile && elTile.dataset.mode === "host") {\n',
        new='        if (false) {\n',
    ),
    # --- The uncontained-execution disclosure (host mode wave 4) ---
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testEnteringAHostProjectWarnsBeforeTheProjectOpens'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        # Uncontained execution shipped with no disclosure at all.
        old=(
            '        if (elTile.dataset.warningAcknowledged !== "true") '
            '{\n'
            '            _fnWarnBeforeEnteringHostProject(sName, '
            'elTile);\n'
            '            return;\n'
            '        }\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testGoingBackReleasesTheProjectItJustClaimed'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        # Declining closes the dialog and the claim stands: the project
        # reads as in use by a session that never opened it.
        old=(
            '                fnOnCancel: async function () {\n'
            '                    await fnReleaseClaim(sName);\n'
            '                    await fnLoadContainers();\n'
            '                },\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testContinuingShowsThePermanentUncontainedBadge'
        ),
        source='vaibify/gui/static/scriptApplication.js',
        old='        var bHost = sProjectMode === "host";\n',
        new='        var bHost = false;\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testAContainerProjectShowsNoUncontainedBadge'
        ),
        source='vaibify/gui/static/scriptApplication.js',
        # The other direction: a false alarm on every project trains
        # the researcher to ignore the real one.
        old='        var bHost = sProjectMode === "host";\n',
        new='        var bHost = true;\n',
    ),
    # --- Adding a host project from the dialog (host mode wave 4) ---
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testChoosingTheHostTierReOffersBothPathsWithTheDisclosure'
        ),
        source='vaibify/gui/static/scriptContainerManager.js',
        # Wired to one path instead of the stage: the other silently
        # disappears, and an existing host directory can then only be
        # registered as a container.
        old=(
            '        document.getElementById("btnChoiceHostMode")'
            '.addEventListener(\n'
            '            "click", function () { '
            '_fnShowAddChoiceStage(true); }\n'
            '        );\n'
        ),
        new=(
            '        document.getElementById("btnChoiceHostMode")'
            '.addEventListener(\n'
            '            "click", function () { '
            'VaibifyWorkflowManager.fnOpenCreateWizard("host"); }\n'
            '        );\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostProjectPicker.py::'
            'testTheHostCreateWizardSkipsEveryContainerPage'
        ),
        source='vaibify/gui/static/scriptWorkflowManager.js',
        # The host wizard walks the container page list, asking for a
        # Python version, repositories, features and packages that
        # govern an image the project will never have.
        old=(
            '        return _dictWizardData.sMode === "host"\n'
            '            ? _T_HOST_WIZARD_PAGES : '
            '_T_CONTAINER_WIZARD_PAGES;\n'
        ),
        new='        return _T_CONTAINER_WIZARD_PAGES;\n',
    ),
    # --- Cancelling a host run (host mode wave 5) ---
    #
    # The dangerous mutant on this path is not "Cancel does nothing".
    # It is "Cancel signals a group it cannot prove is still ours",
    # which on a researcher's own machine means killing a stranger's
    # process with the researcher's authority. Both halves of the one
    # predicate are therefore mutated, and the refusing direction is
    # scored by a test in which a LIVE process must survive.
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testAProvenGroupIsTerminated'
        ),
        source='vaibify/host/hostCancellation.py',
        old='    if dictHolder["bHolderProven"]:\n',
        new='    if False:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testARecycledIdentityIsRefusedAndItsProcessSurvives'
        ),
        source='vaibify/host/hostCancellation.py',
        # "Make cancel more reliable by signalling whatever the record
        # names" -- the plausible mutation, and the one the whole
        # module exists to forbid.
        old='    if dictHolder["bHolderProven"]:\n',
        new='    if True:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testARunThatAlreadyFinishedIsReportedAsExitedNotRefused'
        ),
        source='vaibify/host/hostCancellation.py',
        old=(
            '    if fbProcessGroupProvedEmpty(iProcessGroup):\n'
            '        dictOutcome["listAlreadyExited"].append({\n'
        ),
        new=(
            '    if False:\n'
            '        dictOutcome["listAlreadyExited"].append({\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testAnUnusableProcessGroupIsNeverSignalled'
        ),
        source='vaibify/host/hostCancellation.py',
        # os.killpg(0, SIGKILL) signals the CALLER's process group:
        # without this guard a journal record carrying a zero group id
        # makes Cancel kill the hub.
        old=(
            '    if not fbIsUsablePid(iProcessGroup):\n'
            '        return\n'
        ),
        new='    if False:\n        return\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testTheListingNamesOnlyHostExecRecords'
        ),
        source='vaibify/config/operationJournal.py',
        # Both kinds carry a holder pid, so an unfiltered listing hands
        # the terminator the hub's own carrier worker.
        old=(
            '        if dictRecord.get("sKind") != "host-exec":\n'
            '            continue\n'
            '        listHolders.append(\n'
        ),
        new='        listHolders.append(\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testTheListingMarksARecycledIdentityUnproven'
        ),
        source='vaibify/config/operationJournal.py',
        # The bare existence check -- the shortcut taken when reading a
        # start clock looks like overhead. It also fails the
        # recycled-identity test above, because one implementation has
        # two observers; that one proves the consequence, this one
        # proves the property.
        old=(
            '        and fbIsProcessAliveSince(\n'
            '            iHolderPid, dictRecord.get("sInFlightIso"),\n'
            '        )\n'
        ),
        new=(
            '        and fbIsProcessAliveSince(\n'
            '            iHolderPid, None,\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testAnUnreadableJournalRaisesInsteadOfReportingNothing'
            'ToCancel'
        ),
        source='vaibify/config/operationJournal.py',
        # Falling back the way the busy-oracle predicate does. Safe for
        # a veto, dishonest for an action: it answers "0 processes"
        # about a machine whose work could not be enumerated at all.
        old=(
            '    if dictOutcomeRead["sReadState"] not in '
            '("absent", "valid"):\n'
            '        raise OperationJournalUnreadableError(\n'
        ),
        new=(
            '    if False:\n'
            '        raise OperationJournalUnreadableError(\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testKillingAHostProjectSignalsTheJournalAndNeverThe'
            'ProcessTable'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='        if fbIsHostProject(sContainerId):\n',
        new='        if False:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testKillingAContainerProjectStillSweepsItsProcessTable'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        old='        if fbIsHostProject(sContainerId):\n',
        new='        if True:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostCancel.py::'
            'testARefusedCancellationReachesTheResponse'
        ),
        source='vaibify/gui/routes/pipelineRoutes.py',
        # The key stays, so the container-direction test above is
        # untouched and this kill isolates: what is lost is only the
        # CONTENT, which is the whole report.
        old='            "listCancellationRefusals": listRefused,\n',
        new='            "listCancellationRefusals": [],\n',
    ),
    # The two frontend halves. A refusal the dashboard never renders
    # is a dashboard that says the machine is quiet.
    Falsification(
        nodeid=(
            'tests/browser/testHostCancelSurface.py::'
            'testTheHostConfirmationNeverSaysTheProcessesAreInAContainer'
        ),
        source='vaibify/gui/static/scriptPipelineRunner.js',
        old='        if (VaibifyApp.fsGetProjectMode() === "host") {\n',
        new='        if (false) {\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostCancelSurface.py::'
            'testTheContainerConfirmationIsUnchanged'
        ),
        source='vaibify/gui/static/scriptPipelineRunner.js',
        old='        if (VaibifyApp.fsGetProjectMode() === "host") {\n',
        new='        if (true) {\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostCancelSurface.py::'
            'testARefusedCancellationIsShownAndNotRoundedDownToZero'
        ),
        source='vaibify/gui/static/scriptPipelineRunner.js',
        old='        if (listRefusals.length > 0) {\n',
        new='        if (false) {\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testHostCancelSurface.py::'
            'testACleanCancellationRaisesNoAlarm'
        ),
        source='vaibify/gui/static/scriptPipelineRunner.js',
        new='        if (true) {\n',
        old='        if (listRefusals.length > 0) {\n',
    ),
    # --- The host-diagnostics sweeper (host mode wave 5) ---
    #
    # A sweeper does permanent harm by working correctly on the wrong
    # tree, and this repository has already lost a container to one.
    # Both directions of the TTL, plus the two ways a traversal can be
    # talked into following a link out of the subtree.
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testStaleOperationDirectoriesGoAndRecentOnesStay'
        ),
        source='vaibify/host/hostScratch.py',
        old='    if dictOperation["fModifiedEpoch"] >= fCutoff:\n',
        new='    if False:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testAPlantedSymlinkIsUnlinkedAndItsTargetSurvives'
        ),
        source='vaibify/host/hostScratch.py',
        # Describing entries through the link: the age read becomes the
        # TARGET's age and the size becomes the target's size, so the
        # sweeper is deciding about somebody else's file.
        old='        tStat = os.lstat(sPath)\n',
        new='        tStat = os.stat(sPath)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testAPlantedSymlinkToADirectoryIsUnlinkedNotWalked'
        ),
        source='vaibify/host/hostScratch.py',
        # The module's entire symlink discipline is this one line.
        # Without the islink half, a link TO a directory is a directory
        # to every traversal and removal here: rmtree then refuses the
        # symlinked root and the ignored error leaves the entry forever.
        # Not observable to the link-to-a-FILE test beside it, which is
        # why that case is scored separately.
        old=(
            '    return os.path.isdir(sPath) and not '
            'os.path.islink(sPath)\n'
        ),
        new='    return os.path.isdir(sPath)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testTheCredentialStoreBesideTheSubtreeIsNeverTouched'
        ),
        source='vaibify/host/hostScratch.py',
        # Rooting the traversal one level up, at the shared parent. The
        # sweep then walks the ephemeral credential store, where age is
        # emphatically not evidence of garbage and where a deletion has
        # already cost this project a container.
        old=(
            '    for sProjectPath in _flistScanPathsQuietly('
            '_S_HOST_DIAGNOSTICS_ROOT):\n'
            '        if not _fbIsRealDirectory(sProjectPath):\n'
            '            listOperations.append('
            '_fdictDescribeEntry(sProjectPath))\n'
        ),
        new=(
            '    for sProjectPath in _flistScanPathsQuietly('
            'os.path.dirname(_S_HOST_DIAGNOSTICS_ROOT)):\n'
            '        if not _fbIsRealDirectory(sProjectPath):\n'
            '            listOperations.append('
            '_fdictDescribeEntry(sProjectPath))\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testTheByteCapRetiresTheOldestSurvivorsFirst'
        ),
        source='vaibify/host/hostScratch.py',
        # Newest-first: the cap still holds and the capture the
        # researcher is most likely to want is the one thrown away.
        old=(
            '        listOperations, key=lambda dictEntry: '
            'dictEntry["fModifiedEpoch"],\n'
        ),
        new=(
            '        listOperations, key=lambda dictEntry: '
            '-dictEntry["fModifiedEpoch"],\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostScratchSweeper.py::'
            'testEveryLevelOfTheSubtreeIsPrivateToTheUser'
        ),
        source='vaibify/host/hostScratch.py',
        # The obvious one-liner. Its leaf is 0700 and every level above
        # it is the default, which is what the first draft shipped.
        old='    _fnEnsurePrivateDirectory(sDirectory)\n',
        new=(
            '    os.makedirs(sDirectory, '
            'mode=I_SCRATCH_DIRECTORY_MODE, exist_ok=True)\n'
        ),
    ),
    # --- Abandoning a host journal (host mode wave 5) ---
    #
    # Two exits from a quarantine that look alike and are not: one
    # proves, one asserts and is recorded. The mutations below are the
    # ways that distinction can be lost.
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testAbandoningRecordsTheAssertionAndThenClearsTheMarker'
        ),
        source='vaibify/config/reconciliation.py',
        # An anonymous deletion. Everything a researcher sees is
        # identical; the only difference is that nobody can ever say
        # who gave up on this project's proof.
        old=(
            '        _fnRecordAbandonmentOnce('
            'sContainerName, sExpectedSha256)\n'
        ),
        new='        pass\n',
    ),
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testTheAuditIsOnDiskBeforeTheMarkerIsUnlinked'
        ),
        source='vaibify/config/reconciliation.py',
        # The ordering inverted: unlink first, record after. Correct in
        # every run that finishes, and unrecoverable in the one that
        # does not.
        old=(
            '        _fnAssertMarkerStillHashesTo('
            'sContainerName, sExpectedSha256)\n'
            '        _fnRecordAbandonmentOnce('
            'sContainerName, sExpectedSha256)\n'
            '        try:\n'
        ),
        new=(
            '        _fnAssertMarkerStillHashesTo('
            'sContainerName, sExpectedSha256)\n'
            '        try:\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testRerunningAfterACrashRecordsOneEventNotTwo'
        ),
        source='vaibify/config/reconciliation.py',
        old=(
            '    if abandonmentAudit.fbHasRecordedAbandonment(\n'
            '        sContainerName, sExpectedSha256,\n'
            '    ):\n'
            '        return\n'
        ),
        new='    if False:\n        return\n',
    ),
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testAContainerProjectCannotBeAbandoned'
        ),
        source='vaibify/config/reconciliation.py',
        old=(
            '    _fnRequireHostProject(sContainerName, '
            '"Abandoning a journal")\n'
        ),
        new='    pass\n',
    ),
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testAHostProjectCannotBeBreakGlassed'
        ),
        source='vaibify/config/reconciliation.py',
        # The other direction: the PROVING exit run against a project
        # it can prove nothing about. It stops nothing, learns nothing
        # by stopping nothing, and clears the marker regardless.
        old=(
            'def _fnRefuseHostProject(sContainerName, sSubject, '
            'sAlternative):\n'
            '    """Refuse an operation whose containment a host project '
            'cannot have."""\n'
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if fbIsHostProject(sContainerName):\n'
        ),
        new=(
            'def _fnRefuseHostProject(sContainerName, sSubject, '
            'sAlternative):\n'
            '    """Refuse an operation whose containment a host project '
            'cannot have."""\n'
            '    from vaibify.config.registryManager import '
            'fbIsHostProject\n'
            '    if False:\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testAbandonHostJournal.py::'
            'testAStaleHashWritesNoAuditAndClearsNothing'
        ),
        source='vaibify/config/reconciliation.py',
        # Recording the assertion before checking the hash: a
        # misdirected request then leaves behind an audit entry saying
        # a researcher abandoned a marker they never touched.
        old=(
            '        _fnAssertMarkerStillHashesTo('
            'sContainerName, sExpectedSha256)\n'
            '        _fnRecordAbandonmentOnce('
            'sContainerName, sExpectedSha256)\n'
        ),
        new=(
            '        _fnRecordAbandonmentOnce('
            'sContainerName, sExpectedSha256)\n'
            '        _fnAssertMarkerStillHashesTo('
            'sContainerName, sExpectedSha256)\n'
        ),
    ),
    # --- What a quarantined HOST project says and offers (wave 5) ---
    Falsification(
        nodeid=(
            'tests/testHostQuarantineSurface.py::'
            'testAHostQuarantineNamesTheMachineAndWhatCannotBeSeen'
        ),
        source='vaibify/config/containerLock.py',
        old='    if not fbIsHostProject(sProjectName):\n',
        new='    if True:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostQuarantineSurface.py::'
            'testAContainerQuarantineKeepsItsOwnSentence'
        ),
        source='vaibify/config/containerLock.py',
        # The other direction: every containerized researcher told to
        # terminate recorded host processes for work that runs
        # somewhere else entirely.
        old='    if not fbIsHostProject(sProjectName):\n',
        new='    if False:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostQuarantineSurface.py::'
            'testTerminateRecordedSignalsTheJournaledGroupThenReproves'
        ),
        source='vaibify/cli/commandReconcile.py',
        # Terminate, report, stop. The machine changes and the
        # dashboard does not: the project stays quarantined by the very
        # record the researcher just settled.
        old=(
            '        iOutcome = fiTerminateRecordedHostProcesses('
            'sContainerName)\n'
            '        if iOutcome != 0:\n'
            '            return iOutcome\n'
            '        return fiRunCrashTimeReconcile('
            'sContainerName, bAssumeYes)\n'
        ),
        new=(
            '        return fiTerminateRecordedHostProcesses('
            'sContainerName)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testHostQuarantineSurface.py::'
            'testTerminateRecordedRefusesAContainerProject'
        ),
        source='vaibify/cli/commandReconcile.py',
        # Without the mode check a containerized project's journal --
        # whose holder pids are the HUB's own workers -- is handed to a
        # process-group terminator.
        old='    if not fbIsHostProject(sContainerName):\n',
        new='    if False:\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostQuarantineSurface.py::'
            'testTerminateRecordedRefusesWhileALiveHubHoldsTheProject'
        ),
        source='vaibify/cli/commandReconcile.py',
        # The flag falls through to the terminator while a hub is live,
        # killing processes it is still streaming from and leaving it
        # reading pipes whose writers vanished.
        old='        if bTerminateRecorded:\n',
        new='        if False:\n',
    ),
    # --- Backend-supplied workspace root + the paused label (wave 5) ---
    Falsification(
        nodeid=(
            'tests/testWorkspaceRootHandshake.py::'
            'testAHostProjectIsToldItsOwnDirectory'
        ),
        source='vaibify/gui/pipelineServer.py',
        old=(
            '        return fsResolveProjectRoot('
            'sResourceId, WORKSPACE_ROOT)\n'
        ),
        new='        return WORKSPACE_ROOT\n',
    ),
    Falsification(
        nodeid=(
            'tests/testWorkspaceRootHandshake.py::'
            'testAContainerProjectIsStillToldTheContainerRoot'
        ),
        source='vaibify/gui/pipelineServer.py',
        # The other direction: every containerized dashboard pointed at
        # a HOST path the container cannot see.
        old=(
            '        return fsResolveProjectRoot('
            'sResourceId, WORKSPACE_ROOT)\n'
        ),
        new=(
            '        from vaibify.config.registryManager import '
            'fdictGetProject\n'
            '        return (fdictGetProject(sResourceId) or {}).get('
            '"sDirectory", WORKSPACE_ROOT)\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/browser/testWorkspaceRootReachesThePanels.py::'
            'testTheFilePanelBrowsesTheRootTheServerSent'
        ),
        source='vaibify/gui/static/scriptFiles.js',
        # Storing the answer and not using it: green everywhere, and
        # the host file browser still opens a path nobody has.
        old=(
            '        sCurrentPath = sPath || '
            'VaibifyApp.fsGetWorkspaceRoot();\n'
        ),
        new='        sCurrentPath = sPath || "/workspace";\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testWorkspaceRootReachesThePanels.py::'
            'testTheContainerRootStillReachesThePanel'
        ),
        source='vaibify/gui/static/scriptApplication.js',
        # Dropping the fallback: an older server that sends no root at
        # all leaves every panel browsing "undefined".
        old=(
            '        _dictSessionState.sWorkspaceRoot = '
            'sWorkspaceRoot || "/workspace";\n'
        ),
        new='        _dictSessionState.sWorkspaceRoot = sWorkspaceRoot;\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testPausedBadgeIndicator.py::'
            'testAPausedRefreshSaysSoAndNamesWhatIsBusy'
        ),
        source='vaibify/gui/static/scriptGitBadges.js',
        # Keeping the last known map without saying so: badges from an
        # earlier minute, read as current.
        old='            _fnShowRefreshPaused(true, dictResult.sPausedBy);\n',
        new='            void dictResult.sPausedBy;\n',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testPausedBadgeIndicator.py::'
            'testACompletedRefreshClearsTheLabel'
        ),
        source='vaibify/gui/static/scriptGitBadges.js',
        # The other direction: a staleness warning that is always on is
        # one nobody reads.
        old='        _fnShowRefreshPaused(false, "");\n        var dictDiff = _fbBadgeMapChanged(\n',
        new='        var dictDiff = _fbBadgeMapChanged(\n',
    ),
    # --- The adversarial host path corpus (host mode wave 5) ---
    Falsification(
        nodeid=(
            'tests/testHostPathGuardCorpus.py::'
            'testEveryPathTakingMethodRefusesEveryHostilePath'
        ),
        source='vaibify/host/hostConnection.py',
        # The guard dropped from the ONE method that swallows OSError,
        # where an escape looks exactly like an ordinary absent file.
        old=(
            '                tStat = os.stat(\n'
            '                    self._fsValidateHostPath('
            'sContainerId, sPath),\n'
            '                )\n'
        ),
        new='                tStat = os.stat(sPath)\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostPathGuardCorpus.py::'
            'testTheLegitimateProjectPathsAreStillAdmitted'
        ),
        source='vaibify/host/hostConnection.py',
        # The other direction: a guard that refuses everything is not
        # secure, it is broken, and a refusal-only corpus cannot tell.
        old='        for sAllowedRoot in (sProjectRoot, sScratchRoot):\n',
        new='        for sAllowedRoot in ():\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostPathGuardCorpus.py::'
            'testEveryPathTakingMethodIsInTheCorpusTable'
        ),
        source='vaibify/host/hostConnection.py',
        # The class-level failure, expressed as what it actually is: a
        # new path-taking method arriving with no guard and no corpus
        # entry. The refusal corpus above passes happily beside it.
        old=(
            '    def fdictReadFilesystemUsage(self, sContainerId, sPath):\n'
        ),
        new=(
            '    def fbContainerPathIsReadable(self, sContainerId, sPath):\n'
            '        """A new entry point nobody added to the corpus."""\n'
            '        del sContainerId\n'
            '        return os.access(sPath, os.R_OK)\n'
            '\n'
            '    def fdictReadFilesystemUsage(self, sContainerId, sPath):\n'
        ),
    ),
    # --- Process signalling joins the dangerous vocabulary (2026-08-10) ---
    Falsification(
        nodeid=(
            'tests/testMutationInventory.py::'
            'testSignallingAProcessIsAnAcquisition'
        ),
        source='tools/generateMutationInventory.py',
        # Emptying the vocabulary returns all seven of this package's
        # signalling sites to being invisible, including the one that
        # aims a group kill at a number read off disk.
        old='SET_OS_SIGNAL_MEMBERS = frozenset({"kill", "killpg"})\n',
        new='SET_OS_SIGNAL_MEMBERS = frozenset()\n',
    ),
    # --- The Repos poll becomes a typed read (2026-08-10) ---
    #
    # One migration, five separately-observable guarantees, because the
    # shell script it replaced was wrong in five separate ways and a
    # single happy-path test would have covered none of them.
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testTheBatchIssuesNoCommandAtAll'
        ),
        source='vaibify/gui/trackedReposManager.py',
        # The migration undone: back to an exec, and with it the route
        # goes back outside the commit-guard boundary.
        old=(
            '        listRaw = connectionDocker.flistReadGitRepoStatuses(\n'
            '            sContainerId, listRepoPaths,\n'
            '        )\n'
        ),
        new=(
            '        listRaw = connectionDocker.ftResultExecuteCommand(\n'
            '            sContainerId, "echo []",\n'
            '        ) and []\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testAnswersAreKeyedByPathNotByPosition'
        ),
        source='vaibify/gui/trackedReposManager.py',
        # Positional matching: the natural way to write it, and
        # silently wrong -- every repository inherits its neighbour's
        # branch, url and dirtiness, and the panel looks plausible.
        old=(
            '    return [\n'
            '        _fdictStatusFromRawRecord(sName, dictByPath.get(sPath))\n'
            '        for sName, sPath in zip(listRepoNames, listRepoPaths)\n'
            '    ]\n'
        ),
        new=(
            '    return [\n'
            '        _fdictStatusFromRawRecord(\n'
            '            sName,\n'
            '            listRaw[iIndex] if iIndex < len(listRaw) else None,\n'
            '        )\n'
            '        for iIndex, sName in enumerate(listRepoNames)\n'
            '    ]\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testAFailedReadReportsMissingRatherThanRaising'
        ),
        source='vaibify/gui/trackedReposManager.py',
        old='    except (OSError, ValueError):\n',
        new='    except ():\n',
    ),
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testARepositoryNameNeverReachesAShell'
        ),
        source='vaibify/gui/trackedReposManager.py',
        # Names re-entering command text. Distinct from the
        # no-command mutant above: this one keeps the typed read AND
        # adds a shell beside it, which is how a migration half-lands.
        old=(
            '    listRepoPaths = [\n'
            '        posixpath.join(sRepoRoot, sName) for sName in listRepoNames\n'
            '    ]\n'
        ),
        new=(
            '    listRepoPaths = [\n'
            '        posixpath.join(sRepoRoot, sName) for sName in listRepoNames\n'
            '    ]\n'
            '    for sName in listRepoNames:\n'
            '        connectionDocker.ftResultExecuteCommand(\n'
            '            sContainerId, "git -C /workspace/" + sName + " status",\n'
            '        )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testTheStatusRouteNoLongerAwaitsACarrierMode'
        ),
        source='vaibify/gui/routeScope.py',
        # Putting it back on the ambient admission, where the
        # declaration buys nothing.
        old=(
            '    ("GET", "/api/pipeline/{sContainerId}/workflow-discovery"),\n'
            '    ("GET", "/api/repos/{sContainerId}/{sRepoName}/dirty-files"),\n'
            '    ("GET", "/api/steps/{sContainerId}"),\n'
        ),
        new=(
            '    ("GET", "/api/pipeline/{sContainerId}/workflow-discovery"),\n'
            '    ("GET", "/api/repos/{sContainerId}/status"),\n'
            '    ("GET", "/api/repos/{sContainerId}/{sRepoName}/dirty-files"),\n'
            '    ("GET", "/api/steps/{sContainerId}"),\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testRepoStatusTypedRead.py::'
            'testAFilenameContainingAPipeNoLongerCorruptsTheAnswer'
        ),
        source='vaibify/gui/trackedReposManager.py',
        # The old transport put back: porcelain split at pipes, so a
        # filename containing one becomes two lines. Observable only
        # through the artefact filter, which is why the test's fixture
        # is an artefact rather than an ordinary file.
        old='        fsFilterArtifacts(dictRecord.get("sPorcelain") or ""),\n',
        new=(
            '        fsFilterArtifacts(\n'
            '            (dictRecord.get("sPorcelain") or "").replace(\n'
            '                "|", chr(10),\n'
            '            ),\n'
            '        ),\n'
        ),
    ),
    # --- What the FIRST real host workflow found (2026-08-10) ---
    #
    # Four backend paths written as the container constant, each found
    # by opening a host workflow in a browser and reading the network
    # log, and each breaking a different part of the journey. Plus the
    # relative-path rule, which the file poll disproved on the first
    # tick.
    Falsification(
        nodeid=(
            'tests/testWorkspaceRootHandshake.py::'
            'testThePipelineStatePathFollowsTheProject'
        ),
        source='vaibify/gui/pipelineState.py',
        old=(
            '    return posixpath.join(\n'
            '        fsResolveProjectRoot(sResourceId, WORKSPACE_ROOT),\n'
            '        _S_STATE_RELATIVE,\n'
            '    )\n'
        ),
        new='    return S_STATE_PATH\n',
    ),
    Falsification(
        nodeid=(
            'tests/testWorkspaceRootHandshake.py::'
            'testTheLogsDirectoryFollowsTheProject'
        ),
        source='vaibify/gui/workflowManager.py',
        # The symptom this one produced is the reason it is registered
        # separately: the step SUCCEEDED and the pipeline reported exit
        # 1, so a researcher would have gone looking at their script.
        old=(
            '    return posixpath.join(\n'
            '        fsResolveProjectRoot(sResourceId, DEFAULT_SEARCH_ROOT),\n'
            '        VAIBIFY_LOGS_DIR,\n'
            '    )\n'
        ),
        new=(
            '    return posixpath.join(\n'
            '        DEFAULT_SEARCH_ROOT, VAIBIFY_LOGS_DIR,\n'
            '    )\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testWorkspaceRootHandshake.py::'
            'testTheTrackedRepositoriesSidecarFollowsTheProject'
        ),
        source='vaibify/gui/trackedReposManager.py',
        old=(
            '    return posixpath.join(\n'
            '        fsRepositoryRootFor(sResourceId), _S_SIDECAR_RELATIVE,\n'
            '    )\n'
        ),
        new='    return S_TRACKED_REPOS_PATH\n',
    ),
    Falsification(
        nodeid=(
            'tests/testHostConnection.py::TestHostPathGuard::'
            'test_relative_path_resolves_against_the_project_root'
        ),
        source='vaibify/host/hostConnection.py',
        # The rule this replaced. Written before any host workflow had
        # been opened, and disproved by the first one: the file poll
        # asks about repo-relative paths, which is the wire contract.
        old=(
            '        if not os.path.isabs(sPath):\n'
            '            sPath = os.path.join(sProjectRoot, sPath)\n'
        ),
        new=(
            '        if not os.path.isabs(sPath):\n'
            '            raise HostPathOutsideProjectError(sPath)\n'
        ),
    ),
    # --- The launch path told the researcher three untrue things ---
    #
    # Found by a maintainer trying to follow a test plan (2026-08-10).
    # From outside they compose into "vaibify is hanging", which is not
    # what was happening and is why each is scored separately.
    Falsification(
        nodeid=(
            'tests/testGuiLaunchHonesty.py::'
            'testGuiWithNoProjectLaunchesTheHubNotTheSingleProjectViewer'
        ),
        source='vaibify/cli/main.py',
        # What shipped: the single-project viewer, with a container
        # path as its workspace root, while the help promised the
        # landing page.
        old='        fnLaunchHub(None)\n        return\n',
        new='        pass\n',
    ),
    Falsification(
        nodeid=(
            'tests/testGuiLaunchHonesty.py::'
            'testANamedProjectThatCannotResolveStopsTheCommand'
        ),
        source='vaibify/cli/main.py',
        # The swallowed exit, restored: a resolver's sys.exit caught
        # and discarded, so a fatal message printed as noise before a
        # successful start.
        # Anchored through the following line: this call spells the
        # same three lines in three commands, and only the gui one is
        # the subject here.
        old=(
            '    configProject = fconfigResolveProject(sProjectName)\n'
            '    sUrl = "http://127.0.0.1:8050"\n'
        ),
        new=(
            '    try:\n'
            '        configProject = fconfigResolveProject(sProjectName)\n'
            '    except SystemExit:\n'
            '        configProject = None\n'
            '    sUrl = "http://127.0.0.1:8050"\n'
        ),
    ),
    Falsification(
        nodeid=(
            'tests/testGuiLaunchHonesty.py::'
            'testTheAnnouncementNeverOffersTheAddressAsTheWayIn'
        ),
        source='vaibify/cli/main.py',
        # The bare address alone, which is the one string that cannot
        # sign in.
        old=(
            '    click.echo(\n'
            '        "Opening your browser. The dashboard signs in with '
            'a one-time "\n'
            '        "link, so that tab is the way in — this address '
            'alone cannot "\n'
            '        "sign in. If no window opened, re-run this command."\n'
            '    )\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testUnsignedTabSaysSo.py::'
            'testAnUnsignedTabNamesTheProblemAndTheFix'
        ),
        source='vaibify/gui/static/scriptApplication.js',
        # Carrying on with no credential: the first 401 kills
        # initialization, the static placeholder stays, and a refused
        # dashboard is pixel-identical to a slow one.
        old=(
            '        if (!_dictSessionState.sSessionToken) {\n'
            '            _fnReportNoCredentialAndStop();\n'
            '            return;\n'
            '        }\n'
        ),
        new='',
    ),
    Falsification(
        nodeid=(
            'tests/browser/testUnsignedTabSaysSo.py::'
            'testASignedTabStillLoadsItsContainers'
        ),
        source='vaibify/gui/static/scriptApplication.js',
        # The other direction: refusing every tab would replace the
        # dashboard with an error for everybody.
        old='        if (!_dictSessionState.sSessionToken) {\n',
        new='        if (true) {\n',
    ),
]
