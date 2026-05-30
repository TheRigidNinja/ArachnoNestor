import pygame
import sys

# Initialize pygame and the joystick module
pygame.init()
pygame.joystick.init()

# Check for connected controllers
joystick_count = pygame.joystick.get_count()
if joystick_count == 0:
    print("❌ No controller found. Please connect your 8BitDo Ultimate 2C.")
    sys.exit()

# Connect to the first available controller
controller = pygame.joystick.Joystick(0)
controller.init()
print(f"🎮 Connected to: {controller.get_name()}")

# Main control loop
running = True
try:
    while running:
        # Pump events to refresh controller state
        pygame.event.pump()
        
        # 1. Read Analog Joysticks and Triggers (Axes)
        # Axis 0: Left Stick X | Axis 1: Left Stick Y
        # Axis 2: Left Trigger | Axis 3: Right Stick X | Axis 4: Right Stick Y | Axis 5: Right Trigger
        num_axes = controller.get_numaxes()
        axes_values = [round(controller.get_axis(i), 2) for i in range(num_axes)]
        
        # 2. Read Face Buttons & Bumpers
        num_buttons = controller.get_numbuttons()
        buttons_state = [controller.get_button(i) for i in range(num_buttons)]
        
        # 3. Read D-Pad (Hats)
        num_hats = controller.get_numhats()
        hats_state = [controller.get_hat(i) for i in range(num_hats)]
        
        # Print status updates seamlessly on a single line
        print(f"Axes: {axes_values} | Buttons: {buttons_state} | D-Pad: {hats_state}", end="\r")
        
        # Frame rate control (~60Hz) to prevent CPU flooding
        pygame.time.Clock().tick(60)

except KeyboardInterrupt:
    print("\nStopping controller reader...")
finally:
    pygame.quit()