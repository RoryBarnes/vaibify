"""Shared infrastructure for quantitative regression testing of vaibified pipelines.

The submodule exposes:

* ``fLoadValue`` and ``fdictParseAccessPath`` — the canonical access-path
  parser and value loader (re-exported from :mod:`vaibify.gui.dataLoaders`
  so write-side and read-side cannot drift).
* A symmetric write side that produces / regenerates standards JSON files
  from live data — exposed via ``vaibify generate-standards`` on the CLI.
* A static stochastic-RNG detector that flags unseeded random sources in
  step scripts.
"""

from vaibify.testing.standards import (
    fdictParseAccessPath,
    fLoadValue,
    fdictGenerateQuantitativeStandards,
    fnWriteStandards,
    fnGenerateFromWorkflow,
    fnUpdateWorkflowStandards,
    fnRegenerateStandardsFile,
)
from vaibify.testing.stochasticDetector import (
    ftDetectStochastic,
    fnPrintReport,
)

__all__ = [
    "fdictParseAccessPath",
    "fLoadValue",
    "fdictGenerateQuantitativeStandards",
    "fnWriteStandards",
    "fnGenerateFromWorkflow",
    "fnUpdateWorkflowStandards",
    "fnRegenerateStandardsFile",
    "ftDetectStochastic",
    "fnPrintReport",
]
