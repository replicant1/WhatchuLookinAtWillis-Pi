#!/bin/bash
# Launch Willis on the Pi, having first got the hardware to itself.
#
#   bash run_willis.sh                 # the box
#   bash run_willis.sh --shoot         # one photograph, then exit
#   bash run_willis.sh --rotation 180
#
# This script exists for one reason: the camera and /dev/spidev0.0 cannot be
# shared, and on this machine the AsciiArt project's ascii-camera.service is
# enabled and holds both from boot. Stopping it is not optional and forgetting
# to is the single most likely reason Willis fails to start.
#
# `systemctl stop` is the correct verb and killing the process is not. The unit
# sets Restart=always with no start limit, so a killed process comes back in
# three seconds and grabs at the camera for ever; a deliberate stop stays
# stopped.

set -u
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

RIVAL=ascii-camera
if systemctl is-active --quiet "$RIVAL"; then
    echo "Stopping $RIVAL, which is holding the camera and the panel..."
    sudo systemctl stop "$RIVAL"
    # The stop is synchronous, but libcamera's release is not quite: the
    # kernel frees the device when the process is reaped, and systemd returns
    # when it has signalled it. A second covers the gap.
    sleep 1
fi

echo "Willis starting. To give the box back afterwards:"
echo "    sudo systemctl start $RIVAL"
echo

cd "$PROJECT_DIR" || exit 1
exec python3 -u willis.py "$@"
