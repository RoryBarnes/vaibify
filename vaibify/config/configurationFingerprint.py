"""Does the image still match the recipe in ``vaibify.yml``?

A researcher edits ``vaibify.yml`` — adds a repository, corrects a
misspelled package, changes the Python version — restarts the
container, and meets the old behaviour again, because everything they
edited is baked into the image at BUILD time. Nothing told them. One
researcher spent an evening re-entering a container to watch the same
three warnings, having already fixed their cause on disk (2026-09-21).

The fix is the same shape as the recipe fingerprint next door
(``dockerfileComposer.S_RECIPE_IMAGE_LABEL``): hash the inputs, stamp
the hash on the image, and compare. What differs is WHICH inputs. The
recipe fingerprint covers the Dockerfile texts vaibify ships; this one
covers the fields of the researcher's own configuration that the build
consumes, so together they answer "was this image built from these
texts and this configuration".

**Only the baked fields are hashed**, and that is the whole design.
Ports, bind mounts, secrets, network isolation and the resource limits
are applied by ``docker run``, so a change to any of them takes effect
the next time the container starts and needs no rebuild. A fingerprint
that covered them would send a researcher through an hour-long build to
publish a port, which is how a true warning becomes one people turn
off.

Three-state, like every other drift verdict in this codebase: an image
carrying no label at all predates this and is reported as *nothing
determined*, never as drift. Only a stamped fingerprint that DIFFERS is
a warning.
"""

__all__ = [
    "S_CONFIGURATION_IMAGE_LABEL",
    "T_BAKED_CONFIGURATION_FIELDS",
    "fsComputeConfigurationFingerprint",
    "fdictCompareConfigurationAgainstImage",
    "flistDescribeConfigurationDrift",
]

import hashlib
import json

S_CONFIGURATION_IMAGE_LABEL = "vaibify-configuration-sha256"

# The configuration fields the IMAGE is built from. Adding a field here
# is a statement that changing it requires a rebuild; leaving one out
# is a statement that it does not. Both are claims about the build, so
# neither may be made by guessing — check what the Dockerfile build
# args, the generated container.conf, and the overlay selection
# actually read.
#
# ``sProjectName`` is a build arg too (it lands as an ENV in the image)
# but is deliberately ABSENT: a project renamed in the registry points
# at a differently-tagged image anyway, so the comparison is moot, and
# including it would demand an hour-long rebuild to change a display
# name that changes nothing a result rests on.
T_BAKED_CONFIGURATION_FIELDS = (
    "sContainerUser",
    "sPythonVersion",
    "sBaseImage",
    "sWorkspaceRoot",
    "sPackageManager",
    "sPipInstallFlags",
    "listRepositories",
    "listSystemPackages",
    "listPythonPackages",
    "listCondaPackages",
    "listBinaries",
    "features",
)


def _fjsonNormalizeValue(jsonValue):
    """Return a value in a form two equal configurations always share.

    Order is preserved for LISTS, deliberately. ``systemPackages`` is
    installed in the order it is written and a reordering is a
    different build; sorting here would call two different images the
    same. What is normalized is only what carries no meaning: a
    dataclass or object becomes its field mapping, and mapping key
    order is fixed by the JSON dump.
    """
    if hasattr(jsonValue, "__dict__"):
        return {
            sKey: _fjsonNormalizeValue(jsonAny)
            for sKey, jsonAny in sorted(vars(jsonValue).items())
        }
    if isinstance(jsonValue, dict):
        return {
            str(sKey): _fjsonNormalizeValue(jsonAny)
            for sKey, jsonAny in sorted(jsonValue.items())
        }
    if isinstance(jsonValue, (list, tuple)):
        return [_fjsonNormalizeValue(jsonAny) for jsonAny in jsonValue]
    return jsonValue


def fsComputeConfigurationFingerprint(config):
    """Return the SHA-256 of the baked half of a project configuration.

    A field the object does not carry is skipped rather than defaulted:
    a configuration shape that predates a field must not hash as though
    the field were present and empty, which would make every older
    image read as drifted the moment a field is added here.
    """
    dictBaked = {}
    for sField in T_BAKED_CONFIGURATION_FIELDS:
        if not hasattr(config, sField):
            continue
        dictBaked[sField] = _fjsonNormalizeValue(getattr(config, sField))
    sCanonical = json.dumps(dictBaked, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def fdictCompareConfigurationAgainstImage(sStampedFingerprint, config):
    """Compare a project's configuration with the image it was built from.

    ``bConfigurationChanged`` is THREE-state. False is an image built
    from this configuration; True is one built from a different one;
    ``None`` means nothing was determined — no label on the image, or
    no configuration to hash — and no surface may warn from it.
    """
    sStamped = str(sStampedFingerprint or "")
    if not sStamped or config is None:
        return {
            "bConfigurationChanged": None,
            "sStampedFingerprint": sStamped,
            "sCurrentFingerprint": "",
        }
    sCurrent = fsComputeConfigurationFingerprint(config)
    return {
        "bConfigurationChanged": sStamped != sCurrent,
        "sStampedFingerprint": sStamped,
        "sCurrentFingerprint": sCurrent,
    }


def flistDescribeConfigurationDrift(dictComparison):
    """Return the sentences a researcher is shown, or none.

    Named rather than free text so one wording reaches every surface,
    and it names the REMEDY: a warning that says a file changed without
    saying what to do about it is the shape this codebase keeps
    finding at the bottom of a support round trip.
    """
    if not dictComparison.get("bConfigurationChanged"):
        return []
    return [
        "vaibify.yml has changed since this image was built, so the "
        "container is still running the old recipe.",
        "Rebuild the environment to apply it. Ports, mounts, secrets "
        "and resource limits are not part of this — those apply on the "
        "next start and never need a rebuild.",
    ]
