#!/usr/bin/env python3
"""Read the fitted SPIN's production identity without modifying OTP."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

import prog


STATUS_URL = os.getenv("OWNTESTER_STATUS_URL", "http://127.0.0.1:8080").rstrip("/")


class OtpCheckError(RuntimeError):
    pass


def _request(path, method="GET", payload=None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(STATUS_URL + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise OtpCheckError(f"Unable to contact owntester.service: {error}") from error


def _status():
    return _request("/api/status")


def _set_dut_power(enabled):
    response = _request(
        "/api/diagnostics/relay",
        method="POST",
        payload={"relay": "DUT", "enabled": enabled},
    )
    if response.get("status") != "accepted":
        raise OtpCheckError("The tester did not accept the DUT relay command")


def _wait_for_dut_power(enabled, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        relays = (_status().get("diagnostics") or {}).get("relays") or {}
        if bool(relays.get("DUT")) is enabled:
            return
        time.sleep(0.1)
    raise OtpCheckError(f"DUT relay did not turn {'on' if enabled else 'off'}")


def read_fitted_spin_identity():
    current = _status()
    if not (current.get("controller") or {}).get("connected"):
        raise OtpCheckError("The SPIN tester controller is not connected")
    test = current.get("test") or {}
    if test.get("state") in ("running", "failed") and not test.get("finished_at"):
        raise OtpCheckError("A test is active; finish or stop it before reading OTP")

    power_requested = False
    try:
        _set_dut_power(True)
        power_requested = True
        _wait_for_dut_power(True)
        time.sleep(0.5)
        uid = prog.read_stm32_uid()
        identity = prog.find_otp_identity(prog.read_stm32_otp())
    finally:
        if power_requested:
            try:
                _set_dut_power(False)
                _wait_for_dut_power(False)
            except OtpCheckError as error:
                print(f"WARNING: {error}", file=sys.stderr)

    return uid, identity


def main():
    try:
        uid, identity = read_fitted_spin_identity()
    except (OtpCheckError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    serial = identity.get("serial")
    print(f"STM32 UID: {uid or 'unavailable'}")
    print(f"OTP serial: {serial or 'not present'}")
    print(f"OTP passphrase record: {'present' if identity.get('passphrase') else 'not present'}")
    return 0 if serial else 2


if __name__ == "__main__":
    raise SystemExit(main())
