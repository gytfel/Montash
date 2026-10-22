"""Точка входа: без аргументов — GUI, с аргументами — консольный режим."""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from montash.cli import main
    else:
        from montash.gui import main
    main()
