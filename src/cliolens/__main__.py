"""Allow running the tool as ``python -m cliolens``."""

import sys

from cliolens.cli import main

if __name__ == "__main__":
    sys.exit(main())
