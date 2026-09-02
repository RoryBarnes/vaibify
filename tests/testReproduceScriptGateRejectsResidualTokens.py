"""The L3 reproduce.sh gate must refuse a script that cannot run.

``fbVerifyReproduceScript`` used to ask two questions -- is the file
present, and is its path in ``MANIFEST.sha256`` -- and a script whose
commands still carried vaibify template tokens answered yes to both.
It is hashed, pinned, and unrunnable: it creates a literal
``{sPlotDirectory}`` directory and dies on the first cross-step path.
Reporting that green sent the researcher into an hours-long
``verify-l3-reproducibility`` rebuild to discover it at the end.

Renderer-side rejection does not cover this. Scripts emitted before
the renderer resolved tokens are already on disk in real projects, and
regenerating is exactly what a researcher does *after* the gate tells
them something is wrong. So the residue is checked where the verdict
is formed.

The snapshot lane is driven with the REAL ``SnapshotRepoFiles``, not a
permissive fake. That adapter answers only the paths one container
exec sampled and raises otherwise; a hand-written double that answers
anything would pass here while the shipped gate raised on the poll
path and blanked every badge on the dashboard.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.reproducibility.levelGates import fbVerifyReproduceScript
from vaibify.reproducibility.repoFiles import (
    TUPLE_SNAPSHOT_CONTENT_PATHS,
    TUPLE_SNAPSHOT_SKIP_TEXT_PATHS,
    HostRepoFiles,
    SnapshotRepoFiles,
    fnInjectManifestTextIntoSnapshot,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
)


S_RUNNABLE_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "( cd 'FitCurves' && \\\n"
    "    python plot.py /work/Plot/fits.png )\n"
)
S_TOKEN_BEARING_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "( cd 'FitCurves' && \\\n"
    "    python plot.py {sPlotDirectory}/fits.{sFigureType} )\n"
)


class HostShellExecConnection:
    """Run the snapshot exec in a host shell rooted at the fixture tree."""

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        resultProcess = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=resultProcess.returncode,
            sStdout=resultProcess.stdout,
            sStderr=resultProcess.stderr,
        )


def _fnSeedRepo(pathRoot, sScriptBody):
    """Write a repo whose manifest pins reproduce.sh."""
    (pathRoot / ".vaibify").mkdir(exist_ok=True)
    (pathRoot / S_REPRODUCE_SCRIPT_FILENAME).write_text(sScriptBody)
    (pathRoot / "MANIFEST.sha256").write_text(
        "# vaibify manifest v1\n"
        f"0000000000000000000000000000000000000000000000000000000000000000  {S_REPRODUCE_SCRIPT_FILENAME}\n"
    )
    (pathRoot / ".vaibify" / "environment.json").write_text(
        json.dumps({"sImageDigest": "img@sha256:def"}),
    )


@pytest.mark.parametrize(
    "sScript, bExpected",
    [(S_RUNNABLE_SCRIPT, True), (S_TOKEN_BEARING_SCRIPT, False)],
)
def test_gate_reads_the_script_not_only_its_presence(
    tmp_path, sScript, bExpected,
):
    """A pinned script bearing tokens fails; the same script resolved passes.

    The falsification pair is the point: both files exist and both are
    named in the manifest, so presence and manifest membership cannot
    tell them apart. Only reading the body can.
    """
    _fnSeedRepo(tmp_path, sScript)
    filesRepo = HostRepoFiles(str(tmp_path))
    assert fbVerifyReproduceScript(filesRepo, {}) is bExpected


@pytest.mark.parametrize(
    "sScript, bExpected",
    [(S_RUNNABLE_SCRIPT, True), (S_TOKEN_BEARING_SCRIPT, False)],
)
def test_gate_survives_the_real_poll_snapshot_adapter(
    tmp_path, sScript, bExpected,
):
    """The same verdict through SnapshotRepoFiles, which fails closed.

    This is the lane the dashboard's file-status poll uses. A gate
    that raised here would 500 the poll and blank every badge, and
    every permissive test double in the suite would still be green.
    """
    _fnSeedRepo(tmp_path, sScript)
    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        HostShellExecConnection(), "cid", str(tmp_path),
    )
    # The manifest BODY is excluded from the snapshot exec by design
    # and hydrated afterwards from a sha-keyed cache; the poll route
    # does this on every fetch, so a snapshot without it is not the
    # shape the gate ever sees in production.
    fnInjectManifestTextIntoSnapshot(
        filesSnapshot,
        (tmp_path / "MANIFEST.sha256").read_text(),
    )
    assert fbVerifyReproduceScript(filesSnapshot, {}) is bExpected


def test_the_poll_snapshot_carries_the_script_body():
    """Pin the gate's read against the set the snapshot actually samples.

    The gate now reads reproduce.sh's TEXT. SnapshotRepoFiles raises
    KeyError for an unsampled path and FileNotFoundError for a sampled
    path whose body was skipped -- so this gate's requirement is a
    subset relationship across two modules, and each edit looks
    complete on its own. Dropping reproduce.sh from the content set,
    or adding it to the skip-text set, breaks the poll lane silently.
    """
    assert S_REPRODUCE_SCRIPT_FILENAME in TUPLE_SNAPSHOT_CONTENT_PATHS
    assert S_REPRODUCE_SCRIPT_FILENAME not in TUPLE_SNAPSHOT_SKIP_TEXT_PATHS
