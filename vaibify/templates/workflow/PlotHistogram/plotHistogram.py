"""Bin the sampled values and write a histogram as SVG.

Second step of the shipped example workflow. The sample file arrives
as a command-line argument, never as a hardcoded relative path: the
workflow's ``{step:generate-samples.samples}`` token is what tells
vaibify that this step depends on the first one. A path written into
this file instead would make the dependency invisible to the dashboard,
so the figure would not be marked stale when the samples change.

Standard library only, for the reason given in ``generateSamples.py``:
a brand-new project has no packages installed yet, and the first Run
should still produce a figure. SVG is one of the formats the figure
viewer renders, and it is plain text, so it costs no dependency to
emit.
"""

import argparse
import json
import os


_I_CHART_WIDTH = 640
_I_CHART_HEIGHT = 400
_I_CHART_MARGIN = 40


def fdaReadSamples(sSamplesPath):
    """Return the sample values recorded by the upstream step."""
    with open(sSamplesPath, "r", encoding="utf-8") as fileHandle:
        dictPayload = json.load(fileHandle)
    daSamples = dictPayload.get("daSamples")
    if not daSamples:
        raise ValueError(
            f"'{sSamplesPath}' contains no samples; re-run the "
            f"GenerateSamples step."
        )
    return daSamples


def fliaCountPerBin(daSamples, iBinCount):
    """Return per-bin counts spanning the sample range."""
    dMinimum = min(daSamples)
    dMaximum = max(daSamples)
    dSpan = (dMaximum - dMinimum) or 1.0
    liaCounts = [0] * iBinCount
    for dValue in daSamples:
        iIndex = int((dValue - dMinimum) / dSpan * iBinCount)
        liaCounts[min(iIndex, iBinCount - 1)] += 1
    return liaCounts


def flistBuildBarElements(liaCounts):
    """Return one SVG rect element per bin, scaled to the chart area."""
    iPlotWidth = _I_CHART_WIDTH - 2 * _I_CHART_MARGIN
    iPlotHeight = _I_CHART_HEIGHT - 2 * _I_CHART_MARGIN
    iTallest = max(liaCounts) or 1
    dBarWidth = iPlotWidth / len(liaCounts)
    listElements = []
    for iIndex, iCount in enumerate(liaCounts):
        dBarHeight = iCount / iTallest * iPlotHeight
        dLeft = _I_CHART_MARGIN + iIndex * dBarWidth
        dTop = _I_CHART_HEIGHT - _I_CHART_MARGIN - dBarHeight
        listElements.append(
            f'  <rect x="{dLeft:.2f}" y="{dTop:.2f}" '
            f'width="{dBarWidth - 1:.2f}" height="{dBarHeight:.2f}" '
            f'fill="#4c72b0" />'
        )
    return listElements


def fsRenderSvg(liaCounts, iSampleCount):
    """Return the complete SVG document for the histogram."""
    listElements = flistBuildBarElements(liaCounts)
    iBaseline = _I_CHART_HEIGHT - _I_CHART_MARGIN
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_I_CHART_WIDTH}" height="{_I_CHART_HEIGHT}" '
        f'viewBox="0 0 {_I_CHART_WIDTH} {_I_CHART_HEIGHT}">',
        '  <rect width="100%" height="100%" fill="white" />',
        *listElements,
        f'  <line x1="{_I_CHART_MARGIN}" y1="{iBaseline}" '
        f'x2="{_I_CHART_WIDTH - _I_CHART_MARGIN}" y2="{iBaseline}" '
        f'stroke="#333" />',
        f'  <text x="{_I_CHART_MARGIN}" y="{_I_CHART_MARGIN - 15}" '
        f'font-family="sans-serif" font-size="14" fill="#333">'
        f'Distribution of {iSampleCount} samples</text>',
        '</svg>',
        '',
    ])


def fnWriteFigure(sSvg, sOutputPath):
    """Write the SVG, creating the plot directory if it is missing."""
    sDirectory = os.path.dirname(sOutputPath)
    if sDirectory:
        os.makedirs(sDirectory, exist_ok=True)
    with open(sOutputPath, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(sSvg)


def fnParseArgumentsAndRun():
    """Parse the command line and write the histogram figure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples", dest="sSamplesPath", required=True,
        help="JSON sample file produced by the GenerateSamples step.",
    )
    parser.add_argument(
        "--output", dest="sOutputPath", required=True,
        help="Path of the SVG figure to write.",
    )
    parser.add_argument(
        "--bins", dest="iBinCount", type=int, default=24,
        help="Number of histogram bins.",
    )
    arguments = parser.parse_args()
    daSamples = fdaReadSamples(arguments.sSamplesPath)
    liaCounts = fliaCountPerBin(daSamples, arguments.iBinCount)
    fnWriteFigure(
        fsRenderSvg(liaCounts, len(daSamples)), arguments.sOutputPath,
    )
    print(f"Wrote histogram to {arguments.sOutputPath}")


if __name__ == "__main__":
    fnParseArgumentsAndRun()
