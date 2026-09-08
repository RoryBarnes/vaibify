"""Frontend contract checks for the "Reproduce a published project" card.

JavaScript is not executed by this suite; the browser lane does that.
What is pinned here is the wiring a browser test cannot cheaply see:
the modal markup the module populates, the load order (the module
must precede the container manager that binds it), the one HTTP choke
point, and the vocabulary -- the name ruling 9 fixes, the four
verdicts, and the absence of any publish, deposit, push or attest
control on a reproduction.
"""

import os
import re

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    with open(os.path.join(_sStaticDir, sName), "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_the_third_kind_card_carries_the_ruled_name():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="btnChoiceKindReproduce"' in sSource
    iCard = sSource.index('id="btnChoiceKindReproduce"')
    assert "Reproduce a published project" in sSource[iCard:iCard + 400]


def test_the_modal_carries_every_stage_the_module_populates():
    sSource = _fsReadStaticFile("index.html")
    sModule = _fsReadStaticFile("scriptReproducePublished.js")
    assert 'id="modalReproducePublished"' in sSource
    for sId in re.findall(r'_felById\("([A-Za-z]+)"\)', sModule):
        assert f'id="{sId}"' in sSource, f"the module populates #{sId}"


def test_the_module_loads_before_the_container_manager_binds_it():
    sSource = _fsReadStaticFile("index.html")
    iModule = sSource.index("scriptReproducePublished.js")
    iManager = sSource.index("scriptContainerManager.js")
    assert iModule < iManager
    sManager = _fsReadStaticFile("scriptContainerManager.js")
    assert "VaibifyReproducePublished.fnBind()" in sManager
    assert "VaibifyReproducePublished.fnOpen()" in sManager


def test_http_goes_through_the_api_client_only():
    sModule = _fsReadStaticFile("scriptReproducePublished.js")
    assert "fetch(" not in sModule
    assert "VaibifyApi.fdictPost" in sModule
    assert "VaibifyApi.fdictGet" in sModule


def test_the_module_offers_no_publishing_action_and_speaks_the_verdicts():
    sModule = _fsReadStaticFile("scriptReproducePublished.js")
    sMarkup = _fsReadStaticFile("index.html")
    iStart = sMarkup.index('id="modalReproducePublished"')
    iEnd = sMarkup.index("<!-- Add Container Modal", iStart)
    # The question is what the researcher is OFFERED, so the controls
    # are what is read: a modal whose prose says "published project"
    # offers nothing, a button labelled Publish would.
    listButtonLabels = re.findall(
        r"<button[^>]*>([^<]*)</button>", sMarkup[iStart:iEnd],
    )
    assert listButtonLabels, "the modal has controls"
    for sLabel in listButtonLabels:
        for sVerb in ("publish", "deposit", "push", "attest"):
            assert sVerb not in sLabel.lower(), sLabel
    assert 'id="btnReproduceRun"' in sMarkup
    for sVerdict in ("reproduced", "diverged", "no verdict"):
        assert sVerdict in sModule
    assert "Level 3" not in sModule


def test_the_poll_is_disarmed_on_settle():
    """The interval is cleared when the server stops reporting live."""
    sModule = _fsReadStaticFile("scriptReproducePublished.js")
    iPoll = sModule.index("async function _fnPollOnce")
    sPoll = sModule[iPoll:sModule.index("function _fsFormatBytes")]
    assert "if (dictJob.bLive)" in sPoll
    assert "_fnDisarmPoll();" in sPoll.split("if (dictJob.bLive)")[1]
