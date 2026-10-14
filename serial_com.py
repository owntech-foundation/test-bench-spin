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
test_serial = None

def _sample_upload_worker():
    global _sfp_thread
    try:
        try:
            sfp_ret = prog.bootloader_write()
        except Exception as error:
            print(f"Sample firmware upload failed: {error}")
            sfp_ret = 255

        if not _sfp_abort_requested.is_set():
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
    global test_serial
    otp_data = prog.read_stm32_otp()
    existing_serial = prog.find_otp_serial(otp_data)
    if existing_serial is not None:
        test_serial = existing_serial
        return existing_serial

    generated_serial = production_client.generate_serial()
    # Keep the API identity even if the irreversible OTP write fails, so the
    # provisioning failure report can still be uploaded against that serial.
    test_serial = generated_serial
    return prog.write_otp_serial(generated_serial, otp_data)

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
    global fp, isFileOpen, test_serial
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
            
            if received_data == "C_RDY":
                service_ready = True

            elif received_data == "C_PRP":
                common.send("R_PRP")

            elif received_data == "C_PIN":
                if connect_production_api():
                    common.send("R_PIN_SUC")
                else:
                    common.send("R_PIN_TKO")

            elif received_data == "C_SLR":
                print(f"Command received: {received_data}")
                rs_ret = prog.resetStLink()
                if (rs_ret == 0):
                    common.send("R_SLR_SUC")
                elif (rs_ret == 1):
                    common.send("R_SLR_FND")
                elif (rs_ret == 2):
                    common.send("R_SLR_FPM")
                elif (rs_ret == 255):
                    common.send("R_SLR_PNA")

            elif received_data == "C_OBP":
                print(f"Command received: {received_data}")
                common.send("R_OBP_ACK")
                try:
                    setLogFileUid(fp, prog.read_stm32_uid())
                    assigned_serial = provision_dut_serial()
                    setLogFileSerial(fp, assigned_serial)
                    ob_ret = prog.ob_procedure()
                except Exception as error:
                    writeInLogFile(fp, f"T_ID_TKO {error}")
                    setLogFileSerial(fp, test_serial)
                    print(f"DUT identity provisioning failed: {error}")
                    ob_ret = -1
                if (ob_ret == 1):
                    common.send("R_OBP_SUC")
                else:
                    common.send("R_OBP_TKO")

            elif received_data == "C_TFP":
                common.send("R_TFP_ACK")
                try:
                    tfp_ret = prog.firm_write("bootloader-v1.1.0.bin") #("fast_blinky.bin")
                except Exception as error:
                    print(f"Test firmware upload failed: {error}")
                    tfp_ret = 255
                if (tfp_ret == 0):
                    common.send("R_TFP_SUC")
                else:
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

            elif received_data == "C_SRP":
                sd_ret = prog.shutdown()
                print("shutdown" + str(sd_ret))
                sys.exit(0)

            elif received_data == "":
                pass

            elif received_data.startswith("T_"):
                if (received_data == "T_STR"):
                    test_serial = None
                    common.clear_controller_commands()
                    closeLogFile()
                    now = datetime.datetime.now()
                    os.makedirs('test_reports', exist_ok=True)
                    fp = open('test_reports/SPIN-TEST_' + str(now.isoformat()) + '.txt', 'w+')
                    isFileOpen = True
                    writeInLogFile(fp, "STM32_UID UNAVAILABLE")
                    writeInLogFile(fp, "SERIAL UNAVAILABLE")
                    writeInLogFile(fp, str(now))
                    writeInLogFile(fp, received_data)
                elif(received_data == "T_STP"):
                    if isFileOpen:
                        writeInLogFile(fp, received_data)
                        upload_current_report(fp)
                        closeLogFile()
                    else:
                        print("Ignoring T_STP: no test report is open")
                else:
                    writeInLogFile(fp, received_data)
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
