"""Good News News: evidence-first constructive news pipeline and static site."""
import os
from pathlib import Path

# GNN_ROOT lets tests and the offline demo run against a scratch copy of the repo,
# so sample content never lands in content/ and gets deployed.
ROOT = Path(os.environ.get("GNN_ROOT") or Path(__file__).resolve().parent.parent)
__version__ = "0.1.0"
