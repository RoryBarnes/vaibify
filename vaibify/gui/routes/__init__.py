"""Route modules for the Vaibify FastAPI application."""

__all__ = [
    "stepRoutes",
    "fileRoutes",
    "syncRoutes",
    "testRoutes",
    "plotRoutes",
    "pipelineRoutes",
    "terminalRoutes",
    "workflowRoutes",
    "settingsRoutes",
    "figureRoutes",
    "scriptRoutes",
    "systemRoutes",
    "repoRoutes",
]

from . import (
    stepRoutes,
    fileRoutes,
    syncRoutes,
    testRoutes,
    plotRoutes,
    pipelineRoutes,
    terminalRoutes,
    workflowRoutes,
    settingsRoutes,
    figureRoutes,
    scriptRoutes,
    systemRoutes,
    repoRoutes,
)
