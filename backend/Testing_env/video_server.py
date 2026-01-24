import cv2
import time
from flask import Flask, Response, render_template_string

app = Flask(__name__)

# 8 cameras total, split by fps
CAMERA_CONFIGS = [
    {"index": 0, "name": "Cam 1", "width": 640, "height": 480, "fps": 25},
    # {"index": 2, "name": "Cam 2", "width": 640, "height": 480, "fps": 25},
    # {"index": 4, "name": "Cam 3", "width": 640, "height": 480, "fps": 25},
    # {"index": 6, "name": "Cam 4", "width": 640, "height": 480, "fps": 25},

    
    # {"index": 12,  "name": "Cam 5", "width": 160, "height": 120, "fps": 30},
    # {"index": 10, "name": "Cam 6", "width": 160, "height": 120, "fps": 30},
    # {"index": 14, "name": "Cam 7", "width": 160, "height": 120, "fps": 30},
    # {"index": 8, "name": "Cam 8", "width": 160, "height": 120, "fps": 30},
]


camera_caps = [None] * len(CAMERA_CONFIGS)

def init_camera(idx, config):
    cap = cv2.VideoCapture(config["index"], cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config["height"])
    cap.set(cv2.CAP_PROP_FPS, config["fps"])
    camera_caps[idx] = cap

    # Check negotiated values
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[INFO] {config['name']} (index {config['index']}) started "
          f"=> {int(actual_w)}x{int(actual_h)} @ {int(actual_fps)}fps")

@app.route("/video/<int:cam_id>")
def video_feed(cam_id):
    def generate():
        cap = camera_caps[cam_id]
        frame_count, start_time, fps_display = 0, time.time(), 0

        while True:
            ret, frame = cap.read()
            if not ret:
                print(f"[WARN] Camera {cam_id} failed to read.")
                time.sleep(0.1)
                continue

            # FPS counter
            frame_count += 1
            if (time.time() - start_time) >= 1.0:
                fps_display = frame_count
                frame_count, start_time = 0, time.time()

            cv2.putText(frame, f"FPS: {fps_display}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

            ret, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ret:
                continue

            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + jpeg.tobytes() + b"\r\n")

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/")
def index():
    template = """
    <html>
    <head><title>🕷️ 8-Camera Stream with FPS</title></head>
    <body style="background:black; color:white; text-align:center;">
        <h1>8-Camera Real-Time Stream</h1>
        <table style="width:100%">
            <tr>
                {% for cam_id, cam in cams %}
                <td>
                    <h3>{{ cam.name }}</h3>
                    <img src="/video/{{ cam_id }}" width="320" height="240"><br>
                    Index {{ cam.index }}
                </td>
                {% if loop.index % 4 == 0 %}
            </tr><tr>
                {% endif %}
                {% endfor %}
            </tr>
        </table>
    </body>
    </html>
    """
    return render_template_string(template, cams=list(enumerate(CAMERA_CONFIGS)))

if __name__ == "__main__":
    for i, config in enumerate(CAMERA_CONFIGS):
        init_camera(i, config)
        time.sleep(0.3)  # stagger to avoid USB collision
    app.run(host="0.0.0.0", port=8080, threaded=True)
