#!/usr/bin/env python3
"""
Tones on the PS1240 piezo, driven by the SoC's own PWM peripheral.

    python3 src/control/buzzer.py             # play the start-up tune
    python3 src/control/buzzer.py --goodbye   # play the shutdown tune

Functions rather than a class, because there is nothing to remember between
one tune and the next.

**This is hardware PWM, and the change was made by ear.** It used to be
lgpio's tx_pwm, which is software PWM: a helper thread in C toggling the pin.
The tune plays during the busiest half second of start-up, with libcamera and
the panel both coming up on a Zero 2, so that thread was scheduled irregularly
and the period wandered. An irregular period is exactly what static is, and
Rod described it as staticky on every hearing. Driven from the PWM block -
a clock divider and a counter in silicon - the waveform is indifferent to what
the CPU is doing, and the roughness is simply gone. Confirmed by listening to
the same two notes both ways.

The pin is GPIO 13, which is why GPIO 13 was chosen in the first place: it is
the only free pin on this board that reaches a hardware PWM channel. It is
PWM1, reached as /sys/class/pwm/pwmchip0/pwm1, and the mapping is made by
"dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4" in config.txt. Name the
pins explicitly, always: the overlay's defaults are GPIO 18 and 19, which on
this board are the panel backlight and the encoder's CLK.

**The note's length is python's job again, and that is only safe because the
tune runs in a child process.** The old lgpio version handed a cycle count to
C so that a late wake-up could not stretch a note - it had, once, to 431 ms
against 250 asked for. Nothing in the PWM block can end a note: it plays until
something changes it, so a late wake-up now stretches rather than truncates.
in_process is what makes that a non-issue, by giving the tune an interpreter
whose GIL nobody else is holding. The two changes are a pair; do not use a
thread here and expect this to sound right.

Notes run into each other on purpose. `enable` is left on between them and only
the period changes, so the pair reads as one gesture rather than two events. A
consequence of the same choice: duty is written to zero before a period change,
because shrinking the period while the old duty is larger than the new one is
rejected by the driver. This particular tune escapes that by a single
nanosecond - 50% of 440 Hz is 1,136,363 ns against 880 Hz's period of
1,136,364 - which is luck rather than a margin to build on. A tune that more
than doubles in pitch does hit it: 440 Hz to 1760 Hz is refused outright. The
zeroing is written in so the rule holds for whatever notes it is handed.

50% duty is as loud as a square wave gets: above it the fundamental shrinks
again, so 90% sounds like 10% rather than louder.

The part is a bare transducer with no oscillator in it, so the pitch is
whatever it is driven at. That is the whole reason for choosing it over the
KY-006 that came first, and it is checkable before anything drives it: a piezo
reads open at DC and a coil reads short. docs/guides/enclosure-build-guide.html
has the pull-up test and why it goes first.
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# This file is also the child process `in_process` starts, so it needs to be
# able to name itself.
SCRIPT = os.path.abspath(__file__)

PIN = 13                 # the only free pin that reaches hardware PWM
CHANNEL = 1              # GPIO 13 is PWM1; the overlay makes that so
PWMCHIP = "/sys/class/pwm/pwmchip0"
DUTY = 50                # loudest a square wave gets; see the module docstring
NANOSECONDS = 1_000_000_000     # the units every file under pwmchip0 speaks

# 440 Hz then 880 Hz, a quarter second each: an octave apart, so the two notes
# are unmistakably different even on a disc driven far below its resonance.
HELLO = ((440, 0.25), (880, 0.25))

# The same two notes the other way up.  Derived rather than written out, so the
# pair cannot drift apart: changing the greeting changes the farewell to match,
# and "the opposite order" stays true by construction rather than by anyone
# remembering to edit both.
GOODBYE = tuple(reversed(HELLO))


def sysfs_write(chip, channel, name, value):
    """
    Write one PWM attribute. The default `write` for `play`.

    Kept as a plain function taking the chip and channel rather than a bound
    object, so the double a test passes in has nothing to set up and records a
    flat list of (name, value) in the order they were written - which is the
    whole contract here, since ordering is what the driver enforces.
    """
    (Path(chip) / f"pwm{channel}" / name).write_text(str(value))


def ensure_exported(chip=PWMCHIP, channel=CHANNEL):
    """
    Make sure the channel exists, and say so rather than raising if it cannot.

    Normally there is nothing to do: deploy/pwm_export.sh runs as root from the
    service's ExecStartPre and hands the exported channel to rod, because the
    app runs as rod and everything under pwmchip0 is root-owned by default.
    This is the path for a human running the module by hand, where the export
    may not have happened yet and sudo may not be in play.

    Returns True if the channel is usable afterwards.
    """
    channel_path = Path(chip) / f"pwm{channel}"
    if channel_path.exists():
        return True
    try:
        (Path(chip) / "export").write_text(str(channel))
    except OSError as e:                            # noqa: BLE001
        logger.warning("Could not export PWM channel %d: %s", channel, e)
        return False
    # The driver creates the directory asynchronously; a fresh export is not
    # readable the instant the write returns.
    for _ in range(20):
        if channel_path.exists():
            return True
        time.sleep(0.05)
    logger.warning("PWM channel %d did not appear after export", channel)
    return False


def play(notes, chip=PWMCHIP, channel=CHANNEL, duty=DUTY, write=None):
    """
    Play (frequency_hz, seconds) pairs in order, then silence the channel.

    `write` is how an attribute reaches the driver, and exists so a test can
    pass one that records instead of one that makes a noise - the same reason
    the lgpio version took a `gpio` module.

    Blocking, and the sleep is what gives each note its length. Nothing in the
    PWM block can end a note by itself: it plays until something changes it. So
    a late wake-up stretches a note here, where the old lgpio version would
    have left a gap instead. That is the right trade only because the tune runs
    in a child process - see `in_process`, and the module docstring, which
    explains why the two changes are a pair.

    `enable` is deliberately left on between notes, so the tune is one
    continuous sound whose pitch changes rather than two separate events.
    """
    if write is None:
        write = sysfs_write

    try:
        for frequency, seconds in notes:
            period = round(NANOSECONDS / frequency)
            # Duty to zero first. Shrinking the period while the old duty is
            # larger than the new period is rejected by the driver. The
            # greeting happens to clear that by one nanosecond, which is luck;
            # a jump of more than an octave - 440 Hz to 1760 Hz, say - is
            # refused outright. Zeroed here so the rule holds for any tune.
            write(chip, channel, "duty_cycle", 0)
            write(chip, channel, "period", period)
            write(chip, channel, "duty_cycle", period * duty // 100)
            write(chip, channel, "enable", 1)
            time.sleep(seconds)
    finally:
        # Silence in both of the ways it can be asked for. Duty first, so that
        # a channel which refuses to disable is at least quiet.
        write(chip, channel, "duty_cycle", 0)
        write(chip, channel, "enable", 0)


def hello(chip=PWMCHIP, channel=CHANNEL, duty=DUTY, write=None):
    """
    The start-up tune: the box saying it is alive before it can show anything.

    Worth having for the same reason the panel's splash screen is: the camera
    takes about twenty seconds to produce its first frame, and in a sealed box
    twenty seconds of silence and blank glass is indistinguishable from broken
    hardware.  This one arrives in the first half second, before the panel has
    anything at all.

    Blocking.  Start-up wants `in_background`.
    """
    play(HELLO, chip=chip, channel=channel, duty=duty, write=write)


def duration(notes):
    """How long a tune runs, before anything plays it."""
    return sum(seconds for _, seconds in notes)


def goodbye(chip=PWMCHIP, channel=CHANNEL, duty=DUTY, write=None):
    """
    The farewell: the greeting backwards, as the last thing the app does.

    Blocking, and that is the point rather than an oversight.  The greeting is
    a courtesy nobody waits for; this one is the signal that the box has
    finished with the camera and the panel and is safe to unplug, which is
    worth nothing at all if the process exits while it is still sounding.

    The caller still wants a bound on the wait - see MainRenderLooper._say_goodbye,
    which plays it on a thread and joins with a timeout, so a buzzer that
    somehow never returns cannot hold a shutdown open.
    """
    play(GOODBYE, chip=chip, channel=channel, duty=duty, write=write)


def in_background(notes=HELLO, chip=PWMCHIP, channel=CHANNEL, duty=DUTY,
                  write=None, name="Tune"):
    """
    Start a tune on a thread of its own and return it, without waiting.

    NOT what start-up calls any more - `in_process` is, and the reason is
    written up there. A thread keeps the tune off the critical path but leaves
    it sharing this process's GIL, which is audible: the notes come apart. This
    is kept because it is the right answer when the caller is not fighting the
    GIL, and because `play` on a thread is a smaller thing to reason about than
    a child process.

    Start-up must not stand still for half a second of sound.  Nothing later
    depends on the tune having finished, and nothing about the tune depends on
    what start-up does next, so the two have no reason to be in step.

    A daemon thread, so a tune still playing cannot hold the process open at
    shutdown.  Losing the pin claim that way is safe: the kernel drops the
    chip handle when the process goes, which is the same guarantee that makes
    a crash mid-tune survivable.

    Failures are logged here rather than raised, because by the time one
    happens there is no longer a caller to raise to.  The returned thread is
    for tests and for anyone who does want to wait; ignoring it is the normal
    case.

    `name` is what the log calls this tune.  It exists because the first
    version said "Start-up tune" whatever it was playing, so the farewell
    announced itself as a greeting - which is the sort of small lie that costs
    an hour when a log is the only witness left.
    """
    def run():
        try:
            play(notes, chip=chip, channel=channel, duty=duty, write=write)
        except Exception as e:                      # noqa: BLE001
            logger.warning("Buzzer fell silent: %s: %s", type(e).__name__, e)

    thread = threading.Thread(target=run, name="buzzer", daemon=True)
    thread.start()
    # Said out loud, because otherwise a tune that played and a tune that never
    # started look identical in the log - and on a board with no buzzer fitted
    # they sound identical too.
    logger.info("%s: %s on GPIO %d", name,
                ", ".join(f"{hz} Hz for {s}s" for hz, s in notes), PIN)
    return thread


def in_process(name="Tune", python=None, script=None, popen=None):
    """
    Play the start-up tune in a child process, and return without waiting.

    A thread was not enough, and the reason is the GIL rather than anything to
    do with sound. `play` ends each note in C on a cycle count, so a note
    cannot be stretched - but the *next* note cannot begin until python wakes
    up, and during start-up the main thread is holding the GIL while libcamera
    and the panel come up. A late wake-up is a silence between two notes that
    are meant to be one gesture. That silence is what this removes.

    A child has its own interpreter and its own GIL, so nothing this process
    does can delay it.

    The cost is one interpreter start-up, measured at about 130 ms on this Pi -
    not the ~400 ms the whole child takes, because the in-process version
    already paid for importing lgpio (~104 ms) and opening the chip (~100 ms)
    before its own first note. So the greeting arrives about an eighth of a
    second later than it used to, and arrives whole.

    subprocess is imported here rather than at the top of the file because this
    module IS the child: a module-level import would be paid again by every
    child, on the one path where start-up latency is the thing being bought.

    `popen` is the spawner, and exists so a test can pass one that records
    instead of one that makes a noise - the same reason `play` takes `gpio`.

    The child is reaped on a daemon thread, which costs nothing: waitpid
    releases the GIL, so the thread is asleep in the kernel rather than
    competing with anything. Without it the finished child stays a zombie for
    the life of the app, which is untidy rather than harmful, but the thread is
    also the only place a non-zero exit can be noticed at all.
    """
    import subprocess                    # see the docstring: the child pays it

    if popen is None:
        popen = subprocess.Popen
    if python is None:
        python = sys.executable
    if script is None:
        script = SCRIPT

    child = popen([python, script],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reap():
        code = child.wait()
        if code:
            logger.warning("%s: the buzzer process exited with %s", name, code)

    threading.Thread(target=reap, name="buzzer-reap", daemon=True).start()
    # Said out loud, because otherwise a tune that played and a tune that never
    # started look identical in the log - and on a board with no buzzer fitted
    # they sound identical too.
    logger.info("%s: %s on GPIO %d, in a child process", name,
                ", ".join(f"{hz} Hz for {s}s" for hz, s in HELLO), PIN)
    return child


def main():
    logging.basicConfig(level=logging.INFO)
    tune = GOODBYE if "--goodbye" in sys.argv else HELLO
    ensure_exported()
    play(tune)       # blocking here: a script with nothing else to do
    print(f"played {len(tune)} notes on GPIO {PIN}: "
          + ", ".join(f"{hz} Hz for {s}s" for hz, s in tune))


if __name__ == "__main__":
    main()
