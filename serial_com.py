import datetime
import common
import prog
import serial
import sys

isFileOpen = False
fp = None

def writeInLogFile(file, line):
    if (isFileOpen == True):
        file.write(line + "\n")
    else:
        print("! FILE IS NOT OPEN")
        #abort test command ??

def serialCom():
    global fp, isFileOpen
    try:
        while True:
            received_data = common.ser.readline().decode('utf-8').strip()

            if received_data == "C_PRP":
                common.send("R_PRP")

            elif received_data == "C_SLR":
                print(f"Command received: {received_data}")
                rs_ret = prog.resetStLink()
                if (rs_ret == 0):
                    common.send("R_SLR_SUC")
                elif (rs_ret == 1):
                    common.send("R_SLR_FND")
                elif (rs_ret == 2):
                    common.send("R_SLR_FPM")

            elif received_data == "C_OBP":
                print(f"Command received: {received_data}")
                common.send("R_OBP_ACK")
                ob_ret = prog.ob_procedure()
                if (ob_ret == 1):
                    common.send("R_OBP_SUC")
                else:
                    common.send("R_OBP_FAI")

            elif received_data == "C_TFP":
                common.send("R_TFP_ACK")
                tfp_ret = prog.firm_write("bootloader-v1.1.0.bin") #("fast_blinky.bin")
                if (tfp_ret == 0):
                    common.send("R_TFP_SUC")
                else:
                    common.send("R_TFP_FAI")

            elif received_data == "C_SFP":
                common.send("R_SFP_ACK")
                sfp_ret = prog.bootloader_write()

                if (sfp_ret == 0):
                    common.send("R_SFP_SUC")
                else:
                    common.send("R_SFP_FAI")

            elif received_data == "C_SRP":
                sd_ret = prog.shutdown()
                print("shutdown" + str(sd_ret))
                sys.exit(0)

            elif received_data == "":
                pass

            elif received_data.startswith("T_"):
                if (received_data == "T_STR"):
                    now = datetime.datetime.now()
                    fp = open('test_reports/SPIN-TEST_' + str(now.isoformat()) + '.txt', 'w')
                    isFileOpen = True
                    writeInLogFile(fp, str(now))
                    writeInLogFile(fp, received_data)
                elif(received_data == "T_STP"):
                    writeInLogFile(fp, received_data)
                    fp.close()
                    isFileOpen = False
                else:
                    writeInLogFile(fp, received_data)
            else:
                print(f"received: `{received_data}`")

    except KeyboardInterrupt:
        common.ser.close()
        print("Program terminated.")
        sys.exit(0)
    except serial.SerialException as e: #device disconnected
        common.ser.close()
    finally:
        common.ser.close()  # Close the serial port when done
