"""Parser for Sonicare BLE advertisements."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto

from bleak import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from sensor_state_data import SensorDeviceClass, SensorUpdate, Units
from sensor_state_data.enum import StrEnum

from .const import (
    BRUSHING_UPDATE_INTERVAL_SECONDS,
    CHARACTERISTIC_BATTERY,
    CHARACTERISTIC_BRUSHING_TIME,
    CHARACTERISTIC_CURRENT_TIME,
    CHARACTERISTIC_STATE,
    NOT_BRUSHING_UPDATE_INTERVAL_SECONDS,
    TIMEOUT_RECENTLY_BRUSHING,
)

_LOGGER = logging.getLogger(__name__)


class SonicareSensor(StrEnum):
    BRUSHING_TIME = "brushing_time"
    CURRENT_TIME = "current_time"
    SECTOR = "sector"
    NUMBER_OF_SECTORS = "number_of_sectors"
    SECTOR_TIMER = "sector_timer"
    TOOTHBRUSH_STATE = "toothbrush_state"
    MODE = "mode"
    SIGNAL_STRENGTH = "signal_strength"
    BATTERY_PERCENT = "battery_percent"


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
    2: "run",
    3: "charge",
    4: "shutdown",
    6: "validate",
    7: "lightsout",
}


SONICARE_MANUFACTURER = 477


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
        for service in client.services:
            _LOGGER.debug("Service uuid=%s handle=%s", service.uuid, service.handle)
            for characteristic in service.characteristics:
                try:
                    value_char = client.services.get_characteristic(characteristic.uuid)
                    value_payload = await client.read_gatt_char(value_char)
                    _LOGGER.error(
                        "Characteristic uuid=%s handle=%s ValueChar=%s ValuePayload=%s",
                        characteristic.uuid,
                        characteristic.handle,
                        value_char,
                        value_payload,
                    )
                except Exception:
                    _LOGGER.debug("Exception reading characteristic")

        try:
            battery_char = client.services.get_characteristic(CHARACTERISTIC_BATTERY)
            battery_payload = await client.read_gatt_char(battery_char)

            brushing_time_char = client.services.get_characteristic(
                CHARACTERISTIC_BRUSHING_TIME
            )
            brushing_time_payload = await client.read_gatt_char(brushing_time_char)

            state_char = client.services.get_characteristic(CHARACTERISTIC_STATE)
            state_payload = await client.read_gatt_char(state_char)
            tb_state = STATES.get(state_payload[0], f"unknown state {state_payload[0]}")

            current_time_char = client.services.get_characteristic(
                CHARACTERISTIC_CURRENT_TIME
            )
            current_time_payload = await client.read_gatt_char(current_time_char)
        finally:
            _LOGGER.error("Unable to get data in async_poll")
            await client.disconnect()
        self.update_sensor(
            str(SonicareSensor.BRUSHING_TIME),
            None,
            brushing_time_payload,
            None,
            "Brushing time",
        )
        self.update_sensor(
            str(SonicareSensor.BATTERY_PERCENT),
            Units.PERCENTAGE,
            battery_payload[0],
            SensorDeviceClass.BATTERY,
            "Battery",
        )

        self.update_sensor(
            str(SonicareSensor.TOOTHBRUSH_STATE),
            None,
            tb_state,
            None,
            "Toothbrush State",
        )

        self.update_sensor(
            str(SonicareSensor.CURRENT_TIME),
            None,
            current_time_payload,
            None,
            "Toothbrush current time",
        )

        return self._finish_update()
