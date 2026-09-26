# QuickStart

In this guide you will first reproduce an existing result within 15 minutes. These results, however, will not be byte-identical to those that were published because you will reproduce the results locally on a computer that is slightly different than the original author's. Thus, you will then recreate the author's computing environment inside your computer and verify that you reproduce the results exactly. This latter step can take over an hour if you do not already have Docker installed on your computer.

## 1. Install

You need Python 3.9 or later to run `vaibify`.

```bash
pip install vaibify
vaibify
```

Run `vaibify` with no arguments in any directory to start the **Environments Hub** —
a local web server on `http://127.0.0.1:8050` — in your browser. You
should see the `vaibify` logo, the motto, and an empty **Environments**
list.

## 2. Download the example project

Return to the terminal and run

```bash
git clone https://github.com/RoryBarnes/aigreenhouse.git
cd aigreenhouse
pip install -r .vaibify/requirements.txt
```

This repository contains the data to generate two figures from the `vaibify` paper (Barnes 2026) a real, finished analysis: estimates for how long it will be before the waste heat from AI
data centers becomes large enough for Earth to begin losing water permanently to space. The Project consists of two
computational steps, a figure from each, three tiers of tests, and a
declaration of which AI models helped write it. It is published at PROOF
Level 3, which means its author committed a manifest of every artifact's
SHA-256 hash along with the pinned environment that produced them.

## 3. View the example project in vaibify

Back in the hub, click the **+** next to *Environments* to add this project to the list of environments that `vaibify` can access. The wizard asks
two things: where the project runs — choose **This machine** rather than
*Container* — and whether the project already exists — choose **Add
Existing** and point it at your clone. Now you will return to the Environment Hub and see a registered environment: `aigreenhouse`. Select it.

You will now move to the **Projects Hub**. An environment is a *place* projects run, and it can hold more than one, so
this hub offers you one or more Projects to work on. You will see two options: A **Blank Project** is an empty
workspace (a "sandbox") in which you can experiment in the environment. All environments include a "Blank Project". The second option is **AI Greenhouse**, the project you are reproducing in this guide. Select it. 

You are now in the dashboard for this project. Take a look around. Along the top are some fundamental facts about the Project as well as menus for interacting with the Project. The left side shows the steps of the project and information about the project as a whole. The top right consists of two "viewing windows" that displays images and ASCII text files for you to examine and compare. The bottom left is one large reproduction of a terminal. You can create multiple tabs and multiple terminals. This dashboard is where you will use coding agents to modify files, examine their efforts, lock down verified results, and connect to external resources like Overleaf and Zenodo.

Although the published result is at PROOF Level 3, meaning the author verified they reproduced the results at the bit level, your copy is not at that level. In fact, you're at Level 0, meaning your results aren't even being labeled as "self-consistent"! This status is because you have not run any of the steps locally and verified the results pass the unit tests, which check that the output files match the author's results to some tolerance that they deemed acceptable for scientific reproduction, which is in general a lower bar than byte-reproducibility. Before we start driving your copy up the PROOF Ladder, we're going to explore the repository a bit more.

## 4. Check the archive before touching it

From the **Run** menu, choose **Check Files Against Manifest**. (The manifest is the list of artifacts in the repository and their SHA-256 hashes, which is a summary of the bytes in the file.) This action regenerates the hashes of every file in the repository and reports that all
of them match. 

This result has to hold for a freshly cloned Level 3 project because nothing has changed. You have
confirmed that the files in your clone are byte-for-byte identical to the files the
author committed. Note that this check is *not* a statement about the
accuracy of the science; it is a check on the archive's integrity, and nothing more.

## 5. Recreate the data

Next we will rerun the results locally and see if they pass the unit tests. First, we need to get rid of the old outputs and start clean.
From the **Run** menu, choose **Clean Outputs**, and confirm. Every automatic step's data files and figures are deleted and every
verification mark resets to untested. You will be prompted about the AI Declaration because it is interactive. Select **Skip** for this step because that is the author's statement and should not be deleted or modified until you make changes that you plan on publishing..

Now we're ready to rerun the pipeline. From the **Run** menu, choose **Run All Steps**. Each step runs its data commands, then its plots, and
pulses orange as it runs and turns to solid orange once it finishes. Orange means some part of the step is verified, but not everything. You'll also see the terminal output of the command in the top left viewing window. 

Take a closer look at Step A01 by clicking on it, which expands the step to show you everything about it. There are multiple sections here about input data, scripts, plots, connections to remote resources, and verification. Files change colors based on their status and you can hover over them to learn about what issues they may have. In this case, the input and output data as well as the plot file are orange. Click on the plot file and you will see the figure appear in the top right viewing window and resize if necessary. Compare it to Figure 2 in Barnes (2026). It looks the same, but yet is orange. That's because you have not yet verified the results.

So let's run them: From the Run
menu's select **Run All Unit Tests** and you will see messages in the top right that state that they pass. Now look at the Step A01 block under verification and you can see that the Unit Tests marker has changed to "Pass". If you expand the Unit Tests, you can see the three types of unit tests - Qualitative, Quantitative, and Integrity - all pass, too. 

Note, however, that the research field still says "Untested". You have not declared the results to meet your own standard of scientific rigor. When you're using `vaibify` for your own research, you'll want to be very careful about passing the researcher test because that means you are declaring the results to be accurate. While the process can of course be undone, waiting until you are sure is the best approach with `vaibify`. But for this example, go ahead and click the researcher marker for Step A01 to "Passed". After a couple seconds or so, the left-most orange marker in the banner for Step A01 will turn to a blue check, indicating this step has reached PROOF Level 1. Then do the same for Step A02. This time, when the dashboard detects the change, not only will the main orange marker flip to a check, the entire dashboard theme color will change to purple and you will now see a single check to the right of "AI Greenhouse" along the top bar. The number of checks next to the project name indicates the current PROOF Level of the project, Level 1 in this case.

### Doing the same thing with an agent

Using the dashboard to click through tasks is sooo 2025. Now we generally ask AI agents to perform tasks like "Run all steps and their unit tests." `vaibify` includes an [agent action catalog](dashboard.md#agent-actions) that provides deterministic commands that agents can pass to the host machine and run in your container. While this functionality might appear to expose your host machine to the container, the commands are tightly monitored to only direct the `vaibify` to perform operations inside a container. In this example, you are in host mode, though, so these operations would be performed on your local machine.

## 6. Check the bytes again

So are the new files identical to the author's manifest? From the **Run** menu, choose **Check Files Against Manifest** again.

They will fail. (Unless you are running on an Ubuntu v24.04 machine shortly after September 2026).

Not all of it, but the numbers you just regenerated are not identical
to the numbers the author published, and neither are the figures. Here's why not:

- **The fitted values differ in their last digits.** A least-squares fit
  is a sequence of floating-point operations, and different environment's
  build orders and vectorization schemes are different. The science is identical
  to twelve significant figures, which is close enough for most scientific reproducibility expectations. The bytes, however, are not identical.
- **The figures differ significantly.** Matplotlib stamps its own version into
  every PNG it writes, so a different matplotlib version guarantees
  different bytes before you even reach font rendering, which also
  differs by platform.

Your run passed every *scientific test* the project defines, but did
not reproduce byte-for-byte because your computing environment is slightly different from the one the author used. These differences arise because the standard rules that govern computing standards (the IEEE) allow changes in the last digits of some functions, like transcendentals. Thus, progress from here requires rerunning the work in the exact same environment that the author used, which is the topic of the next section.

This next section is technically optional, but discouraged. While the `vaibify` dashboard offers a complete view of your computer and the `vaibify` Projects, your computer is not secure and the results cannot be fully reproduced. We bring this up because the next step can take an hour or more for Mac and some Linux users who need to install Docker.

## 7. Containerize the same project

Before we begin this step, note the following:

- **The project must not be open anywhere else.** If it is open in the
  tab you are clicking from, `vaibify` closes it for you. A session in
  another browser or on another machine refuses the conversion instead,
  because the conversion renames the key that the project's lock,
  lease, and journal all hang from.
- **Install Docker or Colima if you don't have them yet.** This step requires Docker containers, which are run as `colima` on macOS. See the [install guide](install.md#docker-on-macos) to install. *Note that this process can require over 60 minutes.*
- **Your clone is not moved or changed.** The container gets a *copy* of the files you choose. The outputs you regenerated in section 5 stay in your folder exactly as they are.

If you're ready, then from the dashboard, press the **Admin** pulldown menu in the top right corner and select **Environments** to return to the original landing page. Open the kebab menu (**⋮**) on this environment's tile, and choose **Containerize Environment**.

A five-page wizard opens. Every page has a **?** that explains the choice in more detail; what follows is what to enter to finish this walkthrough.

| # | Page | What to enter here |
|---|------|--------------------|
| 1 | **Project Name** | The container, image, and registry name. It is pre-filled with a Docker-safe version of your directory name. Accept it unless it collides with another environment on this machine. |
| 2 | **Environment** | Leave **Use the author's pinned image** selected — `vaibify` selects it for you because this clone pins an image it can obtain. The page shows the pinned image, the platform it was built for, and your Docker daemon's platform. If they do not match (for example, an Apple Silicon Mac running an image built for Intel), tick **Allow emulation**: the run will be slower and is recorded as emulated. Do **not** choose *Build from the Dockerfile*: a new build is a different environment and cannot reproduce the author's bytes. |
| 3 | **Features & Authentication** | Tick **at least one coding agent**. Agents are added on top of the author's image; they do not change the environment the steps run in. |
| 4 | **Files to Copy** | Leave every entry ticked. Below the list, `vaibify` reports that some files the manifest pins differ from the last commit — these are the outputs you regenerated in section 5. Tick **Start from the committed files**. The container then starts with the author's versions of those files; your own versions stay in your folder. |
| 5 | **Summary** | Read it back — it names the pinned image and says the differing files will be the committed versions — then press **Convert** and confirm with **Convert and obtain**. |

`vaibify` now downloads the author's image, first from a registry and otherwise from its archived copy on Zenodo (about 1 GB for this project), checks it against the hash the author recorded, and starts the container. When it finishes, open the project again. It looks the same, except the `host-mode` badges become `contained`, and the steps now run in the author's environment rather than on your computer.

If the conversion fails partway, you are left with a registered container whose image has not been obtained yet. Fix the cause (run `vaibify doctor`, section 8) and choose **Re-obtain the pinned image** from the tile's menu. Nothing in your clone was touched, so nothing needs to be recovered.

### Reproduce the bytes

From the **Run** menu, choose **Verify Level 3 Reproducibility**, and press **Copy and verify**. `vaibify` copies the project into a fresh, throwaway container built from the author's pinned image, runs every step there, and compares each output with the author's `MANIFEST.sha256`. The dialog tells you that the run is recorded as *your* reproduction, because the author's attestation was committed by someone else. Your reproduction never overwrites it.

When the run finishes, open the **PROOF** tab and press **Compare manifests** on the **Your reproduction** card. The two viewing windows show the author's `MANIFEST.sha256` and your `REPRODUCED.sha256` side by side, one line per file, each colored by its outcome. **Every line matching is the payoff:** you rebuilt the author's results byte for byte in the author's environment, which is what section 6 could not do on your own computer.

If you skipped **Start from the committed files** in the wizard, Verify stops first and explains that the manifest no longer describes the files in the container. It then offers **Restore the committed versions**. That restores the author's files inside the container, leaves your computer alone, and carries on to the verification. Do not choose **Regenerate the envelope** here: that would replace the author's manifest with a description of your own outputs, and the comparison would then only check your files against themselves.

For readers without `vaibify`, the project also ships `reproduce.sh`, which performs the same rerun and ends with `sha256sum -c MANIFEST.sha256`; `vaibify reproduce --from <url> --rerun` does it from the published URL alone. [The reproducibility ladder](reproducibility.md) describes both.

## 8. When something is wrong

Run `vaibify doctor`, and do what it says.

```bash
vaibify doctor              # every scope
vaibify doctor --container  # only what is inside the running container
```

It is the command for the moment when a build will not build, a
container will not start, an agent inside the container cannot reach
anything, or the dashboard is saying something you did not expect. It
reports on three scopes — this machine, the inside of the running
container, and vaibify's own record of the project — and every finding
that needs action names the exact command to run, correct for the
Docker runtime you are actually using.

Three things about the report are worth knowing before you read one.

**Doctor changes nothing.** It never starts, stops, removes or restarts
anything. You can run it against a container that has just died without
destroying the evidence of why.

**"Not checked" is not "ok".** A check that could not run says so, with
the reason, in its own group, and it is counted in its own column. A
diagnostic that quietly reports success for having run nothing is worse
than no diagnostic.

**A finding you can act on names its own command.** Where vaibify can
apply the fix itself, that command is `vaibify repair`. Where it
cannot — a network that is down, a daemon that needs more memory — the
finding says so plainly rather than sending you to a tab.

### Outputs that were re-run outside the container

If you run a step's script yourself — in a terminal, in your editor, on
a cluster — the outputs it writes are not the outputs vaibify recorded,
and the dashboard will turn those files red. Nothing is broken and
nothing was lost: the badge is telling you the truth, which is that the
file on disk is no longer the one the recorded run produced.

The way forward is to run the step through vaibify (Run Step, or
`vaibify run`), so the run that produced the file is the run vaibify
recorded. Vaibify deliberately does not offer a way to mark a file
"fine as it is": a green badge over a file whose provenance nobody
knows is exactly the claim this tool exists not to make.

## 9. Where to next

- **[The three templates: sandbox, toolkit, workflow](templates.md)** —
  starting your own project rather than driving someone else's.
- **[The dashboard tour](dashboard.md)** — every panel, the status
  colors, and the verification state machine.
- **[The reproducibility ladder](reproducibility.md)** — what Levels 1
  through 3 each certify, and what none of them do.
- **[Security model](security.md)** — what a container protects against
  and what host mode does not. Worth reading before you let an agent
  write code anywhere.
- **[Install guide](install.md)** — Docker and Colima, plus
  platform-specific troubleshooting.
- **[Command line interface](cli.md)** — everything above, scriptable.

A note on what you will see in `git status` afterwards: opening a
project can refresh the small `conftest.py` that vaibify installs in
each step's `tests/` directory, because it records where the project
repository sits on the machine reading it. That file is vaibify's test
harness, not part of the analysis, and it is not pinned in the
manifest.
