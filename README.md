<p align="center">
  <img width = "350" src="docs/vaibify_logo.png?raw=true"/>
</p>

<h1 align="center">Vibe Boldly. Verify Everything.</h1>

<p align="center">
  <a href="https://RoryBarnes.github.io/vaibify">
    <img src="https://img.shields.io/badge/Read-the_docs-blue.svg?style=flat">
  </a>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/docs.yml/badge.svg">
  <a href="https://RoryBarnes.github.io/vaibify/conduct.html">
    <img src="https://img.shields.io/badge/Code%20of-Conduct-black.svg">
  </a>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/pip-install.yml/badge.svg">
  <br>
  <!-- Endpoint badges read counts from json on the orphan `badges` branch,
       refreshed by .github/workflows/badges.yml on every push to main. They
       display "no data" until badges.yml runs on main for the first time.

       All of them share ONE colour, deliberately. A count is not a
       verdict: `falsification tests` shipped in brightgreen and read as
       "falsification: passing" beside the status badges that really do
       mean that. The label already says which count it is, so a hue per
       badge buys nothing and costs a false signal. Never paint one in a
       pass/fail colour -- testCountBadgesAreNotPaintedLikeVerdicts
       fails the build if you do. -->
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/tests.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/falsification.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/invariants.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/browser.json">
  <br>
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/security.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/style.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/ssh.json">
  <br>
  <!-- Merge-gate status. These report the checks that gated the LAST MERGE
       into main, resolved by badges.yml from the merge commit's pull
       request. GitHub's own workflow badges are deliberately not used
       here: they show the newest run on ANY branch, so a contributor's
       failing pull request would redden the README while main is fine. -->
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusTestsLinux.json">
  <img src="https://img.shields.io/badge/Ubuntu%2022--24-Python%203.9--3.14-7d93c7.svg">
  <br>
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusTestsMacos.json">
  <img src="https://img.shields.io/badge/macOS%2015--26-Python%203.9--3.14-7d93c7.svg">
  <br>
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusFalsification.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusBrowser.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusAgentDocs.json">
  <br>
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusStyle.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusSsh.json">
  <br>
  <!-- The security lane carried a hand-typed "scope: regression subset"
       label until 2026-09-02. It was computed by nothing and asserted by
       nothing, and it had already gone stale in the direction that
       matters: it said the boundary-hardening invariants were absent
       from the lane, which they had not been since cca86404. A caveat
       that does not change when the lane changes is not a caveat. The
       badge now means what it says -- the security lane passed -- and
       what that lane actually runs is a row in docs/testing.md, beside
       the workflow it describes. -->
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/statusSecurity.json">
  <br>
  <!-- The scheduled lanes keep GitHub's own badges, but MUST pin
       ?branch=main. Unqualified, that badge reports the newest run on
       any branch: fresh-image-build also triggers on pull requests
       touching the image, so every one of its recent runs was a
       contributor branch and the badge stopped describing main at all.
       Nobody watches a nightly or weekly run, so these are the badges
       most likely to be the only sign of a failure. -->
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/containerAcceptance.yml/badge.svg?branch=main">
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/freshImageBuild.yml/badge.svg?branch=main">
  <a href="https://codecov.io/gh/RoryBarnes/vaibify">
  <img src="https://codecov.io/gh/RoryBarnes/vaibify/branch/main/graph/badge.svg">
</a>
</p>


`vaibify` creates secure, containerized environments for AI-assisted data analysis that can be accessed through a web browser. It fully embraces agentic AI code development, but recognizes that a human must verify all results. `vaibify` uses Docker containers to prevent AI agents from harming your sensitive data. These containers can be monitored and modified through an application that includes terminal window(s) for running agents like `Claude Code`, `codex`, or `Gemini` and "viewing windows" for inspecting results (data files, figures, animations). Work with agents to be creative in a sandbox, develop a toolkit, or enter "Project" mode, which enables pipeline development with automated and manual verification tracking for each step. `vaibify` is vigilant, alerting you to changes in your dependencies, so when your agent edits a critical file that updates an critical file, you immediately know all the downstream consequences. Seamlessly link your work with external resources like GitHub, Overleaf, and Zenodo for monitoring software development, writing reports, and archiving your results. `vaibify` allows you to vibe code with confidence: your host machine stays safe while the agents freely develop code and build your analysis pipeline — all with minimal IDE interaction — enabling you to focus on vetting the results via visual inspection, writing up a summary, and acting on the new insight. `vaibify` takes the pain out of creating byte-reproducible science. 

<p align="center">
<img src="docs/vaibify_screenshot.png">
</p>

In this screenshot of the `vaibify` dashboard, the steps to your workflow are tracked on the left. View the contents of the `vaibify` container along the top row in "viewing windows". Manage your agents and navigate the container yourself in terminal window(s) in the bottom of the GUI. Use buttons and menus to perform most basic tasks, or ask your agent to make changes. Additional pages allow you to create and manage containers and projects (see documentation).

Note that a full `vaibify` installation can take over an hour; the container requires the installation of a specific operating system. See the [full documentation](https://RoryBarnes.github.io/vaibify) for installation instructions, CLI reference, configuration, security model, and contributor guidelines. But you can get started without the container if you just want to quickly try it out. Read the [Quick Start Guide](https://RoryBarnes.github.io/vaibify/quickStart.html), then just run `vaibify` to launch the GUI that will guide you through building containers, creating workflows, syncing with external services, and verifying your vibe-coded scientific workflows.

Found a bug or something confusing? Please [open an issue](https://github.com/RoryBarnes/vaibify/issues). Two things make a report much easier to act on: the output of `vaibify doctor` (a pre-flight check of your Docker environment), and the relevant lines from the host log at `~/.vaibify/vaibify.log`.

If you use `vaibify` in your research, please consider citing "Barnes, R. (2026), PASP, submitted."

© 2026 Rory Barnes.
