#!/usr/bin/env python3
"""
The power LED on GPIO 4: lit while the box is up, dark when it is not.

    python3 src/control/power_led.py          # light it
    python3 src/control/power_led.py --off    # put it out

Functions rather than a class, for the same reason buzzer.py is one: there is
nothing worth remembering between switching it on and switching it off again.

The whole module rests on one measured fact - **the pin keeps its level after
the process that set it has gone**.  Checked on this Pi three ways, and all
three leave GPIO 4 reading "op | hi": claim then close, claim then exit without
closing, and claim then gpio_free then close.  Freeing the line does *not* hand
the pad back to being an input.  That is worth stating out loud because the
backlight on GPIO 18 behaves the opposite way under RPi.GPIO, where releasing
the pin lets the module's own pull-up relight the panel - AsciiArt's deploy/asciiart.shutdown
depends on that difference in the other direction, and the two are easy to
confuse into an afternoon.

That fact is what makes a power LED possible from a program that returns.
Nothing has to hold the pin for the hours between start-up and shutdown: `on()`
sets it and leaves, the app gets on with the camera, and the LED stays lit
through the app being restarted, crashed, or stopped for a test.  It goes out
when something puts it out - AsciiArt's deploy/asciiart.shutdown, after every service has
stopped - or when a reboot resets the pad, which returns GPIO 4 to an input
with the chip's own pull-up.  That pull-up passes microamps and shows nothing;
the LED is dark from power-on until the app lights it.

So the LED tracks the *machine*, not this process, which is what "power LED"
has to mean.  One that went out whenever the app restarted would be an app LED
wearing the wrong label.

Wiring, polarity and the resistor are in CLAUDE.md, along with the two device
tree overlays that would quietly take GPIO 4 away if either were ever enabled.
Nothing in software can see this LED - grim photographs the HDMI output and the
LED is not in it - so the last line of a manual run says what a human should
expect to see rather than claiming it happened.
"""

import logging
import sys

logger = logging.getLogger(__name__)

PIN = 4                  # free here, but not free by default; see CLAUDE.md
CHIP = 0


def set_level(level, pin=PIN, chip=CHIP, gpio=None):
    """
    Drive the LED pin high or low, then let go of it, leaving the level behind.

    `gpio` is the lgpio module, and exists so a test can pass one that records
    instead of one that lights an LED.  Imported late by default because lgpio
    only exists on the Pi, exactly as buzzer.py and encoder.py import it.

    The line is handed back rather than held.  Keeping the claim would buy
    nothing - the level survives being freed, which was measured rather than
    hoped - and a claim left behind is what makes the *next* caller fail to
    start.  gpio_free is deliberately inside the `try` and not the `finally`:
    freeing a pin that was never claimed raises, and a cleanup step that
    throws on the way out replaces the real error with a confusing one.
    Closing the chip is the step that must happen either way, and it releases
    any claim by itself.
    """
    if gpio is None:
        import lgpio as gpio            # imported late: only exists on the Pi

    handle = gpio.gpiochip_open(chip)
    try:
        gpio.gpio_claim_output(handle, pin, 1 if level else 0)
        gpio.gpio_free(handle, pin)
    finally:
        gpio.gpiochip_close(handle)


def on(pin=PIN, chip=CHIP, gpio=None):
    """Light it: the box has power and something is running."""
    set_level(1, pin=pin, chip=chip, gpio=gpio)


def off(pin=PIN, chip=CHIP, gpio=None):
    """Put it out: the last thing to see before the machine stops."""
    set_level(0, pin=pin, chip=chip, gpio=gpio)


def main():
    logging.basicConfig(level=logging.INFO)
    dark = "--off" in sys.argv
    off() if dark else on()
    state = "off" if dark else "on"
    print(f"power LED on GPIO {PIN} driven {state} - "
          f"the LED should now be {state.upper()}; nothing here can see it")


if __name__ == "__main__":
    main()
