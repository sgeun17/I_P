"""Install outbound policy before importing the actual model worker."""
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root.parent / 'phase2_통합'))
from offline_network import install
install()
sys.argv = [str(root / 'chunk_retriever.py'), '--persistent-worker']
runpy.run_path(sys.argv[0], run_name='__main__')
