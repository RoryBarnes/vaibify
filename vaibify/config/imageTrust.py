"""How a persistent container may run an image vaibify did not build.

An image the researcher obtained (a published environment, a tarball, a
registry reference) carries an entrypoint and a ``USER`` its author
chose. Giving it the trust vaibify gives its own image -- a root phase,
the entrypoint capabilities, the researcher's stored credentials -- is a
decision, and it is the researcher's. This module is where the three
answers, the words that explain them, and the rule that enforces them
live, so the dashboard modal, the command line and the launch path read
one source and cannot drift apart.

THE RULE
--------

An image built by vaibify never asks. Every other image, including one
whose provenance cannot be established, needs a recorded answer for its
exact digest before a persistent container is created from it. The
answer is stored in the host-held registry; a different digest is a new
question. The modal is a convenience and the launch path is the guard:
:func:`fdictResolveLaunchPosture` raises for an unanswered digest, so no
caller can skip the question by skipping the modal.
"""

__all__ = [
    "DICT_CREDENTIAL_OPTION",
    "ImageProvenanceUnavailableError",
    "ImageTrustInspectOnlyError",
    "ImageTrustRequiredError",
    "LIST_TRUST_OPTIONS",
    "S_TRUST_AS_BUILT",
    "S_TRUST_INSPECT",
    "S_TRUST_RESTRICTED",
    "TUPLE_TRUST_CHOICES",
    "fbImageWasBuiltByVaibify",
    "fdictBuildTrustPrompt",
    "fdictBuildTrustRecord",
    "fdictDescribeTrustChoices",
    "fdictResolveLaunchPosture",
    "fnRequireValidTrustChoice",
]

import copy
from datetime import datetime, timezone

from vaibify.config.registryManager import (
    S_IMAGE_TRUST_KEY,
    fbProjectImageIsObtained,
)

S_TRUST_RESTRICTED = "restricted"
S_TRUST_AS_BUILT = "as-built"
S_TRUST_INSPECT = "inspect"
TUPLE_TRUST_CHOICES = (S_TRUST_RESTRICTED, S_TRUST_AS_BUILT, S_TRUST_INSPECT)

S_BUILT_MODE = "built"
S_RESTRICTED_DOCUMENTATION_URL = (
    "https://github.com/RoryBarnes/vaibify/blob/main/docs/security.md"
    "#restricted-mode-compatibility-contract"
)

LIST_TRUST_OPTIONS = [
    {
        "sChoice": S_TRUST_RESTRICTED,
        "sLabel": "Run it restricted",
        "sSummary": "Safer. The image's own startup is not run.",
        "listDetailLines": [
            "The image's own entrypoint is not run. The container starts "
            "with the same idle entrypoint the disposable verification "
            "lane uses, as the unprivileged container user, with all "
            "capabilities dropped and no-new-privileges set. It never "
            "runs as root.",
            "Safer: code in the image cannot act as root inside the "
            "container.",
            "Risk: images that expect root or their own entrypoint may "
            "fail to start, or behave differently (file ownership, "
            "HOME, paths).",
            "Reproducibility: outputs may differ from the author's for "
            "reasons unrelated to the science, so a Level 3 comparison "
            "can diverge.",
            "If the container fails to start, vaibify says so plainly "
            "and lets you choose another option. It never retries "
            "silently.",
        ],
    },
    {
        "sChoice": S_TRUST_AS_BUILT,
        "sLabel": "Run it as its author built it",
        "sSummary": "Most faithful. The image's own startup runs.",
        "listDetailLines": [
            "The image's own entrypoint runs as its author wrote it. "
            "vaibify starts it the way it starts its own images: as "
            "root, with the capabilities the entrypoint needs, so an "
            "entrypoint that drops to its own USER can do so.",
            "Most faithful to the original environment.",
            "Risk: whatever the entrypoint does runs with that "
            "authority inside the container. Container isolation still "
            "holds: no host mounts beyond the workspace and the mounts "
            "you configured, and the network follows the project's "
            "setting.",
        ],
    },
    {
        "sChoice": S_TRUST_INSPECT,
        "sLabel": "Inspect only",
        "sSummary": "No persistent project. Nothing is kept.",
        "listDetailLines": [
            "No persistent project is created. The image runs only in "
            "the disposable verification lane: restricted, no "
            "credentials, network off.",
            "You can rerun and compare outputs, but you cannot develop "
            "in it.",
            "Nothing persists.",
        ],
    },
]

DICT_CREDENTIAL_OPTION = {
    "sLabel": (
        "Make my stored credentials (GitHub, Zenodo, Overleaf, agent "
        "logins) available inside this container"
    ),
    "sSummary": "Off by default. The image's code can read them.",
    "listDetailLines": [
        "Which credentials: the secrets configured for this project "
        "(mounted read-only under /run/secrets), the credentials volume "
        "that holds the container keyring, and the agent bridge that "
        "lets an in-container agent call back to vaibify.",
        "How they are delivered: attached when the container is "
        "created. Every process in the container can read them, "
        "including the image's own code.",
        "Leave this unchecked unless you trust the image's author. "
        "Unchecked, none of them are attached.",
        "Disabled under Inspect only, which never has credentials.",
    ],
}


class ImageTrustRequiredError(RuntimeError):
    """The image's digest has no recorded answer; a container is refused."""

    def __init__(self, sProjectName, sImageDigest):
        self.sProjectName = sProjectName
        self.sImageDigest = sImageDigest
        super().__init__(
            f"'{sProjectName}' would run an image vaibify did not build "
            f"({sImageDigest}), and you have not yet chosen how it may "
            "run. Choose in the dashboard, or pass --image-trust "
            "{restricted,as-built,inspect} (and --with-credentials if "
            "its code may read your stored credentials) to "
            "vaibify start."
        )


class ImageTrustInspectOnlyError(RuntimeError):
    """The recorded answer is 'inspect only', which has no persistent container."""

    def __init__(self, sProjectName):
        self.sProjectName = sProjectName
        super().__init__(
            f"'{sProjectName}' is set to Inspect only, so no persistent "
            "container is created from its image. Rerun it in the "
            "verification lane, or change the choice to run it "
            "restricted or as its author built it."
        )


class ImageProvenanceUnavailableError(RuntimeError):
    """The daemon could not describe the image, so no trust can be judged."""

    def __init__(self, sProjectName, sImageReference):
        self.sProjectName = sProjectName
        super().__init__(
            f"vaibify could not inspect the image '{sImageReference}' for "
            f"'{sProjectName}', so it cannot establish who built it and "
            "will not start a container from it. Check that Docker is "
            "running and that the image exists locally; build or obtain "
            "it first."
        )


def fdictDescribeTrustChoices():
    """Return the words for the three answers and the credentials box.

    A copy, so a caller cannot edit the shared text.
    """
    return {
        "listOptions": copy.deepcopy(LIST_TRUST_OPTIONS),
        "dictCredentials": copy.deepcopy(DICT_CREDENTIAL_OPTION),
        "sRestrictedDocumentationUrl": S_RESTRICTED_DOCUMENTATION_URL,
    }


def fnRequireValidTrustChoice(sChoice):
    """Raise ``ValueError`` unless the choice is one of the three answers."""
    if sChoice not in TUPLE_TRUST_CHOICES:
        raise ValueError(
            f"{sChoice!r} is not an image-trust choice; the choices are "
            + ", ".join(TUPLE_TRUST_CHOICES)
        )


def fbImageWasBuiltByVaibify(dictProject, dictImage):
    """True only when vaibify's own build is established for the image.

    Two facts must hold: the project does not declare its image
    obtained (an obtained image is somebody else's build even if it
    carries a vaibify label, because the label travels with the
    bytes), and the image carries vaibify's recipe fingerprint label.
    Anything less -- no image description, no label -- is not
    established, and an unestablished image counts as not built here.
    """
    from vaibify.reproducibility.dockerfileComposer import (
        S_RECIPE_IMAGE_LABEL,
    )
    if dictImage is None or fbProjectImageIsObtained(dictProject):
        return False
    return bool((dictImage.get("dictLabels") or {}).get(S_RECIPE_IMAGE_LABEL))


def fdictBuildTrustRecord(sImageDigest, sChoice, bWithCredentials):
    """Return the record stored for one answered digest.

    Inspect only never carries credentials, whatever the caller passed.
    """
    fnRequireValidTrustChoice(sChoice)
    return {
        "sImageDigest": str(sImageDigest),
        "sChoice": sChoice,
        "bWithCredentials": bool(bWithCredentials)
        and sChoice != S_TRUST_INSPECT,
        "sRecordedIso": datetime.now(timezone.utc).isoformat(),
    }


def fdictResolveLaunchPosture(dictProject, dictImage, sProjectName):
    """Return how a persistent container may be launched, or raise.

    ``dictImage`` is the daemon's description of the image the launch
    would use (``None`` when it could not be described). Returns
    ``{"sMode", "bWithCredentials", "sImageDigest"}`` where ``sMode`` is
    ``built``, ``restricted`` or ``as-built``.
    """
    if dictImage is None:
        raise ImageProvenanceUnavailableError(sProjectName, "")
    if fbImageWasBuiltByVaibify(dictProject, dictImage):
        return {
            "sMode": S_BUILT_MODE, "bWithCredentials": True,
            "sImageDigest": dictImage["sId"],
        }
    dictRecord = (dictProject or {}).get(S_IMAGE_TRUST_KEY) or {}
    if dictRecord.get("sImageDigest") != dictImage["sId"]:
        raise ImageTrustRequiredError(sProjectName, dictImage["sId"])
    if dictRecord.get("sChoice") == S_TRUST_INSPECT:
        raise ImageTrustInspectOnlyError(sProjectName)
    fnRequireValidTrustChoice(dictRecord.get("sChoice"))
    return {
        "sMode": dictRecord["sChoice"],
        "bWithCredentials": bool(dictRecord.get("bWithCredentials")),
        "sImageDigest": dictImage["sId"],
    }


def fdictBuildTrustPrompt(dictProject, dictImage, dictOriginRecord):
    """Return what the modal and the command line show before asking.

    Provenance is facts the daemon and the origin record state, never
    claims the image makes about itself. ``dictOriginRecord`` is the
    live origin record (or ``None``).
    """
    dictOrigin = dictOriginRecord or {}
    return {
        "sImageDigest": dictImage["sId"],
        "bBuiltByVaibify": fbImageWasBuiltByVaibify(dictProject, dictImage),
        "iSizeBytes": int(dictImage.get("iSizeBytes") or 0),
        "sDeclaredUser": str(dictImage.get("sUser") or ""),
        "listDeclaredEntrypoint": list(dictImage.get("listEntrypoint") or []),
        "sObtainedFrom": str(dictOrigin.get("sObtainedFrom") or ""),
        "sVersionDoi": str(dictOrigin.get("sVersionDoi") or ""),
        "sPinnedImageReference": str(
            dictOrigin.get("sPinnedImageReference") or ""),
        "bDigestMatchesRecordedDeposit": bool(
            dictOrigin
            and dictImage["sId"] in (
                dictOrigin.get("sBaseImageId"),
                dictOrigin.get("sRunningImageId"),
            )
        ),
        "dictCurrentAnswer": dict(
            (dictProject or {}).get(S_IMAGE_TRUST_KEY) or {}),
        **fdictDescribeTrustChoices(),
    }
