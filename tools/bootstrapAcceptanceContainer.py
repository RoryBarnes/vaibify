#!/usr/bin/env python3
"""Start a container and seed a disposable project repo inside it.

CI-only setup for the container acceptance lane. It exists because
``vaibify start`` is the *interactive* path: ``flistBuildRunArgs``
returns ``--rm -it`` unless asked for detached, so on a runner with no
TTY it fails, and even when it does not, an attached container blocks
the job. The GUI already starts containers detached through
``fsStartContainerDetached``; this uses that same API rather than
inventing a third way to start a container.

It also supplies the thing the acceptance assertions need and the
inline heredoc scaffold never had: a real git repository. Note *where*
-- at ``/workspace/<name>``, never at ``/workspace`` itself.
``/workspace`` is a Docker-managed named volume and the discovery root,
not a repo; making it one reintroduces the all-grey-badges bug that
AGENTS.md documents at length.

    python tools/bootstrapAcceptanceContainer.py --project-dir /tmp/x
"""

import argparse
import json
import pathlib
import subprocess
import sys
import time


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

S_ACCEPTANCE_REPO_NAME = "acceptanceProject"

DICT_MINIMAL_WORKFLOW = {
    "sWorkflowName": "Acceptance Project",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 1,
    "listSteps": [
        {
            "sName": "Generate",
            "sDirectory": "Generate",
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python3 generate.py"],
            "saOutputDataFiles": ["Generate/output.dat"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested", "sUser": "untested",
            },
        },
    ],
}


def _fnRunInContainer(sContainer, sScript):
    """Run a shell snippet inside the container, raising on failure."""
    result = subprocess.run(
        ["docker", "exec", sContainer, "bash", "-lc", sScript],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"in-container setup failed ({result.returncode}): "
            f"{sScript[:80]}\n{result.stderr.strip()}"
        )
    return result.stdout


def fnWaitUntilResponsive(sContainer, fTimeoutSeconds=60.0):
    """Block until the container answers a trivial command."""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        result = subprocess.run(
            ["docker", "exec", sContainer, "true"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"container {sContainer} never became responsive"
    )


def fnSeedProjectRepository(sContainer, sWorkspaceRoot):
    """Create a git repo with a minimal workflow inside the workspace."""
    sRepo = f"{sWorkspaceRoot}/{S_ACCEPTANCE_REPO_NAME}"
    sWorkflowJson = json.dumps(DICT_MINIMAL_WORKFLOW)
    _fnRunInContainer(sContainer, f"mkdir -p {sRepo}/Generate")
    _fnRunInContainer(sContainer, f"mkdir -p {sRepo}/.vaibify/workflows")
    _fnRunInContainer(
        sContainer,
        f"cd {sRepo} && git init -q . "
        "&& git config user.email acceptance@example.invalid "
        "&& git config user.name 'Acceptance Fixture'",
    )
    _fnRunInContainer(
        sContainer,
        f"cat > {sRepo}/.vaibify/workflows/project.json <<'JSON'\n"
        f"{sWorkflowJson}\nJSON",
    )
    _fnRunInContainer(
        sContainer,
        f"printf 'seed\\n' > {sRepo}/Generate/output.dat",
    )
    _fnRunInContainer(
        sContainer,
        f"cd {sRepo} && git add -A && git commit -q -m 'seed fixture'",
    )
    return sRepo


def main():
    """Start the container detached, seed it, print what CI needs."""
    parser = argparse.ArgumentParser(
        description=(
            "Start the acceptance container detached and seed a "
            "disposable project repository inside its workspace."
        ),
    )
    parser.add_argument(
        "--project-dir", required=True,
        help="Directory holding the project's vaibify.yml.",
    )
    args = parser.parse_args()

    from vaibify.config.projectConfig import fconfigLoadFromFile
    from vaibify.docker import containerManager

    sConfigPath = str(pathlib.Path(args.project_dir) / "vaibify.yml")
    config = fconfigLoadFromFile(sConfigPath)
    sDockerDir = str(REPO / "docker")

    containerManager.fsStartContainerDetached(config, sDockerDir)
    fnWaitUntilResponsive(config.sProjectName)
    sRepo = fnSeedProjectRepository(
        config.sProjectName, config.sWorkspaceRoot,
    )
    print(f"container={config.sProjectName}")
    print(f"repo={sRepo}")


if __name__ == "__main__":
    main()
