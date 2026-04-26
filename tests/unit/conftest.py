"""Add event-engine to sys.path for unit test imports."""
import sys
from pathlib import Path

_EE = Path(__file__).parent.parent.parent / "services" / "event-engine"
if str(_EE) not in sys.path:
    sys.path.insert(0, str(_EE))
