# Whatchu Lookin At, Willis

A camera in a box that says what it can see.

Press the knob. The 2.4 inch panel shows you the frame it just took, then a
short phrase describing it. That is the whole product.

    press ──▶ chirp ──▶ [the frame] ──▶ looking... ──▶ "a tabby cat asleep on
                                                        a stack of post"

Built on a Raspberry Pi Zero 2 W with a camera module, an ILI9341 SPI panel, a
KY-040 rotary encoder, a piezo buzzer and an indicator LED. The description
comes from Claude.

## Hardware

| Part | Pins | Used for |
|---|---|---|
| Camera module | CSI ribbon | the picture |
| ILI9341 2.4" panel | SPI0, DC 25, RST 27, BL 18 | the only output |
| KY-040 encoder | CLK 19, DT 26, **SW 6** | SW is the shutter |
| PS1240 piezo | GPIO 13 (hardware PWM) | start-up tune, shutter chirp |
| Indicator LED | GPIO 4 via 220Ω | power light |
| Power button | GPIO 3 | `dtoverlay=gpio-shutdown` |

Only the encoder's **press** is used. Turning it does nothing here — the knob
is a button that happens to rotate.

Wiring detail for the panel and the encoder is in the module docstrings of
`src/panel/ili9341.py` and `src/control/encoder.py`, which carry the measured
facts that made those choices (why SW is on GPIO 6 rather than any free pin;
why the backlight pin is never released).

## Install

    bash deploy/setup.sh          # check; install missing packages
    bash deploy/setup.sh --fix    # ...and add config.txt lines and groups

Four `config.txt` lines are load-bearing and are **shared machine state** —
this project checks them, it does not own them:

    dtparam=spi=on                                     the panel
    gpio=18=op,dl                                      backlight off at boot
    dtoverlay=gpio-shutdown                            the power button
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4  the buzzer

The API key goes in the environment or in `~/.config/willis/api_key` — never
in the working tree. (The AsciiArt project on this machine keeps its own at
`~/.config/asciicam/api_key`; a separate file is deliberate, so rotating one
cannot break the other.) Without one the box still takes photographs and shows them; it
says on the panel that it cannot describe them.

## Running

    bash run_willis.sh                 # the box
    bash run_willis.sh --shoot         # one photograph, then exit
    bash run_willis.sh --rotation 180  # if the camera is mounted upside down

`--shoot` exists for development. The box itself has only the knob.

To start it at boot:

    sudo cp deploy/willis.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now willis

## Sharing the hardware

This Pi also carries the **AsciiArt** project, whose `ascii-camera.service` is
enabled and holds the camera and `/dev/spidev0.0` from boot. The two cannot run
at once. `run_willis.sh` stops it for you; `sudo systemctl start ascii-camera`
gives the box back.

Three things are worth knowing before debugging a mysterious failure:

**Stop the service, never kill the process.** The unit sets `Restart=always`
with no start limit, so a killed process comes back in three seconds and grabs
at the camera for ever. A deliberate `systemctl stop` stays stopped.

**Nothing needs handing back.** The only two exclusive resources are the camera
and the encoder's lgpio line claim, and both are released by the kernel when
the process dies — including on `kill -9`. Whichever program starts next
re-initialises everything from scratch, because both have to survive a reboot
anyway.

**But the collision is silent on the panel.** The panel's data pins are driven
through RPi.GPIO, which writes the pad registers directly and takes no kernel
lock. Two writers produce garbage on the glass rather than an error. Do not
expect a "device busy" to protect you.

What survives a dead process is *levels*, not ownership: the backlight on GPIO
18, the LED on GPIO 4, and any PWM duty left on GPIO 13 — a killed process can
leave the buzzer sounding with nothing owning it. Willis zeroes the PWM channel
at start as well as at exit, which is the only thing that covers a crashed
predecessor. `python3 tools/hardware/panel_blank.py` puts a stranded panel out.

## Tests

    python3 tests/panel/caption_test.py     # layout arithmetic, no hardware
    python3 tests/eyes/describe_test.py     # the request and the answer, no network
    python3 tests/panel/panel_selftest.py   # lights the panel, then asks you

The first two are ordinary tests. The third is not, and says so: **nothing in
software can see this panel.** `grim` photographs the HDMI output and the SPI
panel is not in it, and the module does not wire SDO usefully — register
read-back returns all zeros. The selftest checks the RGB565 arithmetic against
hand-computed values, draws four screens, and then prints a description of what
should be on the glass and asks a person. A clean run is not a pass.

The same is true of the buzzer, the LED, and whether the captions are any
*good*. Those are answered by standing in front of the box.

## Where things are

    willis.py                 the program: press, capture, ask, show
    src/capture/still.py      one frame, on demand
    src/panel/ili9341.py      the panel driver, over spidev
    src/panel/caption.py      words onto 320x240
    src/eyes/describe.py      a photograph in, one short line out
    src/eyes/client.py        the client, and where its key comes from
    src/control/             encoder, buzzer, power LED

`ili9341.py`, `encoder.py`, `buzzer.py` and `power_led.py` were copied from the
AsciiArt project rather than shared with it. Each is self-contained — they
import nothing but the standard library, numpy and the Pi's own modules — and
copying keeps this repository free-standing at the cost of two places to fix a
driver bug. That trade was made deliberately.
