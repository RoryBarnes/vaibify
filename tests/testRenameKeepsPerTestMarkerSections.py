"""Renaming a step carries every test result its marker holds.

The per-test sections of a marker (``dictTestFiles``, ``dictRuns``,
``dictLegacyCategories``, ``dictUnattributedFailure``) are keyed by test
files and node ids RELATIVE to the step directory, and the rename moves
that directory whole without renaming a file inside it. So a rename needs
to rewrite only the marker's ``sDirectory``; anything else it rewrote
would orphan the results it meant to carry.
"""

import json

from vaibify.gui import stepRename

S_MARKER_DIRECTORY = ".vaibify/test_markers/study"
S_WORKFLOW_PATH = "/workspace/repo/.vaibify/projects/study.json"
S_NODE_ID = "tests/test_integrity_a.py::test_one"

DICT_PER_TEST_MARKER = {
    "sDirectory": "OldStep", "sLabel": "A01",
    "dictRuns": {"r1": {
        "fTimestamp": 9.0, "sRunAtUtc": "2026-10-02T00:00:00Z",
        "iExitStatus": 0, "dictOutputHashes": {}, "dictInputHashes": {}}},
    "dictTestFiles": {"test_integrity_a.py": {
        "listNodeIds": [S_NODE_ID],
        "dictOutcomes": {S_NODE_ID: {"sOutcome": "passed", "sRunId": "r1"}},
        "dictCollectionError": None}},
    "dictLegacyCategories": {
        "quantitative": {"iPassed": 2, "iFailed": 0, "sRunId": "r1"}},
    "dictUnattributedFailure": None,
}


class _FakeRepoFiles:
    def __init__(self, dictFiles):
        self.dictFiles = dict(dictFiles)

    def fbIsFile(self, sRelPath):
        return sRelPath in self.dictFiles

    def fsReadText(self, sRelPath):
        return self.dictFiles[sRelPath]

    def fnWriteTextAtomic(self, sRelPath, sContent):
        self.dictFiles[sRelPath] = sContent

    def fbRemoveFile(self, sRelPath):
        del self.dictFiles[sRelPath]
        return True


def test_a_rename_rewrites_only_the_directory_of_a_per_test_marker():
    filesRepo = _FakeRepoFiles({
        S_MARKER_DIRECTORY + "/OldStep.json": json.dumps(
            DICT_PER_TEST_MARKER)})
    bMoved = stepRename._fbMoveMarkerFile(
        filesRepo, {"sOldDirectory": "OldStep", "sNewDirectory": "NewStep"},
        S_WORKFLOW_PATH)
    assert bMoved is True
    dictMoved = json.loads(
        filesRepo.dictFiles[S_MARKER_DIRECTORY + "/NewStep.json"])
    assert dictMoved.pop("sDirectory") == "NewStep"
    dictExpected = dict(DICT_PER_TEST_MARKER)
    dictExpected.pop("sDirectory")
    assert dictMoved == dictExpected
