import serial
import time
from crcmod import crcmod  # Install using: pip install crcmod

from config.settings import load_config
from logutil.logger import get_logger

CONFIG = load_config()

log = get_logger("drivers.bld510b")

# RS485 configuration
SERIAL_PORT = CONFIG["motion"]["serial_port"]
BAUD_RATE = CONFIG["motion"]["baud_rate"]
PARITY = serial.PARITY_NONE   # No parity
STOP_BITS = serial.STOPBITS_ONE  # 1 stop bit
BYTE_SIZE = serial.EIGHTBITS  # 8 data bits
TIMEOUT = 1                   # Timeout in seconds

# Modbus device address
DEVICE_ADDRESS = CONFIG["motion"]["device_address"]

# Motor configuration
MOTOR_POLES_PAIRS = 2  # Number of poles in the motor

## ------------------------------------- CRC calculation function
def calculate_crc(data):
    crc16 = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)
    crc_value = crc16(data)
    return [(crc_value & 0xFF), (crc_value >> 8) & 0xFF]  # Return CRC as [LSB, MSB]

## ------------------------------------- Function to send a Modbus RTU command with CRC
def send_modbus_command(ser, function_code, address, value=None, count=None):
    # Build the Modbus RTU frame
    frame = bytearray()
    frame.append(DEVICE_ADDRESS)  # Slave address
    frame.append(function_code)  # Function code
    frame.extend(address.to_bytes(2, byteorder='big'))  # Register address

    if function_code == 0x06:  # Write single register
        frame.extend(value.to_bytes(2, byteorder='big'))  # Value to write (big-endian)
    elif function_code == 0x03:  # Read holding registers
        frame.extend(count.to_bytes(2, byteorder='big'))  # Number of registers to read

    # Calculate CRC and append to the frame
    crc_values = calculate_crc(frame)
    frame.extend(crc_values)

    spaced_string = space_hex_string(frame.hex())
    log.debug(f"Command sent: {spaced_string}")

    # Send the command
    ser.write(frame)

    # Wait for a response (adjust the sleep time if necessary)
    time.sleep(0.15)
    response = ser.read_all()
    if response:
        log.debug(f"Response received: {response.hex()}")
        return response
    else:
        log.warning("No response received")
        return None

## ------------------------------------- Function to format a hex string with spaces
def space_hex_string(hex_string):
    spaced_hex = ' '.join(hex_string[i:i+2] for i in range(0, len(hex_string), 2))
    return spaced_hex

## ------------------------------------- Function to start the motor in forward or reverse direction
def start_motorFR(ser, FR):
    if FR == "F":
        send_modbus_command(ser, 0x06, 0x8000, 0x0902)  # Forward
    else:
        send_modbus_command(ser, 0x06, 0x8000, 0x0B02)  # Reverse

## ------------------------------------- Function to stop the motor (natural stop)
def stop_motor_natural(ser):
    send_modbus_command(ser, 0x06, 0x8000, 0x0802)

## -------------------------------------  Function to stop the motor (braking stop)
def stop_motor_braking(ser):
    send_modbus_command(ser, 0x06, 0x8000, 0x0D02)

## ------------------------------------- Function to write speed (RPM)
def write_rpm(ser, speed):
    # Ensure speed is within valid range (0–4000 RPM)
    if speed < 0 or speed > 4000:
        log.error("Speed must be between 0 and 4000 RPM")
        return
    
    # Convert speed to the appropriate value for the motor controller
    speed_value = int(speed)  # Convert to integer
    
    # Format the speed value as little-endian (low byte first, high byte second)
    speed_low_byte = speed_value & 0xFF          # Low byte
    speed_high_byte = (speed_value >> 8) & 0xFF  # High byte
    
    # Combine the low and high bytes into a single 16-bit value (little-endian)
    speed_combined = (speed_low_byte << 8) | speed_high_byte

    log.info(f"Setting speed to {speed} RPM (0x{speed_combined:04X})")

    # Send the command
    response = send_modbus_command(ser, 0x06, 0x8005, speed_combined)
    
    if response:
        log.info(f"Speed set to {speed} RPM successfully.")
    else:
        log.error("Failed to set speed. Check wiring and Modbus address.")


## ------------------------------------- Function to read real speed (RPM)
# Function to read actual RPM
def read_rpm(ser):
    """
    Reads the RPM value that has been set (from register 0x8005).
    """
    response = send_modbus_command(ser, 0x03, 0x8005, count=1)
    if response and len(response) >= 7:
        # Extract set RPM **in little-endian**
        set_rpm = int.from_bytes(response[3:5], byteorder="little")  

        log.info(f"Stored Set RPM: {set_rpm} RPM")
        return set_rpm
    else:
        log.error("Failed to read set RPM.")
        return None


## ------------------------------------- Function to read real speed (RPM)
def read_actual_rpm(ser):
    """
    Reads the actual RPM from register 0x8018.
    Formula: Actual RPM = (Raw Speed Value * 20) / (Number of Pole Pairs * Scaling Factor)
    """
    response = send_modbus_command(ser, 0x03, 0x8018, count=1)
    if response and len(response) >= 7:
        # Extract the raw RPM value (bytes 3 and 4) in little-endian format
        raw_speed = int.from_bytes(response[3:5], byteorder="little")  # little-endian


        # Convert raw speed to actual RPM using the formula
        actual_rpm = (raw_speed * 20) / (MOTOR_POLES_PAIRS*2)

        log.info(f"Actual RPM: {actual_rpm:.2f} RPM")
        return actual_rpm
    else:
        log.error("No valid RPM response received.")
        return None
    

# Function to control revolutions
def run_for_revolutions(ser, target_rpm, num_revolutions, direction, ramp_step=50, ramp_delay=0.2):
    """
    Runs the motor for a specific number of revolutions with a smooth start and stop.

    The function will **block execution** until all revolutions are completed.

    :param ser: Serial connection
    :param target_rpm: Target speed in RPM
    :param num_revolutions: Number of revolutions to complete
    :param direction: "F" for forward, "R" for reverse
    :param ramp_step: RPM increment per step (default 50 RPM)
    :param ramp_delay: Time delay between steps (default 0.2s)
    """

    # 🔼 **Ramp Up to Target RPM**
    current_rpm = 0
    while current_rpm < target_rpm:
        current_rpm = min(current_rpm + ramp_step, target_rpm)
        write_rpm(ser, current_rpm)
        time.sleep(ramp_delay)  # Small delay for smooth ramp-up

    start_motorFR(ser, direction)

    # 🟢 **Ensure Motor Starts Moving**
    actual_rpm = 0
    attempts = 0
    while actual_rpm == 0 and attempts < 5:
        actual_rpm = read_actual_rpm(ser)
        if actual_rpm is None:
            log.warning("Couldn't read actual RPM. Retrying...")
        time.sleep(0.2)
        attempts += 1

    if actual_rpm == 0:
        log.error("Motor did not start. Stopping function.")
        stop_motor_natural(ser)
        return  

    # 🔹 **Calculate estimated time per revolution**
    time_per_rev = 60 / actual_rpm  
    total_time = num_revolutions * time_per_rev

    log.info(f"Running for {num_revolutions} revolutions (~{total_time:.2f} seconds)...")

    # ⏳ **Wait while monitoring RPM**
    elapsed_time = 0
    while elapsed_time < total_time:
        time.sleep(0.1)
        actual_rpm = read_actual_rpm(ser)  # Keep reading real-time RPM
        if actual_rpm is None:
            log.warning("Lost RPM reading. Stopping for safety.")
            break
        elapsed_time += 0.1  # Increment elapsed time

    log.info(f"Target reached: {num_revolutions} revolutions completed!")
    stop_motor_natural(ser)
    return 0
    # 🔽 **Ramp Down to Stop Smoothly**
    while current_rpm > 0:
        current_rpm = max(current_rpm - ramp_step, 0)
        write_rpm(ser, current_rpm)
        time.sleep(ramp_delay)

    stop_motor_natural(ser)
    log.info("Motor stopped.")

## ------------------------------------- Function to read start torque and sensorless start speed
def read_start_torque_sensorless_speed(ser):
    response = send_modbus_command(ser, 0x03, 0x8002, count=1)
    if response:
        # Decode the response
        start_torque = response[3]  # First byte: start torque
        sensorless_speed = response[4]  # Second byte: sensorless start speed
        log.info(f"Start Torque: {start_torque}, Sensorless Start Speed: {sensorless_speed}")
        return start_torque, sensorless_speed
    return None, None

## ------------------------------------- Function to read acceleration and deceleration time
def read_accel_decel_time(ser):
    response = send_modbus_command(ser, 0x03, 0x8004, count=1)
    if response:
        # Decode the response
        accel_time = response[3]  # First byte: acceleration time
        decel_time = response[4]  # Second byte: deceleration time
        log.info(f"Acceleration Time: {accel_time}, Deceleration Time: {decel_time}")
        return accel_time, decel_time
    return None, None

## ------------------------------------- Function to write acceleration and deceleration time
def write_accel_decel_time(ser, accel_time, decel_time):
    # Combine acceleration and deceleration time into a single value
    value = (accel_time << 8) | decel_time
    send_modbus_command(ser, 0x06, 0x8003, value)

## -------------------------------------  Function to read alarms
def read_alarms(ser):
    response = send_modbus_command(ser, 0x03, 0x801B, count=1)
    if response:
        # Decode the response
        alarms = response[3]  # First byte: fault state
        log.info(f"Alarms: {alarms}")
        return alarms
    return None


def adjust_rpm(ser, target_rpm, max_attempts=5):
    """
    Adjusts the motor speed dynamically to get as close as possible to the target RPM.
    """
    for _ in range(max_attempts):
        actual_rpm = read_actual_rpm(ser)
        if actual_rpm is None:
            log.error("Couldn't read actual RPM.")
            return
        
        error = target_rpm - actual_rpm
        if abs(error) < 20:  # If within 10 RPM, stop adjusting
            log.info(f"RPM stabilized at {actual_rpm:.2f} RPM.")
            return

        new_rpm = int(target_rpm + (error * 0.1))  # Apply correction (small step)
        write_rpm(ser, new_rpm)
        time.sleep(0.5)  # Wait and read again

    log.warning(f"Max adjustment attempts reached. Final RPM: {read_actual_rpm(ser)}")


class MotorBus:
    """Simple driver wrapper that owns the serial connection."""

    def __init__(self, port: str | None = None, baudrate: int | None = None):
        port = port or SERIAL_PORT
        baudrate = baudrate or BAUD_RATE
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            parity=PARITY,
            stopbits=STOP_BITS,
            bytesize=BYTE_SIZE,
            timeout=TIMEOUT,
        )

    def write_rpm(self, rpm: int):
        return write_rpm(self.ser, rpm)

    def start(self, direction: str):
        return start_motorFR(self.ser, direction)

    def stop(self):
        return stop_motor_natural(self.ser)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass




## -------------------------------------  Main function
def main():
    # Initialize serial port
    ser = serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        parity=PARITY,
        stopbits=STOP_BITS,
        bytesize=BYTE_SIZE,
        timeout=TIMEOUT
    )

    try:
        # write_rpm(ser, 100)
        # time.sleep(1)
        # run_for_revolutions(ser, target_rpm, num_revolutions, direction):
        # run_for_revolutions(ser, 500, 2, "F")
        # start_motorFR(ser, "F")
        write_rpm(ser, 400)
        # time.sleep(4)

        start_motorFR(ser, "F")
        time.sleep(3)

        # start_motorFR(ser, "R")
        # write_rpm(ser, 300)
        # time.sleep(5)
        # write_rpm(ser,2000)
        # time.sleep(60)

        # write_rpm(ser, 600)
        # time.sleep(5)

        # run_for_revolutions(ser, 1000, 20,"F")
        # Usage inside main():
        # write_rpm(ser, 2000)  # Initial setpoint
        # start_motorFR(ser, "F")
        # adjust_rpm(ser, 2000)  # Fine-tune it
    #    read_rpm(ser)
        # read_actual_rpm(ser)  
        # start_motorFR(ser, "F")
        # # write_accel_decel_time(ser,0,0)
        # write_rpm(ser, 4000)

        # # print(20%10)
        # for g in range(1000):
        #     if((g%20) == 0 and g >= 150):
        #         write_rpm(ser, g)
        #         time.sleep(.5)
        
        # # start_motorFR(ser, "R")
        # time.sleep(5)/
        # write_rpm(ser, 1000)
        # start_motorFR(ser, "F")
        # time.sleep(20)

        # read_actual_rpm(ser)
        stop_motor_natural(ser)

        # Set speed to 600 RPM
        # write_speed(ser, 600)

        # # Start the motor in forward direction
        # start_motorFR(ser, "F")
        # time.sleep(5)

        # target_rpm = 100  
        # num_revolutions = 50  
        # run_for_revolutions(ser, target_rpm, num_revolutions)

    except KeyboardInterrupt:
        # Stop the motor on Ctrl+C
        log.info("Stopping motor...")
        stop_motor_natural(ser)

    finally:
        # Close the serial port
        ser.close()

if __name__ == "__main__":
    main()
