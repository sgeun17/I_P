from pathlib import Path
import sys

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = SRC.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
