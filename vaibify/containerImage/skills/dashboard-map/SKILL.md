---
name: dashboard-map
description: Find any part of the vaibify dashboard by its exact on-screen label and give the researcher the full path to it. Use whenever you tell a researcher where to click, name a button, row, section or menu item, or when they ask where something is or seem stuck.
---

# Where things are on the dashboard

The researcher works in the dashboard; you do not see it. Everything
you tell them to click must be a label they can find, on the path you
give. Agents before you invented labels ("Environment record" for the
row called **Environment snapshot**, a button named after an action ID)
and sent researchers looking for things that do not exist.

## The rules

Bold on this page means an on-screen label, and nothing else.

1. Quote only labels written in bold on this page, exactly as spelled
   here. A label not on this page may exist, but you have not seen
   it: say you do not know what it is called and describe where to
   look, rather than guessing a name.
2. An action name is never a label. `vaibify-do` action IDs such as
   `publish-where-the-zenodo-record-is` name machinery, not buttons.
3. Give the whole path: area, then section, then row, then the
   control. For example: Main tab → Project block →
   **Published copies** → **GitHub mirror** → **Verify now**.
   Sections and rows are collapsed until clicked, so say "expand"
   when it matters.
4. When the researcher seems stuck, or asks where something is, walk
   them through it one step at a time: give the first step, ask what
   they see, then give the next. Do not stack five instructions on
   someone who could not find the first.
5. Labels that carry a value (a DOI, a site name, a file name) are
   described in plain words below, not quoted.

## The layout

- The toolbar across the top has four menus: **Run**, **Sync**,
  **View**, **Admin**.
- The left panel has five tabs: **Main**, **PROOF**, **Files**,
  **Repos**, **Logs**.
- The **Main** tab shows the **Project** block (requirements for the
  whole project), then the **Steps** list.

## The toolbar menus

- **Run**: **Run Selected Steps**, **Run All Steps**,
  **Clean Outputs**, **Force Run All (Clean)**,
  **Stop This Project's Run**, **Verify Outputs**,
  **Run All Unit Tests**, **Verify Dependencies**,
  **Check Files Against Manifest**,
  **Verify Level 3 Reproducibility** (the Level 3 rerun),
  **Standardize All Plots**.
- **Sync**: **Push to Overleaf**, **Push to GitHub**,
  **Archive to Zenodo** (publishes to Zenodo; when the project already
  has a record, the dialog's button reads **Publish new version**),
  **Configure arXiv…**, and **Verify Reproducibility**, which opens a
  dialog with **Verify Manifest**. That dialog is NOT the Level 3
  rerun; the rerun is Run → **Verify Level 3 Reproducibility**.
- **View**: **Dependency Graph**, **Open in VS Code**,
  **Resource Monitor**, **Zenodo Status**.
- **Admin**: **Environments**, **Projects**, **New vaibify window**,
  **Environment Info**.

## The Project block, section by section

Each section heading expands; each row expands to show its detail and
its buttons. When the order of the remaining work matters, the
section holding the row to fix next carries a **Do this next** arrow
on its heading.

- **Repository**: **Git enabled**, **Input data declared**.
- **Software**: one row per declared binary, named by its file, with
  **Capture version + SHA**; or **No declared binaries** /
  **No standalone binaries (declared)**.
- **Artifacts**: **Manifest (MANIFEST.sha256)**,
  **Dependency lock (requirements.lock)**, **Environment snapshot**,
  **Dockerfile**, **Reproduce script (reproduce.sh)**,
  **Environment archive**. The first three offer **Regenerate now**;
  a sandbox archive offers **Make Permanent**.
- **Determinism**: **Speed or repeatable files**, the one question,
  with the choices **Prioritize speed** and
  **Repeatable across computers**. The answer is the researcher's.
- **Published copies**: **GitHub mirror**, **Zenodo archive**,
  **Overleaf manuscript**, **arXiv submission**. Each offers
  **Verify now**; **GitHub mirror** also offers
  **Push changed files to GitHub** when files differ. Until the
  attestation is current, **Zenodo archive** also offers
  **Verify Level 3 reproducibility**, because publishing first costs a
  second Zenodo version. When the project's Zenodo record is on a
  different site from the one it publishes to, that row explains the
  mismatch and offers two buttons that change where the NEXT publish
  goes (one publishes to the record's site from now on, the other
  starts a separate record); neither publishes anything.
- **AI**: **AI models**, **Personal AI Configuration**.
- **Attestation**: **Rebuild attestation**, with
  **Verify Level 3 reproducibility** and, once a record exists,
  **View attestation**.
- **Interrupted promotions**: shown only when a promotion to permanent
  Zenodo was interrupted.

## Common destinations

- Answer the determinism question: Main → Project → **Determinism** →
  **Speed or repeatable files**.
- Regenerate the environment snapshot: Main → Project → **Artifacts**
  → **Environment snapshot** → **Regenerate now**.
- Verify Level 3: Run → **Verify Level 3 Reproducibility** (the same
  button sits on **Rebuild attestation** and on **Zenodo archive**).
- Re-check GitHub after a push: Main → Project → **Published copies**
  → **GitHub mirror** → **Verify now**.
- Publish to Zenodo: Sync → **Archive to Zenodo**, keep the
  preselected files, then the dialog's button.
