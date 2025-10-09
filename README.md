# MediathekView Flatpak

## Build locally

```console
$ flatpak run org.flatpak.Builder \
    --force-clean --user --install --install-deps-from=flathub \
    --repo=.flatpak-repo .flatpak-builddir \
    de.mediathekview.MediathekView.yaml
```

## Update maven dependencies

Run `./update-dependencies.py --flatpak` to update `maven-dependencies.json`.
This spawns a flatpak sandbox to download and build the MediathekView release
referenced in the main manifest, and then extracts all dependency URLs from the
build log.

To run the script directly you need Git, OpenJDK, and a reasonably recent
Python 3.
