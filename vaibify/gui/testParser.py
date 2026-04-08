"""Parse and validate LLM-generated test code."""

__all__ = [
    "fsParseGeneratedCode",
    "fbValidatePythonSyntax",
    "fsRepairMissingImports",
    "fdictParseCombinedOutput",
    "fdictParseQuantitativeJson",
]

import ast
import json
import re


def fsParseGeneratedCode(sRawOutput):
    """Extract Python code from LLM output, stripping markdown fences."""
    sStripped = sRawOutput.strip()
    matchFenced = re.search(
        r"```\w*\s*\n(.*?)```",
        sStripped, re.DOTALL,
    )
    sCode = matchFenced.group(1).strip() if matchFenced else sStripped
    sCode = fsRepairMissingImports(sCode)
    fbValidatePythonSyntax(sCode)
    return sCode


def fbValidatePythonSyntax(sCode):
    """Raise ValueError if code is not valid Python."""
    try:
        ast.parse(sCode)
    except SyntaxError as error:
        raise ValueError(
            f"Generated code has syntax error: {error.msg} "
            f"(line {error.lineno})"
        )


def fsRepairMissingImports(sCode):
    """Add missing standard imports detected by compile check."""
    try:
        ast.parse(sCode)
    except SyntaxError:
        return sCode
    listNeeded = []
    for sModule in ("os", "sys", "json", "pathlib", "csv"):
        if sModule + "." in sCode and f"import {sModule}" not in sCode:
            listNeeded.append(f"import {sModule}")
    if not listNeeded:
        return sCode
    return "\n".join(listNeeded) + "\n\n" + sCode


def fdictParseCombinedOutput(sRawOutput):
    """Parse a combined LLM response into three labeled sections."""
    dictSections = {}
    listLabels = ["INTEGRITY", "QUALITATIVE", "QUANTITATIVE"]
    for sLabel in listLabels:
        matchBlock = re.search(
            r"```" + sLabel + r"\s*\n(.*?)```",
            sRawOutput, re.DOTALL,
        )
        if matchBlock:
            dictSections[sLabel] = matchBlock.group(1).strip()
    return dictSections


def fdictParseQuantitativeJson(sRawOutput):
    """Extract and parse JSON from LLM output for quantitative standards."""
    sStripped = sRawOutput.strip()
    matchFenced = re.search(
        r"```(?:json)?\s*\n(.*?)```", sStripped, re.DOTALL,
    )
    if matchFenced:
        sStripped = matchFenced.group(1).strip()
    try:
        return json.loads(sStripped)
    except json.JSONDecodeError:
        matchBrace = re.search(r"\{.*\}", sStripped, re.DOTALL)
        if matchBrace:
            return json.loads(matchBrace.group(0))
        return {"listStandards": []}
