"""Use of this source code is governed by the MIT license found in the LICENSE file.

Plugwise backend module for Home Assistant Core.
"""
from __future__ import annotations

import datetime as dt

import aiohttp
from defusedxml import ElementTree as etree

# Dict as class
from munch import Munch

# Version detection
import semver

from .constants import (
    # ADAM,
    APPLIANCES,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN_OBJECTS,
    LOCATIONS,
    LOGGER,
    MODULES,
    NOTIFICATIONS,
    REQUIRE_APPLIANCES,
    RULES,
    SMILES,
    STATUS,
    SWITCH_GROUP_TYPES,
    SYSTEM,
    ZONE_THERMOSTATS,
    DeviceData,
    GatewayData,
    PlugwiseData,
)
from .exceptions import (
    InvalidSetupError,
    PlugwiseError,
    ResponseError,
    UnsupportedDeviceError,
)
from .helper import SmileComm, SmileHelper


def remove_empty_platform_dicts(data: DeviceData) -> DeviceData:
    """Helper-function for removing any empty platform dicts."""
    if not data["binary_sensors"]:
        data.pop("binary_sensors")
    if not data["sensors"]:
        data.pop("sensors")
    if not data["switches"]:
        data.pop("switches")

    return data


class SmileData(SmileHelper):
    """The Plugwise Smile main class."""

    def _update_gw_devices(self) -> None:
        """Helper-function for _all_device_data() and async_update().

        Collect data for each device and add to self.gw_devices.
        """
        for device_id, device in self.gw_devices.items():
            data = self._get_device_data(device_id)
            if (
                "binary_sensors" in device
                and "plugwise_notification" in device["binary_sensors"]
            ) or (
                device_id == self.gateway_id
                and (
                    self._is_thermostat
                    or (self.smile_type == "power" and not self._smile_legacy)
                )
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
        self.device_items = self._count
        self.device_list = []
        for device in self.gw_devices:
            self.device_list.append(device)

        self.gw_data.update(
            {
                "gateway_id": self.gateway_id,
                "item_count": self._count,
                "notifications": self._notifications,
                "smile_name": self.smile_name,
            }
        )
        if self._is_thermostat:
            self.gw_data.update({
                "heater_id": self._heater_id, "cooling_present": False
                }
            )

    def get_all_devices(self) -> None:
        """Determine the evices present from the obtained XML-data.

        Run this functions once to gather the initial device configuration,
        then regularly run async_update() to refresh the device data.
        """
        # Gather all the devices and their initial data
        self._all_appliances()
        if self.smile_type == "thermostat":
            self._scan_thermostats()
            # Collect a list of thermostats with offset-capability
            self.therms_with_offset_func = (
                self._get_appliances_with_offset_functionality()
            )

        # Collect switching- or pump-group data
        if group_data := self._get_group_switches():
            self.gw_devices.update(group_data)

        # Collect the remaining data for all device
        self._all_device_data()

    def _device_data_switching_group(
        self, device: DeviceData, device_data: DeviceData
    ) -> DeviceData:
        """Helper-function for _get_device_data().

        Determine switching group device data.
        """
        if device["dev_class"] not in SWITCH_GROUP_TYPES:
            return device_data

        counter = 0
        for member in device["members"]:
            if self.gw_devices[member]["switches"].get("relay"):
                counter += 1
        device_data["switches"]["relay"] = counter != 0
        self._count += 1
        return device_data

    def _device_data_climate(
        self, device: DeviceData, device_data: DeviceData
    ) -> DeviceData:
        """Helper-function for _get_device_data().

        Determine climate-control device data.
        """
        loc_id = device["location"]

        # Presets
        device_data["preset_modes"] = None
        device_data["active_preset"] = None
        self._count += 2
        if presets := self._presets():
            device_data["preset_modes"] = list(presets)
            device_data["active_preset"] = self._preset()

        # Schedule
        avail_schedules, sel_schedule = self._schedules(loc_id)
        device_data["available_schedules"] = avail_schedules
        device_data["select_schedule"] = sel_schedule
        self._count += 2

        # Operation modes: auto, heat, heat_cool, cool and off
        device_data["mode"] = "auto"
        self._count += 1
        if sel_schedule == "None":
            device_data["mode"] = "heat"

        if "None" not in avail_schedules:
            loc_schedule_states = {}
            for schedule in avail_schedules:
                loc_schedule_states[schedule] = (
                    "off" if device_data["mode"] == "auto" else "on"
                )

        return device_data

    def _check_availability(
        self, device: DeviceData, device_data: DeviceData
    ) -> DeviceData:
        """Helper-function for _get_device_data().

        Provide availability status for the wired-commected devices.
        """
        # OpenTherm device
        if device["dev_class"] == "heater_central" and device["name"] != "OnOff":
            device_data["available"] = True
            self._count += 1
            for data in self._notifications.values():
                for msg in data.values():
                    if "no OpenTherm communication" in msg:
                        device_data["available"] = False

        # Smartmeter
        if device["dev_class"] == "smartmeter":
            device_data["available"] = True
            self._count += 1
            for data in self._notifications.values():
                for msg in data.values():
                    if "P1 does not seem to be connected to a smart meter" in msg:
                        device_data["available"] = False

        return device_data

    def _get_device_data(self, dev_id: str) -> DeviceData:
        """Helper-function for _all_device_data() and async_update().

        Provide device-data, based on Location ID (= dev_id), from APPLIANCES.
        """
        device = self.gw_devices[dev_id]
        device_data = self._get_measurement_data(dev_id)
        # Generic
        if self.smile_type == "thermostat" and device["dev_class"] == "gateway":
            # Adam & Anna: the Smile outdoor_temperature is present in DOMAIN_OBJECTS and LOCATIONS - under Home
            # The outdoor_temperature present in APPLIANCES is a local sensor connected to the active device
            outdoor_temperature = self._object_value(
                self._home_location, "outdoor_temperature"
            )
            if outdoor_temperature is not None:
                device_data["sensors"]["outdoor_temperature"] = outdoor_temperature
                self._count += 1

        # Switching groups data
        device_data = self._device_data_switching_group(device, device_data)
        # No need to obtain thermostat data when the device is not a thermostat
        if device["dev_class"] not in ZONE_THERMOSTATS:
            return device_data

        # Thermostat data (presets, temperatures etc)
        device_data = self._device_data_climate(device, device_data)

        return device_data


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
        self._previous_day_number: str = "0"
        self._target_smile: str | None = None

    async def connect(self) -> bool:
        """Connect to Plugwise device and determine its name, type and version."""
        result = await self._request(DOMAIN_OBJECTS)
        # Work-around for Stretch fv 2.7.18
        if not (vendor_names := result.findall("./module/vendor_name")):
            result = await self._request(MODULES)
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
        await self._full_update_device()

        return True

    async def _smile_detect(self, result: etree, dsmrmain: etree) -> None:
        """Helper-function for connect().

        Detect which type of Smile is connected.
        """
        model: str = "Unknown"

        # Stretch: find the MAC of the zigbee master_controller (= Stick)
        if network := result.find("./module/protocols/master_controller"):
            self.smile_zigbee_mac_address = network.find("mac_address").text
        # Find the active MAC in case there is an orphaned Stick
        if zb_networks := result.findall("./network"):
            for zb_network in zb_networks:
                if zb_network.find("./nodes/network_router"):
                    network = zb_network.find("./master_controller")
                    self.smile_zigbee_mac_address = network.find("mac_address").text

        # Legacy Anna or Stretch:
        if (
            result.find('./appliance[type="thermostat"]') is not None
            or network is not None
        ):
            self._system = await self._request(SYSTEM)
            self.smile_fw_version = self._system.find("./gateway/firmware").text
            model = self._system.find("./gateway/product").text
            self.smile_hostname = self._system.find("./gateway/hostname").text
            # If wlan0 contains data it's active, so eth0 should be checked last
            for network in ("wlan0", "eth0"):
                locator = f"./{network}/mac"
                if (net_locator := self._system.find(locator)) is not None:
                    self.smile_mac_address = net_locator.text
        else:
            # P1 legacy:
            if dsmrmain is not None:
                self._status = await self._request(STATUS)
                self.smile_fw_version = self._status.find("./system/version").text
                model = self._status.find("./system/product").text
                self.smile_hostname = self._status.find("./network/hostname").text
                self.smile_mac_address = self._status.find("./network/mac_address").text

            else:  # pragma: no cover
                # No cornercase, just end of the line
                LOGGER.error(
                    "Connected but no gateway device information found, please create"
                    " an issue on http://github.com/plugwise/python-plugwise"
                )
                raise ResponseError

        self._smile_legacy = True

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

        if self.smile_type == "stretch":
            self._stretch_v2 = self.smile_version[1].major == 2
            self._stretch_v3 = self.smile_version[1].major == 3

        if self.smile_type == "thermostat":
            self._is_thermostat = True
            # For Adam, Anna, determine the system capabilities:
            # Find the connected heating/cooling device (heater_central),
            # e.g. heat-pump or gas-fired heater
            onoff_boiler: etree = result.find("./module/protocols/onoff_boiler")
            open_therm_boiler: etree = result.find(
                "./module/protocols/open_therm_boiler"
            )
            self._on_off_device = onoff_boiler is not None
            self._opentherm_device = open_therm_boiler is not None

    async def _update_domain_objects(self) -> None:
        """Helper-function for smile.py: full_update_device() and async_update().

        Request domain_objects data.
        """
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

    async def _full_update_device(self) -> None:
        """Perform a first fetch of all XML data, needed for initialization."""
        await self._update_domain_objects()
        self._locations = await self._request(LOCATIONS)
        self._modules = await self._request(MODULES)
        # P1 legacy has no appliances
        if not (self.smile_type == "power" and self._smile_legacy):
            self._appliances = await self._request(APPLIANCES)

    async def async_update(self) -> PlugwiseData:
        """Perform an incremental update for updating the various device states."""
        # Perform a full update at day-change
        day_number = dt.datetime.now().strftime("%w")
        if (
            day_number  # pylint: disable=consider-using-assignment-expr
            != self._previous_day_number
        ):
            LOGGER.debug(
                "Performing daily full-update, reload the Plugwise integration when a single entity becomes unavailable."
            )
            self.gw_data: GatewayData = {}
            self.gw_devices: dict[str, DeviceData] = {}
            await self._full_update_device()
            self.get_all_devices()
        # Otherwise perform an incremental update
        else:
            await self._update_domain_objects()
            match self._target_smile:
                case "smile_v2":
                    self._modules = await self._request(MODULES)
                case self._target_smile if self._target_smile in REQUIRE_APPLIANCES:
                    self._appliances = await self._request(APPLIANCES)

            self._update_gw_devices()
            self.gw_data["notifications"] = self._notifications

        self._previous_day_number = day_number
        return PlugwiseData(self.gw_data, self.gw_devices)

    async def set_schedule_state(self, state: str) -> None:
        """Activate/deactivate the Schedule.

        Determined from - DOMAIN_OBJECTS.
        Used in HA Core to set the hvac_mode: in practice switch between schedule on - off.
        """
        if state not in ["on", "off"]:
            raise PlugwiseError("Plugwise: invalid schedule state.")

        name = "Thermostat schedule"
        schedule_rule_id: str | None = None
        for rule in self._domain_objects.findall("rule"):
            if rule.find("name").text == name:
                schedule_rule_id = rule.attrib["id"]

        if schedule_rule_id is None:
            raise PlugwiseError("Plugwise: no schedule with this name available.")

        new_state = "false"
        if state == "on":
            new_state = "true"

        locator = f'.//*[@id="{schedule_rule_id}"]/template'
        for rule in self._domain_objects.findall(locator):
            template_id = rule.attrib["id"]

        uri = f"{RULES};id={schedule_rule_id}"
        data = (
            "<rules><rule"
            f' id="{schedule_rule_id}"><name><![CDATA[{name}]]></name><template'
            f' id="{template_id}" /><active>{new_state}</active></rule></rules>'
        )

        await self._request(uri, method="put", data=data)

    async def set_preset(self, preset: str) -> None:
        """Set the given Preset on the relevant Thermostat - from DOMAIN_OBJECTS."""
        if (presets := self._presets()) is None:
            raise PlugwiseError("Plugwise: no presets available.")  # pragma: no cover
        if preset not in list(presets):
            raise PlugwiseError("Plugwise: invalid preset.")

        locator = f'rule/directives/when/then[@icon="{preset}"].../.../...'
        rule = self._domain_objects.find(locator)
        data = f'<rules><rule id="{rule.attrib["id"]}"><active>true</active></rule></rules>'

        await self._request(RULES, method="put", data=data)

    async def set_temperature(self, setpoint: str) -> None:
        """Set the given Temperature on the relevant Thermostat."""
        if setpoint is None:
            raise PlugwiseError(
                "Plugwise: failed setting temperature: no valid input provided"
            )  # pragma: no cover"

        temperature = str(setpoint)
        uri = self._thermostat_uri()
        data = (
            "<thermostat_functionality><setpoint>"
            f"{temperature}</setpoint></thermostat_functionality>"
        )

        await self._request(uri, method="put", data=data)

    async def set_number_setpoint(self, key: str, _: str, temperature: float) -> None:
        """Set the max. Boiler or DHW setpoint on the Central Heating boiler."""
        temp = str(temperature)
        thermostat_id: str | None = None
        locator = f'appliance[@id="{self._heater_id}"]/actuator_functionalities/thermostat_functionality'
        if th_func_list := self._appliances.findall(locator):
            for th_func in th_func_list:
                if th_func.find("type").text == key:
                    thermostat_id = th_func.attrib["id"]

        if thermostat_id is None:
            raise PlugwiseError(f"Plugwise: cannot change setpoint, {key} not found.")

        uri = f"{APPLIANCES};id={self._heater_id}/thermostat;id={thermostat_id}"
        data = f"<thermostat_functionality><setpoint>{temp}</setpoint></thermostat_functionality>"
        await self._request(uri, method="put", data=data)

    async def set_temperature_offset(self, _: str, dev_id: str, offset: float) -> None:
        """Set the Temperature offset for thermostats that support this feature."""
        if dev_id not in self.therms_with_offset_func:
            raise PlugwiseError(
                "Plugwise: this device does not have temperature-offset capability."
            )

        value = str(offset)
        uri = f"{APPLIANCES};id={dev_id}/offset;type=temperature_offset"
        data = f"<offset_functionality><offset>{value}</offset></offset_functionality>"

        await self._request(uri, method="put", data=data)

    async def _set_groupswitch_member_state(
        self, members: list[str], state: str, switch: Munch
    ) -> None:
        """Helper-function for set_switch_state().

        Set the given State of the relevant Switch within a group of members.
        """
        for member in members:
            locator = f'appliance[@id="{member}"]/{switch.actuator}/{switch.func_type}'
            switch_id = self._appliances.find(locator).attrib["id"]
            uri = f"{APPLIANCES};id={member}/{switch.device};id={switch_id}"
            if self._stretch_v2:
                uri = f"{APPLIANCES};id={member}/{switch.device}"
            data = f"<{switch.func_type}><{switch.func}>{state}</{switch.func}></{switch.func_type}>"

            await self._request(uri, method="put", data=data)

    async def set_switch_state(
        self, appl_id: str, members: list[str] | None, model: str, state: str
    ) -> None:
        """Set the given State of the relevant Switch."""
        switch = Munch()
        switch.actuator = "actuator_functionalities"
        switch.device = "relay"
        switch.func_type = "relay_functionality"
        switch.func = "state"

        if self._stretch_v2:
            switch.actuator = "actuators"
            switch.func_type = "relay"

        if members is not None:
            return await self._set_groupswitch_member_state(members, state, switch)

        locator = f'appliance[@id="{appl_id}"]/{switch.actuator}/{switch.func_type}'
        found: list[etree] = self._appliances.findall(locator)
        for item in found:
            if (sw_type := item.find("type")) is not None:
                if sw_type.text == switch.act_type:
                    switch_id = item.attrib["id"]
            else:
                switch_id = item.attrib["id"]
                break

        uri = f"{APPLIANCES};id={appl_id}/{switch.device};id={switch_id}"
        if self._stretch_v2:
            uri = f"{APPLIANCES};id={appl_id}/{switch.device}"
        data = f"<{switch.func_type}><{switch.func}>{state}</{switch.func}></{switch.func_type}>"

        if model == "relay":
            locator = (
                f'appliance[@id="{appl_id}"]/{switch.actuator}/{switch.func_type}/lock'
            )
            # Don't bother switching a relay when the corresponding lock-state is true
            if self._appliances.find(locator).text == "true":
                raise PlugwiseError("Plugwise: the locked Relay was not switched.")

        await self._request(uri, method="put", data=data)

    async def delete_notification(self) -> None:
        """Delete the active Plugwise Notification."""
        await self._request(NOTIFICATIONS, method="delete")
