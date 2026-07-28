"""Draw pseudo-random samples and write them as JSON.

First step of the shipped example workflow. It exists to be *run*: a
new project should produce a figure on the first click of Run, before
the researcher has installed anything or edited any code.

That is why this uses only the Python standard library. A vaibify
container installs the packages named in ``vaibify.yml``, and a fresh
project names none, so a template that imported numpy would fail on
the first run of every new project. Real workflows declare their
dependencies in ``vaibify.yml`` and import freely; a template cannot.

The seed is an explicit argument rather than an implicit default so
that re-running the workflow reproduces the figure byte for byte,
which is the property the rest of vaibify is built to check.
"""

import argparse
import json
import random


def fdaDrawSamples(iCount, iSeed):
    """Return iCount samples from a standard normal distribution."""
    generatorRandom = random.Random(iSeed)
    return [generatorRandom.gauss(0.0, 1.0) for _ in range(iCount)]


def fnWriteSamples(daSamples, iSeed, sOutputPath):
    """Write the samples and the seed that produced them to JSON."""
    dictPayload = {
        "iSeed": iSeed,
        "iCount": len(daSamples),
        "daSamples": daSamples,
    }
    with open(sOutputPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPayload, fileHandle, indent=2)


def fnParseArgumentsAndRun():
    """Parse the command line and write the sample file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", dest="iCount", type=int, default=500,
        help="Number of samples to draw.",
    )
    parser.add_argument(
        "--seed", dest="iSeed", type=int, required=True,
        help="Seed making the draw reproducible.",
    )
    parser.add_argument(
        "--output", dest="sOutputPath", required=True,
        help="Path of the JSON file to write.",
    )
    arguments = parser.parse_args()
    daSamples = fdaDrawSamples(arguments.iCount, arguments.iSeed)
    fnWriteSamples(daSamples, arguments.iSeed, arguments.sOutputPath)
    print(
        f"Wrote {len(daSamples)} samples to "
        f"{arguments.sOutputPath}"
    )


if __name__ == "__main__":
    fnParseArgumentsAndRun()
