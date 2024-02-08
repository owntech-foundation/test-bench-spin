import serial
import sys
import select
from serial.tools import list_ports
import time

# # Configure the serial port settings
# ser = serial.Serial(
#     port='/dev/ttyACM1',  # Replace with the actual serial port name (e.g., 'COM1' on Windows)
#     baudrate=115200,        # Set the baud rate to match your device
#     timeout=1             # Set a timeout value for reading
# )

device_port = None
ser = None
last_send = ""

def send(response, noRepeat=False):
    global last_send

    if (noRepeat==True and response == last_send):
        return
    last_send = response
    print("sending: " + response)
    response = response + "\r\n"
    ser.write(response.encode('utf-8'))

def find_device(target_vid, target_pid, name=None):
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if port.vid == target_vid and port.pid == target_pid:
            if (name != None):
                if (port.description == name):
                    return port.device
                return None
            return port.device
    return None

