import os
import subprocess

def set_camera_low_bandwidth(dev):
    try:
        # Force MJPEG @ 320x240, 15fps
        cmd = [
            "v4l2-ctl",
            "-d", dev,
            "--set-fmt-video=width=320,height=240,pixelformat=H264",
            "--set-parm=15"
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[OK] {dev} set to MJPEG 320x240 @15fps")
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] {dev} -> {e.stderr.decode().strip()}")

def main():
    # Find all /dev/video* devices
    video_devices = [f"/dev/{d}" for d in os.listdir("/dev") if d.startswith("video")]
    video_devices.sort()

    if not video_devices:
        print("No video devices found")
        return

    for dev in video_devices:
        set_camera_low_bandwidth(dev)

if __name__ == "__main__":
    main()
