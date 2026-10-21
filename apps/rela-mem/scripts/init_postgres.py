"""Initialize the one profile table without running schema migrations."""
import sys
from manage import main

if __name__ == "__main__":
    sys.argv.insert(1, "init-postgres")
    main()
