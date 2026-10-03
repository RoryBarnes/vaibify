"""Allow running vaibify as ``python -m vaibify``."""

from vaibify.cli.main import main

# Click builds the arguments of `main` from the command line.
main()  # pylint: disable=no-value-for-parameter
