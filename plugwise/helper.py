"""Use of this source code is governed by the MIT license found in the LICENSE file.

Plugwise Smile protocol helpers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import cast

# This way of importing aiohttp is because of patch/mocking in testing (aiohttp timeouts)
from aiohttp import BasicAuth, ClientError, ClientResponse, ClientSession, ClientTimeout

# Time related
from dateutil import tz
from dateutil.parser import parse
from defusedxml import ElementTree as etree
from munch import Munch
import semver

from .constants import (
    ACTIVE_ACTUATORS,
    ACTUATOR_CLASSES,
    ADAM,
    ANNA,
    ATTR_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    BINARY_SENSORS,
    DATA,
    DEVICE_MEASUREMENTS,
    DHW_SETPOINT,
    ENERGY_KILO_WATT_HOUR,
    ENERGY_WATT_HOUR,
    HEATER_CENTRAL_MEASUREMENTS,
    LIMITS,
    LOCATIONS,
    LOGGER,
    NONE,
    OBSOLETE_MEASUREMENTS,
    P1_MEASUREMENTS,
    POWER_WATT,
    SENSORS,
    SPECIAL_PLUG_TYPES,
    SWITCH_GROUP_TYPES,
    SWITCHES,
    TEMP_CELSIUS,
    THERMOSTAT_CLASSES,
    TOGGLES,
    UOM,
    ActuatorData,
    ActuatorDataType,
    ActuatorType,
    ApplianceType,
    BinarySensorType,
    DeviceData,
    GatewayData,
    ModelData,
    SensorType,
    SwitchType,
    ThermoLoc,
    ToggleNameType,
)
from .exceptions import (
    ConnectionFailedError,
    InvalidAuthentication,
    InvalidXMLError,
    ResponseError,
)
from .util import escape_illegal_xml_characters, format_measure, version_to_model


def check_model(name: str | None, vendor_name: str | None) -> str | None:
    """Model checking before using version_to_model."""
    if vendor_name == "Plugwise" and ((model := version_to_model(name)) != "Unknown"):
        return model

    return name


def power_data_local_format(
    attrs: dict[str, str], key_string: str, val: str
) -> float | int:
    """Format power data."""
    # Special formatting of P1_MEASUREMENT POWER_WATT values, do not move to util-format_measure() function!
    if all(item in key_string for item in ("electricity", "cumulative")):
        return format_measure(val, ENERGY_KILO_WATT_HOUR)
    if (attrs_uom := getattr(attrs, ATTR_UNIT_OF_MEASUREMENT)) == POWER_WATT:
        return int(round(float(val)))

    return format_measure(val, attrs_uom)


class SmileComm:
    """The SmileComm class."""

    def __init__(
        self,
        host: str,
        password: str,
        username: str,
        port: int,
        timeout: float,
        websession: ClientSession | None,
    ) -> None:
        """Set the constructor for this class."""
        if not websession:
            aio_timeout = ClientTimeout(total=timeout)

            async def _create_session() -> ClientSession:
                return ClientSession(timeout=aio_timeout)  # pragma: no cover

            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._websession = ClientSession(timeout=aio_timeout)
            else:
                self._websession = loop.run_until_complete(
                    _create_session()
                )  # pragma: no cover
        else:
            self._websession = websession

        # Quickfix IPv6 formatting, not covering
        if host.count(":") > 2:  # pragma: no cover
            host = f"[{host}]"

        self._auth = BasicAuth(username, password=password)
        self._endpoint = f"http://{host}:{str(port)}"
        self._timeout = timeout

    async def _request_validate(self, resp: ClientResponse, method: str) -> etree:
        """Helper-function for _request(): validate the returned data."""
        # Command accepted gives empty body with status 202
        if resp.status == 202:
            return

        if resp.status == 401:
            msg = "Invalid Plugwise login, please retry with the correct credentials."
            LOGGER.error("%s", msg)
            raise InvalidAuthentication

        if not (result := await resp.text()) or "<error>" in result:
            LOGGER.warning("Smile response empty or error in %s", result)
            raise ResponseError

        try:
            # Encode to ensure utf8 parsing
            xml = etree.XML(escape_illegal_xml_characters(result).encode())
        except etree.ParseError:
            LOGGER.warning("Smile returns invalid XML for %s", self._endpoint)
            raise InvalidXMLError

        return xml

    async def _request(
        self,
        command: str,
        retry: int = 3,
        method: str = "get",
        data: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> etree:
        """Get/put/delete data from a give URL."""
        resp: ClientResponse
        url = f"{self._endpoint}{command}"

        try:
            if method == "delete":
                resp = await self._websession.delete(url, auth=self._auth)
            if method == "get":
                resp = await self._websession.get(url, headers=headers, auth=self._auth)
            if method == "put":
                headers = {"Content-type": "text/xml"}
                resp = await self._websession.put(
                    url,
                    headers=headers,
                    data=data,
                    auth=self._auth,
                )
        except (
            ClientError
        ) as err:  # ClientError is an ancestor class of ServerTimeoutError
            if retry < 1:
                LOGGER.warning(
                    "Failed sending %s %s to Plugwise Smile, error: %s",
                    method,
                    command,
                    err,
                )
                raise ConnectionFailedError
            return await self._request(command, retry - 1)

        return await self._request_validate(resp, method)

    async def close_connection(self) -> None:
        """Close the Plugwise connection."""
        await self._websession.close()


class SmileHelper:
    """The SmileHelper class."""

    def __init__(self) -> None:
        """Set the constructor for this class."""
        self._cooling_activation_outdoor_temp: float
        self._cooling_deactivation_threshold: float
        self._cooling_present = False
        self._count: int
        self._dhw_allowed_modes: list[str] = []
        self._domain_objects: etree
        self._elga = False
        self._heater_id: str
        self._home_location: str
        self._is_thermostat = False
        self._last_active: dict[str, str | None] = {}
        self._last_modified: dict[str, str] = {}
        self._loc_data: dict[str, ThermoLoc] = {}
        self._notifications: dict[str, dict[str, str]] = {}
        self._on_off_device = False
        self._opentherm_device = False
        self._outdoor_temp: float
        self._reg_allowed_modes: list[str] = []
        self._schedule_old_states: dict[str, dict[str, str]] = {}
        self._status: etree
        self._system: etree
        self._thermo_locs: dict[str, ThermoLoc] = {}
        ###################################################################
        # '_cooling_enabled' can refer to the state of the Elga heatpump
        # connected to an Anna. For Elga, 'elga_status_code' in [8, 9]
        # means cooling mode is available, next to heating mode.
        # 'elga_status_code' = 8 means cooling is active, 9 means idle.
        #
        # '_cooling_enabled' cam refer to the state of the Loria or
        # Thermastage heatpump connected to an Anna. For these,
        # 'cooling_enabled' = on means set to cooling mode, instead of to
        # heating mode.
        # 'cooling_state' = on means cooling is active.
        ###################################################################
        self._cooling_active = False
        self._cooling_enabled = False

        self.device_items: int = 0
        self.device_list: list[str]
        self.gateway_id: str
        self.gw_data: GatewayData = {}
        self.gw_devices: dict[str, DeviceData] = {}
        self.smile_fw_version: str | None = None
        self.smile_hw_version: str | None = None
        self.smile_mac_address: str | None = None
        self.smile_model: str
        self.smile_name: str
        self.smile_type: str
        self.smile_version: tuple[str, semver.version.Version]
        self.smile_zigbee_mac_address: str | None = None
        self.therms_with_offset_func: list[str] = []

    def smile(self, name: str) -> bool:
        """Helper-function checking the smile-name."""
        return self.smile_name == name

    def _all_locations(self) -> None:
        """Collect all locations."""
        loc = Munch()

        locations = self._domain_objects.findall("./location")
        for location in locations:
            loc.name = location.find("name").text
            loc.loc_id = location.attrib["id"]

            if loc.name == "Home":
                self._home_location = loc.loc_id

            self._loc_data[loc.loc_id] = {"name": loc.name}

    def _get_module_data(
        self, appliance: etree, locator: str, mod_type: str
    ) -> ModelData:
        """Helper-function for _energy_device_info_finder() and _appliance_info_finder().

        Collect requested info from MODULES.
        """
        model_data: ModelData = {
            "contents": False,
            "firmware_version": None,
            "hardware_version": None,
            "reachable": None,
            "vendor_name": None,
            "vendor_model": None,
            "zigbee_mac_address": None,
        }
        if (appl_search := appliance.find(locator)) is not None:
            link_id = appl_search.attrib["id"]
            loc = f".//services/{mod_type}[@id='{link_id}']...."
            # Not possible to walrus for some reason...
            module = self._domain_objects.find(loc)
            if module is not None:  # pylint: disable=consider-using-assignment-expr
                model_data["contents"] = True
                if (vendor_name := module.find("vendor_name").text) is not None:
                    model_data["vendor_name"] = vendor_name
                    if "Plugwise" in vendor_name:
                        model_data["vendor_name"] = vendor_name.split(" ", 1)[0]
                model_data["vendor_model"] = module.find("vendor_model").text
                model_data["hardware_version"] = module.find("hardware_version").text
                model_data["firmware_version"] = module.find("firmware_version").text
                if zb_node := module.find("./protocols/zig_bee_node"):
                    model_data["zigbee_mac_address"] = zb_node.find("mac_address").text
                    model_data["reachable"] = zb_node.find("reachable").text == "true"

        return model_data

    def _energy_device_info_finder(self, appliance: etree, appl: Munch) -> Munch:
        """Helper-function for _appliance_info_finder().

        Collect energy device info (P1, Plug): firmware, model and vendor name.
        """
        if self.smile_type == "power":
            locator = "./logs/point_log/electricity_point_meter"
            mod_type = "electricity_point_meter"

            module_data = self._get_module_data(appliance, locator, mod_type)
            appl.hardware = module_data["hardware_version"]
            appl.model = module_data["vendor_model"]
            appl.vendor_name = module_data["vendor_name"]
            appl.firmware = module_data["firmware_version"]

            return appl

        if self.smile(ADAM):
            locator = "./logs/interval_log/electricity_interval_meter"
            mod_type = "electricity_interval_meter"
            module_data = self._get_module_data(appliance, locator, mod_type)
            # Filter appliance without zigbee_mac, it's an orphaned device
            appl.zigbee_mac = module_data["zigbee_mac_address"]
            if appl.zigbee_mac is None:
                return None

            appl.vendor_name = module_data["vendor_name"]
            appl.model = check_model(module_data["vendor_model"], appl.vendor_name)
            appl.hardware = module_data["hardware_version"]
            appl.firmware = module_data["firmware_version"]

            return appl

        return appl  # pragma: no cover

    def _appliance_info_finder(self, appliance: etree, appl: Munch) -> Munch:
        """Collect device info (Smile, Thermostats, OpenTherm/On-Off): firmware, model and vendor name."""
        # Collect gateway device info
        if appl.pwclass == "gateway":
            self.gateway_id = appliance.attrib["id"]
            appl.firmware = self.smile_fw_version
            appl.hardware = self.smile_hw_version
            appl.mac = self.smile_mac_address
            appl.model = self.smile_model
            appl.name = self.smile_name
            appl.vendor_name = "Plugwise"

            # Adam: look for the ZigBee MAC address of the Smile
            if self.smile(ADAM) and (
                found := self._domain_objects.find(".//protocols/zig_bee_coordinator")
            ):
                appl.zigbee_mac = found.find("mac_address").text

            # Adam: collect modes and check for cooling, indicating cooling-mode is present
            reg_mode_list: list[str] = []
            locator = "./actuator_functionalities/regulation_mode_control_functionality"
            if (search := appliance.find(locator)) is not None:
                if search.find("allowed_modes") is not None:
                    for mode in search.find("allowed_modes"):
                        reg_mode_list.append(mode.text)
                        if mode.text == "cooling":
                            self._cooling_present = True
                    self._reg_allowed_modes = reg_mode_list

            return appl

        # Collect thermostat device info
        if appl.pwclass in THERMOSTAT_CLASSES:
            locator = "./logs/point_log[type='thermostat']/thermostat"
            mod_type = "thermostat"
            module_data = self._get_module_data(appliance, locator, mod_type)
            appl.vendor_name = module_data["vendor_name"]
            appl.model = check_model(module_data["vendor_model"], appl.vendor_name)
            appl.hardware = module_data["hardware_version"]
            appl.firmware = module_data["firmware_version"]
            appl.zigbee_mac = module_data["zigbee_mac_address"]

            return appl

        # Collect heater_central device info
        if appl.pwclass == "heater_central":
            # Remove heater_central when no active device present
            if not self._opentherm_device and not self._on_off_device:
                return None

            self._heater_id = appliance.attrib["id"]
            #  Info for On-Off device
            if self._on_off_device:
                appl.name = "OnOff"
                appl.vendor_name = None
                appl.model = "Unknown"
                return appl

            # Info for OpenTherm device
            appl.name = "OpenTherm"
            locator = "./logs/point_log[type='flame_state']/boiler_state"
            mod_type = "boiler_state"
            module_data = self._get_module_data(appliance, locator, mod_type)
            appl.vendor_name = module_data["vendor_name"]
            appl.hardware = module_data["hardware_version"]
            appl.model = check_model(module_data["vendor_model"], appl.vendor_name)
            if appl.model is None:
                appl.model = (
                    "Generic heater/cooler"
                    if self._cooling_present
                    else "Generic heater"
                )

            # Anna + Loria: collect dhw control operation modes
            dhw_mode_list: list[str] = []
            locator = "./actuator_functionalities/domestic_hot_water_mode_control_functionality"
            if (search := appliance.find(locator)) is not None:
                if search.find("allowed_modes") is not None:
                    for mode in search.find("allowed_modes"):
                        dhw_mode_list.append(mode.text)
                    self._dhw_allowed_modes = dhw_mode_list

            return appl

        # Collect info from power-related devices
        appl = self._energy_device_info_finder(appliance, appl)

        return appl

    def _p1_smartmeter_info_finder(self, appl: Munch) -> None:
        """Collect P1 DSMR Smartmeter info."""
        loc_id = next(iter(self._loc_data.keys()))
        appl.dev_id = self.gateway_id
        appl.location = loc_id
        appl.mac = None
        appl.model = self.smile_model
        appl.name = "P1"
        appl.pwclass = "smartmeter"
        appl.zigbee_mac = None
        location = self._domain_objects.find(f'./location[@id="{loc_id}"]')
        appl = self._energy_device_info_finder(location, appl)

        self.gw_devices[appl.dev_id] = {"dev_class": appl.pwclass}
        self._count += 1

        for key, value in {
            "firmware": appl.firmware,
            "hardware": appl.hardware,
            "location": appl.location,
            "mac_address": appl.mac,
            "model": appl.model,
            "name": appl.name,
            "zigbee_mac_address": appl.zigbee_mac,
            "vendor": appl.vendor_name,
        }.items():
            if value is not None or key == "location":
                p1_key = cast(ApplianceType, key)
                self.gw_devices[appl.dev_id][p1_key] = value
                self._count += 1

    def _all_appliances(self) -> None:
        """Collect all appliances with relevant info."""
        self._count = 0
        self._all_locations()

        for appliance in self._domain_objects.findall("./appliance"):
            appl = Munch()
            appl.pwclass = appliance.find("type").text
            # Skip thermostats that have this key, should be an orphaned device (Core #81712)
            if (
                appl.pwclass == "thermostat"
                and appliance.find("actuator_functionalities/") is None
            ):
                continue

            appl.location = None
            if (appl_loc := appliance.find("location")) is not None:
                appl.location = appl_loc.attrib["id"]
            # Don't assign the _home_location to thermostat-devices
            # without a location, they are not active
            elif appl.pwclass not in THERMOSTAT_CLASSES:
                appl.location = self._home_location

            appl.dev_id = appliance.attrib["id"]
            appl.name = appliance.find("name").text
            appl.model = appl.pwclass.replace("_", " ").title()
            appl.firmware = None
            appl.hardware = None
            appl.mac = None
            appl.zigbee_mac = None
            appl.vendor_name = None

            # Determine class for this appliance
            # Skip on heater_central when no active device present
            if not (appl := self._appliance_info_finder(appliance, appl)):
                continue

            # P1: for gateway and smartmeter switch device_id - part 1
            # This is done to avoid breakage in HA Core
            if appl.pwclass == "gateway" and self.smile_type == "power":
                appl.dev_id = appl.location

            # Don't show orphaned thermostat-types or the OpenTherm Gateway.
            if appl.pwclass in THERMOSTAT_CLASSES and appl.location is None:
                continue

            self.gw_devices[appl.dev_id] = {"dev_class": appl.pwclass}
            self._count += 1
            for key, value in {
                "firmware": appl.firmware,
                "hardware": appl.hardware,
                "location": appl.location,
                "mac_address": appl.mac,
                "model": appl.model,
                "name": appl.name,
                "zigbee_mac_address": appl.zigbee_mac,
                "vendor": appl.vendor_name,
            }.items():
                if value is not None or key == "location":
                    appl_key = cast(ApplianceType, key)
                    self.gw_devices[appl.dev_id][appl_key] = value
                    self._count += 1

        # For P1 collect the connected SmartMeter info
        if self.smile_type == "power":
            self._p1_smartmeter_info_finder(appl)
            # P1: for gateway and smartmeter switch device_id - part 2
            for item in self.gw_devices:
                if item != self.gateway_id:
                    self.gateway_id = item
                    # Leave for-loop to avoid a 2nd device_id switch
                    break

        # Place the gateway and optional heater_central devices as 1st and 2nd
        for dev_class in ("heater_central", "gateway"):
            for dev_id, device in dict(self.gw_devices).items():
                if device["dev_class"] == dev_class:
                    tmp_device = device
                    self.gw_devices.pop(dev_id)
                    cleared_dict = self.gw_devices
                    add_to_front = {dev_id: tmp_device}
                    self.gw_devices = {**add_to_front, **cleared_dict}

    def _match_locations(self) -> dict[str, ThermoLoc]:
        """Helper-function for _scan_thermostats().

        Match appliances with locations.
        """
        matched_locations: dict[str, ThermoLoc] = {}
        for location_id, location_details in self._loc_data.items():
            for appliance_details in self.gw_devices.values():
                if appliance_details["location"] == location_id:
                    location_details.update(
                        {"master": None, "master_prio": 0, "slaves": set()}
                    )
                    matched_locations[location_id] = location_details

        return matched_locations

    def _control_state(self, loc_id: str) -> str | bool:
        """Helper-function for _device_data_adam().

        Adam: find the thermostat control_state of a location, from DOMAIN_OBJECTS.
        Represents the heating/cooling demand-state of the local master thermostat.
        Note: heating or cooling can still be active when the setpoint has been reached.
        """
        locator = f'location[@id="{loc_id}"]'
        if (location := self._domain_objects.find(locator)) is not None:
            locator = './actuator_functionalities/thermostat_functionality[type="thermostat"]/control_state'
            if (ctrl_state := location.find(locator)) is not None:
                return str(ctrl_state.text)

        return False

    def _presets(self, loc_id: str) -> dict[str, list[float]]:
        """Collect Presets for a Thermostat based on location_id."""
        presets: dict[str, list[float]] = {}
        tag_1 = "zone_setpoint_and_state_based_on_preset"
        tag_2 = "Thermostat presets"
        if not (rule_ids := self._rule_ids_by_tag(tag_1, loc_id)):
            if not (rule_ids := self._rule_ids_by_name(tag_2, loc_id)):
                return presets  # pragma: no cover

        for rule_id in rule_ids:
            directives: etree = self._domain_objects.find(
                f'rule[@id="{rule_id}"]/directives'
            )
            for directive in directives:
                preset = directive.find("then").attrib
                presets[directive.attrib["preset"]] = [
                    float(preset["heating_setpoint"]),
                    float(preset["cooling_setpoint"]),
                ]

        return presets

    def _rule_ids_by_name(self, name: str, loc_id: str) -> dict[str, str]:
        """Helper-function for _presets().

        Obtain the rule_id from the given name and and provide the location_id, when present.
        """
        schedule_ids: dict[str, str] = {}
        locator = f'./contexts/context/zone/location[@id="{loc_id}"]'
        for rule in self._domain_objects.findall(f'./rule[name="{name}"]'):
            if rule.find(locator) is not None:
                schedule_ids[rule.attrib["id"]] = loc_id
            else:
                schedule_ids[rule.attrib["id"]] = NONE

        return schedule_ids

    def _rule_ids_by_tag(self, tag: str, loc_id: str) -> dict[str, str]:
        """Helper-function for _presets(), _schedules() and _last_active_schedule().

        Obtain the rule_id from the given template_tag and provide the location_id, when present.
        """
        schedule_ids: dict[str, str] = {}
        locator1 = f'./template[@tag="{tag}"]'
        locator2 = f'./contexts/context/zone/location[@id="{loc_id}"]'
        for rule in self._domain_objects.findall("./rule"):
            if rule.find(locator1) is not None:
                if rule.find(locator2) is not None:
                    schedule_ids[rule.attrib["id"]] = loc_id
                else:
                    schedule_ids[rule.attrib["id"]] = NONE

        return schedule_ids

    def _appliance_measurements(
        self,
        appliance: etree,
        data: DeviceData,
        measurements: dict[str, DATA | UOM],
    ) -> None:
        """Helper-function for _get_measurement_data() - collect appliance measurement data."""
        for measurement, attrs in measurements.items():
            p_locator = f'.//logs/point_log[type="{measurement}"]/period/measurement'
            if (appl_p_loc := appliance.find(p_locator)) is not None:
                # Skip known obsolete measurements
                updated_date_locator = (
                    f'.//logs/point_log[type="{measurement}"]/updated_date'
                )
                if measurement in OBSOLETE_MEASUREMENTS:
                    if (
                        updated_date_key := appliance.find(updated_date_locator)
                    ) is not None:
                        updated_date = updated_date_key.text.split("T")[0]
                        date_1 = dt.datetime.strptime(updated_date, "%Y-%m-%d")
                        date_2 = dt.datetime.now()
                        if int((date_2 - date_1).days) > 7:
                            continue

                if new_name := getattr(attrs, ATTR_NAME, None):
                    measurement = new_name

                match measurement:
                    # measurements with states "on" or "off" that need to be passed directly
                    case "select_dhw_mode":
                        data["select_dhw_mode"] = appl_p_loc.text
                    case _ as measurement if measurement in BINARY_SENSORS:
                        bs_key = cast(BinarySensorType, measurement)
                        bs_value = appl_p_loc.text in ["on", "true"]
                        data["binary_sensors"][bs_key] = bs_value
                    case _ as measurement if measurement in SENSORS:
                        s_key = cast(SensorType, measurement)
                        s_value = format_measure(
                            appl_p_loc.text, getattr(attrs, ATTR_UNIT_OF_MEASUREMENT)
                        )
                        data["sensors"][s_key] = s_value
                        # Anna: save cooling-related measurements for later use
                        # Use the local outdoor temperature as reference for turning cooling on/off
                        if measurement == "cooling_activation_outdoor_temperature":
                            self._cooling_activation_outdoor_temp = data["sensors"][
                                "cooling_activation_outdoor_temperature"
                            ]
                        if measurement == "cooling_deactivation_threshold":
                            self._cooling_deactivation_threshold = data["sensors"][
                                "cooling_deactivation_threshold"
                            ]
                        if measurement == "outdoor_air_temperature":
                            self._outdoor_temp = data["sensors"][
                                "outdoor_air_temperature"
                            ]
                    case _ as measurement if measurement in SWITCHES:
                        sw_key = cast(SwitchType, measurement)
                        sw_value = appl_p_loc.text in ["on", "true"]
                        data["switches"][sw_key] = sw_value
                    case "c_heating_state":
                        value = appl_p_loc.text in ["on", "true"]
                        data["c_heating_state"] = value
                    case "elga_status_code":
                        data["elga_status_code"] = int(appl_p_loc.text)

            i_locator = f'.//logs/interval_log[type="{measurement}"]/period/measurement'
            if (appl_i_loc := appliance.find(i_locator)) is not None:
                name = cast(SensorType, f"{measurement}_interval")
                data["sensors"][name] = format_measure(
                    appl_i_loc.text, ENERGY_WATT_HOUR
                )

        self._count += len(data["binary_sensors"])
        self._count += len(data["sensors"])
        self._count += len(data["switches"])
        # Don't count the above top-level dicts, only the remaining single items
        self._count += len(data) - 3

    def _wireless_availablity(self, appliance: etree, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Collect the availablity-status for wireless connected devices.
        """
        if self.smile(ADAM):
            # Collect for Plugs
            locator = "./logs/interval_log/electricity_interval_meter"
            mod_type = "electricity_interval_meter"
            module_data = self._get_module_data(appliance, locator, mod_type)
            if module_data["reachable"] is None:
                # Collect for wireless thermostats
                locator = "./logs/point_log[type='thermostat']/thermostat"
                mod_type = "thermostat"
                module_data = self._get_module_data(appliance, locator, mod_type)

            if module_data["reachable"] is not None:
                data["available"] = module_data["reachable"]
                self._count += 1

    def _get_appliances_with_offset_functionality(self) -> list[str]:
        """Helper-function collecting all appliance that have offset_functionality."""
        therm_list: list[str] = []
        offset_appls = self._domain_objects.findall(
            './/actuator_functionalities/offset_functionality[type="temperature_offset"]/offset/../../..'
        )
        for item in offset_appls:
            therm_list.append(item.attrib["id"])

        return therm_list

    def _get_actuator_functionalities(
        self, xml: etree, device: DeviceData, data: DeviceData
    ) -> None:
        """Helper-function for _get_measurement_data()."""
        for item in ACTIVE_ACTUATORS:
            # Skip max_dhw_temperature, not initially valid,
            # skip thermostat for thermo_sensors
            if item == "max_dhw_temperature" or (
                item == "thermostat" and device["dev_class"] == "thermo_sensor"
            ):
                continue

            temp_dict: ActuatorData = {}
            functionality = "thermostat_functionality"
            if item == "temperature_offset":
                functionality = "offset_functionality"

            # When there is no updated_date-text, skip the actuator
            updated_date_location = f'.//actuator_functionalities/{functionality}[type="{item}"]/updated_date'
            if (
                updated_date_key := xml.find(updated_date_location)
            ) is not None and updated_date_key.text is None:
                continue

            for key in LIMITS:
                locator = (
                    f'.//actuator_functionalities/{functionality}[type="{item}"]/{key}'
                )
                if (function := xml.find(locator)) is not None:
                    if key == "offset":
                        # Add limits and resolution for temperature_offset,
                        # not provided by Plugwise in the XML data
                        temp_dict["lower_bound"] = -2.0
                        temp_dict["resolution"] = 0.1
                        temp_dict["upper_bound"] = 2.0
                        self._count += 3
                        # Rename offset to setpoint
                        key = "setpoint"

                    act_key = cast(ActuatorDataType, key)
                    temp_dict[act_key] = format_measure(function.text, TEMP_CELSIUS)
                    self._count += 1

            if temp_dict:
                # If domestic_hot_water_setpoint is present as actuator,
                # rename and remove as sensor
                if item == DHW_SETPOINT:
                    item = "max_dhw_temperature"
                    if DHW_SETPOINT in data["sensors"]:
                        data["sensors"].pop(DHW_SETPOINT)
                        self._count -= 1

                act_item = cast(ActuatorType, item)
                data[act_item] = temp_dict

    def _get_regulation_mode(self, appliance: etree, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Collect the gateway regulation_mode.
        """
        locator = "./actuator_functionalities/regulation_mode_control_functionality"
        if (search := appliance.find(locator)) is not None:
            data["select_regulation_mode"] = search.find("mode").text
            self._count += 1
            self._cooling_enabled = data["select_regulation_mode"] == "cooling"

    def _cleanup_data(self, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Clean up the data dict.
        """
        # Don't show cooling-related when no cooling present,
        # but, keep cooling_enabled for Elga
        if not self._cooling_present:
            if "cooling_state" in data["binary_sensors"]:
                data["binary_sensors"].pop("cooling_state")
                self._count -= 1
            if "cooling_ena_switch" in data["switches"]:
                data["switches"].pop("cooling_ena_switch")  # pragma: no cover
                self._count -= 1  # pragma: no cover
            if not self._elga and "cooling_enabled" in data:
                data.pop("cooling_enabled")  # pragma: no cover
                self._count -= 1  # pragma: no cover

    def _process_c_heating_state(self, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Process the central_heating_state value.
        """
        if self._on_off_device:
            # Anna + OnOff heater: use central_heating_state to show heating_state
            # Solution for Core issue #81839
            if self.smile(ANNA):
                data["binary_sensors"]["heating_state"] = data["c_heating_state"]

            # Adam + OnOff cooling: use central_heating_state to show heating/cooling_state
            if self.smile(ADAM):
                if "heating_state" not in data["binary_sensors"]:
                    self._count += 1
                data["binary_sensors"]["heating_state"] = False
                if "cooling_state" not in data["binary_sensors"]:
                    self._count += 1
                data["binary_sensors"]["cooling_state"] = False
                if self._cooling_enabled:
                    data["binary_sensors"]["cooling_state"] = data["c_heating_state"]
                else:
                    data["binary_sensors"]["heating_state"] = data["c_heating_state"]

        # Anna + Elga: use central_heating_state to show heating_state
        if self._elga:
            data["binary_sensors"]["heating_state"] = data["c_heating_state"]

    def _get_measurement_data(self, dev_id: str) -> DeviceData:
        """Helper-function for smile.py: _get_device_data().

        Collect the appliance-data based on device id.
        """
        data: DeviceData = {"binary_sensors": {}, "sensors": {}, "switches": {}}
        # Get P1 smartmeter data from LOCATIONS
        device = self.gw_devices[dev_id]
        # !! DON'T CHANGE below two if-lines, will break stuff !!
        if self.smile_type == "power":
            if device["dev_class"] == "smartmeter":
                data.update(self._power_data_from_location(device["location"]))

            return data

        measurements = DEVICE_MEASUREMENTS
        if self._is_thermostat and dev_id == self._heater_id:
            measurements = HEATER_CENTRAL_MEASUREMENTS

        if (
            appliance := self._domain_objects.find(f'./appliance[@id="{dev_id}"]')
        ) is not None:
            self._appliance_measurements(appliance, data, measurements)
            self._get_lock_state(appliance, data)

            for toggle, name in TOGGLES.items():
                self._get_toggle_state(appliance, toggle, name, data)

            if appliance.find("type").text in ACTUATOR_CLASSES:
                self._get_actuator_functionalities(appliance, device, data)

            # Collect availability-status for wireless connected devices to Adam
            self._wireless_availablity(appliance, data)

        if dev_id == self.gateway_id and self.smile(ADAM):
            self._get_regulation_mode(appliance, data)

        if "c_heating_state" in data:
            self._process_c_heating_state(data)
            # Remove c_heating_state after processing
            data.pop("c_heating_state")
            self._count -= 1

        if self._is_thermostat and self.smile(ANNA) and dev_id == self._heater_id:
            # Anna+Elga: base cooling_state on the elga-status-code
            if "elga_status_code" in data:
                # Techneco Elga has cooling-capability
                self._cooling_present = True
                data["model"] = "Generic heater/cooler"
                self._cooling_enabled = data["elga_status_code"] in [8, 9]
                data["binary_sensors"]["cooling_state"] = self._cooling_active = (
                    data["elga_status_code"] == 8
                )
                data.pop("elga_status_code", None)
                self._count -= 1
                # Elga has no cooling-switch
                if "cooling_ena_switch" in data["switches"]:
                    data["switches"].pop("cooling_ena_switch")
                    self._count -= 1

            # Loria/Thermastage: cooling-related is based on cooling_state
            # and modulation_level
            else:
                if self._cooling_present and "cooling_state" in data["binary_sensors"]:
                    self._cooling_enabled = data["binary_sensors"]["cooling_state"]
                    self._cooling_active = data["sensors"]["modulation_level"] == 100
                    # For Loria the above does not work (pw-beta issue #301)
                    if "cooling_ena_switch" in data["switches"]:
                        self._cooling_enabled = data["switches"]["cooling_ena_switch"]
                        self._cooling_active = data["binary_sensors"]["cooling_state"]

        self._cleanup_data(data)

        return data

    def _rank_thermostat(
        self,
        thermo_matching: dict[str, int],
        loc_id: str,
        appliance_id: str,
        appliance_details: DeviceData,
    ) -> None:
        """Helper-function for _scan_thermostats().

        Rank the thermostat based on appliance_details: master or slave.
        """
        appl_class = appliance_details["dev_class"]
        appl_d_loc = appliance_details["location"]
        if loc_id == appl_d_loc and appl_class in thermo_matching:
            # Pre-elect new master
            if thermo_matching[appl_class] > self._thermo_locs[loc_id]["master_prio"]:
                # Demote former master
                if (tl_master := self._thermo_locs[loc_id]["master"]) is not None:
                    self._thermo_locs[loc_id]["slaves"].add(tl_master)

                # Crown master
                self._thermo_locs[loc_id]["master_prio"] = thermo_matching[appl_class]
                self._thermo_locs[loc_id]["master"] = appliance_id

            else:
                self._thermo_locs[loc_id]["slaves"].add(appliance_id)

    def _scan_thermostats(self) -> None:
        """Helper-function for smile.py: get_all_devices().

        Update locations with thermostat ranking results and use
        the result to update the device_class of slave thermostats.
        """
        self._thermo_locs = self._match_locations()

        thermo_matching: dict[str, int] = {
            "thermostat": 3,
            "zone_thermometer": 2,
            "zone_thermostat": 2,
            "thermostatic_radiator_valve": 1,
        }

        for loc_id in self._thermo_locs:
            for dev_id, device in self.gw_devices.items():
                self._rank_thermostat(thermo_matching, loc_id, dev_id, device)

        # Update slave thermostat class where needed
        for dev_id, device in self.gw_devices.items():
            if (loc_id := device["location"]) in self._thermo_locs:
                tl_loc_id = self._thermo_locs[loc_id]
                if "slaves" in tl_loc_id and dev_id in tl_loc_id["slaves"]:
                    device["dev_class"] = "thermo_sensor"

    def _thermostat_uri(self, loc_id: str) -> str:
        """Helper-function for smile.py: set_temperature().

        Determine the location-set_temperature uri - from LOCATIONS.
        """
        locator = f'./location[@id="{loc_id}"]/actuator_functionalities/thermostat_functionality'
        thermostat_functionality_id = self._domain_objects.find(locator).attrib["id"]

        return f"{LOCATIONS};id={loc_id}/thermostat;id={thermostat_functionality_id}"

    def _get_group_switches(self) -> dict[str, DeviceData]:
        """Helper-function for smile.py: get_all_devices().

        Collect switching- or pump-group info.
        """
        switch_groups: dict[str, DeviceData] = {}
        # P1 and Anna don't have switchgroups
        if self.smile_type == "power" or self.smile(ANNA):
            return switch_groups

        for group in self._domain_objects.findall("./group"):
            members: list[str] = []
            group_id = group.attrib["id"]
            group_name = group.find("name").text
            group_type = group.find("type").text
            group_appliances = group.findall("appliances/appliance")
            for item in group_appliances:
                # Check if members are not orphaned
                if item.attrib["id"] in self.gw_devices:
                    members.append(item.attrib["id"])

            if group_type in SWITCH_GROUP_TYPES and members:
                switch_groups.update(
                    {
                        group_id: {
                            "dev_class": group_type,
                            "model": "Switchgroup",
                            "name": group_name,
                            "members": members,
                        },
                    },
                )
                self._count += 4

        return switch_groups

    def _heating_valves(self) -> int | bool:
        """Helper-function for smile.py: _device_data_adam().

        Collect amount of open valves indicating active direct heating.
        For cases where the heat is provided from an external shared source (city heating).
        """
        loc_found: int = 0
        open_valve_count: int = 0
        for appliance in self._domain_objects.findall("./appliance"):
            locator = './logs/point_log[type="valve_position"]/period/measurement'
            if (appl_loc := appliance.find(locator)) is not None:
                loc_found += 1
                if float(appl_loc.text) > 0.0:
                    open_valve_count += 1

        return False if loc_found == 0 else open_valve_count

    def power_data_energy_diff(
        self,
        measurement: str,
        net_string: SensorType,
        f_val: float | int,
        direct_data: DeviceData,
    ) -> DeviceData:
        """Calculate differential energy."""
        if (
            "electricity" in measurement
            and "phase" not in measurement
            and "interval" not in net_string
        ):
            diff = 1
            if "produced" in measurement:
                diff = -1
            if net_string not in direct_data["sensors"]:
                tmp_val: float | int = 0
            else:
                tmp_val = direct_data["sensors"][net_string]

            if isinstance(f_val, int):
                tmp_val += f_val * diff
            else:
                tmp_val += float(f_val * diff)
                tmp_val = float(f"{round(tmp_val, 3):.3f}")

            direct_data["sensors"][net_string] = tmp_val

        return direct_data

    def _power_data_peak_value(self, direct_data: DeviceData, loc: Munch) -> Munch:
        """Helper-function for _power_data_from_location() and _power_data_from_modules()."""
        loc.found = True
        # If locator not found look for P1 gas_consumed or phase data (without tariff)
        if loc.logs.find(loc.locator) is None:
            if "log" in loc.log_type and (
                "gas" in loc.measurement or "phase" in loc.measurement
            ):
                # Avoid double processing by skipping one peak-list option
                if loc.peak_select == "nl_offpeak":
                    loc.found = False
                    return loc

                loc.locator = (
                    f'./{loc.log_type}[type="{loc.measurement}"]/period/measurement'
                )
                if loc.logs.find(loc.locator) is None:
                    loc.found = False
                    return loc

        if (peak := loc.peak_select.split("_")[1]) == "offpeak":
            peak = "off_peak"
        log_found = loc.log_type.split("_")[0]
        loc.key_string = f"{loc.measurement}_{peak}_{log_found}"
        if "gas" in loc.measurement or loc.log_type == "point_meter":
            loc.key_string = f"{loc.measurement}_{log_found}"
        if "phase" in loc.measurement:
            loc.key_string = f"{loc.measurement}"
        loc.net_string = f"net_electricity_{log_found}"
        val = loc.logs.find(loc.locator).text
        loc.f_val = power_data_local_format(loc.attrs, loc.key_string, val)

        return loc

    def _power_data_from_location(self, loc_id: str) -> DeviceData:
        """Helper-function for smile.py: _get_device_data().

        Collect the power-data based on Location ID, from LOCATIONS.
        """
        direct_data: DeviceData = {"sensors": {}}
        loc = Munch()
        log_list: list[str] = ["point_log", "cumulative_log", "interval_log"]
        peak_list: list[str] = ["nl_peak", "nl_offpeak"]
        t_string = "tariff"

        search = self._domain_objects
        loc.logs = search.find(f'./location[@id="{loc_id}"]/logs')
        for loc.measurement, loc.attrs in P1_MEASUREMENTS.items():
            for loc.log_type in log_list:
                for loc.peak_select in peak_list:
                    # meter_string = ".//{}[type='{}']/"
                    loc.locator = (
                        f'./{loc.log_type}[type="{loc.measurement}"]/period/'
                        f'measurement[@{t_string}="{loc.peak_select}"]'
                    )
                    loc = self._power_data_peak_value(direct_data, loc)
                    if not loc.found:
                        continue

                    direct_data = self.power_data_energy_diff(
                        loc.measurement, loc.net_string, loc.f_val, direct_data
                    )
                    key = cast(SensorType, loc.key_string)
                    direct_data["sensors"][key] = loc.f_val

        self._count += len(direct_data["sensors"])
        return direct_data

    def _preset(self, loc_id: str) -> str | None:
        """Helper-function for smile.py: device_data_climate().

        Collect the active preset based on Location ID.
        """
        locator = f'./location[@id="{loc_id}"]/preset'
        if (preset := self._domain_objects.find(locator)) is not None:
            return str(preset.text)

        return None

    def _schedules(self, location: str) -> tuple[list[str], str]:
        """Helper-function for smile.py: _device_data_climate().

        Obtain the available schedules/schedules. Adam: a schedule can be connected to more than one location.
        NEW: when a location_id is present then the schedule is active. Valid for both Adam and Anna.
        """
        available: list[str] = [NONE]
        rule_ids: dict[str, str] = {}
        selected = NONE

        # Adam schedules, one schedule can be linked to various locations
        # self._last_active contains the locations and the active schedule name per location, or None
        if location not in self._last_active:
            self._last_active[location] = None

        tag = "zone_preset_based_on_time_and_presence_with_override"
        if not (rule_ids := self._rule_ids_by_tag(tag, location)):
            return available, selected

        schedules: list[str] = []
        for rule_id, loc_id in rule_ids.items():
            name = self._domain_objects.find(f'./rule[@id="{rule_id}"]/name').text
            locator = f'./rule[@id="{rule_id}"]/directives'
            # Show an empty schedule as no schedule found
            if not self._domain_objects.find(locator):
                continue

            available.append(name)
            if location == loc_id:
                selected = name
                self._last_active[location] = selected
            schedules.append(name)

        if schedules:
            available.remove(NONE)
            if self._last_active.get(location) is None:
                self._last_active[location] = self._last_used_schedule(schedules)

        return available, selected

    def _last_used_schedule(self, schedules: list[str]) -> str:
        """Helper-function for _schedules().

        Determine the last-used schedule based on the modified date.
        """
        epoch = dt.datetime(1970, 1, 1, tzinfo=tz.tzutc())
        schedules_dates: dict[str, float] = {}

        for name in schedules:
            result = self._domain_objects.find(f'./rule[name="{name}"]')
            schedule_date = result.find("modified_date").text
            schedule_time = parse(schedule_date)
            schedules_dates[name] = (schedule_time - epoch).total_seconds()

        return sorted(schedules_dates.items(), key=lambda kv: kv[1])[-1][0]

    def _object_value(self, obj_id: str, measurement: str) -> float | int | None:
        """Helper-function for smile.py: _get_device_data() and _device_data_anna().

        Obtain the value/state for the given object from a location in DOMAIN_OBJECTS
        """
        val: float | int | None = None
        search = self._domain_objects
        locator = f'./location[@id="{obj_id}"]/logs/point_log[type="{measurement}"]/period/measurement'
        if (found := search.find(locator)) is not None:
            val = format_measure(found.text, NONE)
            return val

        return val

    def _get_lock_state(self, xml: etree, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Adam: obtain the relay-switch lock state.
        """
        actuator = "actuator_functionalities"
        func_type = "relay_functionality"
        if xml.find("type").text not in SPECIAL_PLUG_TYPES:
            locator = f"./{actuator}/{func_type}/lock"
            if (found := xml.find(locator)) is not None:
                data["switches"]["lock"] = found.text == "true"
                self._count += 1

    def _get_toggle_state(
        self, xml: etree, toggle: str, name: ToggleNameType, data: DeviceData
    ) -> None:
        """Helper-function for _get_measurement_data().

        Obtain the toggle state of a 'toggle' = switch.
        """
        if xml.find("type").text == "heater_central":
            locator = "./actuator_functionalities/toggle_functionality"
            if found := xml.findall(locator):
                for item in found:
                    if (toggle_type := item.find("type")) is not None:
                        if toggle_type.text == toggle:
                            data["switches"][name] = item.find("state").text == "on"
                            self._count += 1
                            # Remove the cooling_enabled binary_sensor when the corresponding switch is present
                            # Except for Elga
                            if toggle == "cooling_enabled" and not self._elga:
                                data["binary_sensors"].pop("cooling_enabled")
                                self._count -= 1
