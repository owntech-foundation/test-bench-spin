import types
import unittest
from unittest import mock

import common
import prog
import serial_com


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

    def test_failed_internet_ping_sends_failure_response(self):
        common.ser = FakeSerial([b"C_PIN\r\n"])
        with mock.patch.object(serial_com, "check_ping", return_value=False), \
             mock.patch.object(common, "send") as send, \
             mock.patch.object(serial_com, "print"), \
             self.assertRaises(SystemExit):
            serial_com.serialCom()
        send.assert_called_once_with("R_PIN_TKO")

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


if __name__ == "__main__":
    unittest.main()
