"""The poll must not advertise a payload that nothing renders.

``listStaleOutputAdvisories`` rode every status poll for a feature the
dashboard never drew: no JavaScript read the key, and the agent guide
described a "Declare as upstream" affordance that did not exist. A
payload with no consumer is a claim the product does not keep, and it
costs a computation on every poll. Until a renderer exists the poll
carries nothing for it; if one is written it reads the key by name, and
this test then stands aside.
"""

import pathlib

PATH_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "vaibify"
S_KEY = "listStaleOutputAdvisories"


def _fbAnyStaticScriptReadsTheKey():
    return any(
        S_KEY in pathScript.read_text(encoding="utf-8")
        for pathScript in (PATH_PACKAGE / "gui" / "static").glob("*.js")
    )


def testThePollOnlyCarriesStaleOutputAdvisoriesIfADashboardModuleReadsThem():
    sRoutes = (
        PATH_PACKAGE / "gui" / "routes" / "pipelineRoutes.py"
    ).read_text(encoding="utf-8")
    assert S_KEY not in sRoutes or _fbAnyStaticScriptReadsTheKey(), (
        f"the status poll emits {S_KEY} but no script under "
        "gui/static reads it; render it or stop sending it"
    )
