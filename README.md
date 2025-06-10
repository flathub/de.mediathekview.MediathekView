# MediathekView Flatpak

## Update maven dependencies

`maven-dependencies.json` lists all Maven dependencies explicitly, to enable the
flatpak builder to download these ahead of time and reconstruct the complete
local Maven repository required for Mediathekview, for the subsequent offline
build.

To update the dependencies run `./update-dependencies.py` with Python 3.
The script needs OpenJDK and git to download and build the MediathekView release
from the main manifest and extract all dependency URLs from the build log.
