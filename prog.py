import subprocess
import re
import shlex
import os
import signal
import threading
from termcolor import colored
import common
import time

_active_process = None
_active_process_lock = threading.Lock()

h = lambda hex_in : str(hex(hex_in)).strip("0x")
ps = lambda text : colored(text, "green")
pe = lambda text : colored(text, "red")
pw = lambda text : colored(text, "light_yellow")
pb = lambda text : colored("0x" + text, "yellow", attrs=["bold"])

factory_expected_ob = 0xffeff8aa
desired_ob = 0xffeff8aa #0xfbeff8aa <-- Nboot0 thinggy. Not required.

def execute_cmd_expected_return(cmd, expected_rtrn):
	print(cmd)
	try:
		command = subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=False)
	except OSError as error:
		print(pe(f"Unable to execute command: {error}"))
		return False, "", 255
	out = command.stdout.rstrip("\n").replace("\r", "").split("\n")
	if not command.stdout.strip() and command.stderr.strip():
		out = command.stderr.rstrip("\n").replace("\r", "").split("\n")
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

def execute_cmd_prog(cmd, timeout_seconds=120):
	global _active_process
	# Start the subprocess and redirect its standard output and error to pipes
	special_ret = 0
	try:
		process = subprocess.Popen(
			shlex.split(cmd),
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			bufsize=1,
			start_new_session=True,
		)
		with _active_process_lock:
			_active_process = process
	except OSError as error:
		print(pe(f"Unable to execute command: {error}"))
		return 255
	patternFirmWrite = re.compile(r'(\d+)/\s(\d+)\s+pages written')
	patternBootloader = re.compile(r"(\d+\.?\d*) (\w+B|B) \/ (\d+\.?\d*) (\w+B|B) (?:\[.*?\]|).*?(\d+\.?\d*)%(?: (\d+\.?\d*) ((?:\w+B|B)\/s)|)")
	patternResetStlinkSuc = re.compile(r'.*?ok')
	patternResetStlinkFaiPerm = re.compile(r'.*?\[Permission denied\]')
	patternResetStlinkFaiNoDev = re.compile(r'No such device found')

	timed_out = False

	def stop_stuck_process():
		nonlocal timed_out
		if process.poll() is not None:
			return
		timed_out = True
		print(pe(f"Command timed out after {timeout_seconds}s; terminating process group"))
		try:
			os.killpg(process.pid, signal.SIGTERM)
		except (OSError, ProcessLookupError):
			try:
				process.terminate()
			except OSError:
				return
		try:
			process.wait(timeout=3)
		except subprocess.TimeoutExpired:
			try:
				os.killpg(process.pid, signal.SIGKILL)
			except (OSError, ProcessLookupError):
				try:
					process.kill()
				except OSError:
					pass

	watchdog = threading.Timer(timeout_seconds, stop_stuck_process)
	watchdog.daemon = True
	watchdog.start()
	# Read output and error in real-time
	for output_line in process.stdout:
		
		matchFirmWrite = patternFirmWrite.search(output_line)
		matchBootloader = patternBootloader.search(output_line)
		# matchResetStlinkSuc = patternResetStlinkSuc.search(output_line)
		matchResetStlinkFaiPerm = patternResetStlinkFaiPerm.search(output_line)
		matchResetStlinkFaiNoDev = patternResetStlinkFaiNoDev.search(output_line)

		# if output_line != "\n":
		#         print(ascii(output_line))
		# if error_line != "\n" and error_line != "":
		#         print("2>" + ascii(error_line))

		if matchFirmWrite:
			written = int(matchFirmWrite.group(1))
			total = int(matchFirmWrite.group(2))
			if total > 0:
				common.send("U_TFP_PER " + str(min(100, (written * 100) // total)), True)
		elif matchBootloader:
			common.send("U_SFP_PER " + str(int(float(matchBootloader.group(5)))), True)
		# elif matchResetStlinkSuc:
		#         common.send("R_SLR_SUC")
		elif matchResetStlinkFaiPerm:
			special_ret = 2 #R_SLR_FPM
		elif matchResetStlinkFaiNoDev:
			special_ret = 1 #R_SLR_FND
	watchdog.cancel()
	process.wait()
	with _active_process_lock:
		if _active_process is process:
			_active_process = None
	time.sleep(1)
	if timed_out:
		return 124
	return special_ret if special_ret else process.returncode

def abort_active_upload():
	"""Terminate the currently running programmer command, if any."""
	with _active_process_lock:
		process = _active_process
	if process is None or process.poll() is not None:
		return 0
	try:
		os.killpg(process.pid, signal.SIGTERM)
		process.wait(timeout=3)
	except subprocess.TimeoutExpired:
		try:
			os.killpg(process.pid, signal.SIGKILL)
		except (OSError, ProcessLookupError):
			process.kill()
	except (OSError, ProcessLookupError):
		try:
			process.terminate()
		except OSError:
			return 255
	return 0

def firm_write(bin_path, addr=0x08000000):
	# A previously programmed DUT may still contain a valid MCUboot image.
	# Replacing only the bootloader pages would let MCUboot start that old
	# application instead of remaining in serial recovery mode. Erase the DUT
	# first so the freshly written bootloader has no valid image to boot.
	erase_ret = execute_cmd_prog("st-flash erase", timeout_seconds=60)
	if erase_ret != 0:
		return erase_ret
	ret = execute_cmd_prog("st-flash --reset write " + bin_path + " " + str(hex(addr)))
	# print("ret" + str(ret))
	return (ret)

def search_for_device(vid, pid, name):
	port = None
	while (port == None):
		common.device_port = common.find_device(vid, pid, "MCUBOOT")

		if common.device_port is not None:
			print(f"> Device found on {common.device_port}")

		print("> Device disconnected, searching for a new device.")
		
def bootloader_write():
	ret = execute_cmd_prog("./mcumgr-rpi --conntype=serial --connstring=dev=/dev/ttyACM2,baud=115200,mtu=128 image upload -e blank-firmware-bootloader.bin", timeout_seconds=120)
	# print("ret" + str(ret))
	return (ret)

def resetStLink():
	try:
		ret = execute_cmd_prog("usbreset 0483:374f") #usb pid vio for stlink v3
		print(">", ret)
		return (ret)
	except:
		print("> Cannot reset st-link")
		return (255)


def shutdown():
	ret = execute_cmd_prog("sudo shutdown -h now")
	return (ret)

def reset_controller(port):
	"""Ask MCUboot to reset the tester after the application closes its port."""
	cmd = f"./mcumgr-rpi --conntype=serial --connstring=dev={port},baud=115200,mtu=128 reset"
	try:
		result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=8, check=False)
		print("controller reset:", result.returncode, result.stdout.strip(), result.stderr.strip())
		return result.returncode
	except (OSError, subprocess.TimeoutExpired) as error:
		print("controller reset failed:", error)
		return 255
