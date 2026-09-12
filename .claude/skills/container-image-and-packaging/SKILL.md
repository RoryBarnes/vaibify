---
name: container-image-and-packaging
description: The vaibify container image: the Dockerfile and entrypoint, the Claude/Codex/Gemini agent overlays and their shared login store, agent-facing docs inside the container, and how runtime resources are located so they ship in the wheel. Use when editing vaibify/containerImage/, vaibify/resources.py, the packaged templates, or the docs staged into the image.
---

# The container image and packaged resources

Two rules dominate this area. Anything the image needs must live under
`vaibify/` and be located through `vaibify/resources.py`, because a
`parents[N]` walk resolves to site-packages in an install. And every
agent in the image reads one canonical instruction file through
symlinks -- a second real file at one of those names silently splits
them.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**`introspectionScript.py` is an f-string executed inside containers.**
Editing it as ordinary Python loses escape sequences and string
delimiters silently. The format-handling duplication with
`dataLoaders.py` is also deliberate — container scripts cannot import
from the host environment.

## A container may host Claude, Codex, or Gemini

Container agents are overlays selected by feature flags
(`vaibify/containerImage/Dockerfile.claude`, `.codex`, `.gemini`). Each installs **as
the unprivileged container user**, not root, so the provider's own
updater can replace its binary without sudo — do not "fix" that by
installing as root, and do not add sudo to the image.

**Cline is the exception to the flat-name rule.** Six of the seven
agents read a repo-root markdown file, so a symlink serves them. Cline
reads a `.clinerules/` **directory**, so `fnLinkClineRules` creates it
with a symlink inside pointing back at the canonical file — a flat
`.clinerules` symlink would be a file where a directory is expected.
It is gated on Cline being installed, because a stray directory in the
researcher's repository is the sort of thing that gets committed by
accident. `tests/testEntrypointAgentDocLinks.py` fails if an agent is
added to the skills loop without a path to the guidance, which is
exactly how Cline came to ship with skills and no instructions.

**Agent-facing docs have one source and several names.** Inside the
container the canonical file is
`/workspace/<repo>/.vaibify/AGENTS.md`; `entrypoint.sh`'s
`fnLinkRepoClaudeMd` symlinks `/workspace/<repo>/CLAUDE.md`,
`/workspace/<repo>/AGENTS.md` and `/workspace/<repo>/GEMINI.md` to it,
and migrates a legacy CLAUDE.md in that directory into place. So write
in-container agent guidance once, to the canonical file. Never author
a provider-specific one — a second *real* file at one of those names
shadows the symlink for that provider only, and the three agents
silently start reading different instructions.

(These are container paths, deliberately absolute:
`tools/checkAgentDocsPaths.py` resolves repo-relative references and
would flag them as broken, because they exist only inside a running
container.)

**All three agents share one login store and one user.** Each agent's
config directory is persisted into the workspace volume and symlinked
back into the home directory (`fnPersistAgentConfig`), so logins
survive container recreation. This pattern predates multi-agent
support; what changed is the blast radius. Every agent runs as the
same container user, so file permissions isolate nothing between
them: whichever agent is compromised can read all three providers'
credentials, and they are reachable through the dashboard's file
routes like any other workspace path. Treat "an agent was
compromised" as "every configured provider's session was exposed"
when reasoning about a threat, and do not add a fourth provider
without revisiting that.

## Runtime resources live inside the package

`vaibify/templates/` and `vaibify/containerImage/` ship in the wheel.
They used to be reached with `parents[N]` from the repository root,
which resolves to `site-packages` in an install — so no wheel ever
contained them and `vaibify init` printed "No templates found" and
exited 0. Four rules follow; the full account is in
[docs/architecture.md](docs/architecture.md) — "Packaging: why runtime
resources live inside the package".

**Locate them only through `vaibify/resources.py`.** It is the single
place that names the trees, and `importlib.resources` resolves them
identically from a checkout, an editable install, and a wheel. **Never
reintroduce a `parents[N]` walk to reach package data** — and after
fixing any resolution bug, grep for every other way the codebase reaches
outside the package, not just the spelling that bit you.

**Treat them as read-only, and give every build its own copy.**
`commandBuild.fsStageBuildContext` mkdtemps a private context under
`~/.vaibify/build/`, discarded on success and kept on failure with its
path printed. It is per *build*, not per project — two dashboard clicks
race, and refreshing a shared directory starts with `rmtree`.
`tests/testPackagedResources.py` fails if this regresses.

**Anything the image needs must live under `vaibify/`.** The curated
agent docs at `vaibify/docs/` are **symlinks onto the Sphinx sources** —
never replace one with a real file, which is the shadowing trap;
`testCuratedDocsRemainSymlinksOntoTheSphinxSources` fails if you do.
When adding one: add the symlink, extend `T_STAGED_DOCS`, extend the
doc-map skill's table, and add the *Sphinx source* path to
`freshImageBuild.yml`'s triggers.

**Prove the distribution, not the import.** `pip-install.yml` runs
`tools/checkInstalledDistribution.py` against an installed sdist and
wheel. It is **release-only**, so a packaging regression can sit on
`main` until a version is cut — after touching `vaibify/resources.py`,
the packaged trees, or the Dockerfile `COPY` set, run it by hand
(`workflow_dispatch`). Never make it a required status check: it cannot
report on a pull request, so every PR would wait on it forever.
