# pylint: disable=protected-access
"""Test Plugwise Home Assistant module and generate test JSON fixtures."""
import asyncio
import importlib
import json

# Fixture writing
import logging
import os
from pprint import PrettyPrinter

# String generation
import random
import string
from unittest.mock import patch

# Testing
import aiohttp
from freezegun import freeze_time
import pytest

pw_constants = importlib.import_module("plugwise.constants")
pw_exceptions = importlib.import_module("plugwise.exceptions")
pw_smile = importlib.import_module("plugwise")

pytestmark = pytest.mark.asyncio

pp = PrettyPrinter(indent=8)

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

# Prepare aiohttp app routes
# taking self.smile_setup (i.e. directory name under userdata/{smile_app}/
# as inclusion point


class TestPlugwise:  # pylint: disable=attribute-defined-outside-init
    """Tests for Plugwise Smile."""

    def _write_json(self, call, data):
        """Store JSON data to per-setup files for HA component testing."""
        path = os.path.join(
            os.path.dirname(__file__), "../fixtures/" + self.smile_setup
        )
        datafile = os.path.join(path, call + ".json")
        if not os.path.exists(path):  # pragma: no cover
            os.mkdir(path)
        if not os.path.exists(os.path.dirname(datafile)):  # pragma: no cover
            os.mkdir(os.path.dirname(datafile))

        with open(datafile, "w", encoding="utf-8") as fixture_file:
            fixture_file.write(
                json.dumps(
                    data,
                    indent=2,
                    separators=(",", ": "),
                    sort_keys=True,
                    default=lambda x: list(x) if isinstance(x, set) else x,
                )
                + "\n"
            )

    async def setup_app(
        self,
        broken=False,
        timeout=False,
        raise_timeout=False,
        fail_auth=False,
        stretch=False,
    ):
        """Create mock webserver for Smile to interface with."""
        app = aiohttp.web.Application()

        if fail_auth:
            app.router.add_get("/{tail:.*}", self.smile_fail_auth)
            app.router.add_route("PUT", "/{tail:.*}", self.smile_fail_auth)
            return app

        app.router.add_get("/core/appliances", self.smile_appliances)
        app.router.add_get("/core/domain_objects", self.smile_domain_objects)
        app.router.add_get("/core/modules", self.smile_modules)
        app.router.add_get("/system/status.xml", self.smile_status)
        app.router.add_get("/system", self.smile_status)

        if broken:
            app.router.add_get("/core/locations", self.smile_broken)
        elif timeout:
            app.router.add_get("/core/locations", self.smile_timeout)
        else:
            app.router.add_get("/core/locations", self.smile_locations)

        # Introducte timeout with 2 seconds, test by setting response to 10ms
        # Don't actually wait 2 seconds as this will prolongue testing
        if not raise_timeout:
            app.router.add_route(
                "PUT", "/core/locations{tail:.*}", self.smile_set_temp_or_preset
            )
            app.router.add_route(
                "DELETE", "/core/notifications{tail:.*}", self.smile_del_notification
            )
            app.router.add_route("PUT", "/core/rules{tail:.*}", self.smile_set_schedule)
            app.router.add_route(
                "DELETE", "/core/notifications{tail:.*}", self.smile_del_notification
            )
            if not stretch:
                app.router.add_route(
                    "PUT", "/core/appliances{tail:.*}", self.smile_set_relay
                )
            else:
                app.router.add_route(
                    "PUT", "/core/appliances{tail:.*}", self.smile_set_relay_stretch
                )
        else:
            app.router.add_route("PUT", "/core/locations{tail:.*}", self.smile_timeout)
            app.router.add_route("PUT", "/core/rules{tail:.*}", self.smile_timeout)
            app.router.add_route("PUT", "/core/appliances{tail:.*}", self.smile_timeout)
            app.router.add_route(
                "DELETE", "/core/notifications{tail:.*}", self.smile_timeout
            )

        return app

    # Wrapper for appliances uri
    async def smile_appliances(self, request):
        """Render setup specific appliances endpoint."""
        userdata = os.path.join(
            os.path.dirname(__file__),
            f"../userdata/{self.smile_setup}/core.appliances.xml",
        )
        with open(userdata, encoding="utf-8") as filedata:
            data = filedata.read()
        return aiohttp.web.Response(text=data)

    async def smile_domain_objects(self, request):
        """Render setup specific domain objects endpoint."""
        userdata = os.path.join(
            os.path.dirname(__file__),
            f"../userdata/{self.smile_setup}/core.domain_objects.xml",
        )
        with open(userdata, encoding="utf-8") as filedata:
            data = filedata.read()
        return aiohttp.web.Response(text=data)

    async def smile_locations(self, request):
        """Render setup specific locations endpoint."""
        userdata = os.path.join(
            os.path.dirname(__file__),
            f"../userdata/{self.smile_setup}/core.locations.xml",
        )
        with open(userdata, encoding="utf-8") as filedata:
            data = filedata.read()
        return aiohttp.web.Response(text=data)

    async def smile_modules(self, request):
        """Render setup specific modules endpoint."""
        userdata = os.path.join(
            os.path.dirname(__file__),
            f"../userdata/{self.smile_setup}/core.modules.xml",
        )
        with open(userdata, encoding="utf-8") as filedata:
            data = filedata.read()
        return aiohttp.web.Response(text=data)

    async def smile_status(self, request):
        """Render setup specific status endpoint."""
        try:
            userdata = os.path.join(
                os.path.dirname(__file__),
                f"../userdata/{self.smile_setup}/system_status_xml.xml",
            )
            with open(userdata, encoding="utf-8") as filedata:
                data = filedata.read()
            return aiohttp.web.Response(text=data)
        except OSError:
            raise aiohttp.web.HTTPNotFound

    @classmethod
    async def smile_set_temp_or_preset(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

    @classmethod
    async def smile_set_schedule(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

    @classmethod
    async def smile_set_relay(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

    @classmethod
    async def smile_set_relay_stretch(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPOk(text=text)

    @classmethod
    async def smile_del_notification(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

    @classmethod
    async def smile_timeout(cls, request):
        """Render timeout endpoint."""
        raise asyncio.TimeoutError

    @classmethod
    async def smile_broken(cls, request):
        """Render server error endpoint."""
        raise aiohttp.web.HTTPInternalServerError(text="Internal Server Error")

    @classmethod
    async def smile_fail_auth(cls, request):
        """Render authentication error endpoint."""
        raise aiohttp.web.HTTPUnauthorized()

    @staticmethod
    def connect_status(broken, timeout, fail_auth):
        """Determine assumed status from settings."""
        assumed_status = 200
        if broken:
            assumed_status = 500
        if timeout:
            assumed_status = 504
        if fail_auth:
            assumed_status = 401
        return assumed_status

    async def connect(
        self,
        broken=False,
        timeout=False,
        raise_timeout=False,
        fail_auth=False,
        stretch=False,
    ):
        """Connect to a smile environment and perform basic asserts."""
        port = aiohttp.test_utils.unused_port()
        test_password = "".join(random.choice(string.ascii_lowercase) for i in range(8))

        # Happy flow
        app = await self.setup_app(broken, timeout, raise_timeout, fail_auth, stretch)

        server = aiohttp.test_utils.TestServer(
            app, port=port, scheme="http", host="127.0.0.1"
        )
        await server.start_server()

        client = aiohttp.test_utils.TestClient(server)
        websession = client.session

        url = f"{server.scheme}://{server.host}:{server.port}/core/locations"

        # Try/exceptpass to accommodate for Timeout of aoihttp
        try:
            resp = await websession.get(url)
            assumed_status = self.connect_status(broken, timeout, fail_auth)
            assert resp.status == assumed_status
        except Exception:  # pylint: disable=broad-except
            assert True

        if not broken and not timeout and not fail_auth:
            text = await resp.text()
            assert "xml" in text

        # Test lack of websession
        try:
            smile = pw_smile.Smile(
                host=server.host,
                username=pw_constants.DEFAULT_USERNAME,
                password=test_password,
                port=server.port,
                websession=None,
            )
            assert False
        except Exception:  # pylint: disable=broad-except
            assert True

        smile = pw_smile.Smile(
            host=server.host,
            username=pw_constants.DEFAULT_USERNAME,
            password=test_password,
            port=server.port,
            websession=websession,
        )

        if not timeout:
            assert smile._timeout == 30

        # Connect to the smile
        try:
            connection_state = await smile.connect()
            assert connection_state
            return server, smile, client
        except (
            pw_exceptions.DeviceTimeoutError,
            pw_exceptions.InvalidXMLError,
            pw_exceptions.InvalidAuthentication,
        ) as exception:
            await self.disconnect(server, client)
            raise exception

    # Wrap connect for invalid connections
    async def connect_wrapper(
        self, raise_timeout=False, fail_auth=False, stretch=False
    ):
        """Wrap connect to try negative testing before positive testing."""
        if fail_auth:
            try:
                _LOGGER.warning("Connecting to device with invalid credentials:")
                await self.connect(fail_auth=fail_auth)
                _LOGGER.error(" - invalid credentials not handled")  # pragma: no cover
                raise self.ConnectError  # pragma: no cover
            except pw_exceptions.InvalidAuthentication:
                _LOGGER.info(" + successfully aborted on credentials missing.")
                raise pw_exceptions.InvalidAuthentication

        if raise_timeout:
            _LOGGER.warning("Connecting to device exceeding timeout in handling:")
            return await self.connect(raise_timeout=True)

        try:
            _LOGGER.warning("Connecting to device exceeding timeout in response:")
            await self.connect(timeout=True)
            _LOGGER.error(" - timeout not handled")  # pragma: no cover
            raise self.ConnectError  # pragma: no cover
        except (pw_exceptions.DeviceTimeoutError, pw_exceptions.ResponseError):
            _LOGGER.info(" + successfully passed timeout handling.")

        try:
            _LOGGER.warning("Connecting to device with missing data:")
            await self.connect(broken=True)
            _LOGGER.error(" - broken information not handled")  # pragma: no cover
            raise self.ConnectError  # pragma: no cover
        except pw_exceptions.InvalidXMLError:
            _LOGGER.info(" + successfully passed XML issue handling.")

        _LOGGER.info("Connecting to functioning device:")
        return await self.connect(stretch=stretch)

    # Generic disconnect
    @classmethod
    @pytest.mark.asyncio
    async def disconnect(cls, server, client):
        """Disconnect from webserver."""
        await client.session.close()
        await server.close()

    @staticmethod
    def show_setup(location_list, device_list):
        """Show informative outline of the setup."""
        _LOGGER.info("This environment looks like:")
        for loc_id, loc_info in location_list.items():
            _LOGGER.info(
                "  --> Location: %s", "{} ({})".format(loc_info["name"], loc_id)
            )
            device_count = 0
            for dev_id, dev_info in device_list.items():
                if dev_info.get("location", "not_found") == loc_id:
                    device_count += 1
                    _LOGGER.info(
                        "      + Device: %s",
                        "{} ({} - {})".format(
                            dev_info["name"], dev_info["dev_class"], dev_id
                        ),
                    )
            if device_count == 0:  # pragma: no cover
                _LOGGER.info("      ! no devices found in this location")
                assert False

    @pytest.mark.asyncio
    async def device_test(
        self, smile=pw_smile.Smile, test_time=None, testdata=None, initialize=True
    ):
        """Perform basic device tests."""
        bsw_list = ["binary_sensors", "central", "climate", "sensors", "switches"]
        # Make sure to test thermostats with the day set to Monday, needed for full testcoverage of schedules_temps()
        # Otherwise set the day to Sunday.
        with freeze_time(test_time):
            if initialize:
                _LOGGER.info("Asserting testdata:")
                await smile._full_update_device()
                smile.get_all_devices()
                data = await smile.async_update()
            else:
                _LOGGER.info("Asserting updated testdata:")
                data = await smile.async_update()

        if "heater_id" in data.gateway:
            self.cooling_present = data.gateway["cooling_present"]
        self.notifications = data.gateway["notifications"]
        self._write_json("all_data", {"gateway": data.gateway, "devices": data.devices})
        self._write_json("device_list", smile.device_list)
        self._write_json("notifications", data.gateway["notifications"])

        location_list = smile._thermo_locs

        _LOGGER.info("Gateway id = %s", data.gateway["gateway_id"])
        _LOGGER.info("Hostname = %s", smile.smile_hostname)
        _LOGGER.info("Gateway data = %s", data.gateway)
        _LOGGER.info("Device list = %s", data.devices)
        self.show_setup(location_list, data.devices)

        # Perform tests and asserts
        tests = 0
        asserts = 0
        for testdevice, measurements in testdata.items():
            tests += 1
            assert testdevice in data.devices
            asserts += 1
            for dev_id, details in data.devices.items():
                if testdevice == dev_id:
                    _LOGGER.info(
                        "%s",
                        "- Testing data for device {} ({})".format(
                            details["name"], dev_id
                        ),
                    )
                    _LOGGER.info("  + Device data: %s", details)
                    for measure_key, measure_assert in measurements.items():
                        _LOGGER.info(
                            "%s",
                            f"  + Testing {measure_key} (should be {measure_assert})",
                        )
                        tests += 1
                        if (
                            measure_key in bsw_list
                            or measure_key in pw_constants.ACTIVE_ACTUATORS
                        ):
                            tests -= 1
                            for key_1, val_1 in measure_assert.items():
                                tests += 1
                                for key_2, val_2 in details[measure_key].items():
                                    if key_1 != key_2:
                                        continue

                                    _LOGGER.info(
                                        "%s",
                                        f"  + Testing {key_1} ({val_1} should be {val_2})",
                                    )
                                    assert val_1 == val_2
                                    asserts += 1
                        else:
                            assert details[measure_key] == measure_assert
                            asserts += 1

        assert tests == asserts
        _LOGGER.debug("Number of test-assert: %s", asserts)

    @pytest.mark.asyncio
    async def tinker_switch(
        self, smile, dev_id=None, members=None, model="relay", unhappy=False
    ):
        """Turn a Switch on and off to test functionality."""
        _LOGGER.info("Asserting modifying settings for switch devices:")
        _LOGGER.info("- Devices (%s):", dev_id)
        for new_state in [False, True, False]:
            tinker_switch_passed = False
            _LOGGER.info("- Switching %s", new_state)
            try:
                await smile.set_switch_state(dev_id, members, model, new_state)
                tinker_switch_passed = True
                _LOGGER.info("  + worked as intended")
            except pw_exceptions.PlugwiseError:
                _LOGGER.info("  + locked, not switched as expected")
                return False
            except (
                pw_exceptions.ErrorSendingCommandError,
                pw_exceptions.ResponseError,
            ):
                if unhappy:
                    tinker_switch_passed = True  # test is pass!
                    _LOGGER.info("  + failed as expected")
                else:  # pragma: no cover
                    _LOGGER.info("  - failed unexpectedly")
                    return False

        return tinker_switch_passed

    @pytest.mark.asyncio
    async def tinker_thermostat_temp(self, smile, unhappy=False):
        """Toggle temperature to test functionality."""
        _LOGGER.info("Assert modifying temperature setpoint")
        test_temp = 22.9
        _LOGGER.info("- Adjusting temperature to %s", test_temp)
        try:
            await smile.set_temperature(test_temp)
            _LOGGER.info("  + worked as intended")
            return True
        except (
            pw_exceptions.ErrorSendingCommandError,
            pw_exceptions.ResponseError,
        ):
            if unhappy:
                _LOGGER.info("  + failed as expected")
                return True
            else:  # pragma: no cover
                _LOGGER.info("  - failed unexpectedly")
                return True

    @pytest.mark.asyncio
    async def tinker_thermostat_preset(self, smile, unhappy=False):
        """Toggle preset to test functionality."""
        for new_preset in ["asleep", "home", "!bogus"]:
            tinker_preset_passed = False
            warning = ""
            if new_preset[0] == "!":
                warning = " Negative test"
                new_preset = new_preset[1:]
            _LOGGER.info("%s", f"- Adjusting preset to {new_preset}{warning}")
            try:
                await smile.set_preset(new_preset)
                tinker_preset_passed = True
                _LOGGER.info("  + worked as intended")
            except pw_exceptions.PlugwiseError:
                _LOGGER.info("  + found invalid preset, as expected")
                tinker_preset_passed = True
            except (
                pw_exceptions.ErrorSendingCommandError,
                pw_exceptions.ResponseError,
            ):
                if unhappy:
                    tinker_preset_passed = True
                    _LOGGER.info("  + failed as expected")
                else:  # pragma: no cover
                    _LOGGER.info("  - failed unexpectedly")
                    return False

        return tinker_preset_passed

    @pytest.mark.asyncio
    async def tinker_thermostat_schedule(self, smile, state, unhappy=False):
        """Toggle schedules to test functionality."""
        _LOGGER.info("- Adjusting schedule to state %s", state)
        try:
            await smile.set_schedule_state(state)
            tinker_schedule_passed = True
            _LOGGER.info("  + working as intended")
        except pw_exceptions.PlugwiseError:
            _LOGGER.info("  + failed as expected")
            tinker_schedule_passed = True
        except (
            pw_exceptions.ErrorSendingCommandError,
            pw_exceptions.ResponseError,
        ):
            tinker_schedule_passed = False
            if unhappy:
                _LOGGER.info("  + failed as expected before intended failure")
                tinker_schedule_passed = True
            else:  # pragma: no cover
                _LOGGER.info("  - succeeded unexpectedly for some reason")
                return False

        return tinker_schedule_passed

        _LOGGER.info("- Skipping schedule adjustments")  # pragma: no cover

    @pytest.mark.asyncio
    async def tinker_thermostat(self, smile, schedule_on=True, unhappy=False):
        """Toggle various climate settings to test functionality."""
        result_1 = await self.tinker_thermostat_temp(smile, unhappy)
        result_2 = await self.tinker_thermostat_preset(smile, unhappy)
        result_3 = await self.tinker_thermostat_schedule(smile, "on", unhappy)
        if schedule_on:
            result_4 = await self.tinker_thermostat_schedule(smile, "off", unhappy)
            result_5 = await self.tinker_thermostat_schedule(smile, "on", unhappy)
            return result_1 and result_2 and result_3 and result_4 and result_5
        return result_1 and result_2 and result_3

    @staticmethod
    async def tinker_max_boiler_temp(smile):
        """Change max boiler temp setpoint to test functionality."""
        new_temp = 60.0
        dev_id = None
        _LOGGER.info("- Adjusting temperature to %s", new_temp)
        for test in ["maximum_boiler_temperature", "bogus_temperature"]:
            try:
                await smile.set_number_setpoint(test, dev_id, new_temp)
                _LOGGER.info("  + worked as intended")
            except pw_exceptions.PlugwiseError:
                _LOGGER.info("  + failed as intended")

    @staticmethod
    async def tinker_temp_offset(smile, dev_id):
        """Change temperature_offset to test functionality."""
        new_offset = 1.0
        _LOGGER.info("- Adjusting temperature offset to %s", new_offset)
        try:
            await smile.set_temperature_offset("dummy", dev_id, new_offset)
            _LOGGER.info("  + worked as intended")
            return True
        except pw_exceptions.PlugwiseError:
            _LOGGER.info("  + failed as intended")
            return False

    @pytest.mark.asyncio
    async def test_connect_legacy_anna(self):
        """Test a legacy Anna device."""
        testdata = {
            "0000aaaa0000aaaa0000aaaa0000aa00": {
                "dev_class": "gateway",
                "firmware": "1.8.22",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "mac_address": "01:23:45:67:89:AB",
                "model": "Gateway",
                "name": "Smile Anna",
                "vendor": "Plugwise",
                "binary_sensors": {"plugwise_notification": False},
            },
            "0d266432d64443e283b5d708ae98b455": {
                "dev_class": "thermostat",
                "firmware": "2017-03-13T11:54:58+01:00",
                "hardware": "6539-1301-500",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "ThermoTouch",
                "name": "Anna",
                "vendor": "Plugwise",
                "thermostat": {
                    "setpoint": 20.5,
                    "lower_bound": 4.0,
                    "upper_bound": 30.0,
                    "resolution": 0.1,
                },
                "preset_modes": ["away", "vacation", "asleep", "home", "no_frost"],
                "active_preset": "home",
                "available_schedules": ["Thermostat schedule"],
                "select_schedule": "Thermostat schedule",
                "mode": "auto",
                "sensors": {"temperature": 20.4, "illuminance": 151, "setpoint": 20.5},
            },
            "04e4cbfe7f4340f090f85ec3b9e6a950": {
                "dev_class": "heater_central",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "4.21",
                "name": "OpenTherm",
                "vendor": "Bosch Thermotechniek B.V.",
                "maximum_boiler_temperature": {
                    "setpoint": 50.0,
                    "lower_bound": 50.0,
                    "upper_bound": 90.0,
                    "resolution": 1.0,
                },
                "binary_sensors": {"flame_state": True, "heating_state": True},
                "sensors": {
                    "water_temperature": 23.6,
                    "intended_boiler_temperature": 17.0,
                    "modulation_level": 0.0,
                    "return_temperature": 21.7,
                    "water_pressure": 1.2,
                },
            },
        }

        self.smile_setup = "legacy_anna"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = thermostat")
        assert smile.smile_type == "thermostat"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "1.8.22"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2020-03-22 00:00:01", testdata)
        assert smile.gateway_id == "0000aaaa0000aaaa0000aaaa0000aa00"
        assert smile.device_items == 44
        assert not self.notifications

        result = await self.tinker_thermostat(smile)
        assert result
        await smile.close_connection()
        await self.disconnect(server, client)

        server, smile, client = await self.connect_wrapper(raise_timeout=True)
        await self.device_test(smile, "2020-03-22 00:00:01", testdata)
        result = await self.tinker_thermostat(smile, unhappy=True)
        assert result
        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_legacy_anna_2(self):
        """Test another legacy Anna device."""
        testdata = {
            "be81e3f8275b4129852c4d8d550ae2eb": {
                "dev_class": "gateway",
                "firmware": "1.8.22",
                "location": "be81e3f8275b4129852c4d8d550ae2eb",
                "mac_address": "01:23:45:67:89:AB",
                "model": "Gateway",
                "name": "Smile Anna",
                "vendor": "Plugwise",
                "binary_sensors": {"plugwise_notification": False},
                "sensors": {"outdoor_temperature": 21.0},
            },
            "9e7377867dc24e51b8098a5ba02bd89d": {
                "dev_class": "thermostat",
                "firmware": "2017-03-13T11:54:58+01:00",
                "hardware": "6539-1301-5002",
                "location": "be81e3f8275b4129852c4d8d550ae2eb",
                "model": "ThermoTouch",
                "name": "Anna",
                "vendor": "Plugwise",
                "thermostat": {
                    "setpoint": 15.0,
                    "lower_bound": 4.0,
                    "upper_bound": 30.0,
                    "resolution": 0.1,
                },
                "preset_modes": ["vacation", "away", "no_frost", "home", "asleep"],
                "active_preset": None,
                "available_schedules": ["Thermostat schedule"],
                "select_schedule": "None",
                "mode": "heat",
                "sensors": {"temperature": 21.4, "illuminance": 19.5, "setpoint": 15.0},
            },
            "ea5d8a7177e541b0a4b52da815166de4": {
                "dev_class": "heater_central",
                "location": "be81e3f8275b4129852c4d8d550ae2eb",
                "model": "Generic heater",
                "name": "OpenTherm",
                "maximum_boiler_temperature": {
                    "setpoint": 70.0,
                    "lower_bound": 50.0,
                    "upper_bound": 90.0,
                    "resolution": 1.0,
                },
                "binary_sensors": {"flame_state": False, "heating_state": False},
                "sensors": {
                    "water_temperature": 54.0,
                    "intended_boiler_temperature": 0.0,
                    "modulation_level": 0.0,
                    "return_temperature": 0.0,
                    "water_pressure": 1.7,
                },
            },
        }

        self.smile_setup = "legacy_anna_2"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = thermostat")
        assert smile.smile_type == "thermostat"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "1.8.22"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2020-05-03 00:00:01", testdata)

        assert smile.gateway_id == "be81e3f8275b4129852c4d8d550ae2eb"
        assert smile.device_items == 44
        assert not self.notifications

        result = await self.tinker_thermostat(smile)
        assert result

        result = await self.tinker_thermostat_schedule(smile, "on")
        assert result

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_smile_p1_v2(self):
        """Test a legacy P1 device."""
        testdata = {
            "aaaa0000aaaa0000aaaa0000aaaa00aa": {
                "dev_class": "gateway",
                "firmware": "2.5.9",
                "location": "938696c4bcdb4b8a9a595cb38ed43913",
                "mac_address": "012345670001",
                "model": "Gateway",
                "name": "Smile P1",
                "vendor": "Plugwise",
            },
            "938696c4bcdb4b8a9a595cb38ed43913": {
                "dev_class": "smartmeter",
                "location": "938696c4bcdb4b8a9a595cb38ed43913",
                "model": "Ene5\\T210-DESMR5.0",
                "name": "P1",
                "vendor": "Ene5\\T210-DESMR5.0",
                "sensors": {
                    "net_electricity_point": 458,
                    "electricity_consumed_point": 458,
                    "net_electricity_cumulative": 1019.201,
                    "electricity_consumed_peak_cumulative": 1155.195,
                    "electricity_consumed_off_peak_cumulative": 1642.74,
                    "electricity_consumed_peak_interval": 250,
                    "electricity_consumed_off_peak_interval": 0,
                    "electricity_produced_point": 0,
                    "electricity_produced_peak_cumulative": 1296.136,
                    "electricity_produced_off_peak_cumulative": 482.598,
                    "electricity_produced_peak_interval": 0,
                    "electricity_produced_off_peak_interval": 0,
                    "gas_consumed_cumulative": 584.433,
                    "gas_consumed_interval": 0.016,
                },
            },
        }

        self.smile_setup = "smile_p1_v2"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = power")
        assert smile.smile_type == "power"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "2.5.9"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.gateway_id == "aaaa0000aaaa0000aaaa0000aaaa00aa"
        assert smile.device_items == 26
        assert not self.notifications

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_smile_p1_v2_2(self):
        """Test another legacy P1 device."""
        testdata = {
            "aaaa0000aaaa0000aaaa0000aaaa00aa": {
                "dev_class": "gateway",
                "firmware": "2.5.9",
                "location": "199aa40f126840f392983d171374ab0b",
                "mac_address": "012345670001",
                "model": "Gateway",
                "name": "Smile P1",
                "vendor": "Plugwise",
            },
            "199aa40f126840f392983d171374ab0b": {
                "dev_class": "smartmeter",
                "location": "199aa40f126840f392983d171374ab0b",
                "model": "Ene5\\T210-DESMR5.0",
                "name": "P1",
                "vendor": "Ene5\\T210-DESMR5.0",
                "sensors": {
                    "net_electricity_point": 458,
                    "electricity_consumed_point": 458,
                    "net_electricity_cumulative": 1019.201,
                    "electricity_consumed_peak_cumulative": 1155.195,
                    "electricity_consumed_off_peak_cumulative": 1642.74,
                    "electricity_consumed_peak_interval": 250,
                    "electricity_consumed_off_peak_interval": 0,
                    "electricity_produced_point": 0,
                    "electricity_produced_peak_cumulative": 1296.136,
                    "electricity_produced_off_peak_cumulative": 482.598,
                    "electricity_produced_peak_interval": 0,
                    "electricity_produced_off_peak_interval": 0,
                    "gas_consumed_cumulative": 584.433,
                    "gas_consumed_interval": 0.016,
                },
            },
        }
        testdata_updated = {
            "199aa40f126840f392983d171374ab0b": {
                "sensors": {
                    "net_electricity_point": -2248,
                    "electricity_consumed_point": 0,
                    "net_electricity_cumulative": 1019.101,
                    "electricity_consumed_peak_cumulative": 1155.295,
                    "electricity_consumed_off_peak_cumulative": 1642.84,
                    "electricity_produced_point": 2248,
                    "electricity_produced_peak_cumulative": 1296.336,
                    "electricity_produced_off_peak_cumulative": 482.698,
                    "gas_consumed_cumulative": 585.433,
                    "gas_consumed_interval": 0,
                },
            },
        }

        self.smile_setup = "smile_p1_v2_2"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = power")
        assert smile.smile_type == "power"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "2.5.9"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.device_items == 26
        assert not self.notifications

        # Now change some data and change directory reading xml from
        # emulating reading newer dataset after an update_interval
        self.smile_setup = "updated/smile_p1_v2_2"
        await self.device_test(
            smile, "2022-05-16 00:00:01", testdata_updated, initialize=False
        )

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_stretch_v31(self):
        """Test a legacy Stretch with firmware 3.1 setup."""
        testdata = {
            "0000aaaa0000aaaa0000aaaa0000aa00": {
                "dev_class": "gateway",
                "firmware": "3.1.11",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "mac_address": "01:23:45:67:89:AB",
                "model": "Gateway",
                "name": "Stretch",
                "vendor": "Plugwise",
                "zigbee_mac_address": "ABCD012345670101",
            },
            "5871317346d045bc9f6b987ef25ee638": {
                "dev_class": "water_heater_vessel",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4028",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Boiler (1EB31)",
                "zigbee_mac_address": "ABCD012345670A07",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 1.19,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "e1c884e7dede431dadee09506ec4f859": {
                "dev_class": "refrigerator",
                "firmware": "2011-06-27T10:47:37+02:00",
                "hardware": "6539-0700-7330",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle+ type F",
                "name": "Koelkast (92C4A)",
                "zigbee_mac_address": "0123456789AB",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 50.5,
                    "electricity_consumed_interval": 0.08,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "aac7b735042c4832ac9ff33aae4f453b": {
                "dev_class": "dishwasher",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4022",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Vaatwasser (2a1ab)",
                "zigbee_mac_address": "ABCD012345670A02",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.71,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "cfe95cf3de1948c0b8955125bf754614": {
                "dev_class": "dryer",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Droger (52559)",
                "zigbee_mac_address": "ABCD012345670A04",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "059e4d03c7a34d278add5c7a4a781d19": {
                "dev_class": "washingmachine",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Wasmachine (52AC1)",
                "zigbee_mac_address": "ABCD012345670A01",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "d950b314e9d8499f968e6db8d82ef78c": {
                "dev_class": "report",
                "model": "Switchgroup",
                "name": "Stroomvreters",
                "members": [
                    "059e4d03c7a34d278add5c7a4a781d19",
                    "5871317346d045bc9f6b987ef25ee638",
                    "aac7b735042c4832ac9ff33aae4f453b",
                    "cfe95cf3de1948c0b8955125bf754614",
                    "e1c884e7dede431dadee09506ec4f859",
                ],
                "switches": {"relay": True},
            },
            "d03738edfcc947f7b8f4573571d90d2d": {
                "dev_class": "switching",
                "model": "Switchgroup",
                "name": "Schakel",
                "members": [
                    "059e4d03c7a34d278add5c7a4a781d19",
                    "cfe95cf3de1948c0b8955125bf754614",
                ],
                "switches": {"relay": True},
            },
        }
        testdata_updated = {
            "aac7b735042c4832ac9ff33aae4f453b": {
                "sensors": {
                    "electricity_consumed": 1000.0,
                    "electricity_consumed_interval": 20.7,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": True},
            },
            "cfe95cf3de1948c0b8955125bf754614": {
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "059e4d03c7a34d278add5c7a4a781d19": {
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "d03738edfcc947f7b8f4573571d90d2d": {
                "members": [
                    "059e4d03c7a34d278add5c7a4a781d19",
                    "cfe95cf3de1948c0b8955125bf754614",
                ],
                "switches": {"relay": False},
            },
        }

        self.smile_setup = "stretch_v31"
        server, smile, client = await self.connect_wrapper(stretch=True)
        assert smile.smile_hostname == "stretch000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = thermostat")
        assert smile.smile_type == "stretch"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "3.1.11"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.gateway_id == "0000aaaa0000aaaa0000aaaa0000aa00"
        assert smile.device_items == 83

        # Now change some data and change directory reading xml from
        # emulating reading newer dataset after an update_interval
        self.smile_setup = "updated/stretch_v31"
        await self.device_test(
            smile, "2022-05-16 00:00:01", testdata_updated, initialize=False
        )

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_stretch_v23(self):
        """Test a legacy Stretch with firmware 2.3 setup."""
        testdata = {
            "0000aaaa0000aaaa0000aaaa0000aa00": {
                "dev_class": "gateway",
                "firmware": "2.3.12",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "mac_address": "01:23:45:67:89:AB",
                "model": "Gateway",
                "name": "Stretch",
                "vendor": "Plugwise",
                "zigbee_mac_address": "ABCD012345670101",
            },
            "09c8ce93d7064fa6a233c0e4c2449bfe": {
                "dev_class": "lamp",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "kerstboom buiten 043B016",
                "zigbee_mac_address": "ABCD012345670A01",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "33a1c784a9ff4c2d8766a0212714be09": {
                "dev_class": "lighting",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Barverlichting",
                "zigbee_mac_address": "ABCD012345670A13",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "199fd4b2caa44197aaf5b3128f6464ed": {
                "dev_class": "airconditioner",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Airco 25F69E3",
                "zigbee_mac_address": "ABCD012345670A10",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 2.06,
                    "electricity_consumed_interval": 1.62,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "713427748874454ca1eb4488d7919cf2": {
                "dev_class": "freezer",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Leeg 043220D",
                "zigbee_mac_address": "ABCD012345670A12",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "fd1b74f59e234a9dae4e23b2b5cf07ed": {
                "dev_class": "dryer",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Wasdroger 043AECA",
                "zigbee_mac_address": "ABCD012345670A04",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 1.31,
                    "electricity_consumed_interval": 0.21,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": True},
            },
            "c71f1cb2100b42ca942f056dcb7eb01f": {
                "dev_class": "tv",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Tv hoek 25F6790",
                "zigbee_mac_address": "ABCD012345670A11",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 33.3,
                    "electricity_consumed_interval": 4.93,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "2cc9a0fe70ef4441a9e4f55dfd64b776": {
                "dev_class": "lamp",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Lamp TV 025F698F",
                "zigbee_mac_address": "ABCD012345670A15",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 4.0,
                    "electricity_consumed_interval": 0.58,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "6518f3f72a82486c97b91e26f2e9bd1d": {
                "dev_class": "charger",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Bed 025F6768",
                "zigbee_mac_address": "ABCD012345670A14",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "828f6ce1e36744689baacdd6ddb1d12c": {
                "dev_class": "washingmachine",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Wasmachine 043AEC7",
                "zigbee_mac_address": "ABCD012345670A02",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 3.5,
                    "electricity_consumed_interval": 0.5,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": True},
            },
            "71e3e65ffc5a41518b19460c6e8ee34f": {
                "dev_class": "tv",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Leeg 043AEC6",
                "zigbee_mac_address": "ABCD012345670A08",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "305452ce97c243c0a7b4ab2a4ebfe6e3": {
                "dev_class": "lamp",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Lamp piano 025F6819",
                "zigbee_mac_address": "ABCD012345670A05",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "bc0adbebc50d428d9444a5d805c89da9": {
                "dev_class": "watercooker",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Waterkoker 043AF7F",
                "zigbee_mac_address": "ABCD012345670A07",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "407aa1c1099d463c9137a3a9eda787fd": {
                "dev_class": "zz_misc",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "0043B013",
                "zigbee_mac_address": "ABCD012345670A09",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": False, "lock": False},
            },
            "2587a7fcdd7e482dab03fda256076b4b": {
                "dev_class": "zz_misc",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "0000-0440-0107",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "00469CA1",
                "zigbee_mac_address": "ABCD012345670A16",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 0.0,
                    "electricity_consumed_interval": 0.0,
                    "electricity_produced": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "a28e6f5afc0e4fc68498c1f03e82a052": {
                "dev_class": "lamp",
                "firmware": "2011-06-27T10:52:18+02:00",
                "hardware": "6539-0701-4026",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle type F",
                "name": "Lamp bank 25F67F8",
                "zigbee_mac_address": "ABCD012345670A03",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 4.19,
                    "electricity_consumed_interval": 0.62,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": False},
            },
            "24b2ed37c8964c73897db6340a39c129": {
                "dev_class": "router",
                "firmware": "2011-06-27T10:47:37+02:00",
                "hardware": "6539-0700-7325",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle+ type F",
                "name": "MK Netwerk 1A4455E",
                "zigbee_mac_address": "0123456789AB",
                "vendor": "Plugwise",
                "sensors": {
                    "electricity_consumed": 4.63,
                    "electricity_consumed_interval": 0.65,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": True},
            },
            "f7b145c8492f4dd7a4de760456fdef3e": {
                "dev_class": "switching",
                "model": "Switchgroup",
                "name": "Test",
                "members": ["407aa1c1099d463c9137a3a9eda787fd"],
                "switches": {"relay": False},
            },
        }

        self.smile_setup = "stretch_v23"
        server, smile, client = await self.connect_wrapper(stretch=True)
        assert smile.smile_hostname == "stretch000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = thermostat")
        assert smile.smile_type == "stretch"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "2.3.12"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.device_items == 229

        switch_change = await self.tinker_switch(
            smile, "2587a7fcdd7e482dab03fda256076b4b"
        )
        assert switch_change
        switch_change = await self.tinker_switch(
            smile,
            "f7b145c8492f4dd7a4de760456fdef3e",
            ["407aa1c1099d463c9137a3a9eda787fd"],
        )
        assert switch_change

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_stretch_v27_no_domain(self):
        """Test a legacy Stretch with firmware 2.7 setup, with no domain_objects."""
        # testdata dictionary with key ctrl_id_dev_id => keys:values
        testdata = {
            # Circle+
            "9b9bfdb3c7ad4ca5817ccaa235f1e094": {
                "dev_class": "zz_misc",
                "firmware": "2011-06-27T10:47:37+02:00",
                "hardware": "6539-0700-7326",
                "location": "0000aaaa0000aaaa0000aaaa0000aa00",
                "model": "Circle+ type F",
                "name": "25881A2",
                "vendor": "Plugwise",
                "zigbee_mac_address": "ABCD012345670A04",
                "sensors": {
                    "electricity_consumed": 13.3,
                    "electricity_consumed_interval": 7.77,
                    "electricity_produced": 0.0,
                    "electricity_produced_interval": 0.0,
                },
                "switches": {"relay": True, "lock": True},
            },
            # 76BF93
            "8b8d14b242e24cd789743c828b9a2ea9": {
                "sensors": {"electricity_consumed": 1.69},
                "switches": {"lock": False, "relay": True},
            },
            # 25F66AD
            "d0122ac66eba47b99d8e5fbd1e2f5932": {
                "sensors": {"electricity_consumed_interval": 2.21}
            },
        }

        self.smile_setup = "stretch_v27_no_domain"
        server, smile, client = await self.connect_wrapper(stretch=True)
        assert smile.smile_hostname == "stretch000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = thermostat")
        assert smile.smile_type == "stretch"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "2.7.18"
        _LOGGER.info(" # Assert legacy")
        assert smile._smile_legacy

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.device_items == 190
        _LOGGER.info(" # Assert no master thermostat")

        switch_change = await self.tinker_switch(
            smile, "8b8d14b242e24cd789743c828b9a2ea9"
        )
        assert switch_change

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_fail_legacy_system(self):
        """Test erroneous legacy stretch system."""
        self.smile_setup = "faulty_stretch"
        try:
            _server, _smile, _client = await self.connect_wrapper()
            assert False  # pragma: no cover
        except pw_exceptions.InvalidXMLError:
            assert True

    class PlugwiseTestError(Exception):
        """Plugwise test exceptions class."""

    class ConnectError(PlugwiseTestError):
        """Raised when connectivity test fails."""

    class UnexpectedError(PlugwiseTestError):
        """Raised when something went against logic."""
