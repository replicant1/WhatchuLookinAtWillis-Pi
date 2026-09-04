#!/bin/bash
#
# Hand the buzzer's PWM channel to the user the app runs as.
#
# Run as root from ascii-camera.service's ExecStartPre, with systemd's "+"
# prefix so it keeps full privileges even though the service itself drops to
# rod. Everything under /sys/class/pwm is root-owned, and the app is not root,
# so without this the greeting is a permission error rather than a sound.
#
# GPIO 13 is PWM1. That mapping is made by
#   dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
# in config.txt, and the pins MUST be named: the overlay's own defaults are
# GPIO 18 and 19, which on this board are the panel backlight and the encoder's
# CLK, and taking either of those would be a much louder failure than a quiet
# buzzer.
#
# Exits 0 whatever happens, deliberately. This is an ExecStartPre, so a
# non-zero exit would stop the app starting at all - and a missing buzzer has
# never been a reason to refuse to show a picture. A failure here should cost
# the greeting and nothing else.

set -u

CHIP=/sys/class/pwm/pwmchip0
CHANNEL=1
OWNER=rod

if [ ! -d "$CHIP" ]; then
    echo "pwm_export: no $CHIP - is dtoverlay=pwm-2chan set in config.txt?" >&2
    exit 0
fi

if [ ! -e "$CHIP/pwm$CHANNEL" ]; then
    echo "$CHANNEL" > "$CHIP/export" 2>/dev/null || true
fi

# The driver creates the directory asynchronously, so a fresh export is not
# there the instant the write returns.
for _ in $(seq 20); do
    [ -e "$CHIP/pwm$CHANNEL" ] && break
    sleep 0.05
done

if [ ! -e "$CHIP/pwm$CHANNEL" ]; then
    echo "pwm_export: channel $CHANNEL did not appear after export" >&2
    exit 0
fi

chown -R "$OWNER" "$CHIP/pwm$CHANNEL" 2>/dev/null || true
echo "pwm_export: $CHIP/pwm$CHANNEL ready for $OWNER"
exit 0
