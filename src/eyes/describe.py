"""
A photograph in, one short line out.

The whole of Willis's intelligence is this file, and it is small on purpose.
Everything hard about the box is hardware; this part is one request.

Three things here are decisions rather than defaults:

**The image is resized before it is sent.**  A 1024x768 frame and a 512-pixel
one produce the same caption, and the smaller one costs a fraction of the
input tokens and uploads in a fraction of the time on domestic broadband.  The
long edge is the thing that matters to the model, so `MAX_EDGE` is expressed
that way rather than as a fixed size.

**The model is asked for a length, and the answer is clipped anyway.**  A
prompt is a request, not a guarantee, and a caption that overruns the panel is
a display bug reported by a person rather than a test.  `_clip` is the
guarantee.  This is the same shape of boundary as any validated tool call: the
model proposes, local code disposes.

**A frame too dark or blurred to read has its own answer.**  Willis lives in a
box with a button on it and will be pressed at a wall, in the dark, and with a
thumb over the lens.  A model that always answers cannot be judged on the
occasions where the right answer is "I can't see anything", so it is given the
words to say so and the tests check that the path exists.
"""

import base64
import io
import logging
import time

logger = logging.getLogger(__name__)

# Opus 5.  Willis takes one photograph a minute at most, so per-request cost is
# close to irrelevant here and capability is not - the difference between "a
# dog" and "a terrier mid-yawn on a red sofa" is the entire product.  If that
# ever stops being true, this string is the only thing to change; measure
# against a set of real frames before believing any change to it.
MODEL = "claude-opus-5"

# A caption is not hard work.  Thinking stays ON - it is on by default for this
# model and disabling it is a documented way to get a tool call written into
# visible text - and `effort` is the lever instead.
EFFORT = "low"

# Room for the thinking that precedes a one-line answer.  Not tight: hitting
# this cap truncates mid-sentence, which on a panel looks like a hardware
# fault rather than a limit.
MAX_TOKENS = 2000

MAX_EDGE = 512             # longest side of the image actually sent
JPEG_QUALITY = 70

# What fits the panel at a readable size, measured with src/panel/caption.py
# rather than guessed: 90 characters wraps to three lines at 23 point, which is
# legible at arm's length.  Longer than that and the font drops to a size that
# needs leaning in, which defeats the point of a box you glance at.
MAX_CHARS = 90

CANNOT_SEE = "too dark to tell"

SYSTEM_PROMPT = f"""\
You are the eyes of a small camera in a box. Somebody has just pressed its \
button, and whatever you say next is displayed on a 2.4 inch screen on the \
front of the box and read by the person standing there.

Describe what the camera sees in one phrase of at most {MAX_CHARS} characters. \
No preamble, no explanation, no full stop, no quotation marks - just the phrase.

Be specific rather than safe. "a terrier mid-yawn on a red sofa" is worth \
saying; "an indoor scene" is not, and neither is "an image of a room".

If the frame is too dark, too blurred, or too close to make out, reply with \
exactly: {CANNOT_SEE}

Never guess at what a blurred shape might be. Saying you cannot see is a real \
answer and is always better than a confident wrong one.\
"""


class DescribeError(RuntimeError):
    """The model could not be reached, or answered in a shape we cannot use."""


def _encode(image, max_edge=MAX_EDGE, quality=JPEG_QUALITY):
    """The image as base64 JPEG, scaled so its longest side is `max_edge`."""
    small = image.copy()
    small.thumbnail((max_edge, max_edge))
    buffer = io.BytesIO()
    small.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def _clip(text, limit=MAX_CHARS):
    """
    One line, no longer than `limit`, with no trailing punctuation.

    Cuts at a word boundary where there is one within reach of the limit, so a
    clipped caption reads as a phrase rather than as a fault.
    """
    text = " ".join(text.split()).strip().strip('"').rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    logger.info("Caption clipped from %d to %d characters", len(text), len(cut))
    return cut.rstrip(",;:")


def describe(image, client, model=MODEL):
    """
    Ask what is in `image`.

    Args:
        image: a PIL Image, any size.  It is resized before being sent.
        client: an Anthropic client.  Passed in rather than fetched so a test
            can hand over one that answers without a network.
        model: the model id.

    Returns:
        (caption, seconds) - the caption never longer than MAX_CHARS.

    Raises:
        DescribeError: the request failed, was refused, or came back in a
            shape this code cannot read.  Every one of those is something the
            panel has to be able to say a useful sentence about.
    """
    data = _encode(image)
    started = time.monotonic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    # The image goes first.  The documented ordering is image
                    # before text, and the text is a prompt about the image
                    # rather than the other way round.
                    {"type": "image",
                     "source": {"type": "base64",
                                "media_type": "image/jpeg",
                                "data": data}},
                    {"type": "text", "text": "What is in this frame?"},
                ],
            }],
        )
    except Exception as e:                       # the SDK's whole error surface
        raise DescribeError(str(e)) from e

    elapsed = time.monotonic() - started

    # A refusal is an HTTP 200 with nothing usable in `content`, so this has to
    # be checked before reading the response rather than after.
    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        raise DescribeError(f"the model declined ({category})")

    text = next((block.text for block in response.content
                 if getattr(block, "type", None) == "text"), None)
    if not text or not text.strip():
        # Reached when the model produced only thinking and no answer.  Rare,
        # and indistinguishable from a network failure to somebody looking at
        # the panel, but distinguishable in the log, which is where it matters.
        raise DescribeError("the model returned no text")

    caption = _clip(text)
    logger.info("Caption in %.1f s: %r", elapsed, caption)
    return caption, elapsed
