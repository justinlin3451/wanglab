# core/devices/mfc.py
"""
Alicat Mass Flow Controller driver – ASCII protocol over TCP socket.

The Alicat uses a simple ASCII protocol:
  Poll  : send "<addr>\\r"            → returns space-separated fields
  Set   : send "<addr><width>\\r"     → sets flow via PWM width (0–64000)
  Init  : send "<addr>W16=18119\\r"   → enable setpoint control
  End   : send "<addr>W16=199\\r"     → return to manual/safe mode

Address encoding: addr int → chr(addr + 64)
  addr=1 → 'A',  addr=2 → 'B', etc.

Response fields (space-separated):
  [0] addr_char  [1] pressure  [2] flow  [3..] other fields
"""

import socket
import threading
import time
import logging
import random
from typing import Any, Optional

from .base import DeviceBase, DeviceStatus

logger = logging.getLogger(__name__)

TCP_TIMEOUT    = 2.0    # seconds
RECV_BUFSIZE   = 256
PWM_PERIOD     = 64000  # full-scale PWM counts (from original driver)


class MFCDevice(DeviceBase):
    """
    Alicat MFC over TCP.

    Config keys:
        host             : IP address string, e.g. "192.168.0.7"
        port             : int, TCP port, e.g. 26
        addr             : int, Alicat unit address (1=A, 2=B, …)
        max_flow         : float, full-scale flow in sccm
        gas              : str, gas name for display (e.g. "Ar", "H2")
        simulate         : bool
        poll_interval_ms : int, default 500
    """

    def __init__(self, device_id: str, config: dict):
        super().__init__(device_id, config)
        self.host     = config.get("host", "192.168.0.7")
        self.tcp_port = int(config.get("port", 26))
        addr_int      = int(config.get("addr", 1))
        self.addr_char = chr(addr_int + 64)   # 1→'A', 2→'B'
        self.max_flow  = float(config.get("max_flow", 100.0))
        self.gas       = config.get("gas", "unknown")

        self._socket: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()

        # Simulation
        self._sim_sv = 0.0
        self._sim_pv = 0.0

    # ------------------------------------------------------------------ #
    # DeviceBase interface                                                 #
    # ------------------------------------------------------------------ #

    def connect(self) -> bool:
        if self.simulate:
            self._set_status(DeviceStatus.SIMULATED)
            self.start_polling()
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_TIMEOUT)
            sock.connect((self.host, self.tcp_port))
            self._socket = sock
            self._send_raw(self.addr_char + "W16=18119\r")  # init: enable control
            self._set_status(DeviceStatus.CONNECTED)
            self.start_polling()
            logger.info(f"[{self.device_id}] Connected to {self.host}:{self.tcp_port} (addr={self.addr_char})")
            return True
        except (socket.error, OSError) as e:
            logger.error(f"[{self.device_id}] TCP connect failed: {e}")
            self._set_status(DeviceStatus.ERROR)
            return False

    def disconnect(self):
        self.stop_polling()
        if self._socket:
            try:
                self._send_raw(self.addr_char + "W16=199\r")  # return to safe mode
                time.sleep(0.1)
                self._socket.close()
            except Exception:
                pass
        self._set_status(DeviceStatus.DISCONNECTED)

    def get_value(self, control: str) -> Any:
        return self._cache.get(control)

    def set_value(self, control: str, value: Any) -> bool:
        if control != "flow":
            return False

        sv = float(value)
        sv = max(0.0, min(sv, self.max_flow))

        if self.simulate:
            self._sim_sv = sv
            return True

        width = int(sv * PWM_PERIOD / self.max_flow)
        cmd = f"{self.addr_char}{width}\r"
        return self._send_raw(cmd)

    def poll(self):
        if self.simulate:
            self._simulate_step()
            return
        # Poll the MFC for current readings
        response = self._send_recv(self.addr_char + "\r")
        if response:
            self._parse_response(response)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _send_raw(self, cmd: str) -> bool:
        with self._sock_lock:
            try:
                self._socket.sendall(cmd.encode("ascii"))
                return True
            except socket.error as e:
                logger.error(f"[{self.device_id}] Send error: {e}")
                self._set_status(DeviceStatus.ERROR)
                return False

    def _send_recv(self, cmd: str) -> Optional[str]:
        with self._sock_lock:
            try:
                self._socket.sendall(cmd.encode("ascii"))
                data = self._socket.recv(RECV_BUFSIZE)
                return data.decode("ascii", errors="ignore").strip()
            except socket.timeout:
                logger.warning(f"[{self.device_id}] Recv timeout")
                return None
            except socket.error as e:
                logger.error(f"[{self.device_id}] Recv error: {e}")
                self._set_status(DeviceStatus.ERROR)
                return None

    def _parse_response(self, resp: str):
        """
        Expected: "<addr> <pressure> <flow> ..."
        field[0] = addr char, field[1] = pressure, field[2] = flow
        """
        parts = resp.split()
        if len(parts) >= 3 and parts[0] == self.addr_char and parts[2] != "=":
            try:
                flow = float(parts[2])
                self._emit_reading("flow", flow, "sccm")
            except ValueError:
                logger.warning(f"[{self.device_id}] Bad flow value: {parts[2]}")

    def _simulate_step(self):
        """Lag filter toward setpoint."""
        tau = 2.0   # time constant in seconds
        dt  = self._poll_interval
        alpha = dt / (tau + dt)
        self._sim_pv += alpha * (self._sim_sv - self._sim_pv)
        pv = self._sim_pv + random.uniform(-0.05, 0.05)
        self._emit_reading("flow", round(max(0, pv), 2), "sccm")
