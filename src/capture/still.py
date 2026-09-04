"""
One camera frame, on demand.

Deliberately not the shape of a video pipeline.  There is no capture thread, no
queue and no frame rate: Willis takes a photograph when a person presses a
button, perhaps once a minute, and everything about this module is simpler for
admitting that.

The camera is opened once at start-up and held for the life of the process,
rather than opened per shot.  Two reasons:

  * Opening it costs seconds.  libcamera's initialisation dominated start-up
    when this was measured on a Zero 2 - the panel would sit blank while the
    ISP configured itself, which is exactly the "is it broken?" pause the
    start-up screen exists to prevent.
  * Holding it makes the contention with any other camera program *explicit and
    immediate* rather than intermittent.  A program that grabs the camera only
    during a shot would appear to work, then fail one press in ten with a
    confusing error.  Failing at start-up is the better failure.

The consequence is that Willis and any other camera user cannot both run - see
README.md.  That is a property of the hardware, not a limitation of this file.
"""

import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 1024x768 rather than the sensor's full resolution.  The image is going to be
# scaled down hard before it is sent anywhere - see src/eyes/describe.py, which
# resizes to 512 on the long edge - so capturing more pixels buys nothing and
# costs ISP time and memory on a machine that has little of either.  4:3 to
# match both the sensor and the panel's 320x240, so nothing is cropped.
DEFAULT_SIZE = (1024, 768)


class Still:
    """The camera, held open, handing out one PIL image at a time."""

    def __init__(self, size=DEFAULT_SIZE, rotation=0, picamera2=None):
        """
        Args:
            size: capture size in pixels, width by height.
            rotation: degrees to rotate the captured frame, 0/90/180/270.
                Which value is right is a property of how the camera is
                mounted in the enclosure and cannot be derived here - point it
                at something with a clear top and look.
            picamera2: the Picamera2 class, for tests.  Imported late by
                default because it only exists on the Pi.
        """
        self.size = size
        self.rotation = rotation % 360
        self._picamera2 = picamera2
        self._camera = None

    def start(self):
        """Open and configure the camera.  Raises if it is already in use."""
        if self._camera is not None:
            return self

        if self._picamera2 is None:
            from picamera2 import Picamera2       # only exists on the Pi
            self._picamera2 = Picamera2

        self._camera = self._picamera2()
        config = self._camera.create_still_configuration(
            main={"size": self.size, "format": "RGB888"})
        self._camera.configure(config)
        self._camera.start()

        actual = self._camera.camera_configuration()["main"]["size"]
        if tuple(actual) != tuple(self.size):
            # The ISP rounds to sizes it can produce.  Say so rather than
            # letting a silently different size surprise the caller later.
            logger.info("Camera gave %sx%s, not the %sx%s asked for",
                        actual[0], actual[1], self.size[0], self.size[1])
            self.size = tuple(actual)
        logger.info("Camera open at %sx%s", self.size[0], self.size[1])
        return self

    def grab(self):
        """
        Take one photograph.

        Returns:
            A PIL RGB Image, rotated as configured.

        Raises:
            RuntimeError: if start() has not been called.
        """
        if self._camera is None:
            raise RuntimeError("camera not started")

        frame = self._camera.capture_array()

        # picamera2's "RGB888" hands back channels in B, G, R order.  This is a
        # long-standing quirk of the format naming and not a bug here; reversing
        # the last axis puts it right.  NOTHING IN SOFTWARE CAN CONFIRM THIS -
        # a swapped image is still a valid image, and the model will happily
        # describe a blue-tinted scene without complaint.  It is checked by
        # pointing the camera at something known to be red; see
        # tests/panel/panel_selftest.py.
        frame = frame[:, :, ::-1]

        image = Image.fromarray(np.ascontiguousarray(frame), "RGB")
        if self.rotation:
            image = image.rotate(self.rotation, expand=True)
        return image

    def stop(self):
        """Release the camera.  Safe to call twice, and never raises."""
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
