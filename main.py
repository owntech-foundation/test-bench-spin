import common
import serial_com
import time
import serial
from status_store import status
from web_server import start_server

def connect_controller(device_port):
    common.ser = serial.Serial(port=device_port, baudrate=115200, timeout=1)
    common.send("R_RDY")
    serial_com.serialCom()

def main():
    while True:
        common.device_port = common.find_device(0x2fe3, 0x0101)

        if common.device_port is not None:
            print(f"Device found on {common.device_port}")
            status.set_controller(common.device_port)
            # Do stuff with the connected device
            # Add your custom logic here

            # Wait for the device to be disconnected
            # while find_device() is not None:
            #     print ("stuff")
            #     time.sleep(1)
            try:
                connect_controller(common.device_port)
            except serial.SerialException as error:
                print(f"Unable to open {common.device_port}: {error}")
            status.set_controller(None)
            print("Device disconnected, searching for a new device.")
        
        time.sleep(1)

if __name__ == "__main__":
    start_server()
    main()
