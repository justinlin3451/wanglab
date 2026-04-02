# core/devices/manager.py
"""
DeviceManager – single point of truth for all hardware in the system.

Loads device configuration from a JSON file (or dict), instantiates
the right driver class, connects/disconnects all devices, and provides
a unified interface for the recipe engine and GUI.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .base import DeviceBase, DeviceStatus, DeviceReading
from .furnace import FurnaceDevice
from .mfc     import MFCDevice
from .rail    import RailDevice

logger = logging.getLogger(__name__)

# Map type strings in config → driver classes
DEVICE_REGISTRY: dict[str, type[DeviceBase]] = {
    "furnace": FurnaceDevice,
    "mfc":     MFCDevice,
    "rail":    RailDevice,
}


class DeviceManager:
    """
    Manages lifecycle of all hardware devices.

    Usage:
        mgr = DeviceManager.from_file("config/workspace.json")
        mgr.connect_all()
        mgr.set_value("furnace", "temp", 900.0)
        val = mgr.get_value("furnace", "temp")
        mgr.disconnect_all()
    """

    def __init__(self, device_configs: dict[str, dict]):
        """
        device_configs: dict of { device_id: {type, ...config...} }
        """
        self._devices: dict[str, DeviceBase] = {}
        self._load_devices(device_configs)

    # ------------------------------------------------------------------ #
    # Construction                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_file(cls, path: str | Path) -> "DeviceManager":
        with open(path, "r") as f:
            workspace = json.load(f)
        return cls(workspace.get("devices", {}))

    @classmethod
    def from_dict(cls, workspace: dict) -> "DeviceManager":
        return cls(workspace.get("devices", {}))

    def _load_devices(self, configs: dict[str, dict]):
        for device_id, cfg in configs.items():
            dtype = cfg.get("type", "").lower()
            if dtype not in DEVICE_REGISTRY:
                logger.warning(f"Unknown device type '{dtype}' for '{device_id}', skipping")
                continue
            klass = DEVICE_REGISTRY[dtype]
            device = klass(device_id, cfg)
            self._devices[device_id] = device
            logger.info(f"Loaded device: {device_id} ({dtype})")

    # ------------------------------------------------------------------ #
    # Connection management                                                #
    # ------------------------------------------------------------------ #

    def connect_all(self) -> dict[str, bool]:
        results = {}
        for did, dev in self._devices.items():
            results[did] = dev.connect()
        return results

    def disconnect_all(self):
        for dev in self._devices.values():
            try:
                dev.disconnect()
            except Exception as e:
                logger.warning(f"Disconnect error for {dev.device_id}: {e}")

    def connect_device(self, device_id: str) -> bool:
        dev = self._get(device_id)
        return dev.connect() if dev else False

    def disconnect_device(self, device_id: str):
        dev = self._get(device_id)
        if dev:
            dev.disconnect()

    # ------------------------------------------------------------------ #
    # Value access                                                         #
    # ------------------------------------------------------------------ #

    def set_value(self, device_id: str, control: str, value: Any) -> bool:
        dev = self._get(device_id)
        if not dev:
            return False
        if dev.status not in (DeviceStatus.CONNECTED, DeviceStatus.SIMULATED):
            logger.warning(f"Cannot set {device_id}.{control}: device not ready ({dev.status.name})")
            return False
        return dev.set_value(control, value)

    def get_value(self, device_id: str, control: str) -> Any:
        dev = self._get(device_id)
        return dev.get_value(control) if dev else None

    # ------------------------------------------------------------------ #
    # Subscription                                                         #
    # ------------------------------------------------------------------ #

    def subscribe(self, device_id: str, callback):
        dev = self._get(device_id)
        if dev:
            dev.subscribe(callback)

    def subscribe_all(self, callback):
        for dev in self._devices.values():
            dev.subscribe(callback)

    # ------------------------------------------------------------------ #
    # Inspection                                                           #
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, str]:
        return {did: dev.status.name for did, dev in self._devices.items()}

    def device_ids(self) -> list[str]:
        return list(self._devices.keys())

    def get_device(self, device_id: str) -> Optional[DeviceBase]:
        return self._devices.get(device_id)

    def _get(self, device_id: str) -> Optional[DeviceBase]:
        dev = self._devices.get(device_id)
        if dev is None:
            logger.warning(f"Device not found: {device_id}")
        return dev
