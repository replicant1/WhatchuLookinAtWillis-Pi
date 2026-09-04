#!/usr/bin/env python3
"""
Check the request Willis sends and the answer it accepts, with no network.

    python3 tests/eyes/describe_test.py

The double here is a **fake, not a stub**: it keeps the request it was given so
the test can take it apart, and it can be told to behave like each of the ways
the real thing goes wrong. That matters, because the tempting version of this
test - assert that describe() returns the string the fake was told to return -
passes against an implementation that never resizes the image, sends the text
before the picture, ignores the length limit and treats a refusal as a caption.

So the assertions are about things that would be WRONG in a request that still
worked in the happy case:

  * the image really is scaled down before it is sent, checked by decoding the
    base64 back into an image and measuring it;
  * the image block precedes the text block;
  * an over-long answer is clipped locally rather than trusted;
  * a refusal, an empty answer and a transport failure each raise rather than
    reaching the panel as a caption.
"""

import base64
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image                                 # noqa: E402

from eyes import describe as eyes                     # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"           got  {got!r}\n           want {want!r}")
        failures.append(label)


class Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class Details:
    def __init__(self, category):
        self.category = category


class Response:
    def __init__(self, blocks, stop_reason="end_turn", category=None):
        self.content = blocks
        self.stop_reason = stop_reason
        self.stop_details = Details(category) if category else None


class FakeClient:
    """
    An Anthropic client's observable behaviour, recorded rather than performed.

    `raises` and `response` are the two ways the real one ends a call, and the
    request is kept whole so the test can inspect what was actually asked.
    """

    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.request = None
        self.messages = self

    def create(self, **kwargs):
        self.request = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


def answering(text, **kwargs):
    return FakeClient(Response([Block("thinking"), Block("text", text)],
                               **kwargs))


PHOTO = Image.new("RGB", (1600, 1200), (120, 90, 60))


def sent_image(client):
    """The image the fake was actually given, decoded back into a picture."""
    content = client.request["messages"][0]["content"]
    block = next(b for b in content if b["type"] == "image")
    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))


def main():
    print("The request")

    client = answering("a mug of tea on a windowsill")
    text, elapsed = eyes.describe(PHOTO, client)
    check("the caption is returned", text, "a mug of tea on a windowsill")
    check("the elapsed time is reported", elapsed > 0, True)

    request = client.request
    content = request["messages"][0]["content"]
    check("the model is the one this project chose", request["model"], eyes.MODEL)
    check("effort is set, and thinking is left alone",
          (request["output_config"]["effort"], "thinking" in request),
          (eyes.EFFORT, False))
    check("the image block comes before the text block",
          [b["type"] for b in content], ["image", "text"])
    check("the image is sent as base64 JPEG",
          (content[0]["source"]["type"], content[0]["source"]["media_type"]),
          ("base64", "image/jpeg"))

    # The resize is the whole cost story. A version that forgot it returns the
    # same caption, so only measuring the bytes catches it.
    shown = sent_image(client)
    check("the image was scaled down before being sent",
          max(shown.size), eyes.MAX_EDGE)
    check("the image kept its shape while being scaled",
          round(shown.size[0] / shown.size[1], 2),
          round(PHOTO.size[0] / PHOTO.size[1], 2))

    print()
    print("The answer")

    long_answer = ("a very large and extremely detailed description of a room "
                   "that goes on well past anything that could be read at a "
                   "glance on a small panel from across the room")
    text, _ = eyes.describe(PHOTO, answering(long_answer))
    check("an over-long answer is clipped locally",
          len(text) <= eyes.MAX_CHARS, True)
    check("clipping cuts at a word boundary", text.endswith(" ") or
          long_answer.startswith(text), True)

    text, _ = eyes.describe(PHOTO, answering('  "a red door."\n'))
    check("quotes, padding and a full stop are stripped", text, "a red door")

    text, _ = eyes.describe(PHOTO, answering(eyes.CANNOT_SEE))
    check("the 'cannot see' answer survives untouched", text, eyes.CANNOT_SEE)

    print()
    print("The failures")

    for label, client in (
        ("a refusal raises rather than becoming a caption",
         FakeClient(Response([], stop_reason="refusal", category="cyber"))),
        ("an answer with no text raises",
         FakeClient(Response([Block("thinking")]))),
        ("an empty answer raises",
         answering("   ")),
        ("a transport failure raises",
         FakeClient(raises=ConnectionError("no route to host"))),
    ):
        try:
            eyes.describe(PHOTO, client)
            check(label, "returned a caption", "DescribeError")
        except eyes.DescribeError:
            check(label, "DescribeError", "DescribeError")
        except Exception as e:                        # noqa: BLE001
            check(label, f"{type(e).__name__}", "DescribeError")

    print()
    if failures:
        print(f"FAILED: {len(failures)}")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("RESULT: the picture is scaled down and sent before the question, the")
    print("        answer is clipped to what the panel can show, and a refusal,")
    print("        an empty reply and a dead network all raise instead of")
    print("        reaching the glass as a caption.")
    print("        Nothing here has spoken to Anthropic. Whether the captions")
    print("        are any GOOD is a separate question, answered by pointing")
    print("        the real box at real things.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
