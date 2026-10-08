"""Coverage-closing tests for conftestManager surfaced by mutation testing.

Each test targets a specific surviving mutant in
``vaibify/gui/conftestManager.py`` (or in the conftest.py marker
template it ships into containers). The template-internal helpers are
exercised by exec'ing the generated source into an isolated namespace,
the same technique used in ``testConftestManagerMarker.py``.
"""

import types

import pytest

from vaibify.gui import conftestManager


pytestmark = pytest.mark.falsification


def _fnExecTemplateWithRoot(tmp_path):
    """Exec the prologue+template with _PROJECT_REPO = tmp_path.

    Returns a module-like namespace containing the template's helper
    functions and hooks so tests can drive them without a live pytest
    session.
    """
    sSource = conftestManager.fsBuildConftestSource(str(tmp_path))
    moduleNs = types.ModuleType("vaibify_conftest_cov_ns")
    moduleNs.__dict__["__name__"] = "vaibify_conftest_cov_ns"
    exec(compile(sSource, "<template>", "exec"), moduleNs.__dict__)
    return moduleNs


class _FakeRep:
    """Stand-in for a pytest call report carrying pass/fail booleans."""

    def __init__(self, bPassed, bFailed):
        self.passed = bPassed
        self.failed = bFailed


class _FakeItem:
    """Stand-in for a collected test item with a nodeid and rep_call."""

    def __init__(self, sNodeId, repCall):
        self.nodeid = sNodeId
        self.rep_call = repCall


class _FakeSession:
    """Minimal pytest session exposing .items and .testscollected."""

    def __init__(self, listItems):
        self.items = listItems
        self.testscollected = len(listItems)


# ----------------------------------------------------------------------
# Hole 1: an item's outcome word must not swap passed and failed.
# ----------------------------------------------------------------------


def test_item_outcome_words_follow_the_call_report(tmp_path):
    """A passing call reads passed, a failing one failed, one never run "".

    Asymmetric on purpose: a swap of the two words changes both
    answers. (This replaces the per-category tally the plugin used to
    keep; the marker now records each test's own outcome, and this is
    the one place a report becomes a word.)

    Kills: Conftest template _fsOutcomeOfItem: the passed branch
    answers failed, so a passing test is recorded as failing
    """
    ns = _fnExecTemplateWithRoot(tmp_path)
    assert ns._fsOutcomeOfItem(_FakeItem(
        "test_integrity_a.py::test_x", _FakeRep(True, False))) == "passed"
    assert ns._fsOutcomeOfItem(_FakeItem(
        "test_integrity_c.py::test_z", _FakeRep(False, True))) == "failed"
    assert ns._fsOutcomeOfItem(_FakeItem(
        "test_integrity_d.py::test_skipped", None)) == ""


# ----------------------------------------------------------------------
# Hole 2: sessionfinish marker filename must use '/'->'_' (not '-').
# ----------------------------------------------------------------------


def test_sessionfinish_marker_filename_uses_underscore_for_nested_dir(
    tmp_path, monkeypatch,
):
    """A nested step dir 'a/b' yields marker 'a_b.json' under the slug.

    The host reader looks for the underscore-flattened name; a '-'
    substitution writes a file the reader never finds (dashboard
    desync). Asserting the exact underscore filename and the absence
    of the hyphen variant kills that mutant.

    Kills: Template pytest_sessionfinish (line 704): marker filename
    sStepDirRel.replace('/','_') -> replace('/','-')
    """
    monkeypatch.setenv("VAIBIFY_ACTIVE_WORKFLOW_SLUG", "demoSlug")
    ns = _fnExecTemplateWithRoot(tmp_path)
    sConftestPath = str(tmp_path / "a" / "b" / "tests" / "conftest.py")
    ns.__dict__["__file__"] = sConftestPath
    ns.pytest_sessionfinish(_FakeSession([]), 0)
    sMarkerDir = tmp_path / ".vaibify" / "test_markers" / "demoSlug"
    assert (sMarkerDir / "a_b.json").is_file()
    assert not (sMarkerDir / "a-b.json").exists()


# ----------------------------------------------------------------------
# Hole 3: _fsActiveWorkflowSlug must fall back to 'default', never ''.
# ----------------------------------------------------------------------


def test_activeWorkflowSlug_falls_back_to_default_when_nothing_present(
    tmp_path, monkeypatch,
):
    """With no env slug and no workflow JSONs, slug is the literal 'default'.

    An empty fallback would write markers to the bare test_markers dir
    the host reader no longer scans, so the result vanishes.

    Kills: _fsActiveWorkflowSlug (line 682): final 'default' return
    emptied to ''
    """
    monkeypatch.delenv("VAIBIFY_ACTIVE_WORKFLOW_SLUG", raising=False)
    ns = _fnExecTemplateWithRoot(tmp_path)
    sSlug = ns._fsActiveWorkflowSlug()
    assert sSlug == "default"
    assert sSlug != ""


# ----------------------------------------------------------------------
# Hole 4: _flistPathsWithinRoot must reject sibling repos sharing a prefix.
# ----------------------------------------------------------------------


def test_pathsWithinRoot_rejects_sibling_with_shared_name_prefix():
    """'/workspace/myrepo-evil/...' is not inside '/workspace/myrepo'.

    A bare startswith(root) check would admit the sibling; the proper
    boundary test (==root or startswith(root + '/')) rejects it.

    Kills: _flistPathsWithinRoot (line 356): 'sNorm==root or
    sNorm.startswith(root+"/")' -> 'sNorm.startswith(root)'
    """
    listPaths = ["/workspace/myrepo-evil/step/tests/conftest.py"]
    assert conftestManager._flistPathsWithinRoot(
        listPaths, "/workspace/myrepo",
    ) == []


def test_pathsWithinRoot_keeps_in_root_path():
    """A genuinely in-root path survives the containment filter.

    Kills: a mutation that drops the '==root or startswith(root+"/")'
    keep-branch in _flistPathsWithinRoot, which would wrongly discard a
    legitimately in-root conftest path.
    """
    sInRoot = "/workspace/myrepo/step/tests/conftest.py"
    assert conftestManager._flistPathsWithinRoot(
        [sInRoot], "/workspace/myrepo",
    ) == [sInRoot]
