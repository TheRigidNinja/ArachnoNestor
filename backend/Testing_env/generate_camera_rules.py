import subprocess
import os

output_rules = []

for i in range(8):  # check /dev/video0 to /dev/video7
    dev = f"/dev/video{i}"
    if not os.path.exists(dev):
        continue

    try:
        # run udevadm info
        result = subprocess.run(
            ["udevadm", "info", "-a", "-n", dev],
            capture_output=True,
            text=True,
            check=True
        ).stdout

        idVendor, idProduct, kernels = None, None, None

        for line in result.splitlines():
            line = line.strip()
            if "ATTRS{idVendor}" in line and not idVendor:
                idVendor = line.split("==")[-1].strip().strip('"')
            if "ATTRS{idProduct}" in line and not idProduct:
                idProduct = line.split("==")[-1].strip().strip('"')
            if "KERNELS==" in line and not kernels:
                kernels = line.split("==")[-1].strip().strip('"')

        if idVendor and idProduct and kernels:
            rule = (
                f'SUBSYSTEM=="video4linux", ATTRS{{idVendor}}=="{idVendor}", '
                f'ATTRS{{idProduct}}=="{idProduct}", KERNELS=="{kernels}", '
                f'SYMLINK+="camera{i}"'
            )
            output_rules.append(rule)

    except subprocess.CalledProcessError:
        print(f"Failed to query {dev}")

# Save to file
if output_rules:
    rules_file = "/tmp/99-cameras.rules"
    with open(rules_file, "w") as f:
        for rule in output_rules:
            f.write(rule + "\n")
    print(f"✅ Rules written to {rules_file}")
    print("Copy to /etc/udev/rules.d/ with:\n")
    print(f"  sudo cp {rules_file} /etc/udev/rules.d/99-cameras.rules")
    print("Then reload rules with:")
    print("  sudo udevadm control --reload-rules && sudo udevadm trigger")
else:
    print("❌ No cameras found.")
