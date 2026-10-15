import datetime
import common
import prog
import serial
import sys
import os
import threading
import time
from production import ProductionError, production_client
from status_store import status

isFileOpen = False
fp = None
_sfp_thread = None
_sfp_abort_requested = threading.Event()
_sfp_lock = threading.Lock()
_report_file_lock = threading.RLock()
test_serial = None
test_serial_preexisting = None
report_upload_requested = False
report_uploaded = False
report_relay_states = {}
report_current_step = None
REPORT_STEP_SEPARATOR = "------\t------"

REPORT_EVENT_STEPS = (
    ("T_STR", "start"),
    ("T_SERIAL_PRESENT", "identity"),
    ("T_ID_", "identity"),
    ("T_SHC", "shorts"),
    ("T_CU1", "current_1"),
    ("T_LDO", "ldo"),
    ("T_OBP", "option_bytes"),
    ("T_TFP", "test_firmware"),
    ("T_CU2", "current_2"),
    ("T_USB_3V3", "usb_power"),
    ("T_SFP", "sample_firmware"),
    ("T_RUP", "report"),
    ("T_STP", "finish"),
)

REPORT_COMMAND_STEPS = {
    "C_PRP": "pi",
    "C_PIN": "network",
    "C_IDP": "identity",
    "C_SLR": "stlink",
    "C_OBP": "option_bytes",
    "C_TFP": "test_firmware",
    "C_SFP": "sample_firmware",
    "C_RUP": "report",
}

REPORT_STEP_NAMES = {
    "start": "Start test",
    "pi": "Check Raspberry Pi",
    "network": "Production API",
    "identity": "Provision identity",
    "stlink": "Reset ST-LINK",
    "shorts": "Short-circuit test",
    "current_1": "Initial current",
    "ldo": "LDO voltage",
    "option_bytes": "Configure option bytes",
    "test_firmware": "Program test firmware",
    "current_2": "Programmed current",
    "usb_power": "USB power",
    "sample_firmware": "Program sample firmware",
    "report": "Upload test report",
    "finish": "Finish",
}


def reset_report_relay_states():
    global report_current_step
    report_relay_states.clear()
    report_current_step = None


def should_log_test_event(line):
    """Suppress repeated relay states while preserving real transitions."""
    if not line.startswith("T_RLY "):
        return True

    parts = line.split()
    if len(parts) != 3 or parts[2] not in ("ON", "OFF"):
        return True

    relay = parts[1]
    state = parts[2]
    if report_relay_states.get(relay) == state:
        return False
    report_relay_states[relay] = state
    return True


def report_step_for_event(line):
    if report_current_step == "start" and line in (
            "T_RLY USB_DATA OFF",
            "T_RLY USB_PWR OFF"):
        return "start"
    if line in (
            "T_RLY USB_DATA ON",
            "T_RLY USB_DATA OFF",
            "T_RLY USB_PWR ON",
            "T_RLY USB_PWR OFF"):
        return "usb_power"
    for prefix, step in REPORT_EVENT_STEPS:
        if line.startswith(prefix):
            return step
    return None


def begin_report_step(file, step):
    global report_current_step
    with _report_file_lock:
        if step == report_current_step:
            return
        writeInLogFile(file, REPORT_STEP_SEPARATOR)
        writeInLogFile(file, REPORT_STEP_NAMES[step])
        report_current_step = step


def write_report_result(file, step, success, detail=None):
    with _report_file_lock:
        if not isFileOpen or file is None:
            return
        begin_report_step(file, step)
        result = "SUCCESS" if success else "FAILURE"
        if detail:
            result += f" ({detail})"
        writeInLogFile(file, f"RESULT: {result}")


def write_test_event(file, line):
    """Write one event, separating logical steps and removing relay repeats."""
    global report_current_step
    if not should_log_test_event(line):
        return

    step = report_step_for_event(line)
    if step is not None:
        begin_report_step(file, step)
    writeInLogFile(file, line)

def _sample_upload_worker():
    global _sfp_thread
    try:
        try:
            sfp_ret = prog.bootloader_write()
        except Exception as error:
            print(f"Sample firmware upload failed: {error}")
            sfp_ret = 255

        if not _sfp_abort_requested.is_set():
            write_report_result(
                fp,
                "sample_firmware",
                sfp_ret == 0,
                None if sfp_ret == 0 else f"code {sfp_ret}",
            )
            common.send("R_SFP_SUC" if sfp_ret == 0 else "R_SFP_TKO")
    finally:
        with _sfp_lock:
            _sfp_thread = None

def start_sample_upload():
    global _sfp_thread
    with _sfp_lock:
        if _sfp_thread is not None and _sfp_thread.is_alive():
            return False
        _sfp_abort_requested.clear()
        _sfp_thread = threading.Thread(target=_sample_upload_worker, daemon=True)
        _sfp_thread.start()
        return True

def abort_sample_upload():
    _sfp_abort_requested.set()
    abort_ret = prog.abort_active_upload()
    with _sfp_lock:
        worker = _sfp_thread
    if worker is not None and worker is not threading.current_thread():
        worker.join(timeout=4)
    return abort_ret

def writeInLogFile(file, line):
    with _report_file_lock:
        if isFileOpen and file is not None:
            file.write(line + "\n")
            file.flush()
        else:
            print("! FILE IS NOT OPEN")
            #abort test command ??

def setLogFileUid(file, stm32_uid):
    """Replace the report's first line while preserving all test events."""
    if not isFileOpen or file is None:
        print("! FILE IS NOT OPEN")
        return
    file.flush()
    file.seek(0)
    report_lines = file.readlines()
    uid_line = "STM32_UID " + (stm32_uid or "UNAVAILABLE") + "\n"
    if report_lines and report_lines[0].startswith("STM32_UID "):
        report_lines[0] = uid_line
    else:
        report_lines.insert(0, uid_line)
    file.seek(0)
    file.truncate()
    file.writelines(report_lines)
    file.flush()
    file.seek(0, os.SEEK_END)

def setLogFileSerial(file, serial):
    """Replace the report's second metadata line with the assigned serial."""
    if not isFileOpen or file is None:
        print("! FILE IS NOT OPEN")
        return
    file.flush()
    file.seek(0)
    report_lines = file.readlines()
    serial_line = "SERIAL " + (serial or "UNAVAILABLE") + "\n"
    if len(report_lines) > 1 and report_lines[1].startswith("SERIAL "):
        report_lines[1] = serial_line
    else:
        report_lines.insert(1 if report_lines else 0, serial_line)
    file.seek(0)
    file.truncate()
    file.writelines(report_lines)
    file.flush()
    file.seek(0, os.SEEK_END)

def connect_production_api():
    try:
        return production_client.connect()
    except ProductionError as error:
        print(error)
        return False

def provision_dut_serial():
    global test_serial, test_serial_preexisting
    otp_data = prog.read_stm32_otp()
    existing_identity = prog.find_otp_identity(otp_data)
    existing_serial = existing_identity["serial"]
    test_serial_preexisting = existing_serial is not None
    if existing_serial is not None:
        test_serial = existing_serial
        if existing_identity["passphrase"] is None:
            passphrase = production_client.get_passphrase(existing_serial)
            prog.write_otp_identity(existing_serial, passphrase, otp_data)
        return existing_serial

    generated_identity = production_client.generate_identity()
    generated_serial = generated_identity["serial"]
    # Keep the API identity even if the irreversible OTP write fails, so the
    # provisioning failure report can still be uploaded against that serial.
    test_serial = generated_serial
    prog.write_otp_identity(generated_serial, generated_identity["passphrase"], otp_data)
    return generated_serial

def upload_current_report(file):
    if file is None or not isFileOpen:
        return False
    if test_serial is None:
        print("Test report retained locally: no serial was assigned")
        return False
    file.flush()
    file.seek(0)
    report = file.read()
    file.seek(0, os.SEEK_END)
    try:
        production_client.upload_report(test_serial, report)
        print(f"Test report uploaded for {test_serial}")
        return True
    except ProductionError as error:
        print(f"Test report retained locally: {error}")
        return False

def closeLogFile():
    global fp, isFileOpen
    if fp is not None and not fp.closed:
        fp.close()
    fp = None
    isFileOpen = False

def serialCom(announce_ready=False):
    global fp, isFileOpen, test_serial, test_serial_preexisting, report_upload_requested, report_uploaded
    service_ready = not announce_ready
    next_ready_announcement = 0
    try:
        while True:
            if common.consume_controller_reset():
                # Stop the tester state machine before releasing the serial port.
                common.send("R_STP")
                time.sleep(0.2)
                return "reset"

            command = common.consume_controller_command()
            if command is not None:
                common.send(command)

            if not service_ready:
                now = time.monotonic()
                if now >= next_ready_announcement:
                    common.send("R_RDY")
                    next_ready_announcement = now + 1

            received_data = common.ser.readline().decode('utf-8', errors='replace').strip()
            if received_data.startswith(("C_", "T_")):
                status.record(received_data, "received")

            command_step = REPORT_COMMAND_STEPS.get(received_data)
            if command_step is not None and isFileOpen:
                begin_report_step(fp, command_step)
            
            if received_data == "C_RDY":
                service_ready = True

            elif received_data == "C_PRP":
                write_report_result(fp, "pi", True)
                common.send("R_PRP")

            elif received_data == "C_PIN":
                api_connected = connect_production_api()
                write_report_result(fp, "network", api_connected)
                common.send("R_PIN_SUC" if api_connected else "R_PIN_TKO")

            elif received_data == "C_IDP":
                identity_ready = False
                common.send("R_RLY_DUT_ON")
                time.sleep(0.5)
                try:
                    setLogFileUid(fp, prog.read_stm32_uid())
                    assigned_serial = provision_dut_serial()
                    setLogFileSerial(fp, assigned_serial)
                    write_test_event(fp, f"T_SERIAL_PRESENT {'YES' if test_serial_preexisting else 'NO'}")
                    identity_ready = True
                except Exception as error:
                    write_test_event(fp, f"T_ID_TKO {error}")
                    presence = "UNKNOWN" if test_serial_preexisting is None else ("YES" if test_serial_preexisting else "NO")
                    write_test_event(fp, f"T_SERIAL_PRESENT {presence}")
                    setLogFileSerial(fp, test_serial)
                    print(f"DUT identity provisioning failed: {error}")
                finally:
                    common.send("R_RLY_DUT_OFF")
                if identity_ready:
                    common.send("R_IDP_SUC")
                else:
                    common.send("R_IDP_TKO")

            elif received_data == "C_SLR":
                print(f"Command received: {received_data}")
                rs_ret = prog.resetStLink()
                if (rs_ret == 0):
                    write_report_result(fp, "stlink", True)
                    common.send("R_SLR_SUC")
                elif (rs_ret == 1):
                    write_report_result(fp, "stlink", False, "R_SLR_FND")
                    common.send("R_SLR_FND")
                elif (rs_ret == 2):
                    write_report_result(fp, "stlink", False, "R_SLR_FPM")
                    common.send("R_SLR_FPM")
                elif (rs_ret == 255):
                    write_report_result(fp, "stlink", False, "R_SLR_PNA")
                    common.send("R_SLR_PNA")
                else:
                    write_report_result(fp, "stlink", False, f"code {rs_ret}")

            elif received_data == "C_OBP":
                print(f"Command received: {received_data}")
                common.send("R_OBP_ACK")
                try:
                    ob_ret = prog.ob_procedure()
                except Exception as error:
                    print(f"Option-byte programming failed: {error}")
                    ob_ret = -1
                if (ob_ret == 1):
                    write_report_result(fp, "option_bytes", True)
                    common.send("R_OBP_SUC")
                else:
                    write_report_result(fp, "option_bytes", False, f"code {ob_ret}")
                    common.send("R_OBP_TKO")

            elif received_data == "C_TFP":
                common.send("R_TFP_ACK")
                try:
                    tfp_ret = prog.firm_write("bootloader-v1.1.0.bin") #("fast_blinky.bin")
                except Exception as error:
                    print(f"Test firmware upload failed: {error}")
                    tfp_ret = 255
                if (tfp_ret == 0):
                    write_report_result(fp, "test_firmware", True)
                    common.send("R_TFP_SUC")
                else:
                    write_report_result(fp, "test_firmware", False, f"code {tfp_ret}")
                    common.send("R_TFP_TKO")
                    # Do not leave the tester waiting in the failure-choice
                    # state, where a button press could start the next step
                    # with an unprogrammed DUT. Force a graceful stop.
                    common.send("R_STP")

            elif received_data == "C_SFP":
                common.send("R_SFP_ACK")
                if not start_sample_upload():
                    common.send("R_SFP_TKO")

            elif received_data == "C_SFP_ABORT":
                abort_ret = abort_sample_upload()
                common.send("R_SFP_ABORT_ACK" if abort_ret == 0 else "R_SFP_ABORT_TKO")

            elif received_data == "C_RUP":
                report_upload_requested = True
                report_uploaded = upload_current_report(fp)
                common.send("R_RUP_SUC" if report_uploaded else "R_RUP_TKO")

            elif received_data == "C_SRP":
                sd_ret = prog.shutdown()
                print("shutdown" + str(sd_ret))
                sys.exit(0)

            elif received_data == "":
                pass

            elif received_data.startswith("T_"):
                if (received_data == "T_STR"):
                    test_serial = None
                    test_serial_preexisting = None
                    report_upload_requested = False
                    report_uploaded = False
                    common.clear_controller_commands()
                    reset_report_relay_states()
                    closeLogFile()
                    now = datetime.datetime.now()
                    os.makedirs('test_reports', exist_ok=True)
                    fp = open('test_reports/SPIN-TEST_' + str(now.isoformat()) + '.txt', 'w+')
                    isFileOpen = True
                    writeInLogFile(fp, "STM32_UID UNAVAILABLE")
                    writeInLogFile(fp, "SERIAL UNAVAILABLE")
                    writeInLogFile(fp, str(now))
                    write_test_event(fp, received_data)
                elif(received_data == "T_STP"):
                    if isFileOpen:
                        write_test_event(fp, received_data)
                        # Older firmware and tests stopped before the explicit
                        # report-upload command. Preserve that fallback without
                        # uploading a completed report twice.
                        if not report_upload_requested:
                            report_uploaded = upload_current_report(fp)
                        closeLogFile()
                    else:
                        print("Ignoring T_STP: no test report is open")
                else:
                    write_test_event(fp, received_data)
            else:
                print(f"received: `{received_data}`")

    except KeyboardInterrupt:
        print("Program terminated.")
        sys.exit(0)
    except serial.SerialException as e: #device disconnected
        print(f"Serial connection lost: {e}")
    finally:
        closeLogFile()
        if common.ser is not None and common.ser.is_open:
            common.ser.close()  # Close the serial port when done
