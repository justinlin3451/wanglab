# core/devices/furnace.py
"""
MTI furnace driver – Modbus RTU over RS-485 serial (COM port).

Register map (reverse-engineered from Furnace_MTI.py + MTI OTF-1200X manual):
  Read  PV  : FC03, reg 74, 1 word  (value / 10 = °C)
  Write SV  : FC06, reg  0, 1 word  (value = °C * 10)
  Init regs : FC06, reg 81 ← 1000  (ramp rate or PID param)
               FC06, reg 46 ← 0
               FC06, reg 27 ← 2    (written twice – control mode)
               FC06, reg 47 ← 0
"""

import serial
import struct
import time
import logging
import random
from typing import Any, Optional

from .base import DeviceBase, DeviceStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modbus RTU helpers (minimal, no external library needed)
# ---------------------------------------------------------------------------

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_fc03(addr: int, reg: int, count: int) -> bytes:
    """Read holding registers."""
    pdu = struct.pack(">BBHH", addr, 0x03, reg, count)
    crc = _crc16(pdu)
    return pdu + struct.pack("<H", crc)


def _build_fc06(addr: int, reg: int, value: int) -> bytes:
    """Write single register."""
    pdu = struct.pack(">BBHH", addr, 0x06, reg, value)
    crc = _crc16(pdu)
    return pdu + struct.pack("<H", crc)


def _parse_fc03_response(data: bytes) -> Optional[list[int]]:
    """Parse FC03 response, return list of register values or None on error."""
    if len(data) < 5:
        return None
    # Validate CRC
    crc_recv = struct.unpack("<H", data[-2:])[0]
    crc_calc = _crc16(data[:-2])
    if crc_recv != crc_calc:
        logger.warning("Furnace: CRC mismatch")
        return None
    byte_count = data[2]
    n_regs = byte_count // 2
    regs = []
    for i in range(n_regs):
        regs.append(struct.unpack(">H", data[3 + i*2 : 5 + i*2])[0])
    return regs


# ---------------------------------------------------------------------------
# Furnace device
# ---------------------------------------------------------------------------

class FurnaceDevice(DeviceBase):
    """
    MTI tube furnace controller.

    Config keys:
        port          : COM port string, e.g. "COM5"
        baud_rate     : int, default 9600
        modbus_addr   : int, default 1
        max_temp      : float, default 1400.0  (°C)
        simulate      : bool, default False
        poll_interval_ms : int, default 1000
    """

    TEMP_MAX_DEFAULT = 1400.0
    READ_TIMEOUT     = 0.5   # seconds

    def __init__(self, device_id: str, config: dict):
        super().__init__(device_id, config)
        self.port        = config.get("port", "COM5")
        self.baud_rate   = int(config.get("baud_rate", 9600))
        self.modbus_addr = int(config.get("modbus_addr", 1))
        self.max_temp    = float(config.get("max_temp", self.TEMP_MAX_DEFAULT))
        self._serial: Optional[serial.Serial] = None

        # Simulation state
        self._sim_pv  = 25.0
        self._sim_sv  = 0.0

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
                timeout=self.READ_TIMEOUT
            )
            self._initialize_furnace()
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
        if control != "temp":
            logger.warning(f"[{self.device_id}] Unknown control: {control}")
            return False

        sv = float(value)
        sv = max(0.0, min(sv, self.max_temp))

        if self.simulate:
            self._sim_sv = sv
            logger.info(f"[{self.device_id}] SIM set temp → {sv:.1f}°C")
            return True

        sv_int = int(sv * 10)
        cmd = _build_fc06(self.modbus_addr, 0, sv_int)
        return self._send_cmd(cmd)

    def poll(self):
        if self.simulate:
            self._simulate_step()
            return
        # Request PV (process value = actual temperature)
        cmd = _build_fc03(self.modbus_addr, 74, 1)
        response = self._send_recv(cmd, expected_bytes=7)
        if response:
            regs = _parse_fc03_response(response)
            if regs:
                pv = regs[0] / 10.0
                self._emit_reading("temp", pv, "°C")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _initialize_furnace(self):
        """Send initialization sequence from original driver."""
        init_cmds = [
            _build_fc06(self.modbus_addr, 81, 1000),
            _build_fc06(self.modbus_addr, 46, 0),
            _build_fc06(self.modbus_addr, 27, 2),
            _build_fc06(self.modbus_addr, 27, 2),   # sent twice intentionally
            _build_fc06(self.modbus_addr, 47, 0),
        ]
        for cmd in init_cmds:
            self._send_cmd(cmd)
            time.sleep(0.2)

    def _send_cmd(self, cmd: bytes) -> bool:
        try:
            self._serial.reset_input_buffer()
            self._serial.write(cmd)
            return True
        except serial.SerialException as e:
            logger.error(f"[{self.device_id}] Write error: {e}")
            self._set_status(DeviceStatus.ERROR)
            return False

    def _send_recv(self, cmd: bytes, expected_bytes: int) -> Optional[bytes]:
        try:
            self._serial.reset_input_buffer()
            self._serial.write(cmd)
            response = self._serial.read(expected_bytes)
            if len(response) < expected_bytes:
                logger.warning(f"[{self.device_id}] Short response: {len(response)}/{expected_bytes}")
                return None
            return response
        except serial.SerialException as e:
            logger.error(f"[{self.device_id}] Read error: {e}")
            self._set_status(DeviceStatus.ERROR)
            return None

    def _simulate_step(self):
        """Slowly ramp simulated PV toward SV."""
        dt = self._poll_interval
        ramp_rate = 10.0   # °C/s in simulation
        diff = self._sim_sv - self._sim_pv
        if abs(diff) < 0.5:
            self._sim_pv = self._sim_sv
        else:
            self._sim_pv += ramp_rate * dt * (1 if diff > 0 else -1)
        # Add tiny noise
        pv = self._sim_pv + random.uniform(-0.2, 0.2)
        self._emit_reading("temp", round(pv, 1), "°C")
