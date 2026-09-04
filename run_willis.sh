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

    # WAIT for it, do not sleep a fixed second. Measured on 4 Sep 2026:
    # ascii-camera did not respond to SIGTERM at all and systemd SIGKILLed it
    # fifteen seconds later, at its TimeoutStopSec. A one-second sleep would
    # have handed Willis the panel while the other program was still alive and
    # still able to write to it - and that collision produces garbage on the
    # glass rather than an error, because the panel's data pins go through
    # RPi.GPIO, which takes no kernel line. So the failure mode of getting this
    # wrong is a picture that looks broken for no visible reason.
    #
    # `systemctl stop` is synchronous and returns once the unit has left the
    # active state, whether that ended in a clean exit or a SIGKILL - so by the
    # time this loop runs it has almost always finished already. The loop is
    # for the case where it has not: is-active reports "deactivating" while the
    # timeout runs down.
    for _ in $(seq 40); do
        state="$(systemctl is-active "$RIVAL" || true)"
        [ "$state" = "deactivating" ] || break
        sleep 0.5
    done

    # The kernel frees the camera when the process is reaped, which is a moment
    # after systemd stops accounting for it.
    sleep 1

    state="$(systemctl is-active "$RIVAL" || true)"
    if [ "$state" = "active" ] || [ "$state" = "deactivating" ]; then
        echo "$RIVAL is still $state after 20s - refusing to start Willis" >&2
        echo "and fight it for the camera. Investigate before retrying." >&2
        exit 1
    fi
    # "failed" is a normal outcome of stopping this unit: it means SIGTERM was
    # ignored and systemd killed it. The hardware is free either way, and
    # starting it again clears the state.
    echo "$RIVAL is now $state; the camera and the panel are free."
fi

echo "Willis starting. To give the box back afterwards:"
echo "    sudo systemctl start $RIVAL"
echo

cd "$PROJECT_DIR" || exit 1
exec python3 -u willis.py "$@"
