#!/usr/bin/env python3
"""Generate all three complete local examples without dependencies."""
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
for guests in (4,6,10):
    subprocess.run([sys.executable,str(root/'party-bar-planner/scripts/planner.py'),str(root/f'examples/party-{guests}.json'),'--out',str(root/f'examples/output-{guests}')],check=True)
