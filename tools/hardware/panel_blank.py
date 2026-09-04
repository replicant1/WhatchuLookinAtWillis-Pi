#!/usr/bin/env python3
"""
Blank the panel and hold the backlight off. For when something left it lit.

    python3 tools/hardware/panel_blank.py

Willis and AsciiArt both blank the panel on the way out, but only if they got
to run their cleanup - a process that was killed, or one that died in the
middle of a frame, leaves the last image on the glass and the backlight on.
Nothing reclaims it, because nothing owns it: the data pins are driven through
RPi.GPIO, which writes the pad registers directly rather than holding a kernel
line, so the levels simply stay where the dead process left them.

The backlight pin is driven low and deliberately NOT released, for the reason
given in ili9341.close(): handing it back makes it an input, and the module's
own pull-up then relights the panel.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from panel.ili9341 import ILI9341                     # noqa: E402


def main():
    panel = ILI9341(landscape=True).init()
    panel.fill(0x0000)
    panel.close()
    print("Panel blanked and the backlight driven low.")
    print("It should now be dark. Nothing here can see it - look.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
