"""Parser for Sonicare BLE advertisements."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

from bleak import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from sensor_state_data import SensorDeviceClass, SensorUpdate, Units
from sensor_state_data.enum import StrEnum

from .const import (  # Characteristics; CHARACTERISTIC_HANDLE_TIME,; CHARACTERISTIC_BRUSHING_SESSION_ID,; CHARACTERISTIC_40C0,; Other consts
    BRUSHING_UPDATE_INTERVAL_SECONDS,
    CHARACTERISTIC_40A0,
    CHARACTERISTIC_4030,
    CHARACTERISTIC_AVAILABLE_BRUSHING_ROUTINE_4080,
    CHARACTERISTIC_BATTERY_LEVEL,
    CHARACTERISTIC_BRUSHING_TIME,
    CHARACTERISTIC_INTENSITY,
    CHARACTERISTIC_LOADED_SESSION_ID,
    CHARACTERISTIC_ROUTINE_LENGTH,
    CHARACTERISTIC_UPDATED_HANDLE_SESSION_STATE,
    NOT_BRUSHING_UPDATE_INTERVAL_SECONDS,
    TIMEOUT_RECENTLY_BRUSHING,
)

_LOGGER = logging.getLogger(__name__)


class SonicareSensor(StrEnum):
    BATTERY_LEVEL = "battery_level"
    BRUSHING_TIME = "brushing_time"
    HANDLE_TIME = "handle_time"
    HANDLE_SESSION_STATE = "handle_session_state"
    BRUSHING_SESSION_ID = "brushing_session_id"
    LOADED_SESSION_ID = "loaded_session_id"
    INTENSITY = "intensity"
    AVAILABLE_BRUSHING_ROUTINE = "available_brushing_routine"
    ROUTINE_LENGTH = "routine_length"
    SIGNAL_STRENGTH = "signal_strength"


class SonicareBinarySensor(StrEnum):
    BRUSHING = "brushing"


class Models(Enum):
    HX6340 = auto()


@dataclass
class ModelDescription:
    device_type: str


DEVICE_TYPES = {
    Models.HX6340: ModelDescription("HX6340"),
}

STATES = {
    0: "off",
    1: "standby",
    2: "brushing",
    3: "charging",
    4: "shutdown",
    5: "validate",
    6: "unknown6",
    7: "unknown7",
}


SONICARE_MANUFACTURER = 477


BYTES_TO_MODEL = {
    # b"\x062k": Models.HX6340,
}


class SonicareBluetoothDeviceData(BluetoothData):
    """Data for Sonicare BLE sensors."""

    def __init__(self) -> None:
        super().__init__()
        # If this is True, we are currently brushing or were brushing as of the last advertisement data
        self._brushing = False
        self._last_brush = 0.0

    def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        _LOGGER.debug("Parsing Sonicare BLE advertisement data: %s", service_info)
        manufacturer_data = service_info.manufacturer_data
        address = service_info.address
        _LOGGER.debug(
            "Parsing Sonicare BLE advertisement manufacturer data: %s",
            manufacturer_data,
        )
        if SONICARE_MANUFACTURER not in manufacturer_data:
            return None
        data = manufacturer_data[SONICARE_MANUFACTURER]
        self.set_device_manufacturer("Philips Sonicare")
        _LOGGER.debug("Parsing Sonicare sensor: %s", data)
        msg_length = len(data)
        _LOGGER.debug("Message length: %s", msg_length)
        if msg_length not in (9, 999):
            return

        # model = BYTES_TO_MODEL.get(device_bytes, Models.HX6340)
        model = Models.HX6340
        model_info = DEVICE_TYPES[model]
        self.set_device_type(model_info.device_type)
        name = f"{model_info.device_type} {short_address(address)}"
        self.set_device_name(name)
        self.set_title(name)

    def poll_needed(
        self, service_info: BluetoothServiceInfo, last_poll: float | None
    ) -> bool:
        """
        This is called every time we get a service_info for a device. It means the
        device is working and online.
        """
        _LOGGER.debug("poll_needed called")
        if last_poll is None:
            return True
        update_interval = NOT_BRUSHING_UPDATE_INTERVAL_SECONDS
        if (
            self._brushing
            or time.monotonic() - self._last_brush <= TIMEOUT_RECENTLY_BRUSHING
        ):
            update_interval = BRUSHING_UPDATE_INTERVAL_SECONDS
        return last_poll > update_interval

    async def async_poll(self, ble_device: BLEDevice) -> SensorUpdate:
        """
        Poll the device to retrieve any values we can't get from passive listening.
        """
        _LOGGER.debug("async_poll")
        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, ble_device.address
        )

        async def process_characteristic_value(characteristic_uuid, value):
            _LOGGER.debug(f"Update characteristic {characteristic_uuid} with {value}")
            if characteristic_uuid == CHARACTERISTIC_BATTERY_LEVEL:
                self.update_sensor(
                    str(SonicareSensor.BATTERY_LEVEL),
                    Units.PERCENTAGE,
                    value[0],
                    SensorDeviceClass.BATTERY,
                    "Battery",
                )
            elif characteristic_uuid == CHARACTERISTIC_UPDATED_HANDLE_SESSION_STATE:
                state = STATES.get(value[0], f"unknown state value {value[0]}")
                self.update_sensor(
                    str(SonicareSensor.HANDLE_SESSION_STATE),
                    None,
                    state,
                    None,
                    "Handle session state",
                )
            elif characteristic_uuid == CHARACTERISTIC_BRUSHING_TIME:
                self.update_sensor(
                    str(SonicareSensor.BRUSHING_TIME),
                    None,
                    value[0],
                    None,
                    "Brushing time",
                )
            elif characteristic_uuid == CHARACTERISTIC_AVAILABLE_BRUSHING_ROUTINE_4080:
                self.update_sensor(
                    str(SonicareSensor.AVAILABLE_BRUSHING_ROUTINE),
                    None,
                    value[0],
                    None,
                    "Available brushing routine",
                )
            elif characteristic_uuid == CHARACTERISTIC_ROUTINE_LENGTH:
                self.update_sensor(
                    str(SonicareSensor.ROUTINE_LENGTH),
                    None,
                    value[0],
                    None,
                    "Routine length",
                )
            elif characteristic_uuid == CHARACTERISTIC_INTENSITY:
                self.update_sensor(
                    str(SonicareSensor.INTENSITY),
                    None,
                    value[0],
                    None,
                    "Intensity",
                )
            elif characteristic_uuid == CHARACTERISTIC_LOADED_SESSION_ID:
                self.update_sensor(
                    str(SonicareSensor.LOADED_SESSION_ID),
                    None,
                    value[0],
                    None,
                    "Loaded session id",
                )
            else:
                _LOGGER.debug(f"Unknown characteristic {characteristic_uuid} {value}")

        async def _async_handle_handle_state(
            characteristic: BleakGATTCharacteristic, data: bytearray
        ) -> None:
            """Handle the device going unavailable."""
            # _LOGGER.debug(
            #     f"Notification of_characteristic={characteristic.uuid} data={data}"
            # )
            await process_characteristic_value(characteristic.uuid, data)

            # Work in progress: How to notify HASS of new data?

        for service in client.services:
            for characteristic in service.characteristics:
                try:
                    value_char = client.services.get_characteristic(characteristic.uuid)
                    if "read" in value_char.properties:
                        value = await client.read_gatt_char(characteristic.uuid)
                        await process_characteristic_value(characteristic.uuid, value)
                    if "indicate" in value_char.properties:
                        await client.start_notify(
                            char_specifier=characteristic.uuid,
                            callback=_async_handle_handle_state,
                        )
                    if "notify" in value_char.properties:
                        await client.start_notify(
                            char_specifier=characteristic.uuid,
                            callback=_async_handle_handle_state,
                        )
                except Exception:
                    _LOGGER.debug(f"Error on characteristic {characteristic.uuid}")

        return self._finish_update()
