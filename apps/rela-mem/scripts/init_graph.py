"""Initialize graph indexes/constraints using the configured isolated or production graph."""
import sys
from manage import main

if __name__ == "__main__":
    sys.argv.insert(1, "init-graph")
    main()
