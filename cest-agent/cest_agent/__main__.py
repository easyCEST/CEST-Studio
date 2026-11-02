# -*- coding: utf-8 -*-
"""cest_agent.__main__ —— 让 `python -m cest_agent ...` 可用。"""
import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
