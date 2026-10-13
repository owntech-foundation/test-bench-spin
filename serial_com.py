import datetime
import common
import prog
import serial
import sys
import os
import subprocess

isFileOpen = False
fp = None

def writeInLogFile(file, line):
    if isFileOpen and file is not None:
        file.write(line + "\n")
        file.flush()
    else:
        print("! FILE IS NOT OPEN")
        #abort test command ??

def check_ping(hostname):
    try:
        response = subprocess.run(
            ["ping", "-c", "3", hostname],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return response.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def closeLogFile():
    global fp, isFileOpen
    if fp is not None and not fp.closed:
        fp.close()
    fp = None
    isFileOpen = False

def serialCom():
    global fp, isFileOpen
    try:
        while True:
            received_data = common.ser.readline().decode('utf-8', errors='replace').strip()
            
            if received_data == "C_PRP":
                common.send("R_PRP")

            elif received_data == "C_PIN":
                pingInternet = check_ping(hostname = "owntech.org")
                if pingInternet:
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
                ob_ret = prog.ob_procedure()
                if (ob_ret == 1):
                    common.send("R_OBP_SUC")
                else:
                    common.send("R_OBP_TKO")

            elif received_data == "C_TFP":
                common.send("R_TFP_ACK")
                tfp_ret = prog.firm_write("bootloader-v1.1.0.bin") #("fast_blinky.bin")
                if (tfp_ret == 0):
                    common.send("R_TFP_SUC")
                else:
                    common.send("R_TFP_TKO")

            elif received_data == "C_SFP":
                common.send("R_SFP_ACK")
                sfp_ret = prog.bootloader_write()

                if (sfp_ret == 0):
                    common.send("R_SFP_SUC")
                else:
                    common.send("R_SFP_TKO")

            elif received_data == "C_SRP":
                sd_ret = prog.shutdown()
                print("shutdown" + str(sd_ret))
                sys.exit(0)

            elif received_data == "":
                pass

            elif received_data.startswith("T_"):
                if (received_data == "T_STR"):
                    closeLogFile()
                    now = datetime.datetime.now()
                    os.makedirs('test_reports', exist_ok=True)
                    fp = open('test_reports/SPIN-TEST_' + str(now.isoformat()) + '.txt', 'w')
                    isFileOpen = True
                    writeInLogFile(fp, str(now))
                    writeInLogFile(fp, received_data)
                elif(received_data == "T_STP"):
                    if isFileOpen:
                        writeInLogFile(fp, received_data)
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
