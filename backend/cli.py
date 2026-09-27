#!/usr/bin/env python3
"""Evo Code command-line entry point.

The preferred launcher is ``./evo`` from the project root. Direct invocation is
also supported: ``backend/.venv/bin/python backend/cli.py demo``.
"""

from evo_cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
