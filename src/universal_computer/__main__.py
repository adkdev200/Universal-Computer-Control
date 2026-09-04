"""``python -m universal_computer`` entrypoint."""

import sys

from universal_computer.server import main

if __name__ == "__main__":
    sys.exit(main())
