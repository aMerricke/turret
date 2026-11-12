# =============================================================================
# ENME 441 - Lab 8: Coordinated two-axis motion
#
# This file is stepper_async_timer_shifter.py with added content for Lab 8.
# Everything not marked YOUR CODE HERE is working code -- read it before you
# start, especially _tick() and _arm() to understand what it does, but do not 
# edit it! Do not change any function or method name.
#
# Your only edits should be in the new _move_group() method.
# 
# =============================================================================

import time
import asyncio
import micropython
from machine import Timer, disable_irq, enable_irq

micropython.alloc_emergency_exception_buf(100)


class Stepper:

    # --- Class attributes ----------------------------------------------------
    #Individual bit sequences go thru shift register to driver chip

    SEQ = [0b0001, 0b0011, 0b0010, 0b0110,
           0b0100, 0b1100, 0b1000, 0b1001]

    STEPS_PER_REV = 4096 # half steps
    STEP_FREQ = 833 # Hz
    INVERT = False

    # Class attributes, applies to entire class

    _shifter = None
    _word = 0
    _num_bits = 0
    _count = 0
    _timer = None
    _motors = [] # Holds all motors that are created
    

 # A lock is to avoid modifying bits in the binary word,
    #stuff only occurs when lock is released
    def __init__(self, shifter):
        self.offset = 4 * Stepper._count
        self.mask = 0b1111 << self.offset
        Stepper._count += 1
        Stepper._num_bits = ((Stepper._count * 4 + 7) // 8) * 8
        Stepper._shifter = shifter

        self._phase = 0
        self._position = 0
        self._remaining = 0
        self._dir = 1

        # =====================================================================
        # Lab 8: NEW DDA STATE ATTRIBUTES -- DO NOT EDIT
        # This motor steps on _num of the next _den ticks.  _err is the
        # accumulator.  All three are ints, and only the callback
        # writes _err.
        # =====================================================================
        self._num = 0
        self._den = 1     # never 0 since the callback divides its time by _den
        self._err = 0
        # =====================================================================
        # END NEW CODE BLOCK
        # =====================================================================

        self._done = asyncio.ThreadSafeFlag()
        self._lock = asyncio.Lock()

        Stepper._motors.append(self)

        self.release()
        Stepper.start() #idempotent

    # --- Class-level setup and teardown --------------------------------------

    @classmethod
    def reset(cls):
        cls.stop()
        cls._word = 0
        cls._num_bits = 0
        cls._count = 0
        cls._motors = []
        cls._shifter = None

    @classmethod
    def start(cls, freq=None, timer_id=0):
        if cls._timer is not None:
            return
        cls._timer = Timer(timer_id)
        cls._timer.init(mode=Timer.PERIODIC,
                        freq=freq or cls.STEP_FREQ,
                        callback=cls._tick)

    @classmethod
    def stop(cls):
        if cls._timer is not None:
            cls._timer.deinit()
            cls._timer = None
        for m in cls._motors:
            m.release()

    # --- The timer callback --------------------------------------------------

    @staticmethod
    def _tick(t):
        """
        Timer callback -- runs at STEP_FREQ Hz, forever.

        Unchanged in structure from the starting code: accumulate every moving
        motor's coil pattern into ONE word, then clock the chain out once.  The
        only difference is that _advance() may now decline to move, so the word
        can come back unchanged and the shift can be skipped.
        """
        word = Stepper._word # What is the current word for stepper class? 
        #Holds all 4 bit pairs for every motor thats instantiated
        for m in Stepper._motors:
            if m._remaining:
                word = m._advance(word)

        # A motor that steps always changes its nibble (no two adjacent SEQ
        # entries are equal), so an unchanged word means nothing moved and the
        # ~300 us shift can be skipped.
        if word != Stepper._word:
            Stepper._word = word
            Stepper._shifter.shift(word, Stepper._num_bits)

    def _advance(self, word):
        """
        Advance this motor one half-step and return the updated chain word --
        but only on the ticks this motor is due for one.

        Called only from _tick, and deliberately allocation-free.
        """
        #changes 4 bit pattern to go to the next half step (forward or backward in the sequence)
        s = Stepper.SEQ[self._phase]
        if Stepper.INVERT:
            s = ~s & 0b1111
        word = (word & ~self.mask) | (s << self.offset)

        # =====================================================================
        # Lab 8: NEW DDA GATE CODE - DO NOT EDIT
        # Add this motor's step count to the accumulator once per tick.  Over
        # _den ticks the accumulator crosses _den exactly _num times, and the
        # crossings are as evenly spaced as integers allow.  The final crossing
        # always lands on tick _den, which is what makes every axis finish
        # together.
        # =====================================================================
        self._err += self._num
        if self._err < self._den:
            return word                 # skip this tick for this motor
        self._err -= self._den
        # =====================================================================
        # END NEW CODE BLOCK
        # =====================================================================

        self._phase = (self._phase + self._dir) % 8
        self._position = (self._position + self._dir) % Stepper.STEPS_PER_REV

        self._remaining -= 1
        #are we done with the movement? Decrememnt the amount of steps. Done Flag is set when motor is done moving
        if not self._remaining:
            self._done.set()

        return word

    # --- Position ------------------------------------------------------------

    @property
    def angle(self):
        return self._position * 360 / Stepper.STEPS_PER_REV

    def zero(self):
        self._position = 0

    def release(self):
        self._remaining = 0

        off = 0b1111 if Stepper.INVERT else 0
        Stepper._word = (Stepper._word & ~self.mask) | (off << self.offset)
        if Stepper._shifter is not None:
            Stepper._shifter.shift(Stepper._word, Stepper._num_bits)

    # --- Internals -----------------------------------------------------------

    @staticmethod
    def _steps_for_delta(delta):
        """Relative move, degrees -> (step count, direction)."""
        n = int(round(abs(delta) * Stepper.STEPS_PER_REV / 360))
        return n, (1 if delta >= 0 else -1)

    def _steps_to_angle(self, new_angle):
        """
        Absolute move, degrees -> (step count, direction), shorter way around.

        Pulled out of _go_angle so the coordinated move can use exactly the
        same math.  Call it with the motor's lock held: the answer depends on
        where the shaft is right now.
        """
        target = int(round((new_angle % 360) * Stepper.STEPS_PER_REV / 360))
        target %= Stepper.STEPS_PER_REV

        delta = (target - self._position) % Stepper.STEPS_PER_REV
        if delta > Stepper.STEPS_PER_REV // 2:
            delta -= Stepper.STEPS_PER_REV

        return abs(delta), (1 if delta >= 0 else -1)

    async def _rotate(self, delta):
        async with self._lock:
            await self._arm(*Stepper._steps_for_delta(delta))

    async def _go_angle(self, new_angle):
        async with self._lock:
            await self._arm(*self._steps_to_angle(new_angle))

    async def _arm(self, num_steps, direction):
        """
        Hand `num_steps` to the timer and wait for it to work through them.
        Call with the lock held.

        A solo move is a one-axis group: _num == _den, so the DDA gate is true
        on every tick and this motor steps at the full tick rate.
        """
        if num_steps == 0:
            return

        self._done.clear()

        # =====================================================================
        # Lab 8: UPDATE DDA ATTRIBUTES
        # =====================================================================
        self._err = 0
        self._num = num_steps
        self._den = num_steps
        # =====================================================================
        # END NEW CODE BLOCK
        # =====================================================================

        # ORDER MATTERS -- _remaining arms the callback, so it is written LAST.
        self._dir = direction
        self._remaining = num_steps

        await self._done.wait()


    # =========================================================================
    # Lab 8: NEW METHOD FOR COORDINATING MOTORS -- ADD YOUR CODE HERE
    # =========================================================================
    @classmethod
    async def _move_group(cls, moves, relative):
        """
        Run one coordinated move.  `moves` is a sequence of (motor, degrees),
        interpreted as relative targets if `relative` is True.

        Work through it in this order:
          1. Take every motor's lock, in an order that cannot deadlock no
             matter what order the caller listed them in.
          2. Only now that the locks are held, convert each motor's target
             into a step count and direction.
          3. Decide how many ticks the whole move gets.
          4. Arm every axis, atomically.
          5. Wait for the move to finish, and release the locks even if
             something above raised.
        """
        # =====================================================================
        # YOUR CODE HERE
        # =====================================================================

        motors = []
        degrees = []
        
        # Sort moves by corresponding motor offsets
        sorted_moves = sorted(moves, key=lambda move: move[0].offset)

        for m, d in sorted_moves:
            motors.append(m) # Creates list of every motor
            degrees.append(d) # Creates list of every degrees
    
        # Raise error after detecting duplicates 
        if len(motors) != len(set(motors)):
            raise ValueError("Duplicate Motors!") # We have sorted list of motors and degrees now


        held = [] # List of motors, its angles, and locks
        try:
            for m in motors:
                await m._lock.acquire() # Wait for lock to be released
                held.append(m._lock) # Put motor lock in held list

            plans = []
            for m, d in zip(motors, degrees): # Creates tuple inside of plans with (motor, steps, and direction)
                if relative:
                    steps, direction = cls._steps_for_delta(d)
                    plans.append((m, steps, direction))
                else:
                    steps, direction = m._steps_to_angle(d)
                    plans.append((m, steps, direction))       
                 
            den = 0
            for p in plans:
                steps = p[1] # 2nd element in tuple (motor, STEPS, direction)
                if steps > den:
                    den = steps
            if den == 0: return # Motor not running, return early

            for m in motors:
                m._done.clear()
            
            state = disable_irq()
            for m, steps, direction in plans:
                m._err = 0
                m._num = steps
                m._den = den 
                m._dir = direction
                m._remaining = steps 
            enable_irq(state)
            
            for m in motors:
                if m._num == 0:
                    continue
                await m._done.wait()
        
        finally:  # Release lock so motors can be acquired again      
            for lock in held:
                lock.release()


    # --- Public API ----------------------------------------------------------
#public methods can be called in main function

    def rotate(self, delta):
        """Turn `delta` degrees from wherever the shaft is now."""
        return asyncio.create_task(self._rotate(delta))

    def go_angle(self, new_angle):
        """Turn to absolute angle `new_angle`, by the shortest path."""
        return asyncio.create_task(self._go_angle(new_angle))

    async def idle(self):
        await asyncio.sleep_ms(0)
        async with self._lock:
            pass

    # =========================================================================
    # Lab 8: NEW PUBLIC CLASS METHODS -- DO NOT EDIT
    # =========================================================================
    @classmethod
    def move_together(cls, *moves):
        """
        Relative coordinated move: move_together((m1, 60), (m2, -20)).
        Every axis starts and finishes on the same tick.
        """
        return asyncio.create_task(cls._move_group(moves, relative=True))

    @classmethod
    def go_together(cls, *moves):
        """
        Absolute coordinated move: go_together((az, 60), (alt, 20)).
        Each axis takes its own shortest path; all of them finish together.
        """
        return asyncio.create_task(cls._move_group(moves, relative=False))
    # =========================================================================
    # END NEW CODE BLOCK
    # =========================================================================


# =============================================================================
# Lab 8 DEMONSTRATION -- DO NOT EDIT
# =============================================================================

async def main():

    from shifter import Shifter

    Stepper.reset()
    s = Shifter(data=5, latch=6, clock=7)

    az = Stepper(s)         # bits 0-3
    alt = Stepper(s)        # bits 4-7
    az.zero()
    alt.zero()

    # Single motor commands:
    az.go_angle(90)
    alt.go_angle(20)
    await az.idle()
    await alt.idle()

    time.sleep(1)

    # Motor group:
    az.zero()
    alt.zero()
    await Stepper.go_together((az, 90), (alt, 20))

    time.sleep(1)

    # Motor group with motor order switched
    az.zero()
    alt.zero()
    await Stepper.go_together((alt, 180),(az, -45))

# =============================================================================
# END NEW CODE BLOCK
# =============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        Stepper.stop()
        asyncio.new_event_loop()


