# Known technical debt

These are known, deliberate, and load-bearing. **Do not "fix" any of
them without discussion** -- each one is the honest interim for a
problem whose real fix is a product decision, and several look like
oversights precisely because the honest interim resembles one.

These are known, deliberate, and load-bearing — do not "fix" them
without discussion:

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. Container scripts cannot import from the host.
- `scriptFigureViewer.js` was not part of the 2026-01 frontend
  refactor. Kept as a single cohesive module.
- Re-export blocks exist across `pipelineRunner`, `pipelineServer`,
  `testGenerator`, and `syncDispatcher` for backward compatibility.
  Callers should migrate toward canonical imports over time; do not
  delete the re-exports until external callers are updated.
- `vaibify/reproducibility/githubWorkflow.py` is implemented, tested,
  and **unreachable** — no product code imports it. Kept rather than
  deleted because wiring it expands remote-execution surface, which is
  a product decision. `tests/testOrphanedPublishMachinery.py` fails if
  the docs re-advertise it or if it gains a caller while still marked
  unreachable.
- `condaPackages` is refused at validation rather than installed. The
  Dockerfile installs Miniforge for a non-pip package manager but has
  no `conda install` step and no build argument carries the list, so
  accepting the field produced a container without the requested
  packages and said nothing. Wiring it is the honest fix; refusing is
  the honest interim.
- **`POST /api/zenodo/{id}/download` cannot work, and its two tests
  pass anyway.** It calls `syncDispatcher.ftResultDownloadDataset`,
  which exists nowhere — verified at runtime, `hasattr` is `False`, so
  every real call raises `AttributeError` and answers 500. The tests in
  `testSyncRoutesCoverage.py` patch the name into existence with
  `create=True`, which is why the suite has been exercising a function
  the product does not have. It is advertised to the in-container agent
  as `download-zenodo-dataset` with `bAgentSafe: True`, so an agent
  asked to fetch a dataset calls it and fails. **Do not "fix" this by
  deleting or loosening the tests** — the missing function is the
  defect. It is also the one mutating route left undeclared by the
  carrier migration, deliberately: inside a carrier that
  `AttributeError` would poison the journal and quarantine a working
  container over a broken button. Writing the function is a feature
  decision.
- `terminalContainment.py` and the `terminal` journal kind also
  reconcile records written by EARLIER hub versions — release, the
  safe reaper and shutdown all settle terminal records through
  `fdictTerminateAndProveRecord` — and
  `tests/testTerminalContainmentLive.py` keeps the container
  process-group prover as the standing demonstration that it cannot
  see a `setsid` descendant (`tests/testHostTerminal.py` carries the
  host leg's twin demonstration). This bullet used to say the
  in-memory registry was permanently empty; the terminal came back
  for containers on 2026-08-11 and for host projects on 2026-08-15,
  so live sessions register in production again.
- `commitCarrier.fdictRequestDurableTaskCancel` has no caller and
  refuses everything. Kept deliberately: Python cannot interrupt a
  worker in `asyncio.to_thread`, so there is no honest generic cancel,
  and a reader who finds no function at all re-derives that from
  scratch — or writes one. The refusal is the answer, in the place the
  question is asked.
- The poison axis is NOT subsumed by the journal quarantine. A
  quarantine record survives a crash; it does not fence a socket that
  is open right now. Poison does both — the pipeline lane is refused at
  the gate and revalidated per frame — so the two are complementary,
  not redundant.
