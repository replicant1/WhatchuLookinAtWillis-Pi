#!/usr/bin/env python3
"""
Whatchu Lookin At, Willis - a camera in a box that says what it can see.

The panel shows what the camera can see.  Press the knob and the picture
freezes, and a few seconds later it is replaced by a phrase describing it; ten
seconds after that the preview comes back.  That is the whole product.

There is no prompt and no instruction anywhere, because a live picture of the
room is self-explanatory and a frozen one is unmistakable.

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

from capture.camera import Camera                          # noqa: E402
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
#
# 4 kHz because the PS1240 is a RESONANT device, nominally around 4 kHz, and
# volume is the only thing being bought here.  Duty is already 50%, which is as
# loud as a square wave gets - above it the fundamental shrinks again, so 90%
# sounds like 10% rather than louder - which leaves frequency as the only lever.
# The first version ran at 1760 Hz for 60 ms and was audible but too quiet:
# far below resonance AND very short, which is the worst pair of choices
# available.  The greeting gets away with 440 and 880 Hz only because its notes
# are a quarter-second each.
SHUTTER = ((4000, 0.09),)

# How often the button is checked.  Fifty milliseconds is far below the
# threshold at which a press feels ignored and costs a lock acquisition; the
# encoder's own callbacks run in lgpio's thread, so nothing is missed between
# polls however slow this loop gets.
POLL_SECONDS = 0.05

# Preview frames between throughput log lines. Often enough to notice a preview
# that has quietly halved in speed, rare enough not to fill the journal.
PREVIEW_LOG_EVERY = 100

# How long the answer stays up before the preview comes back.  Ten seconds is
# long enough to read eighty characters twice and long enough to fetch somebody
# to look, and short enough that a box left alone returns to showing what it
# can see rather than what it saw a while ago.
CAPTION_SECONDS = 10.0


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
        # The frame currently on the panel. This is what a press describes -
        # see _loop() - so it is state rather than a local variable.
        self.showing = None

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
        self.camera = Camera(rotation=self.options.rotation).start()

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

    def capture(self, frame):
        """
        Describe the frame that is currently frozen on the panel.  Never raises.

        **The frame is passed in, not grabbed here, and that is the point.**
        The preview stops updating the instant a press is seen, so whatever is
        on the glass at that moment is what the person chose to photograph.
        Grabbing a fresh frame instead would describe something a fraction of a
        second later than the picture they were looking at - usually the same
        thing, occasionally not, and never checkable afterwards.

        **Nothing is drawn while the model is being asked.** The freeze IS the
        acknowledgement: a preview that was moving and has stopped is an
        unmistakable signal, and it needs no words. An intermediate
        "looking..." screen would replace the very picture the person is
        waiting to hear about.

        Every failure ends with something on the panel, because a box whose
        only output goes blank is indistinguishable from a box that is broken.
        """
        self.captures += 1
        self._chirp()

        if self.client is None:
            self.complain("no API key\nI can see but I cannot say")
            return

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
                # The one path that has no preview to freeze, so it takes its
                # own frame. Development only; the box itself has only the knob.
                self.capture(self.camera.grab())
                time.sleep(self.options.shoot_seconds)
                return 0

            if self.encoder is None:
                self.complain("no button\nnothing can start a photograph")

            return self._loop()
        finally:
            self._release()

    def _loop(self):
        """
        Show what the camera sees, until somebody presses the knob.

        The preview is the resting state and there is no prompt, because a live
        picture of the room needs no caption explaining what it is.

        **Presses are checked before the next frame is drawn, not after.** The
        person pressed while looking at a particular picture, and that picture
        is what `self.showing` holds; drawing a new frame first and then
        noticing the press would describe something they never saw. It is a
        fraction of a second's difference and it is exactly the difference
        between "the box described what I showed it" and "the box described
        something like what I showed it".

        There is no sleep in this loop on purpose. It paces itself on the SPI
        write, which is the slow step and which releases the GIL while it runs,
        so adding a delay would only make the preview worse. The loop is idle
        the moment a capture starts, because a frozen preview draws nothing.
        """
        frames = 0
        started = time.monotonic()

        while self.is_running:
            if self.encoder is not None and self.encoder.take_presses():
                if self.showing is None:
                    # Pressed between the camera starting and the first frame
                    # reaching the glass. There is nothing frozen to describe.
                    logger.info("Press before the first frame; ignored")
                    continue
                self.capture(self.showing)
                self._hold()
                continue

            try:
                frame = self.camera.grab()
            except Exception as e:               # noqa: BLE001
                logger.exception("Preview grab failed")
                self.complain(f"camera trouble\n{type(e).__name__}")
                return 1

            self.showing = frame
            self._show(caption_panel.render_frame(frame))

            frames += 1
            if frames % PREVIEW_LOG_EVERY == 0:
                elapsed = time.monotonic() - started
                logger.info("Preview: %d frames, %.1f fps", frames,
                            frames / elapsed)
        return 0

    def _hold(self, seconds=None):
        """
        Leave the answer up, and come back early if the knob is pressed.

        `seconds` is resolved here rather than in the signature, because a
        default argument is bound once when the function is defined: written
        as `seconds=CAPTION_SECONDS`, the constant becomes uneditable the
        moment this module is imported, and anything that sets it afterwards -
        a test, a future command-line flag - is silently ignored while
        appearing to work. That is exactly how it was written first, and the
        test that caught it looked like a broken test rather than a real
        finding for several minutes.

        A press during the hold means "I have read it": the preview returns
        immediately and no second capture is started. Photographing again
        therefore takes one more press than it strictly could, which is a much
        smaller surprise than a box that fires again the moment somebody
        touches it while reading.

        Checked at POLL_SECONDS rather than slept through, so a `systemctl
        stop` during the hold is acted on in fifty milliseconds instead of ten
        seconds.
        """
        if seconds is None:
            seconds = CAPTION_SECONDS
        deadline = time.monotonic() + seconds
        while self.is_running and time.monotonic() < deadline:
            if self.encoder is not None and self.encoder.take_presses():
                logger.info("Answer dismissed by a press")
                return
            time.sleep(POLL_SECONDS)

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
