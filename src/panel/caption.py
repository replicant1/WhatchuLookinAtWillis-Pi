"""
Words onto 320x240 pixels of glass.

The panel is the only output this box has, so everything a person will ever
learn from Willis has to survive being drawn here: a caption, a status line, or
the photograph itself.  Three functions, one for each.

Two decisions worth stating.

**The font size is chosen, not fixed.**  A caption is between two and ninety
characters and there is no single size that suits both.  `render` tries the
sizes in FONT_SIZES largest first and keeps the first that fits in the box
after wrapping.  This costs a handful of text measurements per caption, on a
path that has just waited seconds for a network round trip, so the cost is
irrelevant and the result is that short captions are large and readable across
a room while long ones still fit.

**Wrapping measures the font rather than counting characters.**  DejaVu Sans is
proportional - "MMM" is nearly three times the width of "iii" - so a wrap that
counts characters overflows on capitals and wastes half the panel on lower
case.  `_wrap` asks the font for the width of each candidate line instead.

Everything here is pure image construction with no hardware in it, which is
what makes it the one part of the display path that a test can genuinely check.
"""

import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# The panel in landscape.  ILI9341(landscape=True) reports exactly this, and
# `show` refuses an image of any other size, so agreeing here is not optional.
PANEL = (320, 240)

# Tried largest first.  The gap between 34 and 28 is deliberate: sizes closer
# together than about 6 points are not distinguishable at arm's length on a
# 2.4 inch panel, so they would cost measurements and buy nothing.
FONT_SIZES = (40, 34, 28, 23, 19, 16, 13)

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FALLBACK_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

# Bilinear, not Lanczos. This function used to run once per button press,
# where the best filter available was obviously right; it now runs on every
# preview frame, where it is on the critical path of the frame rate. Lanczos
# weighs a far larger neighbourhood per output pixel, and at a 2:1 reduction
# onto a 2.4 inch panel the difference is invisible while the cost is not.
PREVIEW_RESAMPLE = Image.BILINEAR

MARGIN = 12                # pixels of dark border, all four sides
LINE_SPACING = 1.15        # multiple of the font's own line height

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
AMBER = (255, 176, 0)      # status lines, so they read as "not the answer"
RED = (255, 72, 72)        # failures


def load_font(size, path=FONT_PATH):
    """
    A TrueType font at `size`, or PIL's built-in if none can be found.

    The fallback is not decoration.  A missing font file would otherwise raise
    at the moment a caption is ready to be shown, throwing away the answer the
    box just spent money and seconds obtaining, in order to complain about
    typography.  An ugly caption beats no caption.
    """
    for candidate in (path,) + FALLBACK_FONT_PATHS:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    logger.warning("No TrueType font found; falling back to PIL's bitmap font")
    return ImageFont.load_default()


def _text_width(draw, text, font):
    """Width of `text` in pixels, in this font."""
    return draw.textbbox((0, 0), text, font=font)[2]


def _wrap(draw, text, font, width):
    """
    Greedily break `text` into lines no wider than `width` pixels.

    A single word too long for the line is left on its own line and allowed to
    overflow rather than being hyphenated or dropped - it is rare, and a
    clipped long word is more informative than a missing one.
    """
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and _text_width(draw, candidate, font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _fit(draw, text, box, sizes=FONT_SIZES):
    """
    The largest size in `sizes` whose wrapped text fits `box`, with its lines.

    Returns (font, lines).  The smallest size is returned even when it does not
    fit, for the same reason load_font has a fallback: showing a caption that
    runs off the bottom is better than showing nothing at all.
    """
    width, height = box
    font = lines = None
    for size in sizes:
        font = load_font(size)
        lines = _wrap(draw, text, font, width)
        line_height = font.getbbox("Ay")[3] * LINE_SPACING
        if len(lines) * line_height <= height:
            return font, lines
    return font, lines


def render(text, size=PANEL, colour=WHITE, background=BLACK):
    """
    A caption, centred, at the largest size that fits.

    Args:
        text: what to say.  Any length; the font shrinks to suit.
        size: image size, defaulting to the panel in landscape.
        colour: text colour.
        background: fill colour.

    Returns:
        A PIL RGB Image of exactly `size`, ready for ILI9341.show().
    """
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)

    box = (size[0] - 2 * MARGIN, size[1] - 2 * MARGIN)
    font, lines = _fit(draw, text, box)

    line_height = font.getbbox("Ay")[3] * LINE_SPACING
    block_height = len(lines) * line_height
    y = (size[1] - block_height) / 2

    for line in lines:
        x = (size[0] - _text_width(draw, line, font)) / 2
        draw.text((x, y), line, font=font, fill=colour)
        y += line_height

    return image


def render_status(text, size=PANEL, colour=AMBER):
    """
    A short line saying what the box is doing, as opposed to what it saw.

    Amber rather than white on purpose: the difference between "looking..."
    and an actual caption should be legible from across the room without
    reading either of them.
    """
    return render(text, size=size, colour=colour)


def render_failure(text, size=PANEL):
    """A failure, in red, worded for somebody standing in front of the box."""
    return render(text, size=size, colour=RED)


def render_frame(frame, size=PANEL, background=BLACK):
    """
    A camera frame, scaled to fill the panel without distorting it.

    Letterboxed rather than cropped.  The whole point of showing the frame is
    to tell the person what the box is about to describe, so trimming the
    edges off would make it a slightly different picture from the one being
    described, which is exactly the thing it exists to rule out.
    """
    image = Image.new("RGB", size, background)
    scaled = frame.copy()
    scaled.thumbnail(size, PREVIEW_RESAMPLE)
    image.paste(scaled, ((size[0] - scaled.size[0]) // 2,
                         (size[1] - scaled.size[1]) // 2))
    return image
