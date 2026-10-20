"""The Zenodo publish offers every file the Level 3 archive checks read.

Level 3 asks the Zenodo record, not the working tree: the envelope must
be in the archive and match (``fbEnvelopeMatchesZenodoArchive``), and
the archived attestation must cover the archived manifest
(``fbAttestationIsPubliclyArchived``). A deposit can satisfy either
only if the dashboard's publish offered those files. The candidate list
once carried only outputs, scripts and tests, so no deposit made from
it could reach Level 3; this pins the union it carries now.
"""

import os

import pytest

from vaibify.gui.syncDispatcher import flistCollectZenodoArchiveCandidates
from vaibify.reproducibility import publicationScope


def _fnWriteText(sRoot, sRelativePath, sText="x\n"):
    sAbsolute = os.path.join(sRoot, sRelativePath)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "w", encoding="utf-8") as fileOut:
        fileOut.write(sText)


@pytest.mark.falsification
def testTheZenodoCandidatesCarryTheEnvelopeAndTheAttestation(tmp_path):
    """Kills: building the candidates from the outputs alone."""
    sRoot = str(tmp_path)
    listRequired = list(publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS) + [
        publicationScope.S_ATTESTATION_REPO_PATH]
    for sRelative in listRequired:
        _fnWriteText(sRoot, sRelative)
    _fnWriteText(sRoot, ".vaibify/projects/project.json", "{}\n")
    listCandidates = flistCollectZenodoArchiveCandidates(
        {"listSteps": []}, {}, {}, sRoot, sRoot)
    setOffered = {dictFile["sPath"] for dictFile in listCandidates}
    listMissing = [sRelative for sRelative in listRequired
                   if os.path.join(sRoot, sRelative) not in setOffered]
    assert listMissing == []
