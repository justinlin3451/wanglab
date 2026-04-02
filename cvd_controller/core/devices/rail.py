# core/devices/rail.py
"""
Arduino linear rail driver – custom ASCII serial protocol.

Command format (reverse-engineered from rail_arduino.py):
  "<addr> <nonce> <command> [args]\\n"

  nonce   : random int 1–32767 (prevents duplicate-command issues)
  addr    : device address string (default "1")

Commands:
  reset_all              – reset firmware state
  speed <value>          – set motor speed
  M1 <position>          – move to absolute position (steps)
  get                    – query current position

Response format for 'get':
  "<addr> <nonce> get <pos1> [pos2 …]\\n"
  → we read pos1 as current position

Position range: 0 – max (default 30000 steps)
"""

import serial
import threading
import time
import logging
import random
from typing import Any, Optional

from .base import DeviceBase, DeviceStatus

logger = logging.getLogger(__name__)


class RailDevice(DeviceBase):
    """
    Arduino-controlled linear rail (opens/closes furnace tube or moves sample).

    Config keys:
        port             : COM port string, e.g. "COM3"
        baud_rate        : int, default 19200
        addr             : str, device address, default "1"
        min_pos          : int, default 0
        max_pos          : int, default 30000
        default_speed    : int, default 50
        simulate         : bool
        poll_interval_ms : int, default 500
    """

    def __init__(self, device_id: str, config: dict):
        super().__init__(device_id, config)
        self.port          = config.get("port", "COM3")
        self.baud_rate     = int(config.get("baud_rate", 19200))
        self.addr          = str(config.get("addr", "1"))
        self.min_pos       = int(config.get("min_pos", 0))
        self.max_pos       = int(config.get("max_pos", 30000))
        self.speed         = int(config.get("default_speed", 50))

        self._serial: Optional[serial.Serial] = None
        self._serial_lock  = threading.Lock()
        self._recv_buffer  = ""

        # Simulation
        self._sim_pos      = 0
        self._sim_target   = 0

    # ------------------------------------------------------------------ #
    # DeviceBase interface                                                 #
    # ------------------------------------------------------------------ #

    def connect(self) -> bool:
        if self.simulate:
            self._set_status(DeviceStatus.SIMULATED)
            self.start_polling()
            return True
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            )
            time.sleep(0.5)  # let Arduino reset after DTR
            self._send_cmd("reset_all")
            time.sleep(0.2)
            self._send_cmd(f"speed {self.speed}")
            self._set_status(DeviceStatus.CONNECTED)
            self.start_polling()
            logger.info(f"[{self.device_id}] Connected on {self.port}")
            return True
        except serial.SerialException as e:
            logger.error(f"[{self.device_id}] Serial connect failed: {e}")
            self._set_status(DeviceStatus.ERROR)
            return False

    def disconnect(self):
        self.stop_polling()
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._set_status(DeviceStatus.DISCONNECTED)

    def get_value(self, control: str) -> Any:
        return self._cache.get(control)

    def set_value(self, control: str, value: Any) -> bool:
        if control == "speed":
            self.speed = int(value)
            if self.simulate:
                return True
            return self._send_cmd(f"speed {self.speed}")

        elif control == "position":
            pos = int(value)
            pos = max(self.min_pos, min(pos, self.max_pos))
            if self.simulate:
                self._sim_target = pos
                return True
            return self._send_cmd(f"M1 {pos}")

        logger.warning(f"[{self.device_id}] Unknown control: {control}")
        return False

    def poll(self):
        if self.simulate:
            self._simulate_step()
            return
        # Send a get request and read response
        self._send_cmd("get")
        # Read available lines
        with self._serial_lock:
            try:
                while self._serial.in_waiting:
                    char = self._serial.read(1).decode("ascii", errors="ignore")
                    if char == "\n":
                        self._parse_line(self._recv_buffer)
                        self._recv_buffer = ""
                    else:
                        self._recv_buffer += char
            except serial.SerialException as e:
                logger.error(f"[{self.device_id}] Read error: {e}")
                self._set_status(DeviceStatus.ERROR)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _nonce(self) -> int:
        return random.randint(1, 32767)

    def _send_cmd(self, command: str) -> bool:
        msg = f"{self.addr} {self._nonce()} {command}\n"
        with self._serial_lock:
            try:
                self._serial.write(msg.encode("ascii"))
                return True
            except serial.SerialException as e:
                logger.error(f"[{self.device_id}] Write error: {e}")
                self._set_status(DeviceStatus.ERROR)
                return False

    def _parse_line(self, line: str):
        """Parse a response line from the Arduino."""
        parts = line.strip().split()
        # Expected: "<addr> <nonce> get <pos>"
        if len(parts) >= 4 and parts[0] == self.addr and parts[2] == "get":
            try:
                pos = int(parts[3])
                self._emit_reading("position", pos, "steps")
            except ValueError:
                pass

    def _simulate_step(self):
        """Move simulated position toward target at fixed speed."""
        dt    = self._poll_interval
        speed = max(1, self.speed)
        step  = int(speed * dt * 100)  # scale speed to steps/s
        diff  = self._sim_target - self._sim_pos
        if abs(diff) <= step:
            self._sim_pos = self._sim_target
        else:
            self._sim_pos += step * (1 if diff > 0 else -1)
        self._emit_reading("position", self._sim_pos, "steps")
