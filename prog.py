import subprocess
import re
from termcolor import colored
import common
import time

h = lambda hex_in : str(hex(hex_in)).strip("0x")
ps = lambda text : colored(text, "green")
pe = lambda text : colored(text, "red")
pw = lambda text : colored(text, "light_yellow")
pb = lambda text : colored("0x" + text, "yellow", attrs=["bold"])

factory_expected_ob = 0xffeff8aa
desired_ob = 0xffeff8aa #0xfbeff8aa <-- Nboot0 thinggy. Not required.

def execute_cmd_expected_return(cmd, expected_rtrn):
        print(cmd)
        #command = subprocess.check_output(cmd.split(" "))
        command = subprocess.run(cmd.split(" "), capture_output=True)
        #print(command)
        #print(command.returncode)
        #print(command.stdout)
        #print(command.stderr)
        out = command.stdout.decode("utf-8").rstrip("\n").replace("\r", "").split("\n") #bytes to string then remove the last empty element and cut
        print(out)
        if (out[-1] == expected_rtrn):
                return True, out[-1], command.returncode
        return False, out[-1], command.returncode


#check if the target is connected and the factory option bytes. Read values and factory expexted missmatch will not stop the procedure

def  ob_read(expected_ob_value):
        initial_read = execute_cmd_expected_return("st-flash --area=option read", h(expected_ob_value))
        if (initial_read[2] > 0):
                print(pe("Error while reading the option bytes. Target is not connected? Aborting procedure"))
                return(-1, None)
        elif (initial_read[0] == False):
                return(1, initial_read[1])
        elif (initial_read[0] == True):
                return(2, initial_read[1])
        else:
                print(pe("unknown state"))
                return(-1, None)

def ob_write(expected_ob_value):
        #Actual write of the option bytes
        write_ob = execute_cmd_expected_return(f"st-flash --area=option write {hex(expected_ob_value)}", h(expected_ob_value)) #needs to succeed
        if (write_ob[2] > 0):
                print(pe("error while writing the option bytes"))
                return(-1)
        else:
                #print(ps("Write of the options bytes was successful."))
                return(1)

def ob_procedure():
        inital_read_code, initial_read_value = ob_read(factory_expected_ob)
        if (inital_read_code == 1):
                print(pw("The option bytes are already the expected value"), pb(initial_read_value), pw(", maybe this board is already flashed?"))
        elif (inital_read_code == 2):
                print(ps("The option bytes are"), pb(initial_read_value), ps("as expected for a new chip!"))
        else:
                return (-1)
        
        write_code = ob_write(desired_ob)
        if (write_code == 1):
                print(ps("Write of the options bytes was successful."))
        else:
                return(-1)
        
        verify_read_code, verify_read_value = ob_read(desired_ob)
        if (verify_read_code == 1):
                print(pe("desired"),  pb(verify_read_value), pe("and read"), pb(verify_read_value), pe("do not match. Something is wrong aborting."))
        elif (verify_read_code == 2):
                print(ps("Verify of the options bytes was successful. Option bytes are now"), pb(verify_read_value), ps("as expexcted."))
        else:
                return (-1)
        return (1)

def execute_cmd_prog(cmd):
        # Start the subprocess and redirect its standard output and error to pipes
        special_ret = 0
        process = subprocess.Popen(cmd.split(" "), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, text=True)
        patternFirmWrite = re.compile(r'(\d+)/\s(\d+)\s+pages written')
        patternBootloader = re.compile(r"(\d+\.?\d*) (\w+B|B) \/ (\d+\.?\d*) (\w+B|B) (?:\[.*?\]|).*?(\d+\.?\d*)%(?: (\d+\.?\d*) ((?:\w+B|B)\/s)|)")
        patternResetStlinkSuc = re.compile(r'.*?ok')
        patternResetStlinkFaiPerm = re.compile(r'.*?\[Permission denied\]')
        patternResetStlinkFaiNoDev = re.compile(r'No such device found')

        # Read output and error in real-time
        while True:
                output_line = process.stdout.readline()
                # error_line = process.stderr.readline()
                if not output_line: # and not error_line:
                        break
                
                matchFirmWrite = patternFirmWrite.search(output_line)
                matchBootloader = patternBootloader.search(output_line)
                # matchResetStlinkSuc = patternResetStlinkSuc.search(output_line)
                matchResetStlinkFaiPerm = patternResetStlinkFaiPerm.search(output_line)
                # matchResetStlinkFaiNoDev = patternResetStlinkFaiNoDev.search(error_line)

                # if output_line != "\n":
                #         print(ascii(output_line))
                # if error_line != "\n" and error_line != "":
                #         print("2>" + ascii(error_line))

                if matchFirmWrite:
                        common.send("U_TFP_PER " + str((int(matchFirmWrite.group(1))*100)//(int(matchFirmWrite.group(2)) - 1)), True)
                elif matchBootloader:
                        common.send("U_SFP_PER " + str(int(float(matchBootloader.group(5)))), True)
                # elif matchResetStlinkSuc:
                #         common.send("R_SLR_SUC")
                elif matchResetStlinkFaiPerm:
                        special_ret = 2 #R_SLR_FPM
                # elif matchResetStlinkFaiNoDev:
                #         pass
                        # common.send("R_SLR_FND")
        process.wait()
        time.sleep(1)
        return process.returncode + special_ret

def firm_write(bin_path, addr=0x08000000):
        ret = execute_cmd_prog("st-flash --reset write " + bin_path + " " + str(hex(addr)))
        # print("ret" + str(ret))
        return (ret)

def search_for_device(vid, pid, name):
        port = None
        while (port == None):
                common.device_port = common.find_device(vid, pid, "MCUBOOT")

                if common.device_port is not None:
                        print(f"Device found on {common.device_port}")

                print("Device disconnected, searching for a new device.")
                
def bootloader_write():
        ret = execute_cmd_prog("./mcumgr-rpi --conntype=serial --connstring=dev=/dev/ttyACM2,baud=115200,mtu=128 image upload -e blank-firmware-bootloader.bin")
        # print("ret" + str(ret))
        return (ret)

def resetStLink():
        ret = execute_cmd_prog("usbreset 0483:374f") #usb pid vio for stlink v3
        print(ret)
        return (ret)

def shutdown():
        ret = execute_cmd_prog("sudo shutdown -h now")
        return (ret)
