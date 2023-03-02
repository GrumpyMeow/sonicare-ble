"""Parser for Sonicare BLE advertisements."""
from __future__ import annotations

from sensor_state_data import (
    BinarySensorDeviceClass,
    BinarySensorValue,
    DeviceKey,
    SensorDescription,
    SensorDeviceClass,
    SensorDeviceInfo,
    SensorUpdate,
    SensorValue,
    Units,
)

from .parser import SonicareBinarySensor, SonicareBluetoothDeviceData, SonicareSensor

__version__ = "0.0.2"

__all__ = [
    "SonicareSensor",
    "SonicareBinarySensor",
    "SonicareBluetoothDeviceData",
    "BinarySensorDeviceClass",
    "BinarySensorValue",
    "SensorDescription",
    "SensorDeviceInfo",
    "DeviceKey",
    "SensorUpdate",
    "SensorDeviceClass",
    "SensorDeviceInfo",
    "SensorValue",
    "Units",
]
