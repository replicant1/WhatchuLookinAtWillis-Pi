"""
The camera, streaming - a frame whenever one is asked for.

This started as a still camera that took one photograph per button press, and
became this when the panel gained a live preview. The difference is the
configuration: a *video* configuration streams continuously and hands back the
newest frame in milliseconds, where a still configuration re-runs the whole
capture pipeline and takes the better part of a second. A preview cannot be
built on the second kind.

**Dropping to 640x480 costs nothing.** It looks like a quality sacrifice for
the sake of the preview and it is not: src/eyes/describe.py scales whatever it
is given so the longest edge is 512 pixels, so a 1024x768 capture and a 640x480
one both arrive at the model as 512x384. The bytes that used to be captured
were being thrown away one function later.

The camera is opened once and held for the life of the process. Opening it
costs seconds - libcamera's initialisation dominated start-up when this was
measured - and holding it makes contention with any other camera program
explicit at start-up rather than intermittent at the moment of a press.
"""

import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 4:3, to match both the sensor and the panel's 320x240, so nothing is cropped.
# Big enough that the analysis image is unaffected (see the module docstring),
# small enough that the per-frame copy and rescale stay inside a preview's
# budget on a Zero 2.
DEFAULT_SIZE = (640, 480)


class Camera:
    """The camera, held open, handing out its newest frame on demand."""

    def __init__(self, size=DEFAULT_SIZE, rotation=0, picamera2=None):
        """
        Args:
            size: capture size in pixels, width by height.
            rotation: degrees to rotate each frame, 0/90/180/270. Which value
                is right is a property of how the camera is mounted in the
                enclosure and cannot be derived here - point it at something
                with a clear top and look.
            picamera2: the Picamera2 class, for tests. Imported late by
                default because it only exists on the Pi.
        """
        self.size = size
        self.rotation = rotation % 360
        self._picamera2 = picamera2
        self._camera = None

    def start(self):
        """Open and configure the camera. Raises if it is already in use."""
        if self._camera is not None:
            return self

        if self._picamera2 is None:
            from picamera2 import Picamera2       # only exists on the Pi
            self._picamera2 = Picamera2

        self._camera = self._picamera2()
        # A video configuration, not a still one. See the module docstring:
        # this is the difference between a preview and a slideshow.
        config = self._camera.create_video_configuration(
            main={"size": self.size, "format": "RGB888"})
        self._camera.configure(config)
        self._camera.start()

        actual = self._camera.camera_configuration()["main"]["size"]
        if tuple(actual) != tuple(self.size):
            # The ISP rounds to sizes it can produce. Say so rather than
            # letting a silently different size surprise the caller later.
            logger.info("Camera gave %sx%s, not the %sx%s asked for",
                        actual[0], actual[1], self.size[0], self.size[1])
            self.size = tuple(actual)
        logger.info("Camera streaming at %sx%s", self.size[0], self.size[1])
        return self

    def grab(self):
        """
        The newest frame.

        Returns:
            A PIL RGB Image, rotated as configured.

        Raises:
            RuntimeError: if start() has not been called.
        """
        if self._camera is None:
            raise RuntimeError("camera not started")

        frame = self._camera.capture_array()

        # picamera2's "RGB888" hands back channels in B, G, R order. This is a
        # long-standing quirk of the format naming and not a bug here;
        # reversing the last axis puts it right. Confirmed on 4 Sep 2026 by
        # pointing the camera at a tomato against a blue sky - the sky is the
        # half that proves it, since a red/blue swap turns a blue sky orange
        # and nothing describes an orange sky as blue.
        frame = frame[:, :, ::-1]

        image = Image.fromarray(np.ascontiguousarray(frame), "RGB")
        if self.rotation:
            image = image.rotate(self.rotation, expand=True)
        return image

    def stop(self):
        """Release the camera. Safe to call twice, and never raises."""
        if self._camera is None:
            return
        try:
            self._camera.stop()
            self._camera.close()
        except Exception as e:                   # noqa: BLE001 - cleanup path
            logger.error("Releasing the camera failed: %s", e)
        finally:
            self._camera = None
            logger.info("Camera released")

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
