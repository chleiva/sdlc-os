import sys
from pathlib import Path

# So `import _common...` works the same way it does inside each server.py,
# regardless of the directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
