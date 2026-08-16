"""Put `scripts/` on the import path — the SerpAPI helper is a script, not a package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
