"""``python -m kionga``: the same command as ``kionga``."""

import sys

from .cli import main

sys.exit(main())
