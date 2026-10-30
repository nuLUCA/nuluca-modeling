"""Plugin archive hashes this wheel release trusts, by platform.

The checksum served with a download only detects corruption, because it comes
from the same server. These come through the signed-off wheel release, so a
plugin update needs a wheel update. Empty until the first public plugin release.
"""

PINNED_ARCHIVES: dict[str, str] = {}
