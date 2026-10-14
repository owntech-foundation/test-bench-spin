import types
import unittest
import json
import http.client
import urllib.request
from unittest import mock

import common
import main
import prog
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
        with mock.patch.object(prog, "abort_active_upload", return_value=0), \
             mock.patch.object(serial_com.common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        send.assert_called_once_with("R_SFP_ABORT_ACK")

    def test_failed_internet_ping_sends_failure_response(self):
        common.ser = FakeSerial([b"C_PIN\r\n"])
        with mock.patch.object(serial_com, "check_ping", return_value=False), \
             mock.patch.object(common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        send.assert_called_once_with("R_PIN_TKO")

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

    def test_status_tracks_a_successful_sequence(self):
        test_status = TestStatus()
        test_status.set_controller("/dev/ttyACM0")
        for message in ("T_STR", "C_PRP", "C_PIN", "C_SLR", "T_SHC_SUC", "T_CU1 12.3", "T_LDO 3.3", "T_OBP_SUC", "T_TFP_SUC", "T_CU2 36.5", "T_SFP_SUC", "T_STP"):
            test_status.record(message)
        snapshot = test_status.snapshot()
        self.assertEqual(snapshot["test"]["state"], "passed")
        self.assertEqual(snapshot["measurements"]["T_LDO"], "3.3 V")
        self.assertTrue(snapshot["controller"]["connected"])

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
