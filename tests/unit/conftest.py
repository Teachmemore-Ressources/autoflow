# Autoflow — tests/unit/conftest — Apache 2.0
"""Add service paths to sys.path for unit test imports."""
import sys
from pathlib import Path

_ROOT    = Path(__file__).parent.parent.parent
_EE      = _ROOT / "services" / "event-engine"
_SHARED  = _ROOT / "services" / "shared"

for _p in (_SHARED, _EE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
