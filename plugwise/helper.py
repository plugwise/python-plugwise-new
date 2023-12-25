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
# from dateutil import tz
# from dateutil.parser import parse
from defusedxml import ElementTree as etree
from munch import Munch
import semver

from .constants import (
    ACTIVE_ACTUATORS,
    ACTUATOR_CLASSES,
    ADAM,
    ANNA,
    APPLIANCES,
    ATTR_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    BINARY_SENSORS,
    DATA,
    DEVICE_MEASUREMENTS,
    DHW_SETPOINT,
    ENERGY_KILO_WATT_HOUR,
    ENERGY_WATT_HOUR,
    FAKE_APPL,
    FAKE_LOC,
    HEATER_CENTRAL_MEASUREMENTS,
    LIMITS,
    LOGGER,
    NONE,
    OBSOLETE_MEASUREMENTS,
    P1_LEGACY_MEASUREMENTS,
    P1_MEASUREMENTS,
    POWER_WATT,
    SENSORS,
    SPECIAL_PLUG_TYPES,
    SWITCH_GROUP_TYPES,
    SWITCHES,
    TEMP_CELSIUS,
    THERMOSTAT_CLASSES,
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


def etree_to_dict(element: etree) -> dict[str, str]:
    """Helper-function translating xml Element to dict."""
    node: dict[str, str] = {}
    if element is not None:
        node.update(element.items())

    return node


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
        # Cornercase for stretch not responding with 202
        if method == "put" and resp.status == 200:
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
                # Work-around for Stretchv2, should not hurt the other smiles
                headers = {"Accept-Encoding": "gzip"}
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
        self._appliances: etree
        self._count: int
        self._domain_objects: etree
        self._heater_id: str
        self._home_location: str
        self._is_thermostat = False
        self._last_modified: dict[str, str] = {}
        self._locations: etree
        self._loc_data: dict[str, ThermoLoc] = {}
        self._modules: etree
        self._notifications: dict[str, dict[str, str]] = {}
        self._on_off_device = False
        self._opentherm_device = False
        self._outdoor_temp: float
        self._smile_legacy = False
        self._status: etree
        self._stretch_v2 = False
        self._stretch_v3 = False
        self._system: etree
        self._thermo_locs: dict[str, ThermoLoc] = {}

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

        locations = self._locations.findall("./location")
        # Legacy Anna without outdoor_temp and Stretches have no locations, create fake location-data
        if not locations and self._smile_legacy:
            self._home_location = FAKE_LOC
            self._loc_data[FAKE_LOC] = {"name": "Home"}
            return

        for location in locations:
            loc.name = location.find("name").text
            loc.loc_id = location.attrib["id"]
            # Filter the valid single location for P1 legacy: services not empty
            locator = "./services"
            if (
                self._smile_legacy
                and self.smile_type == "power"
                and len(location.find(locator)) == 0
            ):
                continue

            if loc.name == "Home":
                self._home_location = loc.loc_id
            # Replace location-name for P1 legacy, can contain privacy-related info
            if self._smile_legacy and self.smile_type == "power":
                loc.name = "Home"
                self._home_location = loc.loc_id

            self._loc_data[loc.loc_id] = {"name": loc.name}

        return

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
            loc = f".//{mod_type}[@id='{link_id}']...."
            # Not possible to walrus for some reason...
            module = self._modules.find(loc)
            if module is not None:  # pylint: disable=consider-using-assignment-expr
                model_data["contents"] = True
                if (vendor_name := module.find("vendor_name").text) is not None:
                    model_data["vendor_name"] = vendor_name
                    if "Plugwise" in vendor_name:
                        model_data["vendor_name"] = vendor_name.split(" ", 1)[0]
                model_data["vendor_model"] = module.find("vendor_model").text
                model_data["hardware_version"] = module.find("hardware_version").text
                model_data["firmware_version"] = module.find("firmware_version").text
                if router := module.find("./protocols/network_router"):
                    model_data["zigbee_mac_address"] = router.find("mac_address").text
                # Also look for the Circle+/Stealth M+
                if coord := module.find("./protocols/network_coordinator"):
                    model_data["zigbee_mac_address"] = coord.find("mac_address").text

        return model_data

    def _energy_device_info_finder(self, appliance: etree, appl: Munch) -> Munch:
        """Helper-function for _appliance_info_finder().

        Collect energy device info (Circle, Plug, Stealth): firmware, model and vendor name.
        """
        if self.smile_type in ("power", "stretch"):
            locator = "./services/electricity_point_meter"
            mod_type = "electricity_point_meter"

            module_data = self._get_module_data(appliance, locator, mod_type)
            # Filter appliance without zigbee_mac, it's an orphaned device
            appl.zigbee_mac = module_data["zigbee_mac_address"]
            if appl.zigbee_mac is None and self.smile_type != "power":
                return None

            appl.hardware = module_data["hardware_version"]
            appl.model = module_data["vendor_model"]
            appl.vendor_name = module_data["vendor_name"]
            if appl.hardware is not None:
                hw_version = appl.hardware.replace("-", "")
                appl.model = version_to_model(hw_version)
            appl.firmware = module_data["firmware_version"]

            return appl

        return appl  # pragma: no cover

    def _appliance_info_finder(self, appliance: etree, appl: Munch) -> Munch:
        """Collect device info (Smile/Stretch, Thermostats, OpenTherm/On-Off): firmware, model and vendor name."""
        # Collect gateway device info
        if appl.pwclass == "gateway":
            self.gateway_id = appliance.attrib["id"]
            appl.firmware = self.smile_fw_version
            appl.hardware = self.smile_hw_version
            appl.mac = self.smile_mac_address
            appl.model = self.smile_model
            appl.name = self.smile_name
            appl.vendor_name = "Plugwise"

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
            locator1 = "./logs/point_log[type='flame_state']/boiler_state"
            locator2 = "./services/boiler_state"
            mod_type = "boiler_state"
            module_data = self._get_module_data(appliance, locator1, mod_type)
            if not module_data["contents"]:
                module_data = self._get_module_data(appliance, locator2, mod_type)
            appl.vendor_name = module_data["vendor_name"]
            appl.hardware = module_data["hardware_version"]
            appl.model = check_model(module_data["vendor_model"], appl.vendor_name)
            if appl.model is None:
                appl.model = "Generic heater"

            return appl

        # Collect info from Stretches
        appl = self._energy_device_info_finder(appliance, appl)

        return appl

    def _p1_smartmeter_info_finder(self, appl: Munch) -> None:
        """Collect P1 DSMR Smartmeter info."""
        loc_id = next(iter(self._loc_data.keys()))
        appl.dev_id = self.gateway_id
        appl.location = loc_id
        if self._smile_legacy:
            appl.dev_id = loc_id
        appl.mac = None
        appl.model = self.smile_model
        appl.name = "P1"
        appl.pwclass = "smartmeter"
        appl.zigbee_mac = None
        location = self._locations.find(f'./location[@id="{loc_id}"]')
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

    def _create_legacy_gateway(self) -> None:
        """Create the (missing) gateway devices for legacy Anna, P1 and Stretch.

        Use the home_location or FAKE_APPL as device id.
        """
        self.gateway_id = self._home_location
        if self.smile_type == "power":
            self.gateway_id = FAKE_APPL

        self.gw_devices[self.gateway_id] = {"dev_class": "gateway"}
        self._count += 1
        for key, value in {
            "firmware": self.smile_fw_version,
            "location": self._home_location,
            "mac_address": self.smile_mac_address,
            "model": self.smile_model,
            "name": self.smile_name,
            "zigbee_mac_address": self.smile_zigbee_mac_address,
            "vendor": "Plugwise",
        }.items():
            if value is not None:
                gw_key = cast(ApplianceType, key)
                self.gw_devices[self.gateway_id][gw_key] = value
                self._count += 1

    def _all_appliances(self) -> None:
        """Collect all appliances with relevant info."""
        self._count = 0
        self._all_locations()

        if self._smile_legacy:
            self._create_legacy_gateway()
            # For legacy P1 collect the connected SmartMeter info
            if self.smile_type == "power":
                appl = Munch()
                self._p1_smartmeter_info_finder(appl)
                # Legacy P1 has no more devices
                return

        for appliance in self._appliances.findall("./appliance"):
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
            # Provide a location for legacy_anna, also don't assign the _home_location
            # to thermostat-devices without a location, they are not active
            elif (
                self.smile_type == "thermostat"
            ) or appl.pwclass not in THERMOSTAT_CLASSES:
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
            # Skip on heater_central when no active device present or on orphaned stretch devices
            if not (appl := self._appliance_info_finder(appliance, appl)):
                continue

            # P1: for gateway and smartmeter switch device_id - part 1
            # This is done to avoid breakage in HA Core
            if appl.pwclass == "gateway" and self.smile_type == "power":
                appl.dev_id = appl.location

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

    def _presets(self) -> dict[str, list[float]]:
        """Helper-function for presets() - collect Presets for a legacy Anna."""
        presets: dict[str, list[float]] = {}
        for directive in self._domain_objects.findall("rule/directives/when/then"):
            if directive is not None and directive.get("icon") is not None:
                # Ensure list of heating_setpoint, cooling_setpoint
                presets[directive.attrib["icon"]] = [
                    float(directive.attrib["temperature"]),
                    0,
                ]

        return presets

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
                if self._smile_legacy and measurement == "domestic_hot_water_state":
                    continue

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
                    case _ as measurement if measurement in SWITCHES:
                        sw_key = cast(SwitchType, measurement)
                        sw_value = appl_p_loc.text in ["on", "true"]
                        data["switches"][sw_key] = sw_value
                    case "c_heating_state":
                        value = appl_p_loc.text in ["on", "true"]
                        data["c_heating_state"] = value

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

    def _get_appliances_with_offset_functionality(self) -> list[str]:
        """Helper-function collecting all appliance that have offset_functionality."""
        therm_list: list[str] = []
        offset_appls = self._appliances.findall(
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
                # Don't support temperature_offset for legacy Anna
                if self._smile_legacy:
                    continue

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
                act_item = cast(ActuatorType, item)
                data[act_item] = temp_dict

    def _process_c_heating_state(self, data: DeviceData) -> None:
        """Helper-function for _get_measurement_data().

        Process the central_heating_state value.
        """
        if self._on_off_device:
            # Anna + OnOff heater: use central_heating_state to show heating_state
            # Solution for Core issue #81839
            if self.smile(ANNA):
                data["binary_sensors"]["heating_state"] = data["c_heating_state"]

    def _get_measurement_data(self, dev_id: str) -> DeviceData:
        """Helper-function for smile.py: _get_device_data().

        Collect the appliance-data based on device id.
        """
        data: DeviceData = {"binary_sensors": {}, "sensors": {}, "switches": {}}
        # Get P1 smartmeter data from LOCATIONS or MODULES
        device = self.gw_devices[dev_id]
        if self.smile_type == "power" and device["dev_class"] == "smartmeter":
            data.update(self._power_data_from_modules())

            return data

        # Get non-p1 data from APPLIANCES, for legacy from DOMAIN_OBJECTS.
        measurements = DEVICE_MEASUREMENTS
        if self._is_thermostat and dev_id == self._heater_id:
            measurements = HEATER_CENTRAL_MEASUREMENTS

        if (
            appliance := self._appliances.find(f'./appliance[@id="{dev_id}"]')
        ) is not None:
            self._appliance_measurements(appliance, data, measurements)
            self._get_lock_state(appliance, data)

            if appliance.find("type").text in ACTUATOR_CLASSES:
                self._get_actuator_functionalities(appliance, device, data)

        if "c_heating_state" in data:
            self._process_c_heating_state(data)
            # Remove c_heating_state after processing
            data.pop("c_heating_state")
            self._count -= 1

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

    def _thermostat_uri(self) -> str:
        """Helper-function for _thermostat_uri().

        Determine the location-set_temperature uri - from APPLIANCES.
        """
        locator = "./appliance[type='thermostat']"
        appliance_id = self._appliances.find(locator).attrib["id"]

        return f"{APPLIANCES};id={appliance_id}/thermostat"

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
                # Check if members are not orphaned - stretch
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
        for appliance in self._appliances.findall("./appliance"):
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
        # or for P1 legacy electricity_point_meter or gas_*_meter data
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
            # P1 legacy point_meter has no tariff_indicator
            elif "meter" in loc.log_type and (
                "point" in loc.log_type or "gas" in loc.measurement
            ):
                # Avoid double processing by skipping one peak-list option
                if loc.peak_select == "nl_offpeak":
                    loc.found = False
                    return loc

                loc.locator = (
                    f"./{loc.meas_list[0]}_{loc.log_type}/"
                    f'measurement[@directionality="{loc.meas_list[1]}"]'
                )
                if loc.logs.find(loc.locator) is None:
                    loc.found = False
                    return loc
            else:
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

    def _power_data_from_modules(self) -> DeviceData:
        """Helper-function for smile.py: _get_device_data().

        Collect the power-data from MODULES (P1 legacy only).
        """
        direct_data: DeviceData = {"sensors": {}}
        loc = Munch()
        mod_list: list[str] = ["interval_meter", "cumulative_meter", "point_meter"]
        peak_list: list[str] = ["nl_peak", "nl_offpeak"]
        t_string = "tariff_indicator"

        search = self._modules
        mod_logs = search.findall("./module/services")
        for loc.measurement, loc.attrs in P1_LEGACY_MEASUREMENTS.items():
            loc.meas_list = loc.measurement.split("_")
            for loc.logs in mod_logs:
                for loc.log_type in mod_list:
                    for loc.peak_select in peak_list:
                        loc.locator = (
                            f"./{loc.meas_list[0]}_{loc.log_type}/measurement"
                            f'[@directionality="{loc.meas_list[1]}"][@{t_string}="{loc.peak_select}"]'
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

    def _preset(self) -> str | None:
        """Helper-function for smile.py: device_data_climate().

        Collect the active preset.
        """
        locator = "./rule[active='true']/directives/when/then"
        if (
            not (active_rule := etree_to_dict(self._domain_objects.find(locator)))
            or "icon" not in active_rule
        ):
            return None

        return active_rule["icon"]

    def _schedules(self, location: str) -> tuple[list[str], str]:
        """Helper-function for _schedules().

        Collect available schedules/schedules for the legacy thermostat.
        """

        available: list[str] = [NONE]
        selected = NONE
        name: str | None = None

        search = self._domain_objects
        for schedule in search.findall("./rule"):
            if rule_name := schedule.find("name").text:
                if "preset" not in rule_name:
                    name = rule_name

        log_type = "schedule_state"
        locator = f"./appliance[type='thermostat']/logs/point_log[type='{log_type}']/period/measurement"
        active = False
        if (result := search.find(locator)) is not None:
            active = result.text == "on"

        if name is not None:
            available = [name]
            if active:
                selected = name

        return available, selected

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

        Adam & Stretches: obtain the relay-switch lock state.
        """
        actuator = "actuator_functionalities"
        func_type = "relay_functionality"
        if self._stretch_v2:
            actuator = "actuators"
            func_type = "relay"
        if xml.find("type").text not in SPECIAL_PLUG_TYPES:
            locator = f"./{actuator}/{func_type}/lock"
            if (found := xml.find(locator)) is not None:
                data["switches"]["lock"] = found.text == "true"
                self._count += 1
