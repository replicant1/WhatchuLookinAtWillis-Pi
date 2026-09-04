#!/usr/bin/env python3
"""
Check that the answer stays on the panel, with no hardware and no network.

    python3 tests/control/loop_test.py

This test exists because of a bug that four successful runs did not find. The
loop used to say "press the knob" immediately after capture() returned, to put
the box back in its resting state. capture() ends by drawing the caption, so
the caption survived about eighty milliseconds - and since "looking..." and
"press the knob" are both amber status screens, a person watching saw the
picture, then amber text, then amber text, and reported that it was stuck on
"looking".

Every assertion below would have passed against that version except one: the
last thing drawn. That is the whole test.

It was missed because --shoot captures once and then sleeps, which is a
different path from the loop the product actually runs. So this drives _loop()
itself rather than capture().

The panel double is a **recorder, not a stub**: it keeps every image it was
given, in order, because "was the caption ever drawn" and "was the caption
still there at the end" are different questions and only the second one is the
product.
"""

import sys
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


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"           got  {got!r}\n           want {want!r}")
        failures.append(label)


class RecordingPanel:
    """Every image, in order. Nothing is thrown away, because order is the point."""

    def __init__(self):
        self.images = []

    def show(self, image):
        self.images.append(image)

    def backlight(self, percent):
        pass

    def close(self):
        pass


class FakeCamera:
    def __init__(self):
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return Image.new("RGB", (640, 480), (200, 40, 40))

    def stop(self):
        pass


class FakeEncoder:
    """One press, then nothing, then stop the loop."""

    def __init__(self, willis, presses=1, polls_before_stopping=3):
        self.willis = willis
        self.presses = presses
        self.polls = 0
        self.limit = polls_before_stopping

    def take_presses(self):
        self.polls += 1
        if self.polls > self.limit:
            self.willis.is_running = False
        if self.presses:
            self.presses -= 1
            return 1
        return 0

    def stop(self):
        pass


class FakeClient:
    def __init__(self, text=CAPTION):
        self.text = text
        self.messages = self

    def create(self, **kwargs):
        block = types.SimpleNamespace(type="text", text=self.text)
        return types.SimpleNamespace(content=[block], stop_reason="end_turn",
                                     stop_details=None)


def build(presses=1):
    options = types.SimpleNamespace(
        rotation=0, brightness=100, buzzer=False, led=False, encoder=True,
        shoot=False, shoot_seconds=0.0, log="CRITICAL")
    willis = app.Willis(options)
    willis.panel = RecordingPanel()
    willis.camera = FakeCamera()
    willis.client = FakeClient()
    willis.encoder = FakeEncoder(willis, presses=presses)
    return willis


def is_same(a, b):
    return a.tobytes() == b.tobytes()


def main():
    # The frame is deliberately held on screen for over a second in the real
    # thing. Nothing here is testing that, and waiting for it would make this
    # test slow enough that nobody runs it.
    app.FRAME_SECONDS = 0.0

    print("One press")
    willis = build(presses=1)
    willis._loop()
    panel = willis.panel

    check("the camera was used exactly once", willis.camera.grabs, 1)
    check("the capture was counted", willis.captures, 1)

    # Three screens: the frame, "looking...", the caption.
    check("three screens were drawn", len(panel.images), 3)
    check("the frame is shown before the model is asked",
          is_same(panel.images[0],
                  caption.render_frame(Image.new("RGB", (640, 480), (200, 40, 40)))),
          True)
    check("the second screen is the status line",
          is_same(panel.images[1], caption.render_status("looking...")), True)

    # THE ONE THAT MATTERS. Against the old loop this was the amber prompt.
    check("the LAST thing on the panel is the answer",
          is_same(panel.images[-1], caption.render(CAPTION)), True)
    check("the answer is not overwritten by the idle prompt",
          is_same(panel.images[-1], caption.render_status("press the knob")),
          False)

    print()
    print("Two presses")
    willis = build(presses=2)
    willis._loop()
    check("both presses were taken", willis.captures, 2)
    check("the answer is still the last thing after a second capture",
          is_same(willis.panel.images[-1], caption.render(CAPTION)), True)

    print()
    if failures:
        print(f"FAILED: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: a press draws the frame, then the status, then the caption -")
    print("        and the caption is what is still on the glass when the loop")
    print("        goes back to waiting. The box's resting state is the last")
    print("        thing it saw, not an instruction its owner already knows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
