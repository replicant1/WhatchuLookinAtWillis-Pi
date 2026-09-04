# WHATCHU LOOKIN AT, WILLIS

A camera in a box that says what it can see. Press the knob, the panel shows
the frame, then a short phrase describing it. Python 3 on a Raspberry Pi
Zero 2 W.

## Directory Summary

There are THREE sibling directories under /Users/rodneybailey/PiProjects/Willis,
and the git repository is the one that is easiest to overlook:

    remote/                        the Pi's /home/rod/Projects/Willis, over SSHFS
    local/                         Mac-only notes, and the unredacted original
                                   of this file. Not mirrored to the Pi.
    WhatchuLookinAtWillis-Pi/      THE GIT REPOSITORY.
                                   Remote: git@github.com:replicant1/WhatchuLookinAtWillis-Pi.git

Raspberry Pi directory: /home/rod/Projects/Willis
Mac mount point: /Users/rodneybailey/PiProjects/Willis/remote
Git repository (NOT inside the mount): /Users/rodneybailey/PiProjects/Willis/WhatchuLookinAtWillis-Pi

Neither remote/ nor local/ is a git repository. For any commit, branch or PR
request, work in WhatchuLookinAtWillis-Pi.

The repo is kept off the mount on purpose: git does constant read-after-write
against its own object store, and reads back through SSHFS can return stale
data when the Pi has written out of band. Handing git a filesystem that can lie
to it is not worth the convenience.

Code moves between the two with sync.sh:

    bash sync.sh            # or "pull": Pi mount -> repo, ready to commit
    bash sync.sh push       # repo -> Pi mount, e.g. after a git pull
    bash sync.sh status     # report differences, copy nothing

sync.sh copies ONLY files named in its explicit arrays. A new module that is
not added to the right array is silently never synced. This is guarded for
src/ and tools/ - the script refuses to run and names the unlisted file - but
ROOT_FILES, TEST_FILES and DEPLOY_FILES are still unguarded. Adding a file to
the project means editing sync.sh in the same change.

"sync.sh pull" regenerates the repo's CLAUDE.md from local/CLAUDE.md with the
Pi's IP address masked. Always edit local/CLAUDE.md, never the repo's copy, and
never push CLAUDE.md back to the Pi.

## This machine is shared with the AsciiArt project

**Read this before debugging anything that looks like broken hardware.**

The same Pi carries /home/rod/Projects/AsciiArt, whose ascii-camera.service is
ENABLED and holds the camera and /dev/spidev0.0 from boot. Willis and AsciiArt
cannot run at the same time.

    bash run_willis.sh                      # stops ascii-camera for you
    sudo systemctl start ascii-camera       # give the box back

Three facts, established by reading both codebases rather than by guessing:

- **Stop the service, never kill the process.** ascii-camera.service sets
  Restart=always with StartLimitIntervalSec=0, so a killed process returns in
  three seconds and grabs at the camera for ever. A deliberate `systemctl stop`
  stays stopped.
- **Nothing has to hand anything back.** The only exclusive resources are the
  camera and the encoder's lgpio line claim, and the kernel frees both when the
  process dies, including on kill -9. Whichever program starts next
  re-initialises from scratch, because both must survive a reboot anyway. The
  recapture procedure really is just `systemctl start ascii-camera`.
- **The collision is SILENT on the panel.** The panel's data pins are driven
  through RPi.GPIO, which writes the pad registers directly and takes no kernel
  line. Two writers produce garbage on the glass, not an error. Do not expect
  "device busy" to protect you.

What survives a dead process is levels, not ownership: the backlight on GPIO
18, the LED on GPIO 4, and any PWM duty left on GPIO 13 - a killed process can
leave the buzzer sounding with nothing owning it. Willis zeroes the PWM channel
at start as well as at exit, which is the only thing that covers a crashed
predecessor. `python3 tools/hardware/panel_blank.py` puts a stranded panel out.

## Hardware, and what can and cannot be verified

    Camera module      CSI ribbon                    the picture
    ILI9341 2.4"       SPI0, DC 25, RST 27, BL 18    the only output
    KY-040 encoder     CLK 19, DT 26, SW 6           SW is the shutter
    PS1240 piezo       GPIO 13, hardware PWM         start-up tune, chirp
    Indicator LED      GPIO 4 via 220 ohm            power light
    Power button       GPIO 3                        dtoverlay=gpio-shutdown

Only the encoder's PRESS is used. Turning it does nothing in this project.

**Verification is the hard part, and there is no software answer.** grim
photographs the Wayland/HDMI output; the SPI panel, the LED and the buzzer are
none of them in it, and the panel module does not wire SDO usefully - register
read-back returns all 00. So NOTHING can confirm what is lit, what sounded, or
what the camera's colours look like except asking Rod to look and listen.

Write tests so the automated part can still genuinely fail - hand-computed
RGB565 values, wrapped-line widths in pixels, the base64 image decoded back and
measured - and then print a distinctive, specific description of what should be
on screen and ask for that exact thing. Never report a panel change, a tune or
an LED as verified on the strength of a clean run.

One that bites specifically here: picamera2's "RGB888" hands back channels in
B, G, R order. src/capture/still.py reverses them. A swapped image is still a
valid image and the model will happily describe a blue-tinted scene without
complaint, so this can only be caught by pointing the camera at something known
to be red.

## Machine state that git does NOT have

Everything in src/, tests/, tools/ and deploy/ comes back from a clone. These
do not, and are SHARED with the AsciiArt project - Willis checks them, it does
not own them. `bash deploy/setup.sh` checks all of it and exits non-zero when
something is missing.

/boot/firmware/config.txt, four load-bearing lines:

    dtparam=spi=on                                     the panel
    gpio=18=op,dl                                      backlight held off at boot
    dtoverlay=gpio-shutdown                            the power button (GPIO 3)
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4  the buzzer on GPIO 13

Name the PWM pins. Always. The overlay's own defaults are pin=18 and pin2=19,
which on this board are the panel backlight and the encoder's CLK.

rod's group memberships: spi, gpio, video (also i2c and input, for AsciiArt).

Python modules: numpy, PIL, picamera2, lgpio, spidev, anthropic.

The API key: ~/.config/willis/api_key or ANTHROPIC_API_KEY, never in the
working tree. AsciiArt keeps its own at ~/.config/asciicam/api_key - a
separate file on purpose, so rotating one cannot break the other.

Willis deliberately installs NO shutdown hook. AsciiArt's
/usr/lib/systemd/system-shutdown/asciiart.shutdown already drives GPIO 18 low,
GPIO 4 low and plays a farewell on GPIO 13, it imports nothing from its own
project, and it will do the right thing for Willis too. A second hook on the
same pins would run alongside it in unspecified order.

## Talking to the Pi

Passwordless SSH is required - the agent cannot type a password. If it ever
stops working, ask the user to run this; never ask for the password in the
conversation:

    ssh-copy-id -i ~/.ssh/id_ed25519.pub rod@192.168.x.x

    ~/run_on_pi.sh "<command>"      run a command on the Pi over SSH
    ~/mountwillis.sh                mount /home/rod/Projects/Willis
    ~/unmountwillis.sh              unmount it

Gotcha inherited from the sibling project: do NOT run "pkill -f <pattern>" over
SSH if <pattern> appears anywhere in the command string being sent. The remote
bash's own command line contains the pattern, pkill matches it, and the SSH
session kills itself - exit code 255 with no output and the cleanup silently
never happens. Kill by PID instead.

The SSHFS cache lags in the Pi -> Mac direction. Writing to remote/ is seen by
the Pi instantly; a file the Pi just wrote out of band can read back stale
once. If a file looks wrong, empty or suspiciously unchanged, read it again
before concluding anything.

Installing packages: the Zero 2 has ~416 MB usable and apt has been OOM-killed
here mid-install. Always disable apt-listchanges:

    sudo APT_LISTCHANGES_FRONTEND=none DEBIAN_FRONTEND=noninteractive \
         apt-get install -y -o Dpkg::Use-Pty=0 <packages>

## Script Summary

Run Willis on the Pi:                  bash /home/rod/Projects/Willis/run_willis.sh
One photograph, then exit:             bash /home/rod/Projects/Willis/run_willis.sh --shoot
Check the machine has what it needs:   bash /home/rod/Projects/Willis/deploy/setup.sh
Light the panel and ask a human:       python3 /home/rod/Projects/Willis/tests/panel/panel_selftest.py
Check layout, no hardware:             python3 /home/rod/Projects/Willis/tests/panel/caption_test.py
Check the request, no network:         python3 /home/rod/Projects/Willis/tests/eyes/describe_test.py
Put out a stranded panel:              python3 /home/rod/Projects/Willis/tools/hardware/panel_blank.py
Give the box back to AsciiArt:         sudo systemctl start ascii-camera
