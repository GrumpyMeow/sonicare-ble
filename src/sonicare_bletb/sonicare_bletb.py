from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakDBusError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)

from sonicare_bletb.const import (
    CHARACTERISTIC_APPEARANCE,
    CHARACTERISTIC_AVAILABLE_BRUSHING_ROUTINE_4080,
    CHARACTERISTIC_BATTERY_LEVEL,
    CHARACTERISTIC_BRUSHING_SESSION_ID,
    CHARACTERISTIC_BRUSHING_TIME,
    CHARACTERISTIC_DEVICE_NAME,
    CHARACTERISTIC_FIRMWARE_REVISION,
    CHARACTERISTIC_HANDLE_TIME,
    CHARACTERISTIC_HARDWARE_REVISION,
    CHARACTERISTIC_INTENSITY,
    CHARACTERISTIC_LAST_SESSION_ID,
    CHARACTERISTIC_LOADED_SESSION_ID,
    CHARACTERISTIC_MANUFACTURER_NAME,
    CHARACTERISTIC_MODEL,
    CHARACTERISTIC_PNP_ID,
    CHARACTERISTIC_PREFERED,
    CHARACTERISTIC_REGULATORY,
    CHARACTERISTIC_ROUTINE_LENGTH,
    CHARACTERISTIC_SERIAL_NUMBER,
    CHARACTERISTIC_SERVICE_CHANGED,
    CHARACTERISTIC_SOFTWARE_REVISION,
    CHARACTERISTIC_SYSTEM_ID,
    CHARACTERISTIC_UNKNOWN1,
    CHARACTERISTIC_UPDATED_HANDLE_SESSION_STATE,
    STATES,
)

from .models import SonicareBLETBState

BLEAK_BACKOFF_TIME = 0.25

__version__ = "0.0.0"


WrapFuncType = TypeVar("WrapFuncType", bound=Callable[..., Any])

RETRY_BACKOFF_EXCEPTIONS = (BleakDBusError,)

_LOGGER = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = sys.maxsize


class SonicareBLETB:
    def __init__(
        self,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        """Init the SonicareBLETBLETB."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = SonicareBLETBState()
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[SonicareBLETBState], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._buf = b""
        _LOGGER.warning("%s: Init", self.name)

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        _LOGGER.warning(
            "%s: set_ble_device_and_advertisement_data: %s %s",
            self.name,
            ble_device,
            advertisement_data,
        )
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def _address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._ble_device.name or self._ble_device.address

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def state(self) -> SonicareBLETBState:
        """Return the state."""
        return self._state

    @property
    def handle_state(self) -> str:
        return self._state.handle_state

    @property
    def brushing_time(self) -> int:
        return self._state.brushing_time

    @property
    def battery_level(self) -> int:
        return self._state.battery_level

    @property
    def routine_length(self) -> int:
        return self._state.routine_length

    @property
    def available_brushing_routine(self) -> int:
        return self._state.available_brushing_routine

    @property
    def intensity(self) -> int:
        return self._state.intensity

    @property
    def loaded_session_id(self) -> int:
        return self._state.loaded_session_id

    @property
    def handle_time(self) -> datetime:
        return self._state.handle_time

    async def stop(self) -> None:
        """Stop the SonicareBLETB."""
        _LOGGER.debug("%s: Stop", self.name)
        await self._execute_disconnect()

    def _fire_callbacks(self) -> None:
        """Fire the callbacks."""
        _LOGGER.warning("%s: _fire_callbacks: %s", self.name, self._state)
        for callback in self._callbacks:
            callback(self._state)

    def register_callback(
        self, callback: Callable[[SonicareBLETBState], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""
        _LOGGER.warning("%s: register_callback", self.name)

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    def _fire_disconnected_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._disconnected_callbacks:
            callback()

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""
        _LOGGER.warning("%s: register_disconnected_callback", self.name)

        def unregister_callback() -> None:
            self._disconnected_callbacks.remove(callback)

        self._disconnected_callbacks.append(callback)
        return unregister_callback

    async def process_characteristic_value(self, characteristic_uuid, value) -> None:
        _LOGGER.warning("Update characteristic %s with %s", characteristic_uuid, value)
        if characteristic_uuid == CHARACTERISTIC_DEVICE_NAME:
            self._ble_device.name = "".join([str(v) for v in value])
        elif characteristic_uuid == CHARACTERISTIC_HANDLE_TIME:
            self._state.handle_time = datetime.fromtimestamp(
                value[0] | value[1] << 8 | value[2] << 16 | value[3] << 24
            )
        elif characteristic_uuid == CHARACTERISTIC_BRUSHING_SESSION_ID:
            self._state.brushing_session_id = value[0]
        elif characteristic_uuid == CHARACTERISTIC_LAST_SESSION_ID:
            self._state.last_session_id = value[0]
        elif characteristic_uuid == CHARACTERISTIC_UNKNOWN1:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_MODEL:
            self._ble_device.name = "".join([str(v) for v in value])
        elif characteristic_uuid == CHARACTERISTIC_MANUFACTURER_NAME:
            self._ble_device.name = "".join([str(v) for v in value])
        elif characteristic_uuid == CHARACTERISTIC_SERIAL_NUMBER:
            self._ble_device.name = list(map(lambda i: f"{int(i):02x}", value))
        elif characteristic_uuid == CHARACTERISTIC_FIRMWARE_REVISION:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_HARDWARE_REVISION:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_SOFTWARE_REVISION:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_REGULATORY:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_PNP_ID:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_APPEARANCE:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_PREFERED:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_SERVICE_CHANGED:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_SYSTEM_ID:
            self._ble_device.name = self._ble_device.name
        elif characteristic_uuid == CHARACTERISTIC_BATTERY_LEVEL:
            self._state.battery_level = value[0]
        elif characteristic_uuid == CHARACTERISTIC_UPDATED_HANDLE_SESSION_STATE:
            state = STATES.get(value[0], f"unknown state value {value[0]}")
            self._state.handle_state = state
        elif characteristic_uuid == CHARACTERISTIC_BRUSHING_TIME:
            self._state.brushing_time = value[0]
        elif characteristic_uuid == CHARACTERISTIC_AVAILABLE_BRUSHING_ROUTINE_4080:
            self._state.available_brushing_routine = value[0]
        elif characteristic_uuid == CHARACTERISTIC_ROUTINE_LENGTH:
            self._state.routine_length = value[0]
        elif characteristic_uuid == CHARACTERISTIC_INTENSITY:
            self._state.intensity = value[0]
        elif characteristic_uuid == CHARACTERISTIC_LOADED_SESSION_ID:
            self._state.loaded_session_id = value[0]
        else:
            _LOGGER.debug(f"Unknown characteristic {characteristic_uuid} {value}")

        self._fire_callbacks()

    async def initialise(self) -> None:
        _LOGGER.warning("%s: initialise", self.name)
        await self._ensure_connected()
        _LOGGER.warning(
            "%s: Subscribe to notifications; RSSI: %s", self.name, self.rssi
        )

        if self._client is not None:
            _LOGGER.warning("%s: Get_services", self.name)
            await self._client.get_services()
            for service in self._client.services:
                for characteristic in service.characteristics:
                    try:
                        value_char = self._client.services.get_characteristic(
                            characteristic.uuid
                        )
                        if "read" in value_char.properties:
                            value = await self._client.read_gatt_char(
                                characteristic.uuid
                            )
                            await self.process_characteristic_value(
                                characteristic.uuid, value
                            )
                        if "indicate" in value_char.properties:
                            await self._client.start_notify(
                                char_specifier=characteristic.uuid,
                                callback=self._async_notification_handler,
                            )
                        if "notify" in value_char.properties:
                            await self._client.start_notify(
                                char_specifier=characteristic.uuid,
                                callback=self._async_notification_handler,
                            )
                    except Exception:
                        _LOGGER.debug("Error on characteristic %s", characteristic.uuid)

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._connect_lock.locked():
            _LOGGER.warning(
                "%s: Connection already in progress, waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        if self._client and self._client.is_connected:
            return
        async with self._connect_lock:
            # Check again while holding the lock
            if self._client and self._client.is_connected:
                return
            _LOGGER.warning("%s: Connecting; RSSI: %s", self.name, self.rssi)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
            _LOGGER.warning("%s: Connected; RSSI: %s", self.name, self.rssi)

            self._client = client

    async def _reconnect(self) -> None:
        """Attempt a reconnect"""
        _LOGGER.warning("ensuring connection")
        try:
            await self._ensure_connected()
            _LOGGER.warning("ensured connection - initialising")
            await self.initialise()
        except BleakNotFoundError:
            _LOGGER.debug("failed to ensure connection - backing off")
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug("reconnecting again")
            asyncio.create_task(self._reconnect())

    async def _async_notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle notification responses."""
        await self.process_characteristic_value(characteristic.uuid, data)

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        self._fire_disconnected_callbacks()
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s", self.name, self.rssi
            )
            return
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.name,
            self.rssi,
        )
        asyncio.create_task(self._reconnect())

    def _disconnect(self) -> None:
        """Disconnect from device."""
        asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting",
            self.name,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                # await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()
