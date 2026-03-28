# Related Repositories

Vaibify is designed to work with the VPLanet ecosystem of tools for
simulating planetary evolution. Each repository solves a specific piece of
the analysis pipeline.

| Repository       | Description                                          |
|-----------------|------------------------------------------------------|
| [VPLanet](https://github.com/VirtualPlanetaryLaboratory/vplanet) | The Virtual Planet Simulator -- a C code that models planetary system evolution by coupling physics modules. |
| [vplot](https://github.com/VirtualPlanetaryLaboratory/vplot)     | Plotting package for VPLanet output with standardized figures and accessible colors. |
| [vspace](https://github.com/VirtualPlanetaryLaboratory/vspace)   | Generate parameter sweeps of VPLanet input files.     |
| [multiplanet](https://github.com/VirtualPlanetaryLaboratory/multi-planet) | Run VPLanet parameter sweeps in parallel across multiple cores. |
| [bigplanet](https://github.com/VirtualPlanetaryLaboratory/bigplanet)     | Compress large VPLanet output suites into indexed HDF5 archives. |
| [alabi](https://github.com/dflemin3/alabi)                       | Machine learning surrogate model for fast Bayesian posterior inference. |
| [vconverge](https://github.com/RoryBarnes/vconverge)             | Derive converged probability distributions for VPLanet output parameters. |
| [MaxLEV](https://github.com/RoryBarnes/MaxLEV)                   | Maximum likelihood estimator for VPLanet simulations. |
| [vplanet_inference](https://github.com/RoryBarnes/vplanet_inference) | Interface between VPLanet and Bayesian inference packages (alabi, emcee, dynesty). |

The `planetary` template for `vaibify init` pre-configures all of these
repositories in a single container. See [Templates](templates.md) for
details.
