"""Use of this source code is governed by the MIT license found in the LICENSE file.

Plugwise backend module for Home Assistant Core.
"""
from __future__ import annotations

import aiohttp
from defusedxml import ElementTree as etree

# Dict as class
from munch import Munch

# Version detection
import semver

from .constants import (
    ADAM,
    APPLIANCES,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN_OBJECTS,
    LOCATIONS,
    LOGGER,
    MAX_SETPOINT,
    MIN_SETPOINT,
    NOTIFICATIONS,
    RULES,
    SMILES,
    SWITCH_GROUP_TYPES,
    ZONE_THERMOSTATS,
    ActuatorData,
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

    def update_for_cooling(self, device: DeviceData) -> DeviceData:
        """Helper-function for adding/updating various cooling-related values."""
        # For heating + cooling, replace setpoint with setpoint_high/_low
        if self._cooling_present:
            thermostat = device["thermostat"]
            sensors = device["sensors"]
            temp_dict: ActuatorData = {
                "setpoint_low": thermostat["setpoint"],
                "setpoint_high": MAX_SETPOINT,
            }
            if self._cooling_enabled:
                temp_dict = {
                    "setpoint_low": MIN_SETPOINT,
                    "setpoint_high": thermostat["setpoint"],
                }
            thermostat.pop("setpoint")
            temp_dict.update(thermostat)
            device["thermostat"] = temp_dict
            if "setpoint" in sensors:
                sensors.pop("setpoint")
            sensors["setpoint_low"] = temp_dict["setpoint_low"]
            sensors["setpoint_high"] = temp_dict["setpoint_high"]
            self._count += 2

        return device

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
                and (self._is_thermostat or self.smile_type == "power")
            ):
                data["binary_sensors"]["plugwise_notification"] = bool(
                    self._notifications
                )
                self._count += 1
            device.update(data)

            # Update for cooling
            if device["dev_class"] in ZONE_THERMOSTATS and not self.smile(ADAM):
                self.update_for_cooling(device)

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
            self.gw_data.update(
                {"heater_id": self._heater_id, "cooling_present": self._cooling_present}
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

    def _device_data_adam(
        self, device: DeviceData, device_data: DeviceData
    ) -> DeviceData:
        """Helper-function for _get_device_data().

        Determine Adam heating-status for on-off heating via valves.
        """
        # Indicate heating_state based on valves being open in case of city-provided heating
        if (
            self.smile(ADAM)
            and device.get("dev_class") == "heater_central"
            and self._on_off_device
            and isinstance(self._heating_valves(), int)
        ):
            device_data["binary_sensors"]["heating_state"] = self._heating_valves() != 0

        return device_data

    def check_reg_mode(self, mode: str) -> bool:
        """Helper-function for device_data_climate()."""
        gateway = self.gw_devices[self.gateway_id]
        return (
            "regulation_modes" in gateway and gateway["select_regulation_mode"] == mode
        )

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
        if presets := self._presets(loc_id):
            device_data["preset_modes"] = list(presets)
            device_data["active_preset"] = self._preset(loc_id)

        # Schedule
        avail_schedules, sel_schedule = self._schedules(loc_id)
        device_data["available_schedules"] = avail_schedules
        device_data["select_schedule"] = sel_schedule
        self._count += 2

        # Control_state, only for Adam master thermostats
        if ctrl_state := self._control_state(loc_id):
            device_data["control_state"] = ctrl_state
            self._count += 1

        # Operation modes: auto, heat, heat_cool, cool and off
        device_data["mode"] = "auto"
        self._count += 1
        if sel_schedule == "None":
            device_data["mode"] = "heat"
            if self._cooling_present:
                device_data["mode"] = (
                    "cool" if self.check_reg_mode("cooling") else "heat_cool"
                )

        if self.check_reg_mode("off"):
            device_data["mode"] = "off"

        if "None" not in avail_schedules:
            loc_schedule_states = {}
            for schedule in avail_schedules:
                loc_schedule_states[schedule] = (
                    "off" if device_data["mode"] == "auto" else "on"
                )

            self._schedule_old_states[loc_id] = loc_schedule_states

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

            # Show the allowed regulation modes
            if self._reg_allowed_modes:
                device_data["regulation_modes"] = self._reg_allowed_modes
                self._count += 1

        # Show the allowed dhw_modes
        if device["dev_class"] == "heater_central" and self._dhw_allowed_modes:
            device_data["dhw_modes"] = self._dhw_allowed_modes
            self._count += 1

        # Check availability of wired-connected devices
        self._check_availability(device, device_data)

        # Switching groups data
        device_data = self._device_data_switching_group(device, device_data)
        # Specific, not generic Adam data
        device_data = self._device_data_adam(device, device_data)
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

        # Check if Anna is connected to an Adam
        if "159.2" in models:
            LOGGER.error(
                "Your Anna is connected to an Adam, make sure to only add the Adam as integration."
            )
            raise InvalidSetupError

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

            # Determine the presence of special features
            locator_1 = "./gateway/features/cooling"
            locator_2 = "./gateway/features/elga_support"
            if result.find(locator_1) is not None:
                self._cooling_present = True
            if result.find(locator_2) is not None:
                self._elga = True

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

    async def async_update(self) -> PlugwiseData:
        """Perform an incremental update for updating the various device states."""
        self.gw_data: GatewayData = {}
        self.gw_devices: dict[str, DeviceData] = {}
        await self._full_update_device()
        self.get_all_devices()

        return PlugwiseData(self.gw_data, self.gw_devices)

    def determine_contexts(
        self, loc_id: str, name: str, state: str, sched_id: str
    ) -> etree:
        """Helper-function for set_schedule_state()."""
        locator = f'.//*[@id="{sched_id}"]/contexts'
        contexts = self._domain_objects.find(locator)
        locator = f'.//*[@id="{loc_id}"].../...'
        if (subject := contexts.find(locator)) is None:
            subject = f'<context><zone><location id="{loc_id}" /></zone></context>'
            subject = etree.fromstring(subject)

        if state == "off":
            self._last_active[loc_id] = name
            contexts.remove(subject)
        if state == "on":
            contexts.append(subject)

        return etree.tostring(contexts, encoding="unicode").rstrip()

    async def set_schedule_state(
        self,
        loc_id: str,
        new_state: str,
        name: str | None = None,
    ) -> None:
        """Activate/deactivate the Schedule, with the given name, on the relevant Thermostat.

        Determined from - DOMAIN_OBJECTS.
        Used in HA Core to set the hvac_mode: in practice switch between schedule on - off.
        """
        # Input checking
        if new_state not in ["on", "off"]:
            raise PlugwiseError("Plugwise: invalid schedule state.")
        if name is None:
            if schedule_name := self._last_active[loc_id]:
                name = schedule_name
            else:
                return

        assert isinstance(name, str)
        schedule_rule = self._rule_ids_by_name(name, loc_id)
        # Raise an error when the schedule name does not exist
        if not schedule_rule or schedule_rule is None:
            raise PlugwiseError("Plugwise: no schedule with this name available.")

        # If no state change is requested, do nothing
        if new_state == self._schedule_old_states[loc_id][name]:
            return

        schedule_rule_id: str = next(iter(schedule_rule))

        template = (
            '<template tag="zone_preset_based_on_time_and_presence_with_override" />'
        )
        if not self.smile(ADAM):
            locator = f'.//*[@id="{schedule_rule_id}"]/template'
            template_id = self._domain_objects.find(locator).attrib["id"]
            template = f'<template id="{template_id}" />'

        contexts = self.determine_contexts(loc_id, name, new_state, schedule_rule_id)
        uri = f"{RULES};id={schedule_rule_id}"
        data = (
            f'<rules><rule id="{schedule_rule_id}"><name><![CDATA[{name}]]></name>'
            f"{template}{contexts}</rule></rules>"
        )
        await self._request(uri, method="put", data=data)
        self._schedule_old_states[loc_id][name] = new_state

    async def set_preset(self, loc_id: str, preset: str) -> None:
        """Set the given Preset on the relevant Thermostat - from LOCATIONS."""
        if (presets := self._presets(loc_id)) is None:
            raise PlugwiseError("Plugwise: no presets available.")  # pragma: no cover
        if preset not in list(presets):
            raise PlugwiseError("Plugwise: invalid preset.")

        current_location = self._domain_objects.find(f'location[@id="{loc_id}"]')
        location_name = current_location.find("name").text
        location_type = current_location.find("type").text

        uri = f"{LOCATIONS};id={loc_id}"
        data = (
            "<locations><location"
            f' id="{loc_id}"><name>{location_name}</name><type>{location_type}'
            f"</type><preset>{preset}</preset></location></locations>"
        )

        await self._request(uri, method="put", data=data)

    async def set_temperature(self, loc_id: str, items: dict[str, float]) -> None:
        """Set the given Temperature on the relevant Thermostat."""
        setpoint: float | None = None

        if "setpoint" in items:
            setpoint = items["setpoint"]

        if self._cooling_present and not self.smile(ADAM):
            if "setpoint_high" not in items:
                raise PlugwiseError(
                    "Plugwise: failed setting temperature: no valid input provided"
                )
            tmp_setpoint_high = items["setpoint_high"]
            tmp_setpoint_low = items["setpoint_low"]
            if self._cooling_enabled:  # in cooling mode
                setpoint = tmp_setpoint_high
                if tmp_setpoint_low != MIN_SETPOINT:
                    raise PlugwiseError(
                        "Plugwise: heating setpoint cannot be changed when in cooling mode"
                    )
            else:  # in heating mode
                setpoint = tmp_setpoint_low
                if tmp_setpoint_high != MAX_SETPOINT:
                    raise PlugwiseError(
                        "Plugwise: cooling setpoint cannot be changed when in heating mode"
                    )

        if setpoint is None:
            raise PlugwiseError(
                "Plugwise: failed setting temperature: no valid input provided"
            )  # pragma: no cover"

        temperature = str(setpoint)
        uri = self._thermostat_uri(loc_id)
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
        if th_func_list := self._domain_objects.findall(locator):
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
            switch_id = self._domain_objects.find(locator).attrib["id"]
            uri = f"{APPLIANCES};id={member}/{switch.device};id={switch_id}"
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
        if model == "dhw_cm_switch":
            switch.device = "toggle"
            switch.func_type = "toggle_functionality"
            switch.act_type = "domestic_hot_water_comfort_mode"

        if model == "cooling_ena_switch":
            switch.device = "toggle"
            switch.func_type = "toggle_functionality"
            switch.act_type = "cooling_enabled"

        if model == "lock":
            switch.func = "lock"
            state = "false" if state == "off" else "true"

        if members is not None:
            return await self._set_groupswitch_member_state(members, state, switch)

        locator = f'appliance[@id="{appl_id}"]/{switch.actuator}/{switch.func_type}'
        found: list[etree] = self._domain_objects.findall(locator)
        for item in found:
            if (sw_type := item.find("type")) is not None:
                if sw_type.text == switch.act_type:
                    switch_id = item.attrib["id"]
            else:
                switch_id = item.attrib["id"]
                break

        uri = f"{APPLIANCES};id={appl_id}/{switch.device};id={switch_id}"
        data = f"<{switch.func_type}><{switch.func}>{state}</{switch.func}></{switch.func_type}>"

        if model == "relay":
            locator = (
                f'appliance[@id="{appl_id}"]/{switch.actuator}/{switch.func_type}/lock'
            )
            # Don't bother switching a relay when the corresponding lock-state is true
            if self._domain_objects.find(locator).text == "true":
                raise PlugwiseError("Plugwise: the locked Relay was not switched.")

        await self._request(uri, method="put", data=data)

    async def set_regulation_mode(self, mode: str) -> None:
        """Set the heating regulation mode."""
        if mode not in self._reg_allowed_modes:
            raise PlugwiseError("Plugwise: invalid regulation mode.")

        uri = f"{APPLIANCES};type=gateway/regulation_mode_control"
        duration = ""
        if "bleeding" in mode:
            duration = "<duration>300</duration>"
        data = f"<regulation_mode_control_functionality>{duration}<mode>{mode}</mode></regulation_mode_control_functionality>"

        await self._request(uri, method="put", data=data)

    async def set_dhw_mode(self, mode: str) -> None:
        """Set the domestic hot water heating regulation mode."""
        if mode not in self._dhw_allowed_modes:
            raise PlugwiseError("Plugwise: invalid dhw mode.")

        uri = f"{APPLIANCES};type=heater_central/domestic_hot_water_mode_control"
        data = f"<domestic_hot_water_mode_control_functionality><mode>{mode}</mode></domestic_hot_water_mode_control_functionality>"

        await self._request(uri, method="put", data=data)

    async def delete_notification(self) -> None:
        """Delete the active Plugwise Notification."""
        await self._request(NOTIFICATIONS, method="delete")
