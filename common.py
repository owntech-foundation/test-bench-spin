import serial
from serial.tools import list_ports

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
    for port in list_ports.comports():
        if port.vid == target_vid and port.pid == target_pid:
            if name is None or port.description == name:
                return port.device
    return None
