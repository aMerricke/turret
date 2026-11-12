# =============================================================================
# ENME 441 - Lab 10: Brokered communication
#
# Code template
#
# Two rules:
#   - _on_message() runs inside check_msg().  No handler may await, sleep, or
#     do network I/O.  Set an attribute or create a task.
#   - Nothing here may call time.sleep().
# =============================================================================

import asyncio
import json
import time

from machine import Pin
from umqtt.simple import MQTTClient

import config
import wifi
from shifter import Shifter
from lab8 import Stepper

LASER_PIN = 8           # onboard LED, active LOW -- stands in for the laser

PUBLISH_MS = 2000       # time between publish events

class Turret:
    """Two axes and a laser"""

    def __init__(self, az, alt, laser_pin=LASER_PIN):
        self.az = az
        self.alt = alt
        self.laser = Pin(laser_pin, Pin.OUT)
        self.laser.value(1)             # active LOW: start off

        self.firing = False
        self.message = ''
        self.echo = None                # Part F: t0 from the last command
        self._task = None               # the move or shot in flight
        self.notify = None              # set by Comms; call to push telemetry

    # --- laser ---------------------------------------------------------------

    def laser_on(self):
        pass  # YOUR CODE HERE

    def laser_off(self):
        pass  # YOUR CODE HERE

    def fire(self, seconds=None):
        """Laser on for exactly `seconds`, then off."""
        pass  # YOUR CODE HERE

    # --- motion --------------------------------------------------------------

    def goto(self, az=None, alt=None):
        """Absolute move.  Both axes must arrive together."""
        pass  # YOUR CODE HERE

    def jog(self, axis, deg):
        """Relative move, degrees."""
        pass  # YOUR CODE HERE

    def step(self, axis, steps):
        """Relative move, raw half-steps."""
        pass  # YOUR CODE HERE

    def zero(self, axis=None):
        """Current position becomes 0 deg, for one axis or both."""
        pass  # YOUR CODE HERE

    def release(self):
        pass  # YOUR CODE HERE

    def stop(self):
        """Abort everything.  Laser off first."""
        pass  # YOUR CODE HERE

    # --- state ---------------------------------------------------------------

    def telemetry(self):
        """Return a dict describing the current state."""
        pass  # YOUR CODE HERE


class Comms:
    """HiveMQ <-> Turret."""

    def __init__(self, turret):
        self.turret = turret
        self.client = None
        self.connected = False

        turret.notify = self.publish_telemetry

        self.dispatch = {
            # 'CMD': lambda d: ...,
            # YOUR CODE HERE
        }

    # --- connection ----------------------------------------------------------

    def connect(self):
        """TLS connect, last will, subscribe, announce."""
        pass  # YOUR CODE HERE

    # --- incoming ------------------------------------------------------------

    def _on_message(self, topic, msg):
        """umqtt callback.  Runs inside check_msg(), so keep it short."""
        pass  # YOUR CODE HERE

    async def _rx_loop(self):
        """Poll the broker forever."""
        pass  # YOUR CODE HERE

    # --- outgoing ------------------------------------------------------------

    def publish_telemetry(self):
        """Send one telemetry packet on the sensor topic."""
        pass  # YOUR CODE HERE

    async def _tx_loop(self):
        """Publish telemetry every PUBLISH_MS forever."""
        pass  # YOUR CODE HERE

    # --- entry point ---------------------------------------------------------

    def start(self):
        """Launch both loops as tasks.  Returns immediately."""
        pass  # YOUR CODE HERE


async def main():
    wifi.connect()

    Stepper.reset()
    s = Shifter(data=5, latch=6, clock=7)
    az = Stepper(s)             # bits 0-3
    alt = Stepper(s)            # bits 4-7
    az.zero()
    alt.zero()

    turret = Turret(az, alt)
    comms = Comms(turret)
    comms.start()

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        Stepper.stop()
        asyncio.new_event_loop()
