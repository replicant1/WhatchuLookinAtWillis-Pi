#!/usr/bin/env python3
"""
Check that words are laid out on 320x240 correctly, with no panel present.

    python3 tests/panel/caption_test.py

This is the one part of the display path a machine can genuinely judge.
Everything below caption.py ends at an SPI bus this module cannot read back,
but the image handed to that bus is an ordinary PIL image, and whether the text
fits inside it is arithmetic.

What is deliberately checked rather than assumed:

  * The font SHRINKS for longer captions. A test that only asserted "the image
    is 320x240" would pass against a version that rendered every caption at 13
    point, which fits everything and is unreadable.
  * No line is wider than the box. A wrapper that counted characters instead of
    measuring the font passes a character-count assertion and overflows the
    glass on a caption full of capitals, so the assertion here is in pixels.
  * A frame is letterboxed, not stretched. The aspect ratio of the pasted
    region is compared with the aspect ratio it went in with.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw                      # noqa: E402

from panel import caption                             # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"           got  {got!r}\n           want {want!r}")
        failures.append(label)


def chosen_size(text):
    """The point size caption.py would pick for `text`."""
    probe = Image.new("RGB", caption.PANEL)
    draw = ImageDraw.Draw(probe)
    box = (caption.PANEL[0] - 2 * caption.MARGIN,
           caption.PANEL[1] - 2 * caption.MARGIN)
    font, _ = caption._fit(draw, text, box)
    return font.size


def widest_line(text):
    """The widest wrapped line, in pixels, at the size that would be chosen."""
    probe = Image.new("RGB", caption.PANEL)
    draw = ImageDraw.Draw(probe)
    box = (caption.PANEL[0] - 2 * caption.MARGIN,
           caption.PANEL[1] - 2 * caption.MARGIN)
    font, lines = caption._fit(draw, text, box)
    return max(caption._text_width(draw, line, font) for line in lines)


SHORT = "a cat"
LONG = ("a tabby cat asleep on a stack of unopened post beside a "
        "half-drunk mug of tea")


def main():
    print("Caption layout")

    check("a caption is exactly panel-sized",
          caption.render(SHORT).size, caption.PANEL)
    check("a status line is exactly panel-sized",
          caption.render_status("looking...").size, caption.PANEL)

    # The point of the whole _fit loop. If this fails, captions are being
    # rendered at a fixed size and the loop is decoration.
    small, large = chosen_size(LONG), chosen_size(SHORT)
    check("a short caption gets a larger font than a long one",
          large > small, True)
    check("the short caption takes the largest size offered",
          large, caption.FONT_SIZES[0])

    # In pixels, not characters - a character-count wrapper passes the
    # character version of this and overflows here.
    box_width = caption.PANEL[0] - 2 * caption.MARGIN
    check("no wrapped line is wider than the box (long caption)",
          widest_line(LONG) <= box_width, True)
    check("no wrapped line is wider than the box (all capitals)",
          widest_line("MMMMM MMMMM MMMMM MMMMM MMMMM MMMMM MMMMM") <= box_width,
          True)

    # A word that cannot fit on any line must still appear. Dropping it would
    # be the tidy-looking bug: the image stays valid and the caption lies.
    monster = "antidisestablishmentarianism" * 3
    probe = ImageDraw.Draw(Image.new("RGB", caption.PANEL))
    font = caption.load_font(13)
    check("an unbreakable word is kept rather than dropped",
          caption._wrap(probe, monster, font, 50), [monster])

    # Status, caption and failure must be distinguishable across a room, which
    # means they must at least differ.
    check("status, caption and failure use three different colours",
          len({caption.WHITE, caption.AMBER, caption.RED}), 3)

    # Letterboxing: a square frame on a 4:3 panel keeps its shape.
    square = Image.new("RGB", (600, 600), (10, 200, 10))
    shown = caption.render_frame(square)
    check("a frame is drawn at panel size", shown.size, caption.PANEL)
    green = [(x, y) for y in range(shown.size[1]) for x in range(shown.size[0])
             if shown.getpixel((x, y))[1] > 100]
    width = max(x for x, _ in green) - min(x for x, _ in green) + 1
    height = max(y for _, y in green) - min(y for _, y in green) + 1
    check("a square frame stays square (letterboxed, not stretched)",
          abs(width - height) <= 1, True)
    check("a square frame is bounded by the panel's height",
          height, caption.PANEL[1])

    print()
    if failures:
        print(f"FAILED: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: captions are panel-sized, shrink to fit, never overflow the")
    print("        box in pixels, keep words they cannot break, and letterbox")
    print("        frames rather than stretching them.")
    print("        Nothing here has seen the panel. Run")
    print("        tests/panel/panel_selftest.py on the Pi and look at it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
