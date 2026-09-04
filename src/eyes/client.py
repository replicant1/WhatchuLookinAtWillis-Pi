"""
The Anthropic client, and where its key comes from.

Kept apart from describe.py so that "can this box talk to the model at all" is
a question with its own answer, checkable at start-up rather than at the moment
somebody presses the button.
"""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Outside the project, and outside anything git or sync.sh will ever look at.
# A key inside the working tree is one careless `git add -A` from being public.
#
# ~/.config/<app>/api_key matches the convention the AsciiArt project on this
# same machine already uses (~/.config/asciicam/api_key). Deliberately a
# separate file rather than a shared one: two projects reading one key means
# rotating it breaks whichever is not being worked on that day, and the
# saving is one `cp`.
KEY_FILE = Path.home() / ".config" / "willis" / "api_key"

TIMEOUT_SECONDS = 60
MAX_RETRIES = 2

_lock = threading.Lock()
_shared = None


def api_key():
    """
    The key, from the environment or from KEY_FILE.

    Returns None rather than raising.  A box with no key should still light its
    panel and say so; refusing to start would turn a missing credential into
    what looks like dead hardware.
    """
    from_env = os.environ.get("ANTHROPIC_API_KEY")
    if from_env:
        return from_env.strip()
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        return None
    return key or None


class NoKey(RuntimeError):
    """No API key was found in the environment or on disk."""


def client(key=None):
    """
    The shared client, built on first use.

    One client, built once, for two reasons - the second of which is the one
    that bites.  Building costs the better part of a second on a Zero 2; and
    building several *at the same time* is not thread-safe, because the SDK
    builds its response models lazily on first construction.  Willis is
    single-threaded through this path today, so only the first reason applies -
    the lock is here so that stays true if a second caller ever appears.

    Raises:
        NoKey: if no key can be found.
    """
    global _shared
    import anthropic                             # ~11 s on a Zero 2; see warm()

    if key:
        return anthropic.Anthropic(api_key=key, timeout=TIMEOUT_SECONDS,
                                   max_retries=MAX_RETRIES)

    with _lock:
        if _shared is None:
            resolved = api_key()
            if not resolved:
                raise NoKey(f"set ANTHROPIC_API_KEY or write a key to {KEY_FILE}")
            _shared = anthropic.Anthropic(api_key=resolved,
                                          timeout=TIMEOUT_SECONDS,
                                          max_retries=MAX_RETRIES)
    return _shared


def warm():
    """
    Pay for `import anthropic` now, while the start-up screen is showing.

    The import alone takes on the order of ten seconds on this hardware.  Paid
    lazily, that cost lands on the first button press, where it looks exactly
    like a very slow model.  Paid here it lands during boot, where there is
    already a splash screen explaining that the box is waking up.

    Never raises: a box that cannot import the SDK should still take
    photographs and say why it cannot describe them.
    """
    try:
        import anthropic                         # noqa: F401
        logger.info("Anthropic SDK imported")
        return True
    except Exception as e:                       # noqa: BLE001 - best effort
        logger.error("Could not import the Anthropic SDK: %s", e)
        return False
