---
name: terminal-sessions
description: The /ws/terminal route, terminal containment, and why a project in which a terminal has run reports quiescence UNPROVEN rather than quiet. Use when touching terminalRoutes.py, terminalContainment.py, hostCancellation.py, the terminal journal kind, or the quiescence claim.
---

# The terminal, and the claim it costs

Containment of a terminal is not proven and cannot be assumed: a shell
can `setsid` out of the session the record tracks. Vaibify therefore
makes the weaker claim rather than the false one. Do not strengthen it.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

## The terminal serves both modes, and costs the quiescence claim

**`/ws/terminal` serves container projects AND host projects
(2026-08-15 ruling).** A host project's shell is a real PTY on the
researcher's own machine, launched by the host gateway's suspended-gate
primitive and journaled with the `terminal` kind before its first
instruction.

Containment of a terminal is **not proven and cannot be assumed**. A
shell can `setsid` out of the session the containment record tracks,
so "the terminal stopped" is not provable. Vaibify therefore does not
claim it: **a project in which a terminal has run reports quiescence
UNPROVEN and routes to `vaibify reconcile`, never quiet.** Do not
weaken that back — a release that reports quiet after a terminal is a
false statement, and the feature is only defensible because the
statement is true. The cost is real and intended, in both modes, and
the host lane adds a second honesty device: every host session's first
output is a banner saying the shell runs on the researcher's own
machine and that processes can outlive the tab (the host-mode modal is
the standing consent; the banner is the per-session reminder).

`terminalContainment` and the `terminal` journal kind are what make the
weaker claim honest — the record is the difference between a detached
process being *unproven* and being *invisible*. Deleting either removes
the honesty, not the risk.

**Ordering in the handler is the contract**: gate, then branch on the
host mode, then `require` the daemon, then build the session. The gate
is the shared `fiContainerSessionRejectionCode` guard the pipeline lane
uses — never an inlined membership check, or the two lanes drift about
who owns a container. A session built before the gate would put a
quarantine-bearing operation on a project for a caller with no
standing in it; `require` before the host branch would answer "install
Docker" about a project that never wanted one; and the branch decides
WHICH session class carries the record — `HostTerminalSession`, never
the Docker class, for a host project.
`testTheTerminalRouteGatesBeforeItBuildsAnything` pins it.

**Host containment is SESSION-wide, on both halves.** A shell's job
control moves children to new process groups within its session
(verified live: a backgrounded `disown`ed job wears its own pgid), so
the probe enumerates by session id, and the drain delivers per-member
(`hostCancellation.fnSignalSessionMembers`) — a `killpg`-only probe or
delivery would miss exactly the stray this machinery exists to find.
The reconcile-time prover for a crashed hub's host terminal record
does the same sweep (`_fdictProbeHostTerminalOperation`); killpg-empty
is treated as necessary, never sufficient.
`I_REJECT_TERMINAL_NOT_ON_HOST` is RESERVED, no longer emitted: hubs
between 2026-08-11 and 2026-08-15 refused host terminals with it.

**The handler resolves the container name before the gate**, so a
caller that can reach the socket can distinguish a real id from a
fabricated one. That is a property of the WebSocket gates in general —
`/ws/pipeline` has the identical ordering — so treat it as one boundary
to fix in both lanes or neither, never as a terminal-specific hole.

Four controls keep the feature contained. A no-callers invariant over
`terminalContainment` **cannot** pass, because the module keeps
production callers for drain, reap and shutdown, so they are narrower:
only `vaibify/gui/routes/terminalRoutes.py` constructs a `TerminalSession`
(`testOnlyTheGatedRouteConstructsATerminalSession`), so every shell is
one the gate admitted; only one handler answers the path
(`testOnlyOneHandlerServesTheTerminalWebSocket`); only the seam names
the record-creation calls
(`testOnlyTheSeamPreparesATerminalExecutionRecord`), so no shell runs
without the record the quiescence claim depends on; and every other
containment caller is cleanup
(`testRemainingContainmentCallsAreCleanupOnly`).

**A terminal journal record is never swept** — not by an upgrade, not
by a later session. It stays on disk and keeps its container
QUARANTINED until the container is positively stopped or its process
group proven empty, i.e. through `vaibify reconcile`. Opening a
terminal settles nothing about an existing record, because it has
proven nothing (`tests/testWithdrawnTerminalLegacyRecords.py`).

**`tests/testTerminalContainment.py` keeps the process-group prover as
a standing demonstration that it cannot see a `setsid` descendant.** It
is not a gate to be satisfied; it is the evidence for the limit stated
above. A green run there is not containment.

**A resize is an ORDERING problem, and it broke the pane two ways.**
xterm re-wraps its buffer the instant it is resized; the program in
the pane learns its width only when SIGWINCH arrives. Between those,
a program that repaints in place — cursor up N rows, erase, reprint,
which is what every full-screen agent does dozens of times a second —
composes a frame for one width and has it painted at another, so its
erase misses and the old frame survives above the new. That is the
duplicated text researchers reported for months. The hub therefore
resizes the pty in the READ loop, **after draining it**, and only
then tells the browser it may reflow
(`_fnApplyPendingResizeAndAcknowledge`): the acknowledgement is an
ordering marker on the output stream, not a reply. Acknowledging
before draining measured three stale frames on a real pane; draining
first, one — and the one that remains belongs to the program's own
model of what it printed, which nothing on this side can reach. Do
not move that ioctl back to where the request lands, and do not
reflow without waiting for the marker: both were measured to be
indistinguishable from having no ordering at all.

The second failure was worse and quieter. A reflow left the viewport
parked away from its own output — measured, 16 pixels down a buffer
1475 tall, showing the fourth line of forty — and because xterm
resumes auto-scrolling only for a pane sitting *exactly* at the
bottom, it never followed again. The terminal read as hung. The
follow state is therefore REMEMBERED as the researcher scrolls
(`fnTrackFollowingOutput`) and restored after a reflow, never
measured when the resize arrives: by then the browser has re-laid-out
the pane, so the element reports its new height against a buffer that
has not reflowed, which measured as a 128-pixel scroll-back nobody
had performed. Restoring unconditionally is the opposite defect — a
researcher reading scrollback must not be yanked to the newest line
because the window changed size — and both directions are
kill-confirmed in
`tests/browser/testAResizeKeepsThePaneFollowingItsOutput.py`.

**These two guarantees have historically traded off, so they are
pinned together.** `d4978e6c` guarded the resize path against reflow
churn; `93d06af6` guarded the deferred fit so a resize could not take
a selection mid-copy. Each was verified against its own symptom and
neither against the other's, which is why fixing one kept appearing
to break the other.
`tests/browser/testResizeAndCopyHoldTogether.py` asserts both in one
pane, including that a deferred fit still LANDS once the selection
clears — deferring is not dropping. Its companion,
`tests/testTerminalResizeOrdering.py`, drives the hub half and asserts
the drain as an ORDER rather than a count, because "the bytes were
sent" is equally true of the defect.
