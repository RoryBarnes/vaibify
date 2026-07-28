#!/usr/bin/env python3
"""Assert an *installed* vaibify can actually do its first-run job.

The release workflow used to test a distribution with ``import
vaibify; print(__version__)``. That passes for a wheel containing no
templates and no Docker build context, which is exactly what every
wheel contained: ``vaibify init`` on a fresh ``pip install vaibify``
printed "No templates found" and exited 0, and the container-context
lookup landed on the Docker SDK's own source directory.

An import is not a smoke test. This script drives the paths a new user
takes in their first five minutes, against whatever ``vaibify`` the
running interpreter resolves.

    python tools/checkInstalledDistribution.py

Run it with the *interpreter of the environment under test* -- in CI,
the venv the distribution was installed into -- and from a directory
that is not a vaibify checkout, so the repository cannot stand in for
the installed package.
"""

import pathlib
import subprocess
import sys


def fnFailWith(sMessage):
    """Print a failure and exit non-zero."""
    print(f"FAIL: {sMessage}")
    sys.exit(1)


def fnCheckPackageIsNotTheCheckout():
    """Refuse to certify the repository standing in for the install."""
    import vaibify
    pathPackage = pathlib.Path(vaibify.__file__).resolve().parent
    if (pathPackage.parent / "pyproject.toml").is_file():
        fnFailWith(
            f"'vaibify' resolved to the source checkout at "
            f"'{pathPackage}', not to an installed distribution. Run "
            f"this from a directory outside the repository, using the "
            f"target environment's interpreter."
        )
    print(f"  package under test: {pathPackage}")


def fnCheckTemplatesAreUsable():
    """Every shipped template must exist and carry a container.conf."""
    from vaibify.resources import fpathTemplatesRoot
    pathTemplates = fpathTemplatesRoot()
    listTemplates = sorted(
        p.name for p in pathTemplates.iterdir() if p.is_dir()
    )
    if not listTemplates:
        fnFailWith(f"no templates shipped in '{pathTemplates}'")
    for sName in listTemplates:
        if not (pathTemplates / sName / "container.conf").is_file():
            fnFailWith(f"template '{sName}' has no container.conf")
    print(f"  templates: {', '.join(listTemplates)}")


def fnCheckContainerContextIsUsable():
    """The build context must be present and be vaibify's own."""
    from vaibify.resources import fpathContainerImageRoot
    pathContext = fpathContainerImageRoot()
    for sRequired in ("Dockerfile", "entrypoint.sh", "vaibifyDo.py"):
        if not (pathContext / sRequired).is_file():
            fnFailWith(
                f"build context '{pathContext}' is missing "
                f"{sRequired}"
            )
    print(f"  container context: {pathContext}")


def fnCheckDashboardAssetsArePresent():
    """The GUI cannot render without its static assets."""
    from importlib import resources
    pathStatic = (
        pathlib.Path(str(resources.files("vaibify"))) / "gui" / "static"
    )
    for sRequired in ("index.html", "vendor/xterm.min.js"):
        if not (pathStatic / sRequired).is_file():
            fnFailWith(f"static asset missing: {sRequired}")
    print(f"  dashboard assets: {pathStatic}")


def fnCheckConsoleScriptRuns():
    """``vaibify`` and ``vaib`` must be on PATH and answer --version."""
    for sCommand in ("vaibify", "vaib"):
        resultProcess = subprocess.run(
            [sCommand, "--version"], capture_output=True, text=True,
        )
        if resultProcess.returncode != 0:
            fnFailWith(
                f"'{sCommand} --version' exited "
                f"{resultProcess.returncode}: {resultProcess.stderr}"
            )
    print(f"  console scripts: {resultProcess.stdout.strip()}")


def fnCheckInitScaffoldsAProject(pathScratch):
    """``vaibify init --template`` must produce a usable project."""
    pathProject = pathScratch / "installCheckProject"
    pathProject.mkdir(parents=True, exist_ok=True)
    resultProcess = subprocess.run(
        ["vaibify", "init", "--template", "sandbox"],
        cwd=str(pathProject), capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        fnFailWith(
            f"'vaibify init --template sandbox' exited "
            f"{resultProcess.returncode}:\n{resultProcess.stdout}\n"
            f"{resultProcess.stderr}"
        )
    if not (pathProject / "vaibify.yml").is_file():
        fnFailWith("'vaibify init' wrote no vaibify.yml")
    print(f"  init scaffolded: {pathProject / 'vaibify.yml'}")


def fnCheckWorkflowTemplateRuns(pathScratch):
    """The shipped example workflow must execute and draw its figure.

    A template whose steps cannot run is worse than no template: it is
    the first thing a new user clicks Run on. The shipped one declared
    two scripts that did not exist and two step directories the slug
    contract forbids, and no check noticed because nothing ever
    executed a template.
    """
    import json

    pathProject = pathScratch / "workflowRun"
    pathProject.mkdir(parents=True, exist_ok=True)
    resultProcess = subprocess.run(
        ["vaibify", "init", "--template", "workflow"],
        cwd=str(pathProject), capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        fnFailWith(
            f"'vaibify init --template workflow' exited "
            f"{resultProcess.returncode}: {resultProcess.stderr}"
        )
    dictWorkflow = json.loads(
        (pathProject / "project.json").read_text()
    )
    fnRunEveryStep(pathProject, dictWorkflow)


def fnRunEveryStep(pathProject, dictWorkflow):
    """Execute each step's commands in order, resolving its tokens."""
    sPlotDirectory = str(
        pathProject / dictWorkflow.get("sPlotDirectory", "Plot")
    )
    dictTokens = {
        "{sPlotDirectory}": sPlotDirectory,
        "{sFigureType}": dictWorkflow.get("sFigureType", "pdf"),
    }
    for dictStep in dictWorkflow["listSteps"]:
        pathStep = pathProject / dictStep["sDirectory"]
        for sCommand in (
            dictStep["saDataCommands"] + dictStep["saPlotCommands"]
        ):
            fnRunStepCommand(sCommand, dictTokens, pathStep)
        for sOutput in dictStep["saOutputDataFiles"]:
            sStem = pathlib.Path(sOutput).stem
            sToken = "{step:%s.%s}" % (dictStep["sStepId"], sStem)
            dictTokens[sToken] = str(pathStep / sOutput)
    sFigure = sPlotDirectory + "/histogram." + dictTokens["{sFigureType}"]
    if not pathlib.Path(sFigure).is_file():
        fnFailWith(f"the example workflow drew no figure at {sFigure}")
    print(f"  example workflow ran: {sFigure}")


def fnRunStepCommand(sCommand, dictTokens, pathStep):
    """Resolve a command's tokens and run it in its step directory."""
    for sToken, sValue in dictTokens.items():
        sCommand = sCommand.replace(sToken, sValue)
    saCommand = [sys.executable] + sCommand.split()[1:]
    resultProcess = subprocess.run(
        saCommand, cwd=str(pathStep), capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        fnFailWith(
            f"step command failed in '{pathStep.name}': {sCommand}\n"
            f"{resultProcess.stdout}\n{resultProcess.stderr}"
        )


def fnCheckNoBytecodeLeaksIntoNewProjects(pathScratch):
    """A scaffolded project must not inherit site-packages bytecode.

    pip byte-compiles the shipped template scripts at install time, so
    a verbatim copy seeds every new project with ``__pycache__``
    directories full of ``.pyc`` files stamped with site-packages
    paths.
    """
    listStray = sorted(
        str(p.relative_to(pathScratch))
        for p in pathScratch.rglob("__pycache__")
    )
    if listStray:
        fnFailWith(
            f"scaffolded projects contain copied bytecode: {listStray}"
        )
    print("  no bytecode copied into scaffolded projects")


def main():
    """Run every check, or exit non-zero at the first failure."""
    import tempfile
    print("Checking the installed vaibify distribution ...")
    fnCheckPackageIsNotTheCheckout()
    fnCheckTemplatesAreUsable()
    fnCheckContainerContextIsUsable()
    fnCheckDashboardAssetsArePresent()
    fnCheckConsoleScriptRuns()
    with tempfile.TemporaryDirectory() as sScratch:
        pathScratch = pathlib.Path(sScratch)
        fnCheckInitScaffoldsAProject(pathScratch)
        fnCheckNoBytecodeLeaksIntoNewProjects(pathScratch)
        fnCheckWorkflowTemplateRuns(pathScratch)
    print("OK: the installed distribution ships everything it needs.")


if __name__ == "__main__":
    main()
