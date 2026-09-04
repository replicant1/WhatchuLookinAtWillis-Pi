#!/usr/bin/env python3
"""
Whatchu Lookin At, Willis - a camera in a box that says what it can see.

Press the knob.  The panel shows you the frame it took, then what the model
made of it.  That is the whole product.

    python3 willis.py                  # the box, as it lives in the enclosure
    python3 willis.py --shoot          # one photograph, then exit (development)
    python3 willis.py --rotation 180   # if the camera is mounted upside down

The shape of the program is a consequence of one fact: nearly all of the
elapsed time in a capture is spent waiting for the network, and during that
wait the person is standing in front of the box wondering whether their press
registered.  So the panel is updated at every step - shutter, frame, looking,
answer - rather than only at the end.  There is no render loop and no threading
beyond what the encoder needs; a state that lasts seconds does not need
sixty frames a second to express it.

Hardware, all of which is optional and none of which is fatal when missing:

    camera        the picture
    ILI9341       the only output; without it the box is mute
    GPIO 4 LED    lit at start-up, put out by the shutdown hook
    GPIO 13       two notes at start-up, one chirp per shutter
    GPIO 6        the encoder's press, which is the shutter button
    GPIO 3        the power button, which is config.txt's business, not ours

The camera and /dev/spidev0.0 cannot be shared.  Nothing here arbitrates that
and nothing should: see README.md, "Sharing the hardware".
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from capture.still import Still                          # noqa: E402
from control import buzzer, power_led                    # noqa: E402
from control.encoder import RotaryEncoder                # noqa: E402
from eyes import client as eyes_client                   # noqa: E402
from eyes.describe import DescribeError, describe        # noqa: E402
from panel import caption as caption_panel               # noqa: E402
from panel.ili9341 import ILI9341                        # noqa: E402
from version import NAME, VERSION                        # noqa: E402

logger = logging.getLogger("willis")

# One short high note.  Deliberately not the two-note greeting: the shutter
# happens while somebody is watching and wants an acknowledgement, not a tune.
SHUTTER = ((1760, 0.06),)

# How often the button is checked.  Fifty milliseconds is far below the
# threshold at which a press feels ignored and costs a lock acquisition; the
# encoder's own callbacks run in lgpio's thread, so nothing is missed between
# polls however slow this loop gets.
POLL_SECONDS = 0.05

# How long the captured frame is shown before "looking..." replaces it.  Long
# enough to see what was framed, short enough not to feel like a delay.
FRAME_SECONDS = 1.2


class Willis:
    """The box: hardware in, one caption out, repeat."""

    def __init__(self, options):
        self.options = options
        self.is_running = True
        self.camera = None
        self.panel = None
        self.encoder = None
        self.client = None
        self.captures = 0

    # --- the panel -------------------------------------------------------

    def _show(self, image):
        """Draw an image, if there is a panel to draw it on."""
        if self.panel is not None:
            self.panel.show(image)

    def say(self, text):
        """A status line: what the box is doing, not what it saw."""
        logger.info("Panel: %s", text)
        self._show(caption_panel.render_status(text))

    def answer(self, text):
        """A caption: what it saw."""
        logger.info("Panel: %r", text)
        self._show(caption_panel.render(text))

    def complain(self, text):
        """A failure, worded for somebody standing in front of the box."""
        logger.warning("Panel: %s", text)
        self._show(caption_panel.render_failure(text))

    # --- start-up --------------------------------------------------------

    def _light_the_power_led(self):
        """
        Light GPIO 4, then let go of the pin.

        The level survives the process exiting, which is what makes this a
        power light rather than a "willis is running" light.  It is put out by
        the shutdown hook, not by this program - so stopping Willis to run
        something else leaves the box looking, correctly, powered on.
        """
        if not self.options.led:
            return
        try:
            power_led.on()
        except Exception as e:                   # noqa: BLE001 - never fatal
            logger.warning("Could not light the power LED: %s", e)

    def _silence_the_buzzer(self):
        """
        Zero the PWM channel before anything else touches it.

        Exit-time cleanup cannot run if the previous process was killed, and
        nothing owns a PWM channel: a duty cycle left non-zero keeps the buzzer
        sounding indefinitely with no process attached to it. `play` already
        silences the channel in a `finally`, which covers every ordinary
        ending; this covers the one it cannot, and it covers a predecessor that
        was not even this program.

        Never fatal, and never noisy about it: on a board with no buzzer, or
        with the overlay missing, there is nothing to silence and nothing to
        report.
        """
        if not self.options.buzzer:
            return
        try:
            if buzzer.ensure_exported():
                buzzer.sysfs_write(buzzer.PWMCHIP, buzzer.CHANNEL,
                                   "duty_cycle", 0)
                buzzer.sysfs_write(buzzer.PWMCHIP, buzzer.CHANNEL, "enable", 0)
                logger.debug("PWM channel left silent before start-up")
        except Exception as e:                       # noqa: BLE001 - best effort
            logger.debug("Could not pre-silence the buzzer: %s", e)

    def _say_hello(self):
        """Two notes, in a child process, before anything slow starts."""
        if not self.options.buzzer:
            return
        try:
            buzzer.in_process(name="Start-up tune")
        except Exception as e:                   # noqa: BLE001 - never fatal
            logger.warning("Could not play the start-up tune: %s", e)

    def _chirp(self):
        """
        One note at the shutter, on a thread rather than in a child.

        A thread was wrong for the greeting, because start-up holds the GIL
        while libcamera and the panel come up and a late wake-up put a silence
        between two notes meant to be one gesture.  None of that applies here:
        this is one note, fired from an otherwise idle loop, so there is no
        second note for a delay to open a gap in front of.
        """
        if not self.options.buzzer:
            return
        try:
            buzzer.in_background(notes=SHUTTER)
        except Exception as e:                   # noqa: BLE001 - never fatal
            logger.debug("No shutter chirp: %s", e)

    def _start_panel(self):
        self.panel = ILI9341(landscape=True).init()
        self.panel.backlight(self.options.brightness)
        self.say(f"{NAME}\nv{VERSION}")

    def _start_camera(self):
        self.camera = Still(rotation=self.options.rotation).start()

    def _start_encoder(self):
        if not self.options.encoder:
            return
        self.encoder = RotaryEncoder().start()

    def _start_client(self):
        """
        Build the client now, so a missing key is a start-up problem.

        A key that is missing is not a reason to refuse to run - the box still
        takes photographs and still shows them - but it IS a reason to say so
        on the panel at start-up rather than at the first press.
        """
        eyes_client.warm()
        try:
            self.client = eyes_client.client()
        except eyes_client.NoKey as e:
            logger.error("No API key: %s", e)
            self.client = None

    # --- one capture -----------------------------------------------------

    def capture(self):
        """
        Take a photograph and describe it.  Never raises.

        Every failure ends with something on the panel, because a box whose
        only output goes blank is indistinguishable from a box that is broken.
        """
        self.captures += 1
        self._chirp()

        try:
            frame = self.camera.grab()
        except Exception as e:                   # noqa: BLE001
            logger.exception("Capture failed")
            self.complain(f"camera trouble\n{type(e).__name__}")
            return

        self._show(caption_panel.render_frame(frame))
        time.sleep(FRAME_SECONDS)

        if self.client is None:
            self.complain("no API key\nI can see but I cannot say")
            return

        self.say("looking...")
        try:
            text, elapsed = describe(frame, self.client)
        except DescribeError as e:
            logger.error("Describe failed: %s", e)
            self.complain(f"could not ask\n{_short(str(e))}")
            return

        logger.info("Capture %d took %.1f s", self.captures, elapsed)
        self.answer(text)

    # --- the loop --------------------------------------------------------

    def _install_signal_handlers(self):
        """
        Stop cleanly when signalled, not only when interrupted at a keyboard.

        In the enclosure there is no keyboard, so a signal is the only way this
        ever ends - and Python's default SIGTERM handling exits without
        unwinding, so the `finally` that releases the camera and blanks the
        panel would never run.  `systemctl stop` sends SIGTERM, which makes
        this the difference between a dark panel and one left showing the last
        caption for ever.
        """
        def stop(signum, _frame):
            logger.info("Signal %d: stopping", signum)
            self.is_running = False

        for received in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(received, stop)
            except ValueError:
                logger.info("Could not handle signal %d here", received)

    def run(self):
        """Start everything, wait for presses, and put it all back."""
        self._install_signal_handlers()
        self._light_the_power_led()
        self._silence_the_buzzer()
        self._say_hello()

        try:
            self._start_panel()
            self._start_client()
            self._start_camera()
            self._start_encoder()

            if self.options.shoot:
                self.capture()
                time.sleep(self.options.shoot_seconds)
                return 0

            if self.encoder is None:
                self.complain("no button\nnothing can start a photograph")
            else:
                self.say("press the knob")

            while self.is_running:
                if self.encoder is not None and self.encoder.take_presses():
                    self.capture()
                    self.say("press the knob")
                time.sleep(POLL_SECONDS)
            return 0
        finally:
            self._release()

    def _release(self):
        """
        Put the hardware back, in the order that leaves it looking right.

        The panel is blanked last so that "stopping..." is on screen for the
        second or two the camera takes to let go, and the backlight is driven
        low and NOT released - handing that pin back makes it an input, and the
        module's own pull-up then relights the panel.  See ili9341.close().

        The power LED is deliberately left alone.  It is the shutdown hook's
        to put out, not this program's; dousing it here would mean the box
        looked switched off every time Willis was stopped to run something
        else.
        """
        if self.panel is not None:
            try:
                self.say("stopping...")
            except Exception:                    # noqa: BLE001 - cleanup path
                pass
        if self.camera is not None:
            self.camera.stop()
        if self.encoder is not None:
            self.encoder.stop()
        if self.panel is not None:
            try:
                self.panel.close()
            except Exception as e:               # noqa: BLE001 - cleanup path
                logger.error("Releasing the panel failed: %s", e)
        logger.info("Stopped after %d captures", self.captures)


def _short(text, limit=60):
    """A one-line version of an error, short enough for the panel."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=f"{NAME} - a camera that says what it sees.")
    parser.add_argument("--rotation", type=int, default=0,
                        choices=(0, 90, 180, 270),
                        help="rotate the captured frame; match the enclosure")
    parser.add_argument("--brightness", type=int, default=100,
                        help="panel backlight, 0-100 (default 100)")
    parser.add_argument("--no-buzzer", dest="buzzer", action="store_false",
                        help="stay silent")
    parser.add_argument("--no-led", dest="led", action="store_false",
                        help="leave the GPIO 4 power LED alone")
    parser.add_argument("--no-encoder", dest="encoder", action="store_false",
                        help="do not claim the encoder pins")
    parser.add_argument("--shoot", action="store_true",
                        help="take one photograph and exit; for development, "
                             "since the box itself has only the knob")
    parser.add_argument("--shoot-seconds", type=float, default=8.0,
                        help="with --shoot, how long to leave the caption up")
    parser.add_argument("--log", default="INFO",
                        help="logging level (default INFO)")
    return parser.parse_args(argv)


def main(argv=None):
    options = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, options.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logger.info("%s v%s starting", NAME, VERSION)
    return Willis(options).run()


if __name__ == "__main__":
    sys.exit(main())
