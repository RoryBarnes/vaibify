"""
Detect stochastic sampling in Python scripts and check for seed presence.

Scans source code for randomness-consuming patterns across the full
scientific Python ecosystem: numpy, scipy, scikit-learn, MCMC/nested
samplers, stochastic optimizers, deep-learning frameworks, and more.
For each detected source, checks whether the corresponding seed
mechanism is present.

Usage:
    python stochastic_detector.py script.py [script2.py ...]

    As a library:
        from vaibify.testing import ftDetectStochastic
        bStochastic, listSources, listSeeds = ftDetectStochastic("script.py")
"""

import re
import sys


# =========================================================================
# Pattern definitions
# =========================================================================

# Each entry: (sCategory, sLabel, sConsumptionRegex, listSeedRegexes)
#
# sConsumptionRegex:  compiled regex that detects randomness usage
# listSeedRegexes:    compiled regexes that would seed this source;
#                     if ANY matches, we consider the source seeded

def _ftCompile(sCategory, sLabel, sConsumption, listSeeds):
    """Return a compiled pattern tuple."""
    return (
        sCategory,
        sLabel,
        re.compile(sConsumption),
        [re.compile(s) for s in listSeeds],
    )


_LIST_PATTERNS = [
    # -----------------------------------------------------------------
    # Category 1: Direct RNG calls
    # -----------------------------------------------------------------

    # numpy legacy global API
    _ftCompile(
        "Direct RNG",
        "numpy legacy random (global state)",
        r"np\.random\."
        r"(?:normal|randn|rand|uniform|choice|randint|shuffle|"
        r"permutation|binomial|poisson|exponential|gamma|lognormal|"
        r"beta|chisquare|dirichlet|geometric|gumbel|hypergeometric|"
        r"laplace|logistic|multinomial|multivariate_normal|"
        r"negative_binomial|noncentral_chisquare|noncentral_f|"
        r"pareto|power|rayleigh|standard_cauchy|standard_exponential|"
        r"standard_gamma|standard_normal|standard_t|triangular|"
        r"vonmises|wald|weibull|zipf)"
        r"\s*\(",
        [
            r"np\.random\.seed\s*\(",
            r"numpy\.random\.seed\s*\(",
        ],
    ),

    # numpy modern Generator API
    _ftCompile(
        "Direct RNG",
        "numpy Generator (default_rng)",
        r"(?:np|numpy)\.random\.(?:default_rng|Generator|SeedSequence)\s*\(",
        [
            # The construction call itself IS the seed mechanism when
            # an integer argument is provided; we check for that below
            # in the seed-presence logic. For pattern matching, having
            # default_rng(N) counts as seeded.
            r"default_rng\s*\(\s*\d+",
        ],
    ),

    # numpy legacy RandomState objects
    _ftCompile(
        "Direct RNG",
        "numpy RandomState",
        r"(?:np|numpy)\.random\.RandomState\s*\(",
        [
            r"RandomState\s*\(\s*\d+",
        ],
    ),

    # Python stdlib random
    _ftCompile(
        "Direct RNG",
        "Python random module",
        r"(?<![.\w])random\.(?:random|uniform|gauss|normalvariate|"
        r"choice|choices|shuffle|sample|randint|randrange|"
        r"triangular|betavariate|expovariate|gammavariate|"
        r"lognormvariate|vonmisesvariate|paretovariate|"
        r"weibullvariate)\s*\(",
        [
            r"(?<![.\w])random\.seed\s*\(",
        ],
    ),

    # scipy.stats distribution sampling
    _ftCompile(
        "Direct RNG",
        "scipy.stats .rvs() sampling",
        r"\.rvs\s*\(",
        [
            r"random_state\s*=\s*\d+",
            r"np\.random\.seed\s*\(",
        ],
    ),

    # scipy.stats.qmc quasi-random sampling
    _ftCompile(
        "Direct RNG",
        "scipy.stats.qmc (Latin Hypercube / Sobol / Halton)",
        r"(?:LatinHypercube|Sobol|Halton|PoissonDisk|MultinomialQMC|"
        r"MultivariateNormalQMC)\s*\(",
        [
            r"(?:rng|seed)\s*=\s*(?:\d+|np\.random)",
        ],
    ),

    # -----------------------------------------------------------------
    # Category 2: MCMC and nested samplers
    # -----------------------------------------------------------------

    # emcee
    _ftCompile(
        "MCMC / Nested Sampler",
        "emcee MCMC",
        r"(?:import\s+emcee|emcee\.EnsembleSampler|\.run_emcee)\s*[(\n]?",
        [
            r"np\.random\.seed\s*\(",
            r"rstate0?\s*=",
        ],
    ),

    # dynesty
    _ftCompile(
        "MCMC / Nested Sampler",
        "dynesty nested sampling",
        r"(?:import\s+dynesty|dynesty\.(?:Nested|Dynamic)"
        r"|\.run_dynesty)\s*[(\n]?",
        [
            r"rstate\s*=\s*(?:np\.random|numpy\.random)",
            r"rstate\s*=\s*\w*rng",
            r"np\.random\.seed\s*\(",
        ],
    ),

    # pymultinest
    _ftCompile(
        "MCMC / Nested Sampler",
        "PyMultiNest nested sampling",
        r"(?:import\s+pymultinest|pymultinest\.run"
        r"|\.run_pymultinest)\s*[(\n]?",
        [
            r"['\"]seed['\"]\s*:\s*\d+",
            r"seed\s*=\s*\d+",
        ],
    ),

    # ultranest
    _ftCompile(
        "MCMC / Nested Sampler",
        "UltraNest nested sampling",
        r"(?:import\s+ultranest|ultranest\.ReactiveNested"
        r"|\.run_ultranest)\s*[(\n]?",
        [
            r"np\.random\.seed\s*\(",
        ],
    ),

    # pymc / pymc3
    _ftCompile(
        "MCMC / Nested Sampler",
        "PyMC Bayesian modeling",
        r"(?:import\s+pymc|pm\.sample\s*\()",
        [
            r"random_seed\s*=\s*\d+",
        ],
    ),

    # numpyro
    _ftCompile(
        "MCMC / Nested Sampler",
        "NumPyro (JAX-based MCMC)",
        r"(?:import\s+numpyro|numpyro\.infer)",
        [
            r"jax\.random\.PRNGKey\s*\(\s*\d+",
            r"rng_key\s*=",
        ],
    ),

    # pystan / cmdstanpy
    _ftCompile(
        "MCMC / Nested Sampler",
        "Stan (PyStan / CmdStanPy)",
        r"(?:import\s+pystan|import\s+cmdstanpy"
        r"|\.sample\s*\(.*chains)",
        [
            r"seed\s*=\s*\d+",
        ],
    ),

    # polychord
    _ftCompile(
        "MCMC / Nested Sampler",
        "PolyChord nested sampling",
        r"(?:import\s+pypolychord|PolyChordSettings)",
        [
            r"seed\s*=\s*\d+",
        ],
    ),

    # nestle
    _ftCompile(
        "MCMC / Nested Sampler",
        "nestle nested sampling",
        r"(?:import\s+nestle|nestle\.sample\s*\()",
        [
            r"rstate\s*=",
            r"np\.random\.seed\s*\(",
        ],
    ),

    # nautilus
    _ftCompile(
        "MCMC / Nested Sampler",
        "nautilus nested sampling",
        r"(?:import\s+nautilus|nautilus\.Sampler\s*\()",
        [
            r"seed\s*=\s*\d+",
            r"np\.random\.seed\s*\(",
        ],
    ),

    # -----------------------------------------------------------------
    # Category 3: Stochastic optimizers
    # -----------------------------------------------------------------

    _ftCompile(
        "Stochastic Optimizer",
        "scipy differential_evolution",
        r"differential_evolution\s*\(",
        [
            r"seed\s*=\s*\d+",
            r"rng\s*=",
        ],
    ),

    _ftCompile(
        "Stochastic Optimizer",
        "scipy dual_annealing",
        r"dual_annealing\s*\(",
        [
            r"seed\s*=\s*\d+",
            r"rng\s*=",
        ],
    ),

    _ftCompile(
        "Stochastic Optimizer",
        "scipy basinhopping",
        r"basinhopping\s*\(",
        [
            r"seed\s*=\s*\d+",
            r"rng\s*=",
        ],
    ),

    _ftCompile(
        "Stochastic Optimizer",
        "scikit-optimize (gp_minimize, etc.)",
        r"(?:gp_minimize|forest_minimize|gbrt_minimize)\s*\(",
        [
            r"random_state\s*=\s*\d+",
        ],
    ),

    # -----------------------------------------------------------------
    # Category 4: ML libraries with random_state
    # -----------------------------------------------------------------

    # scikit-learn estimators (broad detection)
    _ftCompile(
        "ML Library",
        "scikit-learn estimator",
        r"(?:from\s+sklearn|import\s+sklearn)"
        r".*(?:Classifier|Regressor|Cluster|KMeans|PCA|"
        r"train_test_split|cross_val|KFold|ShuffleSplit"
        r"|RandomizedSearchCV)\s*\(?",
        [
            r"random_state\s*=\s*\d+",
        ],
    ),

    # pandas sampling
    _ftCompile(
        "ML Library",
        "pandas DataFrame.sample()",
        r"\.sample\s*\([^)]*(?:n\s*=|frac\s*=)",
        [
            r"random_state\s*=\s*\d+",
        ],
    ),

    # fbpca (randomized PCA/SVD)
    _ftCompile(
        "ML Library",
        "fbpca randomized SVD/PCA",
        r"(?:import\s+fbpca|fbpca\.pca\s*\()",
        [
            r"np\.random\.seed\s*\(",
        ],
    ),

    # alabi surrogate model (uses LHS sampling internally)
    _ftCompile(
        "ML Library",
        "alabi SurrogateModel (GP surrogate with LHS)",
        r"(?:import\s+alabi|SurrogateModel\s*\(|\.init_samples\s*\()",
        [
            r"np\.random\.seed\s*\(",
            r"random_state\s*=\s*\d+",
        ],
    ),

    # -----------------------------------------------------------------
    # Category 5: Deep-learning frameworks
    # -----------------------------------------------------------------

    # PyTorch
    _ftCompile(
        "Deep Learning",
        "PyTorch random ops",
        r"(?:torch\.randn|torch\.rand|torch\.randint"
        r"|torch\.randperm|torch\.bernoulli"
        r"|torch\.multinomial)\s*\(",
        [
            r"torch\.manual_seed\s*\(",
            r"torch\.cuda\.manual_seed(?:_all)?\s*\(",
        ],
    ),

    # TensorFlow
    _ftCompile(
        "Deep Learning",
        "TensorFlow random ops",
        r"(?:tf\.random\.(?:normal|uniform|shuffle|categorical"
        r"|truncated_normal|stateless))\s*\(",
        [
            r"tf\.random\.set_seed\s*\(",
        ],
    ),

    # JAX
    _ftCompile(
        "Deep Learning",
        "JAX random ops",
        r"jax\.random\.(?:normal|uniform|choice|bernoulli"
        r"|categorical|split|fold_in)\s*\(",
        [
            r"jax\.random\.PRNGKey\s*\(\s*\d+",
        ],
    ),

    # -----------------------------------------------------------------
    # Category 6: Non-obvious sources
    # -----------------------------------------------------------------

    # multiprocessing with forked RNG state
    _ftCompile(
        "Non-obvious",
        "multiprocessing Pool (forked RNG state risk)",
        r"(?:multiprocessing\.Pool|Pool\s*\(|pool\.map\s*\("
        r"|pool\.apply|ProcessPoolExecutor)\s*\(?",
        [
            # There is no single seed call that fixes this; each
            # worker must re-seed. Flag as unseeded always so the
            # user is aware.
        ],
    ),
]


# =========================================================================
# Core detection functions
# =========================================================================


def ftDetectStochastic(sScriptPath):
    """Detect stochastic sampling and seed presence in a Python script.

    Returns (bIsStochastic, listSources, listSeeds) where:
      bIsStochastic: True if any randomness source was detected
      listSources:   list of (sCategory, sLabel, bIsSeeded) tuples
      listSeeds:     list of seed-mechanism strings found
    """
    with open(sScriptPath, "r", encoding="utf-8") as fileHandle:
        sSource = fileHandle.read()

    # Strip comments so we don't match patterns inside comment text
    sStripped = _fsStripComments(sSource)

    listSources = []
    listSeeds = []

    for sCategory, sLabel, reConsumption, listSeedRegexes in _LIST_PATTERNS:
        if not reConsumption.search(sStripped):
            continue
        bSeeded = False
        for reSeed in listSeedRegexes:
            matchSeed = reSeed.search(sStripped)
            if matchSeed:
                bSeeded = True
                listSeeds.append(matchSeed.group(0))
        listSources.append((sCategory, sLabel, bSeeded))

    bIsStochastic = len(listSources) > 0
    return bIsStochastic, listSources, listSeeds


def _fsStripComments(sSource):
    """Remove single-line comments but preserve string literals."""
    listLines = []
    for sLine in sSource.splitlines():
        # Naive but sufficient: strip from first # that isn't inside quotes
        iHash = _fiFindUnquotedHash(sLine)
        if iHash >= 0:
            listLines.append(sLine[:iHash])
        else:
            listLines.append(sLine)
    return "\n".join(listLines)


def _fiFindUnquotedHash(sLine):
    """Return index of first # not inside a string, or -1."""
    bInSingle = False
    bInDouble = False
    for i, sChar in enumerate(sLine):
        if sChar == "'" and not bInDouble:
            bInSingle = not bInSingle
        elif sChar == '"' and not bInSingle:
            bInDouble = not bInDouble
        elif sChar == "#" and not bInSingle and not bInDouble:
            return i
    return -1


# =========================================================================
# Reporting
# =========================================================================


def fnPrintReport(sScriptPath, bIsStochastic, listSources, listSeeds):
    """Print a human-readable stochastic detection report."""
    print(f"\n{'=' * 70}")
    print(f"Stochastic Detection Report: {sScriptPath}")
    print(f"{'=' * 70}")

    if not bIsStochastic:
        print("  No stochastic sampling detected.")
        return

    iSeeded = sum(1 for _, _, b in listSources if b)
    iUnseeded = len(listSources) - iSeeded
    print(f"  Found {len(listSources)} randomness source(s): "
          f"{iSeeded} seeded, {iUnseeded} unseeded\n")

    for sCategory, sLabel, bSeeded in listSources:
        sStatus = "SEEDED" if bSeeded else "UNSEEDED"
        sMarker = "  " if bSeeded else "**"
        print(f"  {sMarker} [{sCategory}] {sLabel}: {sStatus}")

    if iUnseeded > 0:
        print(f"\n  WARNING: {iUnseeded} source(s) lack a fixed seed.")
        print("  Outputs will vary between runs, breaking quantitative tests.")
        print("  Add a seed before stochastic calls (e.g. np.random.seed(42)).")

    if listSeeds:
        print(f"\n  Seeds found: {', '.join(listSeeds)}")


# =========================================================================
# CLI entry point
# =========================================================================


def main():
    """Scan scripts given as CLI arguments."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} script.py [script2.py ...]")
        sys.exit(1)

    for sPath in sys.argv[1:]:
        bIsStochastic, listSources, listSeeds = ftDetectStochastic(sPath)
        fnPrintReport(sPath, bIsStochastic, listSources, listSeeds)

    # Exit with code 1 if any script has unseeded stochastic sources
    bAnyUnseeded = False
    for sPath in sys.argv[1:]:
        _, listSources, _ = ftDetectStochastic(sPath)
        if any(not bSeeded for _, _, bSeeded in listSources):
            bAnyUnseeded = True
    sys.exit(1 if bAnyUnseeded else 0)


if __name__ == "__main__":
    main()
