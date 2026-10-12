#!/usr/bin/env python
"""manage.py of the demo. Run from anywhere: it puts the repository root (/app in the image) on the path."""

import os
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
