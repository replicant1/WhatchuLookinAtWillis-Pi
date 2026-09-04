#!/usr/bin/env python3
"""
Check the preview loop: what is shown, what is frozen, and what is described.

    python3 tests/control/loop_test.py

Two bugs are being guarded against here, and both of them look fine from the
outside.

**Describing the wrong frame.** The person pressed the knob while looking at a
particular picture. If capture() grabs a fresh frame instead of using the one
frozen on the glass, the box describes something a fraction of a second later
than the picture they chose. Usually that is the same scene, occasionally it is
not, and it is never checkable afterwards - which is exactly why it needs a
test rather than an eye. The check is identity: the object handed to describe()
must be the very object last drawn to the panel.

**Losing the answer.** An earlier version drew the idle prompt immediately
after capture() returned, so the caption survived about eighty milliseconds.
Both screens were amber, so it read as being stuck. The check is that the
caption is still the last thing on the panel while the hold runs.

The doubles are recorders and fakes rather than stubs: the panel keeps every
image in order, the camera hands out a distinguishable frame each time, and the
encoder plays a script of presses. Order and identity are the whole subject, so
nothing that discards either would be usable.
"""

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image                                 # noqa: E402

import willis as app                                  # noqa: E402
from panel import caption                             # noqa: E402

failures = []

CAPTION = "a ribbed tomato on a pale table"
HOLD = 0.25          # stands in for CAPTION_SECONDS; the real one is 10 s


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"           got  {got!r}\n           want {want!r}")
        failures.append(label)


class RecordingPanel:
    """Every image, in order. Order is the subject, so nothing is discarded."""

    def __init__(self):
        self.images = []

    def show(self, image):
        self.images.append(image)

    def backlight(self, percent):
        pass

    def close(self):
        pass


class FakeCamera:
    """A different frame every time, so 'which frame' is an answerable question."""

    def __init__(self):
        self.frames = []

    def grab(self):
        # A distinct shade per frame, so a mix-up is visible as well as
        # detectable by identity.
        shade = 10 + 20 * len(self.frames)
        frame = Image.new("RGB", (640, 480), (shade, shade, shade))
        self.frames.append(frame)
        return frame

    def stop(self):
        pass


class FakeEncoder:
    """Plays a script of press counts, then stops the loop."""

    def __init__(self, willis, script, stop_after):
        self.willis = willis
        self.script = list(script)
        self.stop_after = stop_after
        self.calls = 0

    def take_presses(self):
        self.calls += 1
        if self.calls >= self.stop_after:
            self.willis.is_running = False
        return self.script.pop(0) if self.script else 0

    def stop(self):
        pass


class Recorder:
    """Stands in for describe(), keeping the frame object it was handed."""

    def __init__(self, text=CAPTION):
        self.text = text
        self.frames = []

    def __call__(self, frame, client, **kwargs):
        self.frames.append(frame)
        return self.text, 0.01


def build(script, stop_after):
    options = types.SimpleNamespace(
        rotation=0, brightness=100, buzzer=False, led=False, encoder=True,
        shoot=False, shoot_seconds=0.0, log="CRITICAL")
    willis = app.Willis(options)
    willis.panel = RecordingPanel()
    willis.camera = FakeCamera()
    willis.client = object()                # never used; describe is replaced
    willis.encoder = FakeEncoder(willis, script, stop_after)
    return willis


def main():
    app.CAPTION_SECONDS = HOLD

    print("A press freezes, and describes what was frozen")
    recorder = Recorder()
    app.describe = recorder
    # poll 1: no press -> frame A.  poll 2: no press -> frame B.
    # poll 3: press    -> describe B, then hold.
    willis = build(script=[0, 0, 1], stop_after=12)
    willis._loop()
    panel, camera = willis.panel, willis.camera

    check("two preview frames were drawn before the press",
          panel.images[0].tobytes() == caption.render_frame(camera.frames[0]).tobytes()
          and panel.images[1].tobytes() == caption.render_frame(camera.frames[1]).tobytes(),
          True)
    check("exactly one capture was made", willis.captures, 1)

    # THE ONE THAT MATTERS. Identity, not equality: a fresh grab would produce
    # an equal-looking frame only by accident, but never the same object.
    check("the frame described is the frame that was frozen on the glass",
          recorder.frames[0] is camera.frames[1], True)
    # Not "is the newest frame" - the preview resumes afterwards and grabs
    # more, so that would only hold while the loop was broken. The claim is
    # that nothing grabbed AFTER the press was the thing described.
    check("it is not any frame grabbed after the press",
          any(recorder.frames[0] is f for f in camera.frames[2:]), False)

    # capture() must not grab. If it ever does again, grabs outrun previews.
    preview_count = sum(1 for i in panel.images
                        if i.tobytes() != caption.render(CAPTION).tobytes())
    check("no frame was grabbed while the answer was up",
          len(camera.frames), preview_count)

    caption_image = caption.render(CAPTION)
    check("the answer was drawn",
          any(i.tobytes() == caption_image.tobytes() for i in panel.images), True)
    check("the answer is not immediately overwritten by a prompt",
          panel.images[2].tobytes() == caption_image.tobytes(), True)

    print()
    print("The answer is held, then the preview comes back")
    recorder = Recorder()
    app.describe = recorder
    willis = build(script=[0, 1], stop_after=40)
    started = time.monotonic()
    willis._loop()
    elapsed = time.monotonic() - started
    check("the hold lasted at least CAPTION_SECONDS", elapsed >= HOLD, True)
    index = next(n for n, i in enumerate(willis.panel.images)
                 if i.tobytes() == caption.render(CAPTION).tobytes())
    check("the preview resumed after the hold",
          len(willis.panel.images) > index + 1, True)

    print()
    print("A press while the answer is up dismisses it, and takes no photograph")
    recorder = Recorder()
    app.describe = recorder
    # poll 1: no press -> frame A.  poll 2: press -> describe A, hold.
    # first poll inside the hold: press -> dismiss early, no new capture.
    willis = build(script=[0, 1, 1], stop_after=6)
    started = time.monotonic()
    willis._loop()
    elapsed = time.monotonic() - started
    check("still exactly one capture", willis.captures, 1)
    check("the model was asked once", len(recorder.frames), 1)
    check("the hold was cut short", elapsed < HOLD, True)

    print()
    if failures:
        print(f"FAILED: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: the preview draws frames until the knob is pressed; the press")
    print("        describes the exact frame that was on the glass, not a newer")
    print("        one; the answer stays up for the hold and is not overwritten;")
    print("        and a press while it is up dismisses it without taking")
    print("        another photograph.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
