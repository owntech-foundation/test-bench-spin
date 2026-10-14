import serial
import threading
from serial.tools import list_ports
from status_store import status

device_port = None
ser = None
last_send = ""
_reset_requested = False
_reset_lock = threading.Lock()
_command_queue = []
_command_lock = threading.Lock()

def request_controller_command(command):
    with _command_lock:
        if len(_command_queue) >= 4:
            return False
        _command_queue.append(command)
        return True

def consume_controller_command():
    with _command_lock:
        if not _command_queue:
            return None
        return _command_queue.pop(0)

def request_controller_reset():
    global _reset_requested
    with _reset_lock:
        if _reset_requested:
            return False
        _reset_requested = True
        return True

def consume_controller_reset():
    global _reset_requested
    with _reset_lock:
        requested = _reset_requested
        _reset_requested = False
        return requested

def send(response, noRepeat=False):
    global last_send

    if (noRepeat==True and response == last_send):
        return
    last_send = response
    print("sending: " + response)
    status.record(response, "sent")
    response = response + "\r\n"
    ser.write(response.encode('utf-8'))

def find_device(target_vid, target_pid, name=None):
    for port in list_ports.comports():
        if port.vid == target_vid and port.pid == target_pid:
            if name is None or port.description == name:
                return port.device
    return None
