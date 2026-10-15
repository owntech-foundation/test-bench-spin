import types
import unittest
import threading
import json
import http.client
import io
import struct
import tempfile
import urllib.request
from unittest import mock

import common
import check_otp_identity
import main
import prog
import production
import serial_com
from status_store import TestStatus
from web_server import start_server


class FakeSerial:
    def __init__(self, lines):
        self._lines = iter(lines)
        self.is_open = True

    def readline(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise KeyboardInterrupt

    def close(self):
        self.is_open = False


class RegressionTests(unittest.TestCase):
    def tearDown(self):
        serial_com.closeLogFile()
        serial_com._sfp_abort_requested.set()
        serial_com.test_serial = None
        serial_com.test_serial_preexisting = None
        serial_com.report_upload_requested = False
        serial_com.report_uploaded = False
        serial_com.reset_report_relay_states()
        common.ser = None

    def test_send_paces_serial_bytes(self):
        common.ser = mock.Mock()
        with mock.patch.object(common.time, "sleep") as sleep:
            common.send("R_RLY_DUT_ON")

        expected = [mock.call(bytes((byte,))) for byte in b"R_RLY_DUT_ON\r\n"]
        self.assertEqual(common.ser.write.call_args_list, expected)
        self.assertEqual(sleep.call_count, len(expected))

    def test_clear_controller_commands(self):
        common.ser = mock.Mock()
        common.request_controller_command("R_RLY_USB_PWR_ON")
        common.clear_controller_commands()
        self.assertIsNone(common.consume_controller_command())

    def test_report_logs_only_relay_state_transitions(self):
        serial_com.reset_report_relay_states()

        self.assertTrue(serial_com.should_log_test_event("T_RLY DUT ON"))
        self.assertTrue(serial_com.should_log_test_event("T_LDO 3.300"))
        self.assertFalse(serial_com.should_log_test_event("T_RLY DUT ON"))
        self.assertFalse(serial_com.should_log_test_event("T_RLY DUT ON"))
        self.assertTrue(serial_com.should_log_test_event("T_RLY DUT OFF"))
        self.assertFalse(serial_com.should_log_test_event("T_RLY DUT OFF"))
        self.assertTrue(serial_com.should_log_test_event("T_RLY DUT ON"))

    def test_report_separates_logical_steps(self):
        serial_com.isFileOpen = True
        serial_com.reset_report_relay_states()
        with tempfile.TemporaryFile(mode="w+") as report_file:
            for event in (
                    "T_STR",
                    "T_RLY DUT ON",
                    "T_CU1 12.300",
                    "T_RLY DUT ON",
                    "T_LDO 3.300",
                    "T_LDO_TKO"):
                serial_com.write_test_event(report_file, event)
            report_file.seek(0)
            self.assertEqual(
                report_file.read(),
                "------\t------\n"
                "Start test\n"
                "T_STR\n"
                "T_RLY DUT ON\n"
                "------\t------\n"
                "Initial current\n"
                "T_CU1 12.300\n"
                "------\t------\n"
                "LDO voltage\n"
                "T_LDO 3.300\n"
                "T_LDO_TKO\n",
            )

    def test_report_names_command_driven_steps(self):
        serial_com.isFileOpen = True
        serial_com.reset_report_relay_states()
        with tempfile.TemporaryFile(mode="w+") as report_file:
            serial_com.begin_report_step(report_file, "pi")
            serial_com.begin_report_step(report_file, "network")
            serial_com.begin_report_step(report_file, "identity")
            report_file.seek(0)
            self.assertEqual(
                report_file.read(),
                "------\t------\n"
                "Check Raspberry Pi\n"
                "------\t------\n"
                "Production API\n"
                "------\t------\n"
                "Provision identity\n",
            )

    def test_report_records_command_step_results(self):
        serial_com.isFileOpen = True
        serial_com.reset_report_relay_states()
        with tempfile.TemporaryFile(mode="w+") as report_file:
            serial_com.write_report_result(report_file, "pi", True)
            serial_com.write_report_result(report_file, "network", False)
            serial_com.write_report_result(report_file, "stlink", False, "R_SLR_FND")
            serial_com.write_report_result(report_file, "option_bytes", True)
            report_file.seek(0)
            self.assertEqual(
                report_file.read(),
                "------\t------\n"
                "Check Raspberry Pi\n"
                "RESULT: SUCCESS\n"
                "------\t------\n"
                "Production API\n"
                "RESULT: FAILURE\n"
                "------\t------\n"
                "Reset ST-LINK\n"
                "RESULT: FAILURE (R_SLR_FND)\n"
                "------\t------\n"
                "Configure option bytes\n"
                "RESULT: SUCCESS\n",
            )

    def test_usb_relays_are_grouped_under_usb_power(self):
        serial_com.isFileOpen = True
        serial_com.reset_report_relay_states()
        with tempfile.TemporaryFile(mode="w+") as report_file:
            for event in (
                    "T_CU2 36.500",
                    "T_RLY USB_DATA ON",
                    "T_RLY USB_PWR ON",
                    "T_USB_3V3 3.290",
                    "T_RLY USB_DATA OFF",
                    "T_RLY USB_PWR OFF"):
                serial_com.write_test_event(report_file, event)
            report_file.seek(0)
            self.assertEqual(
                report_file.read(),
                "------\t------\n"
                "Programmed current\n"
                "T_CU2 36.500\n"
                "------\t------\n"
                "USB power\n"
                "T_RLY USB_DATA ON\n"
                "T_RLY USB_PWR ON\n"
                "T_USB_3V3 3.290\n"
                "T_RLY USB_DATA OFF\n"
                "T_RLY USB_PWR OFF\n",
            )

    def test_otp_checker_powers_dut_off_after_read(self):
        statuses = [
            {"controller": {"connected": True}, "test": {"state": "idle"}},
            {"diagnostics": {"relays": {"DUT": True}}},
            {"diagnostics": {"relays": {"DUT": False}}},
        ]
        otp_data = prog._serial_record("SPN1234567890") + b"\xff" * (prog.STM32_OTP_SIZE - prog.SERIAL_RECORD_SIZE)
        with mock.patch.object(check_otp_identity, "_status", side_effect=statuses), \
             mock.patch.object(check_otp_identity, "_set_dut_power") as set_power, \
             mock.patch.object(check_otp_identity.time, "sleep"), \
             mock.patch.object(prog, "read_stm32_uid", return_value="1234567890ABCDEF10203040"), \
             mock.patch.object(prog, "read_stm32_otp", return_value=otp_data):
            uid, identity = check_otp_identity.read_fitted_spin_identity()
        self.assertEqual(uid, "1234567890ABCDEF10203040")
        self.assertEqual(identity["serial"], "SPN1234567890")
        self.assertEqual(set_power.call_args_list, [mock.call(True), mock.call(False)])

    def test_otp_checker_refuses_during_active_test(self):
        active = {
            "controller": {"connected": True},
            "test": {"state": "running", "finished_at": None},
        }
        with mock.patch.object(check_otp_identity, "_status", return_value=active), \
             mock.patch.object(check_otp_identity, "_set_dut_power") as set_power, \
             self.assertRaises(check_otp_identity.OtpCheckError):
            check_otp_identity.read_fitted_spin_identity()
        set_power.assert_not_called()

    def test_otp_checker_displays_passphrase(self):
        identity = {"serial": "SPN1234567890", "passphrase": "test passphrase/+"}
        with mock.patch.object(
                check_otp_identity,
                "read_fitted_spin_identity",
                return_value=("1234567890ABCDEF10203040", identity)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            result = check_otp_identity.main()

        self.assertEqual(result, 0)
        self.assertIn("OTP passphrase: test passphrase/+", output.getvalue())
        self.assertIn(
            "Online report: https://devices.owntech.org/?s=SPN1234567890&p=test+passphrase%2F%2B",
            output.getvalue(),
        )

    def test_firmware_upload_exception_returns_failure_response(self):
        common.ser = FakeSerial([b"C_TFP\r\n"])
        with mock.patch.object(prog, "firm_write", side_effect=RuntimeError("upload failed")), \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()

        self.assertEqual(send.call_args_list, [
            mock.call("R_TFP_ACK"),
            mock.call("R_TFP_TKO"),
            mock.call("R_STP"),
        ])

    def test_sample_upload_abort_is_acknowledged(self):
        common.ser = FakeSerial([b"C_SFP_ABORT\r\n"])
        with mock.patch.object(serial_com, "abort_sample_upload", return_value=0), \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        send.assert_called_once_with("R_SFP_ABORT_ACK")

    def test_sample_upload_runs_without_blocking_serial_commands(self):
        common.ser = FakeSerial([b"C_SFP\r\n", b"C_SFP_ABORT\r\n"])
        release_upload = threading.Event()

        def upload():
            release_upload.wait(1)
            return 124

        def abort():
            release_upload.set()
            return 0

        with mock.patch.object(prog, "bootloader_write", side_effect=upload), \
             mock.patch.object(prog, "abort_active_upload", side_effect=abort), \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()

        self.assertEqual(send.call_args_list, [
            mock.call("R_SFP_ACK"),
            mock.call("R_SFP_ABORT_ACK"),
        ])

    def test_failed_production_api_login_sends_failure_response(self):
        common.ser = FakeSerial([b"C_PIN\r\n"])
        with mock.patch.object(serial_com, "connect_production_api", return_value=False), \
             mock.patch.object(common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        send.assert_called_once_with("R_PIN_TKO")

    def test_api_ping_does_not_provision_identity(self):
        common.ser = FakeSerial([b"C_PIN\r\n"])
        with mock.patch.object(serial_com, "connect_production_api", return_value=True) as connect, \
             mock.patch.object(serial_com, "provision_dut_serial") as provision, \
             mock.patch.object(common, "send") as send, \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        connect.assert_called_once_with()
        provision.assert_not_called()
        send.assert_called_once_with("R_PIN_SUC")

    def test_api_identity_generation_keeps_passphrase(self):
        client = production.ProductionClient()
        sdk = mock.Mock()
        sdk.generate_serial.return_value = {
            "status_code": 200,
            "content": {"serial": "SPN1234567890", "passphrase": "Ab3dE6gH9k"},
        }
        with mock.patch.object(client, "_connected_sdk", return_value=sdk), \
             mock.patch.object(client, "_setting", side_effect=lambda _name, default=None: default):
            self.assertEqual(
                client.generate_identity(),
                {"serial": "SPN1234567890", "passphrase": "Ab3dE6gH9k"},
            )

    def test_api_passphrase_lookup_selects_exact_serial(self):
        client = production.ProductionClient()
        sdk = mock.Mock()
        sdk.list_products.return_value = {
            "status_code": 200,
            "content": {
                "items": [
                    {"serial": "SPN0000000000", "passphrase": "Wrong12345"},
                    {"serial": "SPN1234567890", "passphrase": "Ab3dE6gH9k"},
                ],
            },
        }
        with mock.patch.object(client, "_connected_sdk", return_value=sdk):
            self.assertEqual(client.get_passphrase("SPN1234567890"), "Ab3dE6gH9k")
        sdk.list_products.assert_called_once_with(page=1, page_size=20, search="SPN1234567890")

    def test_api_step_provisions_identity_before_hardware_tests(self):
        common.ser = FakeSerial([b"C_IDP\r\n"])
        serial_com.isFileOpen = True
        report_file = mock.MagicMock()
        serial_com.fp = report_file
        with mock.patch.object(serial_com.prog, "read_stm32_uid", return_value="1234567890ABCDEF10203040"), \
             mock.patch.object(serial_com, "provision_dut_serial", return_value="SPN1234567890"), \
             mock.patch.object(serial_com, "setLogFileUid") as set_uid, \
             mock.patch.object(serial_com, "setLogFileSerial") as set_serial, \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com.time, "sleep"), \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()

        self.assertEqual(send.call_args_list, [
            mock.call("R_RLY_DUT_ON"),
            mock.call("R_RLY_DUT_OFF"),
            mock.call("R_IDP_SUC"),
        ])
        set_uid.assert_called_once_with(report_file, "1234567890ABCDEF10203040")
        set_serial.assert_called_once_with(report_file, "SPN1234567890")

    def test_controller_connection_announces_service_ready(self):
        controller = mock.Mock()
        with mock.patch.object(main.serial, "Serial", return_value=controller) as serial_port, \
             mock.patch.object(main.serial_com, "serialCom") as serial_com:
            main.connect_controller("/dev/ttyACM0")

        serial_port.assert_called_once_with(port="/dev/ttyACM0", baudrate=115200, timeout=1)
        self.assertIs(common.ser, controller)
        serial_com.assert_called_once_with(announce_ready=True)

    def test_service_readiness_is_retried_until_acknowledged(self):
        common.ser = FakeSerial([b"", b"C_RDY\r\n"])
        with mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com.time, "monotonic", side_effect=[0, 1]), \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom(announce_ready=True)

        self.assertEqual(send.call_args_list, [mock.call("R_RDY"), mock.call("R_RDY")])

    def test_stop_without_open_report_is_ignored(self):
        common.ser = FakeSerial([b"T_STP\r\n"])
        with mock.patch.object(serial_com, "print") as output, \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        output.assert_any_call("Ignoring T_STP: no test report is open")

    @mock.patch.object(prog.time, "sleep")
    @mock.patch.object(prog.subprocess, "Popen")
    def test_programmer_stderr_is_parsed(self, popen, _sleep):
        process = types.SimpleNamespace(
            stdout=iter(["No such device found\n"]),
            returncode=1,
            wait=mock.Mock(),
        )
        popen.return_value = process
        self.assertEqual(prog.execute_cmd_prog("usbreset 0483:374f"), 1)

    def test_stm32_uid_is_formatted_from_three_device_words(self):
        uid_bytes = struct.pack("<III", 0x12345678, 0x90ABCDEF, 0x10203040)

        def write_uid(command, **_kwargs):
            with open(command[2], "wb") as uid_file:
                uid_file.write(uid_bytes)
            return types.SimpleNamespace(returncode=0)

        with mock.patch.object(prog.subprocess, "run", side_effect=write_uid):
            self.assertEqual(prog.read_stm32_uid(), "1234567890ABCDEF10203040")

    def test_stm32_uid_read_failure_returns_none(self):
        result = types.SimpleNamespace(returncode=1)
        with mock.patch.object(prog.subprocess, "run", return_value=result), \
             mock.patch.object(prog, "print"):
            self.assertIsNone(prog.read_stm32_uid())

    def test_stm32_uid_replaces_first_report_line(self):
        serial_com.isFileOpen = True
        with tempfile.TemporaryFile(mode="w+") as report_file:
            report_file.write("STM32_UID UNAVAILABLE\nSERIAL UNAVAILABLE\n2026-10-14 12:00:00\nT_STR\n")
            serial_com.setLogFileUid(report_file, "1234567890ABCDEF10203040")
            serial_com.setLogFileSerial(report_file, "SPN1234567890")
            report_file.seek(0)
            self.assertEqual(
                report_file.read(),
                "STM32_UID 1234567890ABCDEF10203040\nSERIAL SPN1234567890\n2026-10-14 12:00:00\nT_STR\n",
            )

    def test_option_byte_step_runs_after_identity_provisioning(self):
        common.ser = FakeSerial([b"C_OBP\r\n"])
        serial_com.isFileOpen = True
        report_file = mock.MagicMock()
        serial_com.fp = report_file
        with mock.patch.object(serial_com.prog, "ob_procedure", return_value=1) as ob_procedure, \
             mock.patch.object(serial_com.common, "send"), \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()

        ob_procedure.assert_called_once_with()

    def test_otp_serial_record_round_trip(self):
        serial = "SPN1234567890"
        record = prog._serial_record(serial)
        self.assertEqual(len(record), prog.SERIAL_RECORD_SIZE)
        self.assertEqual(prog._decode_serial_record(record), serial)
        otp_data = record + b"\xff" * (prog.STM32_OTP_SIZE - len(record))
        self.assertEqual(prog.find_otp_serial(otp_data), serial)

    def test_otp_passphrase_record_round_trip(self):
        passphrase = "Ab3dE6gH9k"
        record = prog._passphrase_record(passphrase)
        self.assertEqual(len(record), prog.SERIAL_RECORD_SIZE)
        self.assertEqual(prog._decode_passphrase_record(record), passphrase)
        otp_data = record + b"\xff" * (prog.STM32_OTP_SIZE - len(record))
        self.assertEqual(prog.find_otp_passphrase(otp_data), passphrase)

    def test_existing_otp_identity_is_reused_without_api_generation(self):
        serial = "SPN1234567890"
        passphrase = "Ab3dE6gH9k"
        records = prog._serial_record(serial) + prog._passphrase_record(passphrase)
        otp_data = records + b"\xff" * (prog.STM32_OTP_SIZE - len(records))
        with mock.patch.object(prog, "read_stm32_otp", return_value=otp_data), \
             mock.patch.object(serial_com.production_client, "generate_identity") as generate, \
             mock.patch.object(serial_com.production_client, "get_passphrase") as lookup, \
             mock.patch.object(prog, "write_otp_identity") as write:
            self.assertEqual(serial_com.provision_dut_serial(), serial)
        self.assertTrue(serial_com.test_serial_preexisting)
        generate.assert_not_called()
        lookup.assert_not_called()
        write.assert_not_called()

    def test_legacy_otp_serial_gets_api_passphrase_without_new_serial(self):
        serial = "SPN1234567890"
        passphrase = "Ab3dE6gH9k"
        record = prog._serial_record(serial)
        otp_data = record + b"\xff" * (prog.STM32_OTP_SIZE - len(record))
        with mock.patch.object(prog, "read_stm32_otp", return_value=otp_data), \
             mock.patch.object(serial_com.production_client, "generate_identity") as generate, \
             mock.patch.object(serial_com.production_client, "get_passphrase", return_value=passphrase) as lookup, \
             mock.patch.object(prog, "write_otp_identity") as write:
            self.assertEqual(serial_com.provision_dut_serial(), serial)
        self.assertTrue(serial_com.test_serial_preexisting)
        generate.assert_not_called()
        lookup.assert_called_once_with(serial)
        write.assert_called_once_with(serial, passphrase, otp_data)

    def test_blank_otp_gets_api_identity_and_verified_write(self):
        serial = "SPN1234567890"
        passphrase = "Ab3dE6gH9k"
        otp_data = b"\xff" * prog.STM32_OTP_SIZE
        with mock.patch.object(prog, "read_stm32_otp", return_value=otp_data), \
             mock.patch.object(serial_com.production_client, "generate_identity", return_value={"serial": serial, "passphrase": passphrase}) as generate, \
             mock.patch.object(prog, "write_otp_identity", return_value={"serial": serial, "passphrase": passphrase}) as write:
            self.assertEqual(serial_com.provision_dut_serial(), serial)
        self.assertFalse(serial_com.test_serial_preexisting)
        generate.assert_called_once_with()
        write.assert_called_once_with(serial, passphrase, otp_data)

    def test_otp_write_is_enabled_then_read_back_for_verification(self):
        serial = "SPN1234567890"
        blank_otp = b"\xff" * prog.STM32_OTP_SIZE
        record = prog._serial_record(serial)
        verified_otp = record + blank_otp[len(record):]
        with mock.patch.object(prog, "_run_openocd") as openocd, \
             mock.patch.object(prog, "read_stm32_otp", return_value=verified_otp):
            self.assertEqual(prog.write_otp_serial(serial, blank_otp), serial)
        commands = openocd.call_args.args[0]
        self.assertIn("stm32l4x otp 1 enable", commands)
        self.assertIn("stm32l4x otp 1 disable", commands)

    def test_otp_identity_write_programs_and_verifies_both_records(self):
        serial = "SPN1234567890"
        passphrase = "Ab3dE6gH9k"
        blank_otp = b"\xff" * prog.STM32_OTP_SIZE
        serial_record = prog._serial_record(serial)
        passphrase_record = prog._passphrase_record(passphrase)
        serial_only = serial_record + blank_otp[len(serial_record):]
        complete = serial_record + passphrase_record + blank_otp[len(serial_record) + len(passphrase_record):]
        with mock.patch.object(prog, "_run_openocd") as openocd, \
             mock.patch.object(prog, "read_stm32_otp", side_effect=[serial_only, complete]):
            self.assertEqual(
                prog.write_otp_identity(serial, passphrase, blank_otp),
                {"serial": serial, "passphrase": passphrase},
            )
        self.assertEqual(openocd.call_count, 2)

    def test_finished_report_is_uploaded_for_failed_or_successful_test(self):
        common.ser = FakeSerial([b"T_STP\r\n"])
        serial_com.isFileOpen = True
        report_file = mock.MagicMock()
        serial_com.fp = report_file
        with mock.patch.object(serial_com, "upload_current_report", return_value=True) as upload, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        upload.assert_called_once_with(report_file)

    def test_explicit_report_upload_responds_and_is_not_repeated_on_stop(self):
        common.ser = FakeSerial([b"C_RUP\r\n", b"T_STP\r\n"])
        serial_com.isFileOpen = True
        report_file = mock.MagicMock()
        serial_com.fp = report_file
        with mock.patch.object(serial_com, "upload_current_report", return_value=True) as upload, \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        upload.assert_called_once_with(report_file)
        send.assert_called_once_with("R_RUP_SUC")

    def test_test_firmware_erases_existing_application_before_write(self):
        with mock.patch.object(prog, "execute_cmd_prog", side_effect=[0, 0]) as execute:
            self.assertEqual(prog.firm_write("bootloader-v1.1.0.bin"), 0)

        self.assertEqual(execute.call_args_list, [
            mock.call("st-flash erase", timeout_seconds=60),
            mock.call("st-flash --reset write bootloader-v1.1.0.bin 0x8000000"),
        ])

    def test_test_firmware_stops_when_erase_fails(self):
        with mock.patch.object(prog, "execute_cmd_prog", return_value=1) as execute:
            self.assertEqual(prog.firm_write("bootloader-v1.1.0.bin"), 1)

        execute.assert_called_once_with("st-flash erase", timeout_seconds=60)

    def test_status_tracks_a_successful_sequence(self):
        test_status = TestStatus()
        test_status.set_controller("/dev/ttyACM0")
        for message in ("T_STR", "C_PRP", "C_PIN", "C_IDP", "C_SLR", "T_SHC_SUC", "T_CU1 12.3", "T_LDO 3.3", "T_OBP_SUC", "T_TFP_SUC", "T_CU2 36.5", "T_USB_3V3 3.29", "T_SFP_SUC", "C_RUP", "R_RUP_SUC", "T_STP"):
            test_status.record(message)
        snapshot = test_status.snapshot()
        self.assertEqual(snapshot["test"]["state"], "passed")
        self.assertEqual(snapshot["measurements"]["T_LDO"], "3.3 V")
        self.assertEqual(snapshot["measurements"]["T_USB_3V3"], "3.29 V")
        self.assertTrue(snapshot["controller"]["connected"])
        self.assertEqual(snapshot["steps"][-2]["id"], "report")

    def test_web_status_endpoint(self):
        server = start_server("127.0.0.1", 0)
        self.assertIsNotNone(server)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/status") as response:
                payload = json.load(response)
            self.assertEqual(payload["service"]["state"], "running")
            self.assertEqual(len(payload["operator_steps"]), 7)
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("HEAD", "/")
            self.assertEqual(connection.getresponse().status, 200)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
