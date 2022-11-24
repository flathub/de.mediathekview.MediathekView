#!/bin/sh
# -XX flags come from the upstream appimage launcher, see
# https://github.com/mediathekview/MediathekView/blob/master/scripte/appimage.sh
#
# -DexternalUpdateCheck disables the update check of mediathekview, because in
# our case updates are distributed through flathub.
exec java \
    -XX:+UseShenandoahGC -XX:ShenandoahGCHeuristics=compact -XX:MaxRAMPercentage=50.0 -XX:+UseStringDeduplication \
    --add-opens java.desktop/sun.awt.X11=ALL-UNNAMED \
    -DexternalUpdateCheck \
    -jar /app/share/de.mediathekview.MediathekView/MediathekView.jar "$@"
