"""
ILI9341 2.4" SPI LCD driver (240x320, RGB565).

Self-contained on purpose: the manufacturer's library lives outside the project
in ~/LCD_Module_code and is not something the app should depend on.  The init
sequence below is the one from that library, which is known to light this
particular panel, so it is copied rather than re-derived from the datasheet.

Wiring, taken from the manufacturer's own 2inch4_LCD_test.py (RST/DC/BL) and
verified working by that test:

    VCC -> 3.3V        SDI/MOSI -> GPIO 10
    GND -> GND         SCK      -> GPIO 11
    CS  -> GPIO 8      RESET    -> GPIO 27
    DC  -> GPIO 25     LED/BL   -> GPIO 18   (PWM dimmable)

CS is driven by the SPI peripheral itself (CE0), not by this code.

Throughput note: /sys/module/spidev/parameters/bufsiz is 4096 on this Pi, so a
full frame is 153,600 bytes = 38 writes.  Every write is a syscall plus a DMA
setup, and that dominates the frame time far more than the clock rate does.
Raise it with `spidev.bufsiz=65536` on the kernel command line if the refresh
rate ever matters more than the memory it costs.
"""

import logging
import time

import numpy as np
import spidev

logger = logging.getLogger(__name__)

# Panel geometry in its native (portrait) orientation.
NATIVE_WIDTH = 240
NATIVE_HEIGHT = 320

# MADCTL (0x36) values.  Bit 5 (MV) swaps rows and columns; the other bits flip
# the scan direction so the image is not mirrored once swapped.
MADCTL_PORTRAIT = 0x08    # 240x320, ribbon at the bottom
MADCTL_LANDSCAPE = 0x78   # 320x240, rotated clockwise

# The SPI driver refuses a write longer than this in one go.
SPI_CHUNK = 4096


class ILI9341:
    """Drives the panel over SPI, taking whole frames as PIL images."""

    def __init__(self, dc=25, rst=27, bl=18, bus=0, device=0,
                 spi_freq=40_000_000, bl_freq=1000, landscape=False):
        """
        Args:
            dc: BCM pin for data/command select.
            rst: BCM pin for hardware reset.
            bl: BCM pin for the backlight, driven as PWM so it can be dimmed.
            bus, device: which /dev/spidev<bus>.<device> to open.
            spi_freq: SPI clock in Hz. 40 MHz is what the manufacturer's code
                uses and the panel tolerates it; drop it if wiring is long or
                on a breadboard.
            bl_freq: backlight PWM frequency in Hz.
            landscape: True for 320x240 with the long edge horizontal.
        """
        import RPi.GPIO as GPIO      # imported late: only exists on the Pi

        self.GPIO = GPIO
        self.dc = dc
        self.rst = rst
        self.bl = bl
        self.landscape = landscape

        self.width = NATIVE_HEIGHT if landscape else NATIVE_WIDTH
        self.height = NATIVE_WIDTH if landscape else NATIVE_HEIGHT
        self._madctl = MADCTL_LANDSCAPE if landscape else MADCTL_PORTRAIT

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in (self.dc, self.rst, self.bl):
            GPIO.setup(pin, GPIO.OUT)

        self.spi = spidev.SpiDev(bus, device)
        self.spi.max_speed_hz = spi_freq
        self.spi.mode = 0b00

        self._pwm = GPIO.PWM(self.bl, bl_freq)
        # Dark until a caller asks otherwise. The panel's frame memory holds
        # whatever was in it before - undefined after a reset, and often near
        # white - so lighting it here shows a bright flash of garbage for the
        # ~200 ms that init() and the first fill() take. Callers turn it on
        # once there is something on the glass worth seeing.
        self._pwm.start(0)

        logger.info("ILI9341 on spidev%d.%d at %d Hz, %dx%d %s",
                    bus, device, spi_freq, self.width, self.height,
                    "landscape" if landscape else "portrait")

    # ---- low level ------------------------------------------------------

    def _command(self, cmd):
        self.GPIO.output(self.dc, self.GPIO.LOW)
        self.spi.writebytes([cmd])

    def _data(self, *values):
        self.GPIO.output(self.dc, self.GPIO.HIGH)
        self.spi.writebytes(list(values))

    def _write_pixels(self, buf):
        """Push an already-packed RGB565 byte buffer as pixel data."""
        self.GPIO.output(self.dc, self.GPIO.HIGH)
        for start in range(0, len(buf), SPI_CHUNK):
            self.spi.writebytes2(buf[start:start + SPI_CHUNK])

    def reset(self):
        """Pulse the hardware reset line."""
        self.GPIO.output(self.rst, self.GPIO.HIGH)
        time.sleep(0.01)
        self.GPIO.output(self.rst, self.GPIO.LOW)
        time.sleep(0.01)
        self.GPIO.output(self.rst, self.GPIO.HIGH)
        time.sleep(0.12)      # datasheet wants 120 ms before the first command

    def init(self):
        """Run the power-on sequence and turn the display on."""
        self.reset()

        self._command(0x11)                     # sleep out
        time.sleep(0.12)

        self._command(0xCF); self._data(0x00, 0xC1, 0x30)
        self._command(0xED); self._data(0x64, 0x03, 0x12, 0x81)
        self._command(0xE8); self._data(0x85, 0x00, 0x79)
        self._command(0xCB); self._data(0x39, 0x2C, 0x00, 0x34, 0x02)
        self._command(0xF7); self._data(0x20)
        self._command(0xEA); self._data(0x00, 0x00)

        self._command(0xC0); self._data(0x1D)   # power control 1, VRH[5:0]
        self._command(0xC1); self._data(0x12)   # power control 2, SAP/BT
        self._command(0xC5); self._data(0x33, 0x3F)   # VCOM control 1
        self._command(0xC7); self._data(0x92)         # VCOM control 2

        self._command(0x3A); self._data(0x55)   # COLMOD: 16 bits/pixel (RGB565)
        self._command(0x36); self._data(self._madctl)
        self._command(0xB1); self._data(0x00, 0x12)   # frame rate
        self._command(0xB6); self._data(0x0A, 0xA2)   # display function
        self._command(0x44); self._data(0x02)         # tear scanline

        self._command(0xF2); self._data(0x00)   # 3-gamma disable
        self._command(0x26); self._data(0x01)   # gamma curve 1
        self._command(0xE0)                     # positive gamma
        self._data(0x0F, 0x22, 0x1C, 0x1B, 0x08, 0x0F, 0x48, 0xB8,
                   0x34, 0x05, 0x0C, 0x09, 0x0F, 0x07, 0x00)
        self._command(0xE1)                     # negative gamma
        self._data(0x00, 0x23, 0x24, 0x07, 0x10, 0x07, 0x38, 0x47,
                   0x4B, 0x0A, 0x13, 0x06, 0x30, 0x38, 0x0F)

        self._command(0x29)                     # display on
        time.sleep(0.02)
        return self

    def set_window(self, x0, y0, x1, y1):
        """
        Select the rectangle that following pixel data fills.

        Bounds are inclusive, so a full screen is (0, 0, width-1, height-1).
        """
        self._command(0x2A)                     # column address set
        self._data(x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)
        self._command(0x2B)                     # page address set
        self._data(y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)
        self._command(0x2C)                     # memory write

    # ---- drawing --------------------------------------------------------

    def fill(self, colour):
        """
        Flood the whole screen with one RGB565 value.

        Args:
            colour: 16-bit RGB565, e.g. 0xF800 for red.
        """
        hi, lo = colour >> 8, colour & 0xFF
        row = bytes([hi, lo]) * self.width
        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._write_pixels(row * self.height)

    def show_packed(self, packed):
        """
        Push a whole frame that is already in the panel's own RGB565 layout.

        The fast path for a caller that builds its pixels with numpy: it skips
        the PIL round trip and the conversion in `show`.

        Args:
            packed: width*height*2 bytes, high byte of each pixel first.

        Raises:
            ValueError: if the buffer is not exactly one frame.
        """
        expected = self.width * self.height * 2
        if len(packed) != expected:
            raise ValueError(f"got {len(packed)} bytes, panel wants {expected}")

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._write_pixels(packed)

    def show(self, image):
        """
        Draw a PIL RGB image, which must already be the panel's size.

        Raises:
            ValueError: if the image is not exactly width x height.
        """
        if image.size != (self.width, self.height):
            raise ValueError(
                f"image is {image.size[0]}x{image.size[1]}, "
                f"panel is {self.width}x{self.height}")

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._write_pixels(pack_rgb565(image))

    def backlight(self, percent):
        """Set backlight brightness, 0-100."""
        self._pwm.ChangeDutyCycle(max(0, min(100, percent)))

    def close(self):
        """
        Blank the panel, and leave it blank, releasing SPI and the data pins.

        The backlight pin is deliberately NOT released. Handing it back with
        GPIO.cleanup() makes it an input, and the module's own pull-up then
        relights the backlight - so the panel sat there uniformly lit after
        every clean shutdown, which is exactly what a blanking routine is meant
        to prevent. Left as an output driving low it stays dark, and it stays
        that way after this process exits because RPi.GPIO writes the pad
        registers directly rather than holding a kernel line.
        """
        try:
            self._command(0x28)                 # display off
        except OSError:
            pass                                # SPI already gone; keep going
        self._pwm.stop()
        self.spi.close()
        self.GPIO.output(self.bl, self.GPIO.LOW)
        self.GPIO.cleanup([self.dc, self.rst])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def pack_rgb565(image):
    """
    Convert a PIL RGB image to big-endian RGB565 bytes.

    The panel wants the high byte first, which is the opposite of what numpy
    produces on this little-endian Pi, hence the explicit byte split rather
    than a view-cast of a uint16 array.
    """
    rgb = np.asarray(image, dtype=np.uint8)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    packed = np.empty(rgb.shape[:2] + (2,), dtype=np.uint8)
    packed[..., 0] = (r & 0xF8) | (g >> 5)          # RRRRRGGG
    packed[..., 1] = ((g << 3) & 0xE0) | (b >> 3)   # GGGBBBBB
    return packed.tobytes()


def rgb565(r, g, b):
    """Pack 8-bit RGB into a single 16-bit RGB565 value."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
