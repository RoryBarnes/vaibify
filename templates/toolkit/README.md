# Toolkit Container

A toolkit container is a workspace for editing several peer code
repositories side-by-side. It is the right choice when you want to
hack on more than one package at once, for example editing
`vplanet`, `vspace`, and `multiplanet` in tandem while you iterate
on a change that spans them.

Toolkit containers have no workflow. Instead, the Repos panel in
the GUI provides per-repository git status, dirty-file listings,
and push controls for every repository in `/workspace/`.

## How tracking works

- Any repository URL you listed in the creation wizard is cloned
  into `/workspace/` on first container start and is automatically
  tracked, so it appears in the Repos panel with no extra clicks.
- If you later clone a new repository from the terminal, the
  panel will detect it on the next poll and prompt you to choose
  Track or Ignore for that repository.
- Tracked repositories persist across container restarts via the
  sidecar at `/workspace/.vaibify/tracked_repos.json`.

## Push controls

For every tracked repository you can push staged changes or push a
specific list of files, each with its own commit message. Pushes
use whatever credentials the container already has configured for
`git push`.
