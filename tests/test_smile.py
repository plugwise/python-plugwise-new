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
    ):
        """Create mock webserver for Smile to interface with."""
        app = aiohttp.web.Application()

        if fail_auth:
            app.router.add_get("/{tail:.*}", self.smile_fail_auth)
            app.router.add_route("PUT", "/{tail:.*}", self.smile_fail_auth)
            return app

        # TODO: CLEANUP; as the whole test was based on /core/locations this needs rewrite to domain_objects
        # CLEANUP Not used anymore
        # CLEANUP app.router.add_get("/core/appliances", self.smile_appliances)
        # CLEANUP Used but needs the timeout-handling instead of /core/locations
        # CLEANUP app.router.add_get("/core/domain_objects", self.smile_domain_objects)
        # CLEANUP Not used anymore
        # CLEANUP app.router.add_get("/core/modules", self.smile_modules)
        # CLEANUP re-introducing this as a working construct doesn't work
        # CLEANUP app.router.add_get("/core/locations", self.smile_locations)

        # CLEANUP Adjusted to domain_objects
        if broken:
            app.router.add_get("/core/domain_objects", self.smile_broken)
        elif timeout:
            app.router.add_get("/core/domain_objects", self.smile_timeout)
        else:
            app.router.add_get("/core/domain_objects", self.smile_domain_objects)

        # Introducte timeout with 2 seconds, test by setting response to 10ms
        # Don't actually wait 2 seconds as this will prolongue testing
        if not raise_timeout:
            app.router.add_route(
                "PUT", "/core/locations{tail:.*}", self.smile_set_temp_or_preset
            )
            app.router.add_route(
                "DELETE", "/core/notifications{tail:.*}", self.smile_del_notification
            )
            # app.router.add_route("PUT", "/core/rules{tail:.*}", self.smile_set_schedule)
            app.router.add_route(
                "DELETE", "/core/notifications{tail:.*}", self.smile_del_notification
            )
            app.router.add_route(
                "PUT", "/core/appliances{tail:.*}", self.smile_set_relay
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
    async def smile_domain_objects(self, request):
        """Render setup specific domain objects endpoint."""
        userdata = os.path.join(
            os.path.dirname(__file__),
            f"../userdata/{self.smile_setup}/core.domain_objects.xml",
        )
        with open(userdata, encoding="utf-8") as filedata:
            data = filedata.read()
        return aiohttp.web.Response(text=data)

    @classmethod
    async def smile_set_temp_or_preset(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

    # @classmethod
    # async def smile_set_schedule(cls, request):
    #     """Render generic API calling endpoint."""
    #     text = "<xml />"
    #     raise aiohttp.web.HTTPAccepted(text=text)

    @classmethod
    async def smile_set_relay(cls, request):
        """Render generic API calling endpoint."""
        text = "<xml />"
        raise aiohttp.web.HTTPAccepted(text=text)

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
    ):
        """Connect to a smile environment and perform basic asserts."""
        port = aiohttp.test_utils.unused_port()
        test_password = "".join(random.choice(string.ascii_lowercase) for i in range(8))

        # Happy flow
        app = await self.setup_app(broken, timeout, raise_timeout, fail_auth)

        server = aiohttp.test_utils.TestServer(
            app, port=port, scheme="http", host="127.0.0.1"
        )
        await server.start_server()

        client = aiohttp.test_utils.TestClient(server)
        websession = client.session

        # CLEANUP: Adjusted from locations to domain_objects
        url = f"{server.scheme}://{server.host}:{server.port}/core/domain_objects"

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
    async def connect_wrapper(self, raise_timeout=False, fail_auth=False):
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
        return await self.connect()

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

    # @pytest.mark.asyncio
    # async def tinker_switch(
    #     self, smile, dev_id=None, members=None, model="relay", unhappy=False
    # ):
    #     """Turn a Switch on and off to test functionality."""
    #     _LOGGER.info("Asserting modifying settings for switch devices:")
    #     _LOGGER.info("- Devices (%s):", dev_id)
    #     for new_state in [False, True, False]:
    #         tinker_switch_passed = False
    #         _LOGGER.info("- Switching %s", new_state)
    #         try:
    #             await smile.set_switch_state(dev_id, members, model, new_state)
    #             tinker_switch_passed = True
    #             _LOGGER.info("  + worked as intended")
    #         except pw_exceptions.PlugwiseError:
    #             _LOGGER.info("  + locked, not switched as expected")
    #             return False
    #         except (
    #             pw_exceptions.ErrorSendingCommandError,
    #             pw_exceptions.ResponseError,
    #         ):
    #             if unhappy:
    #                 tinker_switch_passed = True  # test is pass!
    #                 _LOGGER.info("  + failed as expected")
    #             else:  # pragma: no cover
    #                 _LOGGER.info("  - failed unexpectedly")
    #                 return False

    #     return tinker_switch_passed

    # @pytest.mark.asyncio
    # async def tinker_thermostat_temp(
    #     self, smile, loc_id, block_cooling=False, unhappy=False
    # ):
    #     """Toggle temperature to test functionality."""
    #     _LOGGER.info("Asserting modifying settings in location (%s):", loc_id)
    #     test_temp = {"setpoint": 22.9}
    #     if smile._cooling_present and not block_cooling:
    #         test_temp = {"setpoint_low": 19.5, "setpoint_high": 23.5}
    #     _LOGGER.info("- Adjusting temperature to %s", test_temp)
    #     try:
    #         await smile.set_temperature(loc_id, test_temp)
    #         _LOGGER.info("  + worked as intended")
    #         return True
    #     except (
    #         pw_exceptions.ErrorSendingCommandError,
    #         pw_exceptions.ResponseError,
    #     ):
    #         if unhappy:
    #             _LOGGER.info("  + failed as expected")
    #             return True
    #         else:  # pragma: no cover
    #             _LOGGER.info("  - failed unexpectedly")
    #             return True

    # @pytest.mark.asyncio
    # async def tinker_thermostat_preset(self, smile, loc_id, unhappy=False):
    #     """Toggle preset to test functionality."""
    #     for new_preset in ["asleep", "home", "!bogus"]:
    #         tinker_preset_passed = False
    #         warning = ""
    #         if new_preset[0] == "!":
    #             warning = " Negative test"
    #             new_preset = new_preset[1:]
    #         _LOGGER.info("%s", f"- Adjusting preset to {new_preset}{warning}")
    #         try:
    #             await smile.set_preset(loc_id, new_preset)
    #             tinker_preset_passed = True
    #             _LOGGER.info("  + worked as intended")
    #         except pw_exceptions.PlugwiseError:
    #             _LOGGER.info("  + found invalid preset, as expected")
    #             tinker_preset_passed = True
    #         except (
    #             pw_exceptions.ErrorSendingCommandError,
    #             pw_exceptions.ResponseError,
    #         ):
    #             if unhappy:
    #                 tinker_preset_passed = True
    #                 _LOGGER.info("  + failed as expected")
    #             else:  # pragma: no cover
    #                 _LOGGER.info("  - failed unexpectedly")
    #                 return False

    #     return tinker_preset_passed

    # @pytest.mark.asyncio
    # async def tinker_thermostat_schedule(
    #     self, smile, loc_id, state, good_schedules=None, single=False, unhappy=False
    # ):
    #     """Toggle schedules to test functionality."""
    #     if good_schedules != []:
    #         if not single and ("!VeryBogusSchedule" not in good_schedules):
    #             good_schedules.append("!VeryBogusSchedule")
    #         for new_schedule in good_schedules:
    #             tinker_schedule_passed = False
    #             warning = ""
    #             if new_schedule is not None and new_schedule[0] == "!":
    #                 warning = " Negative test"
    #                 new_schedule = new_schedule[1:]
    #             _LOGGER.info("- Adjusting schedule to %s", f"{new_schedule}{warning}")
    #             try:
    #                 await smile.set_schedule_state(loc_id, state, new_schedule)
    #                 tinker_schedule_passed = True
    #                 _LOGGER.info("  + working as intended")
    #             except pw_exceptions.PlugwiseError:
    #                 _LOGGER.info("  + failed as expected")
    #                 tinker_schedule_passed = True
    #             except (
    #                 pw_exceptions.ErrorSendingCommandError,
    #                 pw_exceptions.ResponseError,
    #             ):
    #                 tinker_schedule_passed = False
    #                 if unhappy:
    #                     _LOGGER.info("  + failed as expected before intended failure")
    #                     tinker_schedule_passed = True
    #                 else:  # pragma: no cover
    #                     _LOGGER.info("  - succeeded unexpectedly for some reason")
    #                     return False

    #         return tinker_schedule_passed

    #     _LOGGER.info("- Skipping schedule adjustments")  # pragma: no cover

    # @pytest.mark.asyncio
    # async def tinker_thermostat(
    #     self,
    #     smile,
    #     loc_id,
    #     schedule_on=True,
    #     good_schedules=None,
    #     single=False,
    #     block_cooling=False,
    #     unhappy=False,
    # ):
    #     """Toggle various climate settings to test functionality."""
    #     if good_schedules is None:  # pragma: no cover
    #         good_schedules = ["Weekschema"]

    #     result_1 = await self.tinker_thermostat_temp(
    #         smile, loc_id, block_cooling, unhappy
    #     )
    #     result_2 = await self.tinker_thermostat_preset(smile, loc_id, unhappy)
    #     if smile._schedule_old_states != {}:
    #         for item in smile._schedule_old_states[loc_id]:
    #             smile._schedule_old_states[loc_id][item] = "off"
    #     result_3 = await self.tinker_thermostat_schedule(
    #         smile, loc_id, "on", good_schedules, single, unhappy
    #     )
    #     if schedule_on:
    #         result_4 = await self.tinker_thermostat_schedule(
    #             smile, loc_id, "off", good_schedules, single, unhappy
    #         )
    #         result_5 = await self.tinker_thermostat_schedule(
    #             smile, loc_id, "on", good_schedules, single, unhappy
    #         )
    #         return result_1 and result_2 and result_3 and result_4 and result_5
    #     return result_1 and result_2 and result_3

    # @staticmethod
    # async def tinker_dhw_mode(smile):
    #     """Toggle dhw to test functionality."""
    #     for mode in ["auto", "boost", "!bogus"]:
    #         warning = ""
    #         if mode[0] == "!":
    #             warning = " Negative test"
    #             mode = mode[1:]
    #         _LOGGER.info("%s", f"- Adjusting dhw mode to {mode}{warning}")
    #         try:
    #             await smile.set_dhw_mode(mode)
    #             _LOGGER.info("  + worked as intended")
    #         except pw_exceptions.PlugwiseError:
    #             _LOGGER.info("  + found invalid mode, as expected")

    # @staticmethod
    # async def tinker_regulation_mode(smile):
    #     """Toggle regulation_mode to test functionality."""
    #     for mode in ["off", "heating", "bleeding_cold", "!bogus"]:
    #         warning = ""
    #         if mode[0] == "!":
    #             warning = " Negative test"
    #             mode = mode[1:]
    #         _LOGGER.info("%s", f"- Adjusting regulation mode to {mode}{warning}")
    #         try:
    #             await smile.set_regulation_mode(mode)
    #             _LOGGER.info("  + worked as intended")
    #         except pw_exceptions.PlugwiseError:
    #             _LOGGER.info("  + found invalid mode, as expected")

    # @staticmethod
    # async def tinker_max_boiler_temp(smile):
    #     """Change max boiler temp setpoint to test functionality."""
    #     new_temp = 60.0
    #     dev_id = None
    #     _LOGGER.info("- Adjusting temperature to %s", new_temp)
    #     for test in ["maximum_boiler_temperature", "bogus_temperature"]:
    #         try:
    #             await smile.set_number_setpoint(test, dev_id, new_temp)
    #             _LOGGER.info("  + worked as intended")
    #         except pw_exceptions.PlugwiseError:
    #             _LOGGER.info("  + failed as intended")

    # @staticmethod
    # async def tinker_temp_offset(smile, dev_id):
    #     """Change temperature_offset to test functionality."""
    #     new_offset = 1.0
    #     _LOGGER.info("- Adjusting temperature offset to %s", new_offset)
    #     try:
    #         await smile.set_temperature_offset("dummy", dev_id, new_offset)
    #         _LOGGER.info("  + worked as intended")
    #         return True
    #     except pw_exceptions.PlugwiseError:
    #         _LOGGER.info("  + failed as intended")
    #         return False

    @pytest.mark.asyncio
    async def test_connect_p1v4_442_single(self):
        """Test a P1 firmware 4.4 single-phase setup."""
        testdata = {
            "a455b61e52394b2db5081ce025a430f3": {
                "dev_class": "gateway",
                "firmware": "4.4.2",
                "hardware": "AME Smile 2.0 board",
                "location": "a455b61e52394b2db5081ce025a430f3",
                "mac_address": "012345670001",
                "model": "Gateway",
                "name": "Smile P1",
                "vendor": "Plugwise",
                "binary_sensors": {"plugwise_notification": False},
            },
            "ba4de7613517478da82dd9b6abea36af": {
                "dev_class": "smartmeter",
                "location": "a455b61e52394b2db5081ce025a430f3",
                "model": "KFM5KAIFA-METER",
                "name": "P1",
                "vendor": "SHENZHEN KAIFA TECHNOLOGY （CHENGDU） CO., LTD.",
                "available": True,
                "sensors": {
                    "net_electricity_point": 421,
                    "electricity_consumed_peak_point": 0,
                    "electricity_consumed_off_peak_point": 421,
                    "net_electricity_cumulative": 31610.113,
                    "electricity_consumed_peak_cumulative": 13966.608,
                    "electricity_consumed_off_peak_cumulative": 17643.505,
                    "electricity_consumed_peak_interval": 0,
                    "electricity_consumed_off_peak_interval": 21,
                    "electricity_produced_peak_point": 0,
                    "electricity_produced_off_peak_point": 0,
                    "electricity_produced_peak_cumulative": 0.0,
                    "electricity_produced_off_peak_cumulative": 0.0,
                    "electricity_produced_peak_interval": 0,
                    "electricity_produced_off_peak_interval": 0,
                    "electricity_phase_one_consumed": 413,
                    "electricity_phase_one_produced": 0,
                },
            },
        }
        testdata_updated = {
            "ba4de7613517478da82dd9b6abea36af": {
                "sensors": {
                    "net_electricity_point": -2248,
                    "electricity_consumed_peak_point": 0,
                    "electricity_consumed_off_peak_point": 0,
                    "electricity_consumed_peak_interval": 0,
                    "electricity_consumed_off_peak_interval": 0,
                    "electricity_produced_peak_point": 2248,
                    "electricity_produced_off_peak_point": 0,
                    "electricity_produced_peak_cumulative": 6.543,
                    "electricity_produced_off_peak_cumulative": 0.0,
                    "electricity_produced_peak_interval": 1345,
                    "electricity_produced_off_peak_interval": 0,
                    "electricity_phase_one_consumed": 0,
                    "electricity_phase_one_produced": 1998,
                },
            },
        }

        self.smile_setup = "p1v4_442_single"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = power")
        assert smile.smile_type == "power"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "4.4.2"

        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.gateway_id == "a455b61e52394b2db5081ce025a430f3"
        assert smile.device_items == 31
        assert not self.notifications

        # Now change some data and change directory reading xml from
        # emulating reading newer dataset after an update_interval
        self.smile_setup = "updated/p1v4_442_single"
        await self.device_test(
            smile, "2022-05-16 00:00:01", testdata_updated, initialize=False
        )

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_connect_p1v4_442_triple(self):
        """Test a P1 firmware 4 3-phase setup."""
        testdata = {
            "03e65b16e4b247a29ae0d75a78cb492e": {
                "dev_class": "gateway",
                "firmware": "4.4.2",
                "hardware": "AME Smile 2.0 board",
                "location": "03e65b16e4b247a29ae0d75a78cb492e",
                "mac_address": "012345670001",
                "model": "Gateway",
                "name": "Smile P1",
                "vendor": "Plugwise",
                "binary_sensors": {"plugwise_notification": True},
            },
            "b82b6b3322484f2ea4e25e0bd5f3d61f": {
                "dev_class": "smartmeter",
                "location": "03e65b16e4b247a29ae0d75a78cb492e",
                "model": "XMX5LGF0010453051839",
                "name": "P1",
                "vendor": "XEMEX NV",
                "available": True,
                "sensors": {
                    "net_electricity_point": 5553,
                    "electricity_consumed_peak_point": 0,
                    "electricity_consumed_off_peak_point": 5553,
                    "net_electricity_cumulative": 231866.539,
                    "electricity_consumed_peak_cumulative": 161328.641,
                    "electricity_consumed_off_peak_cumulative": 70537.898,
                    "electricity_consumed_peak_interval": 0,
                    "electricity_consumed_off_peak_interval": 314,
                    "electricity_produced_peak_point": 0,
                    "electricity_produced_off_peak_point": 0,
                    "electricity_produced_peak_cumulative": 0.0,
                    "electricity_produced_off_peak_cumulative": 0.0,
                    "electricity_produced_peak_interval": 0,
                    "electricity_produced_off_peak_interval": 0,
                    "electricity_phase_one_consumed": 1763,
                    "electricity_phase_two_consumed": 1703,
                    "electricity_phase_three_consumed": 2080,
                    "electricity_phase_one_produced": 0,
                    "electricity_phase_two_produced": 0,
                    "electricity_phase_three_produced": 0,
                    "gas_consumed_cumulative": 16811.37,
                    "gas_consumed_interval": 0.06,
                    "voltage_phase_one": 233.2,
                    "voltage_phase_two": 234.4,
                    "voltage_phase_three": 234.7,
                },
            },
        }

        self.smile_setup = "p1v4_442_triple"
        server, smile, client = await self.connect_wrapper()
        assert smile.smile_hostname == "smile000000"

        _LOGGER.info("Basics:")
        _LOGGER.info(" # Assert type = power")
        assert smile.smile_type == "power"
        _LOGGER.info(" # Assert version")
        assert smile.smile_version[0] == "4.4.2"
        await self.device_test(smile, "2022-05-16 00:00:01", testdata)
        assert smile.gateway_id == "03e65b16e4b247a29ae0d75a78cb492e"
        assert smile.device_items == 40
        assert self.notifications

        await smile.close_connection()
        await self.disconnect(server, client)

    @pytest.mark.asyncio
    async def test_invalid_credentials(self):
        """Test P1 with invalid credentials setup."""
        self.smile_setup = "p1v4_442_single"
        try:
            await self.connect_wrapper(fail_auth=True)
            assert False  # pragma: no cover
        except pw_exceptions.InvalidAuthentication:
            _LOGGER.debug("InvalidAuthentication raised successfully")
            assert True

    @pytest.mark.asyncio
    async def test_connect_fail_firmware(self):
        """Test a P1 non existing firmware setup."""
        self.smile_setup = "fail_firmware"
        try:
            await self.connect_wrapper()
            assert False  # pragma: no cover
        except pw_exceptions.UnsupportedDeviceError:
            assert True

    # Test connect for timeout
    @patch(
        "plugwise.helper.ClientSession.get",
        side_effect=aiohttp.ServerTimeoutError,
    )
    @pytest.mark.asyncio
    async def test_connect_timeout(self, timeout_test):
        """Wrap connect to raise timeout during get."""
        # pylint: disable=unused-variable
        try:
            self.smile_setup = "p1v4_442_single"
            (
                server,
                smile,
                client,
            ) = await self.connect_wrapper()
            assert False  # pragma: no cover
        except pw_exceptions.PlugwiseException:
            assert True

    class PlugwiseTestError(Exception):
        """Plugwise test exceptions class."""

    class ConnectError(PlugwiseTestError):
        """Raised when connectivity test fails."""

    class UnexpectedError(PlugwiseTestError):
        """Raised when something went against logic."""
