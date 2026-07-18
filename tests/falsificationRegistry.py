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
- ``old``: the EXACT text to replace; must occur exactly once in ``source``.
- ``new``: the replacement (``old != new``); realizes the break the test
  is meant to catch.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Falsification:
    """One falsification test and the source mutation it is proven to kill."""

    nodeid: str
    source: str
    old: str
    new: str


# Each entry below is confirmed by tools/reconfirmFalsification.py to
# actually kill its falsification test.
LIST_FALSIFICATIONS = [

    Falsification(
        nodeid='tests/testContainerOwnership.py::test_agent_token_with_empty_container_id_fails_closed',
        source='vaibify/gui/containerOwnership.py',
        old='if not sPresentedToken or not sContainerId:',
        new='if not sPresentedToken and not sContainerId:',
    ),
    Falsification(
        nodeid='tests/testContainerOwnership.py::test_same_lease_reclaim_refreshes_grace_clock',
        source='vaibify/gui/containerOwnership.py',
        old="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId:
        recordOwner.fLastSeenMonotonic = time.monotonic()
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))""",
        new="""    if sLeaseId and recordOwner.sLeaseId == sLeaseId:
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))""",
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
        nodeid='tests/testWebSocketAuthorization.py::test_empty_shared_token_fails_closed_4401',
        source='vaibify/gui/webSocketAuthorization.py',
        old='return bool(sSharedToken) and sPresented == sSharedToken',
        new='return sPresented == sSharedToken',
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
        old="""in ("passed", "stale"):""",
        new="""in ("passed",):""",
    ),
    Falsification(
        nodeid='tests/testFileStatusManagerStaleness.py::test_fdictParseStatLines_handles_path_with_space',
        source='vaibify/gui/fileStatusManager.py',
        old="""sLine.rsplit(" ", 1)""",
        new="""sLine.split(" ", 1)""",
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
        nodeid='tests/testPipelineRunnerMutationCoverage.py::test_fiRunStepCommands_full_returns_plot_exit_code',
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
        old="""    async def fnKillRunningTasks(sContainerId: str):
        dictCtx["require"]()""",
        new='    async def fnKillRunningTasks(sContainerId: str):',
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
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_max_mtime_by_step_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""        ("maxByStep", sorted(
            (dictResponse.get("dictMaxMtimeByStep") or {}).items(),
        )),""",
        new="""        ("maxByStep", 0),""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_aics_level_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""("aicsLevel", dictResponse.get("iAICSLevel", 0)),""",
        new="""("aicsLevel", 0),""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_l2_blocker_count_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""("l2", dictResponse.get("iL2BlockerCount", 0)),""",
        new="""("l2", 0),""",
    ),
    Falsification(
        nodeid='tests/testPipelineRoutesMutationCoverage.py::TestFileStatusEtagSignals::test_l3_blocker_count_change_advances_tag',
        source='vaibify/gui/routes/pipelineRoutes.py',
        old="""("l3", dictResponse.get("iL3BlockerCount", 0)),""",
        new="""("l3", 0),""",
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
        return _fLoadCsvNegativeRow(sFullPath, sColumn, iIndex)""",
        new="""    if iIndex <= 0:
        return _fLoadCsvNegativeRow(sFullPath, sColumn, iIndex)""",
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
        nodeid='tests/testDirectorMutationCoverage.py::test_fnDownloadDatasets_refuses_sibling_prefix_destination',
        source='vaibify/gui/director.py',
        old='sParentReal.startswith(sRootReal + os.sep)',
        new='sParentReal.startswith(sRootReal)',
    ),
    Falsification(
        nodeid='tests/testDirectorMutationCoverage.py::test_fbValidateWorkflow_requires_saPlotFiles',
        source='vaibify/gui/director.py',
        old="""("sName", "sDirectory", "saPlotCommands", "saPlotFiles")""",
        new="""("sName", "sDirectory", "saPlotCommands")""",
    ),
    Falsification(
        nodeid='tests/testDirectorMutationCoverage.py::test_fbValidateWorkflow_requires_saPlotCommands',
        source='vaibify/gui/director.py',
        old="""("sName", "sDirectory", "saPlotCommands", "saPlotFiles")""",
        new="""("sName", "sDirectory", "saPlotFiles")""",
    ),
    Falsification(
        nodeid='tests/testDirectorMutationCoverage.py::test_fnExecuteStep_defaults_to_plot_only',
        source='vaibify/gui/director.py',
        old="""dictStep.get("bPlotOnly", True)""",
        new="""dictStep.get("bPlotOnly", False)""",
    ),
    Falsification(
        nodeid='tests/testDirectorMutationCoverage.py::test_fiResolveCoreCount_floors_at_one_on_single_core',
        source='vaibify/gui/director.py',
        old='return max(1, iTotal - 1)',
        new='return iTotal - 1',
    ),
    Falsification(
        nodeid='tests/testDirectorMutationCoverage.py::test_fnRegisterFiles_small_file_threshold_boundary',
        source='vaibify/gui/director.py',
        old='if iFileSize < 1024:',
        new='if iFileSize < 100:',
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
        old='            bL1 and bGithub and bZenodo and bArxiv and bDecl,',
        new='            bL1 or bGithub and bZenodo and bArxiv and bDecl,',
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
        old='                detail="git rm --cached failed: " + (sOut or "").strip(),',
        new='                detail="git rm --cached failed: " + (sOut and "").strip(),',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_commit_failure_detail_carries_git_output',
        source='vaibify/gui/routes/gitRoutes.py',
        old='                detail="git commit failed: " + (sOut or "").strip(),',
        new='                detail="git commit failed: " + (sOut and "").strip(),',
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
        old='        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)\n        fnBumpSyncEpoch(dictCtx, sContainerId)\n        if dictResult.get("bSuccess"):',
        new='        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)\n        fnBumpSyncEpoch(dictCtx, sContainerId)\n        if not dictResult.get("bSuccess"):',
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
        old='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitCommitInContainer,\n            docker, sContainerId,\n            "[vaibify] remove AI declaration from the repo",\n            sWorkspace=sRepo,\n        )',
        new='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitCommitInContainer,\n            docker, sContainerId,\n            "[vaibify] remove AI declaration from the repo",\n            sWorkspace=sRepo, listFilePaths=[request.sPath],\n        )',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_modified_declaration_untracks_not_commits_real_git',
        source='vaibify/gui/routes/gitRoutes.py',
        old='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitCommitInContainer,\n            docker, sContainerId,\n            "[vaibify] remove AI declaration from the repo",\n            sWorkspace=sRepo,\n        )',
        new='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitCommitInContainer,\n            docker, sContainerId,\n            "[vaibify] remove AI declaration from the repo",\n            sWorkspace=sRepo, listFilePaths=[request.sPath],\n        )',
    ),
    Falsification(
        nodeid='tests/testDeclarationPushMutationCoverage.py::test_untrack_refuses_when_other_changes_staged_real_git',
        source='vaibify/gui/routes/gitRoutes.py',
        old='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitDiffCachedQuietInContainer,\n            docker, sContainerId, sWorkspace=sRepo,\n        )\n        if iExit != 0:',
        new='        iExit, sOut = await asyncio.to_thread(\n            containerGit.ftResultGitDiffCachedQuietInContainer,\n            docker, sContainerId, sWorkspace=sRepo,\n        )\n        if False and iExit != 0:',
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
        # A short-circuited warnings builder makes a manual
        # project.json edit visible in the GUI only — the CLI and the
        # in-container agent would never see the violation.
        nodeid='tests/testStepSlugContract.py::test_backend_reports_directory_contract_warnings',
        source='vaibify/gui/workflowManager.py',
        old='        if fbStepDirectoryConforms(dictStep):\n            continue',
        new='        if fbStepDirectoryConforms(dictStep) or True:\n            continue',
    ),
]
