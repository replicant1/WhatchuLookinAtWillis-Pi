"""
Rotary encoder input: a KY-040 knob on two GPIO pins.

Wiring as fitted (BCM numbering):

    CLK -> GPIO 19       + -> 3.3V
    DT  -> GPIO 26     GND -> GND
    SW  -> GPIO 6

The module carries its own pull-up resistors on CLK and DT, which is why those
two pins read high at rest even though this chip defaults GPIO 9-27 to pull-down.
An internal pull-up is enabled anyway so the decoder still behaves if the
module's 3.3V line is ever disturbed - an unconnected pin would otherwise float
and invent transitions.

SW gets no pull-up on the module, so it depends on the internal one entirely.
GPIO 6 was chosen for it because this chip defaults GPIO 0-8 to pull-*up*: the
pin therefore idles high from power-on, and a switch wired to ground can never
read as held down during the window between boot and this code configuring it.

Two decisions here are worth stating, because both were measured on this
hardware rather than assumed, on the AsciiArt project this module came from:

Bounce is heavy.  Turning the knob about twenty clicks produced 453 edges that
reduced to 88 once repeats inside a millisecond were dropped - roughly a 5:1
ratio.  Any decoder that simply counts edges, or that reads the partner pin at
each edge, will therefore report bursts of phantom movement.  So this uses a
transition table instead of edge counting: it tracks where the shaft is within
the quadrature cycle and emits a step only on a *complete* cycle.  A contact
rattling between two adjacent states drives the table back and forth over
transitions that emit nothing, so bounce costs CPU and nothing else.  This is
the property being relied on, and encoder_test.py holds it to it by feeding in
recorded-style bounce and demanding an exact step count.

One detent is one full cycle.  Those 88 edges spanned about twenty clicks, so
roughly 4.4 edges per click - a full four-state cycle per detent.  Emitting per
cycle therefore gives one event per click, which is what a knob should
feel like.  An encoder wired for half-step detents would want R_HALF instead.
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Decoder states.  The name says how far round a cycle the shaft has got and
# in which direction, so an unexpected transition can always fall back to START
# without the caller ever seeing a step.
_START = 0x0
_CW_FINAL = 0x1
_CW_BEGIN = 0x2
_CW_NEXT = 0x3
_CCW_BEGIN = 0x4
_CCW_FINAL = 0x5
_CCW_NEXT = 0x6

# Flags OR-ed into the next state when a full cycle completes.
_DIR_CW = 0x10
_DIR_CCW = 0x20
_STATE_MASK = 0x07
_DIR_MASK = 0x30

# Row = current state, column = the new (clk, dt) pin pair as (clk << 1) | dt.
# Every row covers all four pin combinations, so no input can fall through:
# a transition that does not belong to the direction being tracked routes back
# to _START and emits nothing.  That total coverage is what makes the table
# bounce-proof rather than merely bounce-tolerant.
_TABLE = (
    # _START: sitting at rest with both pins high; one pin dropping starts a
    # cycle and which one it is decides the direction.
    (_START,     _CW_BEGIN,  _CCW_BEGIN, _START),
    # _CW_FINAL: three quarters round clockwise; both pins high again completes
    # it and is the only transition in the whole table that emits a CW step.
    (_CW_NEXT,   _START,     _CW_FINAL,  _START | _DIR_CW),
    # _CW_BEGIN
    (_CW_NEXT,   _CW_BEGIN,  _START,     _START),
    # _CW_NEXT
    (_CW_NEXT,   _CW_BEGIN,  _CW_FINAL,  _START),
    # _CCW_BEGIN
    (_CCW_NEXT,  _START,     _CCW_BEGIN, _START),
    # _CCW_FINAL: the mirror of _CW_FINAL, and the only CCW step in the table.
    (_CCW_NEXT,  _CCW_FINAL, _START,     _START | _DIR_CCW),
    # _CCW_NEXT
    (_CCW_NEXT,  _CCW_FINAL, _CCW_BEGIN, _START),
)


class QuadratureDecoder:
    """
    Pin levels in, detents out.  No GPIO, no threads, no clock.

    Kept free of hardware on purpose: this is the part that can be wrong in a
    way nobody notices until the knob feels bad, so it has to be testable on a
    machine with no encoder attached.
    """

    def __init__(self):
        self._state = _START

    def feed(self, clk, dt):
        """
        Advance the state machine.

        Args:
            clk: Current CLK level, 0 or 1.
            dt: Current DT level, 0 or 1.

        Returns:
            +1 for one detent clockwise, -1 for one anticlockwise, 0 for a
            transition that does not complete a cycle - which is most of them,
            and all of the ones bounce produces.
        """
        entry = _TABLE[self._state & _STATE_MASK][(clk << 1) | dt]
        self._state = entry & _STATE_MASK
        direction = entry & _DIR_MASK
        if direction == _DIR_CW:
            return 1
        if direction == _DIR_CCW:
            return -1
        return 0


class RotaryEncoder:
    """
    A KY-040 on two GPIO pins, read through lgpio's edge callbacks.

    Callbacks arrive on lgpio's own thread, so steps are accumulated under a
    lock and the render loop collects them with take().  Nothing here blocks
    that loop, and a knob nobody touches costs it nothing at all.
    """

    def __init__(self, clk=19, dt=26, sw=6, reverse=False, chip=0):
        """
        Args:
            clk: BCM pin for CLK.
            dt: BCM pin for DT.
            sw: BCM pin for the push switch, or a negative number for a knob
                whose switch is not wired.  Harmless to leave at the default
                when it is unwired: the pin idles high on its pull-up and
                simply never reports anything.
            reverse: Swap which way is positive.  Whether clockwise counts as
                forward depends on which of the two pins the user called CLK
                when wiring, and that is a coin toss no amount of code can
                settle - so it is a flag rather than a guess.
            chip: gpiochip number.
        """
        self.clk = clk
        self.dt = dt
        self.sw = sw
        self.reverse = reverse
        self.chip = chip

        self._decoder = QuadratureDecoder()
        self._lock = threading.Lock()
        self._steps = 0
        self._presses = 0
        self._levels = {}
        self._handle = None
        self._callbacks = []

        self.detents = 0        # total movement seen, for the log
        self.presses = 0        # total presses seen, likewise

    def start(self):
        """
        Claim the pins and begin watching.  Raises if the pins are unavailable.

        Deliberately allowed to raise: the caller decides whether a missing
        knob is fatal, exactly as it does for the LCD.
        """
        import lgpio                     # imported late: only exists on the Pi

        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(self.chip)

        # The rotation pins and the switch want very different debounce times,
        # which is the whole reason this is not one loop. 200us on CLK/DT is an
        # optimisation only - correctness rests on the transition table - and is
        # kept short so a fast turn still gets through intact. The switch has no
        # such safety net, and nothing about a button needs sub-millisecond
        # resolution, so it is debounced hard enough that one press is one
        # event.
        for pin in (self.clk, self.dt):
            lgpio.gpio_claim_alert(self._handle, pin, lgpio.BOTH_EDGES,
                                   lgpio.SET_PULL_UP)
            self._debounce(pin, 200)
            self._levels[pin] = lgpio.gpio_read(self._handle, pin)

        if self.sw >= 0:
            lgpio.gpio_claim_alert(self._handle, self.sw, lgpio.BOTH_EDGES,
                                   lgpio.SET_PULL_UP)
            self._debounce(self.sw, 5000)
            self._levels[self.sw] = lgpio.gpio_read(self._handle, self.sw)

        for pin in self._pins():
            self._callbacks.append(
                lgpio.callback(self._handle, pin, lgpio.BOTH_EDGES,
                               self._on_edge))

        logger.info("Rotary encoder on CLK=GPIO%d DT=GPIO%d SW=%s%s",
                    self.clk, self.dt,
                    f"GPIO{self.sw}" if self.sw >= 0 else "not wired",
                    " (reversed)" if self.reverse else "")
        return self

    def _pins(self):
        """Every pin this encoder has claimed."""
        pins = [self.clk, self.dt]
        if self.sw >= 0:
            pins.append(self.sw)
        return pins

    def _debounce(self, pin, micros):
        """Ask the kernel to ignore edges closer together than `micros`."""
        try:
            self._lgpio.gpio_set_debounce_micros(self._handle, pin, micros)
        except AttributeError:
            pass                     # older lgpio; the table copes anyway

    def _on_edge(self, _chip, gpio, level, _tick):
        # Level 2 is lgpio's watchdog tick rather than a real edge.
        if level not in (0, 1):
            return
        self._levels[gpio] = level

        if gpio == self.sw:
            # The switch shorts to ground, so the press is the falling edge.
            # Counting only that makes one press one event however long it is
            # held down, and means releasing the knob does nothing.
            if level == 0:
                with self._lock:
                    self._presses += 1
                    self.presses += 1
            return

        step = self._decoder.feed(self._levels[self.clk],
                                  self._levels[self.dt])
        if not step:
            return
        if self.reverse:
            step = -step
        with self._lock:
            self._steps += step
            self.detents += 1

    def take(self):
        """
        Net detents since the last call, and reset.

        Net rather than a list of events: two clicks one way and two back is
        no change, and nothing downstream should be made to act four times to
        say so.  Returns 0 when the knob has not moved, which is the usual case
        and costs the render loop only a lock.
        """
        with self._lock:
            steps, self._steps = self._steps, 0
        return steps

    def take_presses(self):
        """
        How many times the knob was pressed since the last call, and reset.

        A count rather than a flag, so a caller can tell one press from three
        if it ever wants to - though the current one does not, since pressing
        twice asks for the same thing twice.
        """
        with self._lock:
            presses, self._presses = self._presses, 0
        return presses

    def stop(self):
        """Release the pins.  Safe to call twice, and never raises."""
        for callback in self._callbacks:
            try:
                callback.cancel()
            except Exception:
                pass
        self._callbacks = []
        if self._handle is not None:
            try:
                for pin in self._pins():
                    self._lgpio.gpio_free(self._handle, pin)
                self._lgpio.gpiochip_close(self._handle)
            except Exception as e:
                logger.error("Releasing the encoder failed: %s", e)
            self._handle = None
        logger.info("Rotary encoder stopped: %d detents, %d presses",
                    self.detents, self.presses)
