# core/devices/furnace.py
"""
MTI furnace driver – Modbus RTU over TCP (via USR-TCP232-306 Serial-to-Ethernet adapter).

The furnace RS-485 is NOT directly on a COM port. It connects through a
USR-TCP232-306 serial device server, so we send Modbus RTU frames over
a raw TCP socket — same bytes, different transport.

Register map:
  Read  PV : FC03, reg 74, 1 word  → value / 10 = °C
  Write SV : FC06, reg  0, 1 word  → value = °C * 10
  Init     : FC06 reg 81←1000, 46←0, 27←2 (×2), 47←0
"""

import socket
import struct
import threading
import time
import logging
import random
from typing import Any, Optional

from .base import DeviceBase, DeviceStatus

logger = logging.getLogger(__name__)

TCP_TIMEOUT  = 2.0
RECV_BYTES   = 64


# ── Modbus RTU helpers ────────────────────────────────────────────────────────

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def _fc03(addr, reg, count):
    pdu = struct.pack(">BBHH", addr, 0x03, reg, count)
    return pdu + struct.pack("<H", _crc16(pdu))

def _fc06(addr, reg, value):
    pdu = struct.pack(">BBHH", addr, 0x06, reg, value)
    return pdu + struct.pack("<H", _crc16(pdu))

def _parse_fc03(data: bytes) -> Optional[list]:
    if len(data) < 5: return None
    if _crc16(data[:-2]) != struct.unpack("<H", data[-2:])[0]:
        logger.warning("Furnace: CRC mismatch"); return None
    n = data[2] // 2
    return [struct.unpack(">H", data[3+i*2:5+i*2])[0] for i in range(n)]


# ── Furnace device ────────────────────────────────────────────────────────────

class FurnaceDevice(DeviceBase):
    """
    MTI tube furnace over TCP (USR-TCP232-306 serial bridge).

    Config keys:
        host             : IP of the USR-TCP232-306, e.g. "192.168.0.5"
        port             : TCP port, e.g. 4196
        modbus_addr      : Modbus unit address, default 1
        max_temp         : hard ceiling °C, default 1400
        simulate         : bool
        poll_interval_ms : int, default 1000
    """

    def __init__(self, device_id, config):
        super().__init__(device_id, config)
        self.host        = config.get("host", "192.168.0.5")
        self.tcp_port    = int(config.get("port", 4196))
        self.modbus_addr = int(config.get("modbus_addr", 1))
        self.max_temp    = float(config.get("max_temp", 1400.0))
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

        # Simulation
        self._sim_pv = 25.0
        self._sim_sv = 0.0

    def connect(self) -> bool:
        if self.simulate:
            self._set_status(DeviceStatus.SIMULATED)
            self.start_polling(); return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(TCP_TIMEOUT)
            s.connect((self.host, self.tcp_port))
            self._sock = s
            self._init_furnace()
            self._set_status(DeviceStatus.CONNECTED)
            self.start_polling()
            logger.info(f"[{self.device_id}] Connected to {self.host}:{self.tcp_port}")
            return True
        except (socket.error, OSError) as e:
            logger.error(f"[{self.device_id}] TCP connect failed: {e}")
            self._set_status(DeviceStatus.ERROR); return False

    def disconnect(self):
        self.stop_polling()
        if self._sock:
            try: self._sock.close()
            except: pass
        self._set_status(DeviceStatus.DISCONNECTED)

    def get_value(self, control): return self._cache.get(control)

    def set_value(self, control, value) -> bool:
        if control != "temp": return False
        sv = max(0.0, min(float(value), self.max_temp))
        if self.simulate:
            self._sim_sv = sv; return True
        return self._send_recv(_fc06(self.modbus_addr, 0, int(sv * 10))) is not None

    def poll(self):
        if self.simulate:
            self._sim_step(); return
        resp = self._send_recv(_fc03(self.modbus_addr, 74, 1))
        if resp:
            regs = _parse_fc03(resp)
            if regs: self._emit_reading("temp", regs[0] / 10.0, "°C")

    # ── Internal ──────────────────────────────────────────────────────────

    def _init_furnace(self):
        for reg, val in [(81,1000),(46,0),(27,2),(27,2),(47,0)]:
            self._send_recv(_fc06(self.modbus_addr, reg, val))
            time.sleep(0.2)

    def _send_recv(self, cmd: bytes) -> Optional[bytes]:
        with self._lock:
            try:
                self._sock.sendall(cmd)
                return self._sock.recv(RECV_BYTES)
            except socket.timeout:
                logger.warning(f"[{self.device_id}] Timeout")
                return None
            except socket.error as e:
                logger.error(f"[{self.device_id}] Socket error: {e}")
                self._set_status(DeviceStatus.ERROR)
                return None

    def _sim_step(self):
        dt = self._poll_interval
        diff = self._sim_sv - self._sim_pv
        self._sim_pv += min(abs(diff), 10.0 * dt) * (1 if diff >= 0 else -1)
        self._emit_reading("temp", round(self._sim_pv + random.uniform(-0.1,0.1), 1), "°C")
