#!/bin/bash
# Check - and optionally fix - everything this machine needs that git does not
# carry. Run it first on any machine that is behaving oddly.
#
#   bash deploy/setup.sh              # check everything; install missing packages
#   bash deploy/setup.sh --fix        # ...and add config.txt lines and groups
#   bash deploy/setup.sh --boot-only  # just config.txt and groups; no apt
#
# Exits non-zero when something is missing, so it is usable as a check and not
# only as a thing to read.
#
# Note what this does NOT do: it never edits a config.txt line that is already
# present with a different value, and it never touches AsciiArt's files or
# services. The four config.txt lines below are shared machine state that both
# projects depend on - Willis checks them, it does not own them.

set -u

FIX=0
BOOT_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --fix) FIX=1 ;;
        --boot-only) BOOT_ONLY=1 ;;
        *) echo "unknown argument: $arg"; exit 2 ;;
    esac
done

CONFIG=/boot/firmware/config.txt
PROBLEMS=0

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mMISSING\033[0m %s\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
note() { printf '        %s\n' "$1"; }

# --- config.txt ------------------------------------------------------------
# Lose any of these and the symptom looks unrelated to the cause: no
# /dev/spidev0.0, a panel lit from power-on, no way to switch the box on, and a
# silent buzzer.
#
# The pwm-2chan line names its pins deliberately. The overlay's own defaults
# are pin=18 and pin2=19, which on this board are the panel backlight and the
# encoder's CLK - a bare "dtoverlay=pwm-2chan" would take both, and the failure
# would look like the panel and the knob dying for no reason.
BOOT_LINES=(
    "dtparam=spi=on"
    "gpio=18=op,dl"
    "dtoverlay=gpio-shutdown"
    "dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4"
)

echo "config.txt ($CONFIG)"
if [ ! -f "$CONFIG" ]; then
    bad "$CONFIG does not exist - is this a Raspberry Pi?"
else
    for line in "${BOOT_LINES[@]}"; do
        if grep -qxF "$line" "$CONFIG"; then
            ok "$line"
        elif [ "$FIX" = 1 ]; then
            echo "$line" | sudo tee -a "$CONFIG" >/dev/null
            ok "$line  (added - REBOOT NEEDED)"
        else
            bad "$line"
            note "add it, or re-run with --fix"
        fi
    done
fi

# --- groups ----------------------------------------------------------------
# Without these the app needs root, which the service deliberately does not use.
echo
echo "group memberships for $USER"
for group in spi gpio video; do
    if id -nG "$USER" | tr ' ' '\n' | grep -qx "$group"; then
        ok "$group"
    elif [ "$FIX" = 1 ]; then
        sudo usermod -aG "$group" "$USER"
        ok "$group  (added - LOG OUT AND BACK IN)"
    else
        bad "$group"
        note "sudo usermod -aG $group $USER"
    fi
done

if [ "$BOOT_ONLY" = 1 ]; then
    echo
    [ "$PROBLEMS" = 0 ] && echo "Boot configuration is complete." \
                        || echo "$PROBLEMS problem(s) found."
    exit $((PROBLEMS > 0))
fi

# --- python modules --------------------------------------------------------
# All six are checked by import rather than by dpkg, because "installed" and
# "importable as this user" are different questions and only the second one
# matters. AsciiArt learned this the hard way: for months its setup checked
# four of nine modules, so a fresh machine passed setup and failed at run time.
echo
echo "python modules"
declare -a MISSING_APT=()
check_module() {           # check_module <import name> <apt package|pip:name>
    if python3 -c "import $1" 2>/dev/null; then
        ok "$1"
    else
        bad "$1"
        MISSING_APT+=("$2")
    fi
}
check_module numpy      python3-numpy
check_module PIL        python3-pil
check_module picamera2  python3-picamera2
check_module lgpio      python3-lgpio
check_module spidev     python3-spidev
check_module anthropic  pip:anthropic

if [ ${#MISSING_APT[@]} -gt 0 ]; then
    echo
    echo "Installing ${#MISSING_APT[@]} missing module(s)..."
    APT_PKGS=()
    for pkg in "${MISSING_APT[@]}"; do
        [ "${pkg#pip:}" = "$pkg" ] && APT_PKGS+=("$pkg")
    done
    if [ ${#APT_PKGS[@]} -gt 0 ]; then
        # APT_LISTCHANGES_FRONTEND=none is not optional on a Zero 2. With only
        # ~416 MB of usable RAM, apt-listchanges has been OOM-killed here
        # mid-install, leaving packages half-configured and every later install
        # blocked until dpkg was repaired by hand.
        sudo APT_LISTCHANGES_FRONTEND=none DEBIAN_FRONTEND=noninteractive \
             apt-get install -y -o Dpkg::Use-Pty=0 "${APT_PKGS[@]}"
    fi
    for pkg in "${MISSING_APT[@]}"; do
        case "$pkg" in
            pip:*) pip3 install --break-system-packages "${pkg#pip:}" ;;
        esac
    done
fi

# --- the API key -----------------------------------------------------------
# Checked, never created, and never printed. A box with no key still runs and
# still shows photographs; it just cannot describe them, and says so.
echo
echo "API key"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    ok "ANTHROPIC_API_KEY is set in the environment"
elif [ -s "$HOME/.config/willis/api_key" ]; then
    ok "$HOME/.config/willis/api_key exists"
else
    bad "no API key"
    note "export ANTHROPIC_API_KEY=..., or:"
    note "mkdir -p ~/.config/willis && chmod 700 ~/.config/willis"
    note "then write the key to ~/.config/willis/api_key, chmod 600"
    if [ -s "$HOME/.config/asciicam/api_key" ]; then
        note "the AsciiArt project has one you could copy:"
        note "cp ~/.config/asciicam/api_key ~/.config/willis/api_key"
    fi
fi

# --- the other tenant ------------------------------------------------------
# Not a problem, and deliberately not counted as one. It is a fact worth
# knowing before wondering why the camera is busy.
echo
echo "sharing this machine"
if systemctl is-enabled --quiet ascii-camera 2>/dev/null; then
    note "ascii-camera.service is ENABLED and will hold the camera and the"
    note "panel from boot. run_willis.sh stops it; 'systemctl start"
    note "ascii-camera' gives the box back."
else
    note "ascii-camera.service is not enabled here."
fi

echo
if [ "$PROBLEMS" = 0 ]; then
    echo "Everything Willis needs is present."
else
    echo "$PROBLEMS problem(s) found."
fi
exit $((PROBLEMS > 0))
