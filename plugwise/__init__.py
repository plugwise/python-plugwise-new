"""Use of this source code is governed by the MIT license found in the LICENSE file.

Plugwise backend module for Home Assistant Core.
"""
from __future__ import annotations

import aiohttp
from defusedxml import ElementTree as etree

# Version detection
import semver

from .constants import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN_OBJECTS,
    LOGGER,
    SMILES,
    DeviceData,
    GatewayData,
    PlugwiseData,
)
from .exceptions import (
    ResponseError,
    UnsupportedDeviceError,
)
from .helper import SmileComm, SmileHelper, remove_empty_platform_dicts


class SmileData(SmileHelper):
    """The Plugwise Smile main class."""

    def _update_gw_devices(self) -> None:
        """Helper-function for _all_device_data() and async_update().

        Collect data for each device and add to self.gw_devices.
        """
        for device_id, device in self.gw_devices.items():
            data = self._get_measurement_data(device_id)

            # Check availability of wired-connected devices
            self._check_availability(device, data)

            # Add/update the plugwise notification(s)
            if (device_id == self.gateway_id and self.smile_type == "power") or (
                "binary_sensors" in device
                and "plugwise_notification" in device["binary_sensors"]
            ):
                data["binary_sensors"]["plugwise_notification"] = bool(
                    self._notifications
                )
                self._count += 1

            device.update(data)

            remove_empty_platform_dicts(device)

    def _all_device_data(self) -> None:
        """Helper-function for get_all_devices().

        Collect data for each device and add to self.gw_data and self.gw_devices.
        """
        self._update_gw_devices()
        self._update_gw_data()

        self.device_items = self._count
        self.device_list = []
        for device in self.gw_devices:
            self.device_list.append(device)

    def get_all_devices(self) -> None:
        """Determine the evices present from the obtained XML-data.

        Run this functions once to gather the initial device configuration,
        then regularly run async_update() to refresh the device data.
        """
        # Gather all the devices and their initial data
        self._all_appliances()
        # Collect the remaining data for all device
        self._all_device_data()


class Smile(SmileComm, SmileData):
    """The Plugwise SmileConnect class."""

    # pylint: disable=too-many-instance-attributes, too-many-public-methods

    def __init__(
        self,
        host: str,
        password: str,
        username: str = DEFAULT_USERNAME,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        websession: aiohttp.ClientSession | None = None,
    ) -> None:
        """Set the constructor for this class."""
        super().__init__(
            host,
            password,
            username,
            port,
            timeout,
            websession,
        )
        SmileData.__init__(self)

        self.smile_hostname: str | None = None
        self._target_smile: str | None = None

    async def _smile_detect(self, result: etree, dsmrmain: etree) -> None:
        """Helper-function for connect().

        Detect which type of Smile is connected.
        """
        model: str = "Unknown"
        if (gateway := result.find("./gateway")) is not None:
            if (v_model := gateway.find("vendor_model")) is not None:
                model = v_model.text
            self.smile_fw_version = gateway.find("firmware_version").text
            self.smile_hw_version = gateway.find("hardware_version").text
            self.smile_hostname = gateway.find("hostname").text
            self.smile_mac_address = gateway.find("mac_address").text

        if model == "Unknown" or self.smile_fw_version is None:  # pragma: no cover
            # Corner case check
            LOGGER.error(
                "Unable to find model or version information, please create"
                " an issue on http://github.com/plugwise/python-plugwise"
            )
            raise UnsupportedDeviceError

        ver = semver.version.Version.parse(self.smile_fw_version)
        self._target_smile = f"{model}_v{ver.major}"
        LOGGER.debug("Plugwise identified as %s", self._target_smile)
        if self._target_smile not in SMILES:
            LOGGER.error(
                "Your version Smile identified as %s seems unsupported by our plugin, please"
                " create an issue on http://github.com/plugwise/python-plugwise",
                self._target_smile,
            )
            raise UnsupportedDeviceError

        self.smile_model = "Gateway"
        self.smile_name = SMILES[self._target_smile].smile_name
        self.smile_type = SMILES[self._target_smile].smile_type
        self.smile_version = (self.smile_fw_version, ver)

    async def _update_device(self) -> None:
        """Perform a fetch of the required XML data."""
        self._domain_objects = await self._request(DOMAIN_OBJECTS)

        # If Plugwise notifications present:
        self._notifications = {}
        for notification in self._domain_objects.findall("./notification"):
            try:
                msg_id = notification.attrib["id"]
                msg_type = notification.find("type").text
                msg = notification.find("message").text
                self._notifications.update({msg_id: {msg_type: msg}})
                LOGGER.debug("Plugwise notifications: %s", self._notifications)
            except AttributeError:  # pragma: no cover
                LOGGER.debug(
                    "Plugwise notification present but unable to process, manually investigate: %s",
                    f"{self._endpoint}{DOMAIN_OBJECTS}",
                )

    async def connect(self) -> bool:
        """Connect to Plugwise device and determine its name, type and version."""
        result = await self._request(DOMAIN_OBJECTS)
        vendor_names = result.findall("./module/vendor_name")

        names: list[str] = []
        for name in vendor_names:
            names.append(name.text)

        vendor_models = result.findall("./module/vendor_model")
        models: list[str] = []
        for model in vendor_models:
            models.append(model.text)

        dsmrmain = result.find("./module/protocols/dsmrmain")
        if "Plugwise" not in names and dsmrmain is None:  # pragma: no cover
            LOGGER.error(
                "Connected but expected text not returned, we got %s. Please create"
                " an issue on http://github.com/plugwise/python-plugwise",
                result,
            )
            raise ResponseError

        # Determine smile specifics
        await self._smile_detect(result, dsmrmain)

        # Update all endpoints on first connect
        await self._update_device()

        return True

    async def async_update(self) -> PlugwiseData:
        """Perform an update."""
        self.gw_data: GatewayData = {}
        self.gw_devices: dict[str, DeviceData] = {}
        await self._update_device()
        self.get_all_devices()

        return PlugwiseData(self.gw_data, self.gw_devices)
