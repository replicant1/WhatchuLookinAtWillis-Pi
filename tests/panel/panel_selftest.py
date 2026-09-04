#!/usr/bin/env python3
"""
Light the panel, and ask a human whether it looks right.

    python3 tests/panel/panel_selftest.py          # on the Pi

**Nothing in software can confirm what is on this glass**, and this file must
not pretend otherwise. Two independent facts combine: grim photographs the
Wayland/HDMI output and the SPI panel is not part of it, and this module does
not wire SDO usefully - register read-back returns all zeros. So the only
instrument is a person.

What IS checked automatically, and can genuinely fail:

  * the RGB565 packing arithmetic, against values worked out by hand;
  * that a caption image is exactly the size the panel will accept, which is
    the difference between a picture and a ValueError at the moment somebody
    presses the button.

Everything after that is drawn and described, and the last thing this prints is
a question. A clean run is NOT a pass.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from panel import caption                             # noqa: E402
from panel.ili9341 import ILI9341, pack_rgb565, rgb565  # noqa: E402

failures = []

HOLD = 3.0          # seconds each screen stays up, long enough to look at


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"           got  {got!r}\n           want {want!r}")
        failures.append(label)


def check_the_arithmetic():
    """RGB565 packing, against values computed by hand rather than by code."""
    print("RGB565 packing (no hardware needed)")

    # Pure red: R=0xFF -> 5 bits of 1s in the top; G and B zero.
    # 11111 000000 00000 = 0xF800
    check("pure red packs to 0xF800", rgb565(255, 0, 0), 0xF800)
    # Pure green: 00000 111111 00000 = 0x07E0
    check("pure green packs to 0x07E0", rgb565(0, 255, 0), 0x07E0)
    # Pure blue: 00000 000000 11111 = 0x001F
    check("pure blue packs to 0x001F", rgb565(0, 0, 255), 0x001F)
    check("white packs to 0xFFFF", rgb565(255, 255, 255), 0xFFFF)
    check("black packs to 0x0000", rgb565(0, 0, 0), 0x0000)

    # The panel wants the high byte first, which is the opposite of what numpy
    # produces on this little-endian Pi. A byte-order bug shows up as a picture
    # in wrong but plausible colours, which is exactly the kind of fault a
    # human glancing at the panel would forgive.
    from PIL import Image
    one_red_pixel = Image.new("RGB", (1, 1), (255, 0, 0))
    check("packed bytes are big-endian (high byte first)",
          tuple(pack_rgb565(one_red_pixel)), (0xF8, 0x00))


def check_the_sizes():
    """The images caption.py makes must be the size the panel will take."""
    print()
    print("Image sizes (no hardware needed)")
    check("a caption matches the landscape panel",
          caption.render("test").size, (320, 240))
    check("the panel reports the size caption.py assumes",
          caption.PANEL, (320, 240))


def show_the_screens():
    """Draw four screens, describing each as it goes up."""
    print()
    print("Lighting the panel. Watch it.")
    panel = ILI9341(landscape=True).init()
    try:
        panel.backlight(100)

        print(f"  1. three colour bars: RED left, GREEN middle, BLUE right"
              f"  ({HOLD:.0f}s)")
        from PIL import Image, ImageDraw
        bars = Image.new("RGB", (320, 240), (0, 0, 0))
        draw = ImageDraw.Draw(bars)
        for i, colour in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
            draw.rectangle([i * 107, 0, (i + 1) * 107 - 1, 239], fill=colour)
        panel.show(bars)
        time.sleep(HOLD)

        print(f"  2. a short caption, LARGE, white on black          ({HOLD:.0f}s)")
        panel.show(caption.render("a red mug"))
        time.sleep(HOLD)

        print(f"  3. a long caption, SMALLER, wrapped to 3-4 lines   ({HOLD:.0f}s)")
        panel.show(caption.render(
            "a tabby cat asleep on a stack of unopened post beside a "
            "half-drunk mug of tea"))
        time.sleep(HOLD)

        print(f"  4. the word looking... in AMBER, then a RED failure({HOLD:.0f}s)")
        panel.show(caption.render_status("looking..."))
        time.sleep(HOLD / 2)
        panel.show(caption.render_failure("could not ask\nno route to host"))
        time.sleep(HOLD)
    finally:
        panel.close()
        print("  panel blanked and the backlight driven low.")


def main():
    check_the_arithmetic()
    check_the_sizes()

    if failures:
        print()
        print(f"FAILED: {len(failures)} - not lighting the panel.")
        for name in failures:
            print(f"  - {name}")
        return 1

    if "--no-panel" in sys.argv:
        print()
        print("RESULT: the arithmetic holds. --no-panel given, so nothing was "
              "drawn.")
        return 0

    show_the_screens()

    print()
    print("RESULT: the packing arithmetic and the image sizes are correct, and")
    print("        four screens were sent to the panel.")
    print()
    print("        THIS IS NOT A PASS UNTIL SOMEBODY ANSWERS THIS. On the")
    print("        2.4 inch panel, in this order, you should have seen:")
    print()
    print("          1. three vertical bars, RED on the LEFT, green in the")
    print("             middle, BLUE on the RIGHT - if red and blue are")
    print("             swapped, the byte order is wrong;")
    print("          2. the words 'a red mug', large, filling most of the")
    print("             glass, white on black;")
    print("          3. a longer sentence about a tabby cat, in smaller text,")
    print("             wrapped over three or four centred lines, all of it on")
    print("             screen with nothing running off the edges;")
    print("          4. 'looking...' in amber, then a red two-line failure;")
    print("          5. the panel dark, and STAYING dark.")
    print()
    print("        If any of that is wrong, say which one - each points at a")
    print("        different half of the code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
