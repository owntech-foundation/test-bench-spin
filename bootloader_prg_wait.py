import serial.tools.list_ports

def get_product_name(port):
    try:
        port_info = serial.tools.list_ports.comports()[port - 1]
        return port_info.description
    except IndexError:
        return None

# Example: Get the product name of a specific serial port
serial_port_to_check = 1  # Change this to the port number you want to check

product_name = get_product_name(serial_port_to_check)

if product_name is not None:
    print(f"Product name of port {serial_port_to_check}: {product_name}")
else:
    print(f"No product name found for port {serial_port_to_check}")
