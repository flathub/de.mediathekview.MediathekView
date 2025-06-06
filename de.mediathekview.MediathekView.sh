#!/bin/sh
# -XX flags come from the upstream appimage launcher, see
# https://github.com/mediathekview/MediathekView/blob/master/scripte/appimage.sh
#
# -DexternalUpdateCheck disables the update check of mediathekview, because in
# our case updates are distributed through flathub.

basedir=/app/share/de.mediathekview.MediathekView

exec java \
    -XX:+UseShenandoahGC -XX:ShenandoahGCHeuristics=compact \
    -XX:MaxRAMPercentage=50.0 -XX:+UseStringDeduplication \
    --enable-native-access=ALL-UNNAMED --add-modules jdk.incubator.vector \
    --add-exports=java.desktop/sun.swing=ALL-UNNAMED \
    --add-opens java.desktop/sun.awt.X11=ALL-UNNAMED \
    -cp "${basedir}/MediathekView.jar:${basedir}/dependency/*" \
    -DexternalUpdateCheck \
    mediathek.Main \
    "${XDG_CONFIG_HOME}/mediathek3"
