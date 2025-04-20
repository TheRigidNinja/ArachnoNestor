import serial
import time

def scan_rs485_addresses(port='/dev/ttyUSB0', baudrate=9600, timeout=0.1):
    try:
        ser = serial.Serial(port, baudrate, timeout=timeout, parity=serial.PARITY_NONE, stopbits=1, bytesize=8)
    except serial.SerialException as e:
        print(f"Error opening serial port: {e}")
        return

    print("Scanning for active Modbus RTU addresses...")

    for address in range(1, 250):  # Modbus addresses range from 1 to 247
        request = bytes([address, 0x03, 0x00, 0x00, 0x00, 0x01])  # Function 0x03 (Read Holding Registers)
        crc = calculate_crc(request)
        request += crc
        
        ser.flushInput()  # Clear input buffer
        ser.write(request)
        time.sleep(0.1)  # Allow time for response
        
        response = ser.read(7)  # Expecting a 7-byte response
        if response and len(response) >= 5:  # Valid responses should be at least 5 bytes
            print(f"Device found at address: {address} -> Response: {response.hex()}")

    ser.close()
    print("Scan complete.")

def calculate_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

if __name__ == "__main__":
    scan_rs485_addresses()
