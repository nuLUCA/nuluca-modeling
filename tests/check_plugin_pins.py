"""Stop a wheel release that carries no plugin hashes for a platform."""

import re
import sys

from nuluca_modeling_connect.plugin_pins import PINNED_ARCHIVES

missing = [name for name in ("mac", "win") if not re.fullmatch(r"[0-9a-f]{64}", PINNED_ARCHIVES.get(name, ""))]
if missing:
    sys.exit(f"plugin_pins.py has no SHA-256 for: {', '.join(missing)}")
