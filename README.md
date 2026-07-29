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
       display "no data" until badges.yml runs on main for the first time. -->
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/tests.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/falsification.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/invariants.json">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/RoryBarnes/vaibify/badges/browser.json">
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
  <!-- The scheduled lanes keep GitHub's own badges: they really do run on
       main, on a timer, so "the latest run" IS main's state. Nobody
       watches a nightly or weekly run, so these are the badges most
       likely to be the only sign of a failure. -->
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/containerAcceptance.yml/badge.svg">
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/freshImageBuild.yml/badge.svg">
  <a href="https://codecov.io/gh/RoryBarnes/vaibify">
  <img src="https://codecov.io/gh/RoryBarnes/vaibify/branch/main/graph/badge.svg">
</a>
</p>


`vaibify` creates secure, containerized environments for AI-assisted data analysis that can be accessed through a web application. It fully embraces agentic AI code development, but recognizes that a human must verify all results. `vaibify` builds secure environments (Docker containers) that prevent AI agents from harming your sensitive data. These containers can be monitored and modified through an application that includes terminal window(s) for running agents like `Claude Code`, Codex, or Gemini and "viewing windows" for inspecting results (data files, figures, animations). Work with agents to be creative in a sandbox, develop a toolkit, or enter "workflow" mode, which enables pipeline development with automated and manual verification tracking for each step. `vaibify` is vigilant, alerting you to changes in your dependencies, so when your agent edits a critical file that updates an output file in Step 3, you immediately know all the downstream consequences. Seamlessly link your work with external resources like GitHub, Overleaf, and Zenodo for monitoring software development, writing reports, and archiving your results. `vaibify` allows you to vibe code with confidence: your host machine stays safe while the agents freely develop code and build your analysis pipeline — all with minimal IDE interaction — enabling you to focus on vetting the results via visual inspection, writing up a summary, and acting on the new insight.

<p align="center">
<img src="docs/vaibify_screenshot.png">
</p>

In this screenshot of the `vaibify` dashboard, the steps to your workflow are tracked on the left. View the contents of the `vaibify` container along the top row in "viewing windows". Manage your agents and navigate the container yourself in terminal window(s) in the bottom of the GUI. Use buttons and menus to perform most basic tasks, or ask your agent to make changes. Additional pages allow you to create and manage containers and workflows (see documentation).

Note that `vaibify` can take over an hour to install -- the container requires the installation of a specific operating system. See the [full documentation](https://RoryBarnes.github.io/vaibify) for installation instructions, CLI reference, configuration, security model, and contributor guidelines. But you can get started with just a few commands, depending on your system. Read the [Quick Start Guide](https://RoryBarnes.github.io/vaibify/quickStart.html), then just run `vaibify` to launch the GUI that will guide you through building containers, creating workflows, syncing with external services, and verifying your vibe-coded scientific workflows.

Found a bug or something confusing? Please [open an issue](https://github.com/RoryBarnes/vaibify/issues). Two things make a report much easier to act on: the output of `vaibify doctor` (a pre-flight check of your Docker environment), and the relevant lines from the host log at `~/.vaibify/vaibify.log`.

If you use `vaibify` in your research, please consider citing "Barnes, R. et al. (2026), ApJ, submitted."

© 2026 Rory Barnes.
