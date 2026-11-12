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

# Teammates: Jason (Jaegeon) Lee, Jenita Shrestha, Merrick Summers, Daniel Zheng

import asyncio
import json
import time
import ssl

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
        self.laser.value(0) # 0 is on
        self.firing = True

    def laser_off(self):
        self.laser.value(1)
        self.firing = False

    async def fire(self, seconds=None):
        """Laser on for exactly `seconds`, then off."""
        try:
            self.laser_on()
            if seconds is not None:
                await asyncio.sleep(seconds)
        finally:
                self.laser_off()

    # --- motion --------------------------------------------------------------

    async def goto(self, az=None, alt=None):
        """Absolute move.  Both axes must arrive together."""
        if az is None:
            az = self.az.angle
        if alt is None:
            alt = self.alt.angle
        Stepper.go_together((self.az, az), (self.alt, alt))

    async def jog(self, axis, deg):
        """Relative move, degrees."""
        if axis == "az":
            Stepper.move_together((self.az, deg))
        elif axis == "alt":
            Stepper.move_together((self.alt, deg))

    # Note: It seems awful and disgusting that we are converting to degrees and
    # then back to steps internally to do this, but hopefully it doesn't cause
    # any actual precision errors - Merrick S. 2026-11-12
    async def step(self, axis, steps):
        """Relative move, raw half-steps."""
        deg = steps * 360 / Stepper.STEPS_PER_REV
        if axis == "az":
            Stepper.move_together((self.az, deg))
        elif axis == "alt":
            Stepper.move_together((self.alt, deg))


    def zero(self, axis=None):
        """Current position becomes 0 deg, for one axis or both."""
        if axis is None:
            self.az.zero()
            self.alt.zero()
        elif axis == "az":
            self.az.zero()
        elif axis == "alt":
            self.alt.zero()    
            
    def release(self):
        self.az.release()
        self.alt.release()

    def stop(self):
        """Abort everything.  Laser off first."""
        self.laser_off()
        self.release()

    # --- state ---------------------------------------------------------------

    def telemetry(self):
        """Return a dict describing the current state."""
        return {
            'az_angle': self.az.angle, 
            'alt_angle': self.alt.angle,
            'laser_on': self.firing,
            'move_in_progress': self.az._remaining > 0 or self.alt._remaining > 0,
            'last_message_received': self.message
        }


class Comms:
    """HiveMQ <-> Turret."""

    def __init__(self, turret):
        self.turret = turret
        self.client = None
        self.connected = False

        turret.notify = self.publish_telemetry

        self.dispatch = {
            'LASER_ON': lambda d: self.turret.laser_on(),
            'LASER_OFF': lambda d: self.turret.laser_off(),
            'FIRE': lambda d: asyncio.create_task(self.turret.fire(d.get('seconds', 3))),
            'GOTO': lambda d: asyncio.create_task(self.turret.goto(d.get('az'), d.get('alt'))),
            'JOG': lambda d: asyncio.create_task(self.turret.jog(d.get('axis'), d.get('deg'))),
            'STEP': lambda d: asyncio.create_task(self.turret.step(d.get('axis'), d.get('steps'))),
            'ZERO': lambda d: self.turret.zero(d.get('axis')),
            'STOP': lambda d: self.turret.stop(),
            'STATUS': lambda d: self.turret.notify(),
        }

    # --- connection ----------------------------------------------------------

    def connect(self):
        """TLS connect, last will, subscribe, announce."""
        ssl_params = {'server_hostname': config.MQTT_BROKER}
        self.client = MQTTClient(
            client_id=config.TERPMAIL_USERNAME,
            server=config.MQTT_BROKER,
            port=config.MQTT_PORT,
            user=config.MQTT_USER,
            password=config.MQTT_PASS,
            keepalive=5,
            ssl=True,
            ssl_params=ssl_params
        )
        # Set callback and connect
        self.client.set_callback(self._on_message)
        self.client.set_last_will(config.TOPIC_STATUS, b'offline', retain=True)  # execute if connection dropped
        self.client.connect()
        self.client.subscribe(config.TOPIC_COMMANDS)
        self.connected = True
        print(f'Connected to MQTT broker, subscribed to {config.TOPIC_COMMANDS}')

        # Publish online status
        self.client.publish(config.TOPIC_STATUS, b'online', retain=True)
   
   
    # --- incoming ------------------------------------------------------------

    def _on_message(self, topic, msg):
        """umqtt callback.  Runs inside check_msg(), so keep it short."""
        print(f'Received on {topic}: {msg.decode()}')
        self.turret.message = msg.decode()
        try:
            data = json.loads(msg.decode())
            cmd = data.get('cmd', '')
            if cmd in self.dispatch:
                self.dispatch[cmd](data)
            else:
                self.turret.message = f'Unknown command: {cmd}'
        except Exception as e:
            print('Error parsing message:', e)

    async def _rx_loop(self):
        """Poll the broker forever."""
        while True:
            try:
                if self.client: #is server running 
                    self.client.check_msg() 
            except Exception as e:
                print('RX error:', e)
            await asyncio.sleep_ms(100)

    # --- outgoing ------------------------------------------------------------

    def publish_telemetry(self):
        """Send one telemetry packet on the sensor topic."""
        if not self.connected or not self.client:
            return
        state = self.turret.telemetry()
        payload = json.dumps(state)
        self.client.publish(config.TOPIC_SENSOR, payload.encode('utf-8'))
        # print(f'Published: {payload}')

    async def _tx_loop(self):
        """Publish telemetry every PUBLISH_MS forever."""
        while True:
            self.publish_telemetry()
            await asyncio.sleep(PUBLISH_MS/1000)

    # --- entry point ---------------------------------------------------------

    def start(self):
        """Launch both loops as tasks.  Returns immediately."""
        if not self.connected:
            self.connect()
        asyncio.create_task(self._rx_loop())
        asyncio.create_task(self._tx_loop())


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
