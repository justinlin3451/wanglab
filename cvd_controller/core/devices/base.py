# core/devices/base.py
"""
Abstract base class for all CVD system hardware devices.
Every device driver inherits from DeviceBase and implements these methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional
import threading
import time
import logging

logger = logging.getLogger(__name__)


class DeviceStatus(Enum):
    DISCONNECTED = auto()
    CONNECTING    = auto()
    CONNECTED     = auto()
    ERROR         = auto()
    SIMULATED     = auto()   # offline / demo mode


@dataclass
class DeviceReading:
    """A single timestamped value returned from a device."""
    device_id: str
    control:   str
    value:     Any
    timestamp: float = field(default_factory=time.time)
    units:     str   = ""


class DeviceBase(ABC):
    """
    Base class for all hardware device drivers.

    Subclasses must implement:
        connect()   – open the port / socket
        disconnect()– close cleanly
        get_value(control) -> Any
        set_value(control, value)
        poll()      – called periodically by the polling thread; should call
                      self._emit_reading() for each fresh value

    The base class manages:
        - status tracking
        - a background polling thread
        - a callback registry so the GUI / recipe engine can subscribe
        - simulation mode (returns fake data when hardware is absent)
    """

    def __init__(self, device_id: str, config: dict):
        self.device_id  = device_id
        self.config     = config
        self.status     = DeviceStatus.DISCONNECTED
        self.simulate   = config.get("simulate", False)

        self._callbacks: list[Callable[[DeviceReading], None]] = []
        self._poll_interval = config.get("poll_interval_ms", 500) / 1000.0
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event  = threading.Event()
        self._lock        = threading.Lock()

        # Last known values cache  {control: value}
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Abstract interface                                                   #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def connect(self) -> bool:
        """Open connection. Return True on success."""

    @abstractmethod
    def disconnect(self):
        """Close connection cleanly."""

    @abstractmethod
    def get_value(self, control: str) -> Any:
        """Request a value synchronously. Returns cached/None if unavailable."""

    @abstractmethod
    def set_value(self, control: str, value: Any) -> bool:
        """Send a setpoint. Return True on success."""

    @abstractmethod
    def poll(self):
        """Called by polling thread. Should call self._emit_reading() for new data."""

    # ------------------------------------------------------------------ #
    # Polling thread management                                            #
    # ------------------------------------------------------------------ #

    def start_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"poll-{self.device_id}",
            daemon=True
        )
        self._poll_thread.start()
        logger.debug(f"[{self.device_id}] Polling started ({self._poll_interval*1000:.0f} ms)")

    def stop_polling(self):
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=3)

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                if self.status in (DeviceStatus.CONNECTED, DeviceStatus.SIMULATED):
                    self.poll()
            except Exception as e:
                logger.warning(f"[{self.device_id}] Poll error: {e}")
            self._stop_event.wait(self._poll_interval)

    # ------------------------------------------------------------------ #
    # Callback / subscription system                                       #
    # ------------------------------------------------------------------ #

    def subscribe(self, callback: Callable[[DeviceReading], None]):
        """Register a callback invoked whenever new data arrives."""
        self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[DeviceReading], None]):
        self._callbacks = [c for c in self._callbacks if c != callback]

    def _emit_reading(self, control: str, value: Any, units: str = ""):
        reading = DeviceReading(
            device_id=self.device_id,
            control=control,
            value=value,
            units=units
        )
        with self._lock:
            self._cache[control] = value
        for cb in self._callbacks:
            try:
                cb(reading)
            except Exception as e:
                logger.warning(f"[{self.device_id}] Callback error: {e}")

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def cached(self, control: str) -> Any:
        return self._cache.get(control)

    def _set_status(self, status: DeviceStatus):
        self.status = status
        logger.info(f"[{self.device_id}] Status → {status.name}")

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.device_id} status={self.status.name}>"
