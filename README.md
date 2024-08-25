# MediathekView Flatpak

## Update maven dependencies

`maven-dependencies.json` lists all Maven dependencies explicitly, to enable the
flatpak builder to download these ahead of time and reconstruct the complete
local Maven repository required for Mediathekview, for the subsequent offline
build.

Install OpenJDK >= 22, `bsdtar`, `curl`, and [deno](https://deno.com/). Then
update the Mediathekview source in the manifest, and run
`./update-dependencies.ts` with deno.

The script downloads Mediathekview, verifies its checksum, runs a complete build
of Mediathekview against a fresh local repository, extracts all downloaded
artifacts from the build logs, and writes a complete list to
`maven-dependencies.json`.
