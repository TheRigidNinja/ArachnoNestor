#!/usr/bin/env python3
import cv2
from flask import Flask, Response

def find_camera(max_idx=5):
    """Scan video0…video{max_idx-1}, return first working VideoCapture."""
    for i in range(max_idx):
        cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            continue
        ret, _ = cap.read()
        if ret:
            print(f"🔍 Found camera at index {i}")
            return cap
        cap.release()
    raise RuntimeError("❌ No working camera found")

app = Flask(__name__)
cap = find_camera()

@app.route('/video_feed')
def video_feed():
    def gen():
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            # you can overlay text here if you like, e.g. roll angle
            # cv2.putText(frame, f"Roll: {latest_roll:.1f}°", (10,30),
            #             cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
            _, jpg = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n'
                   + jpg.tobytes() + b'\r\n')
    return Response(gen(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return '<html><body><h1>Camera Stream</h1><img src="/video_feed"></body></html>'

if __name__ == '__main__':
    # listen on all interfaces (so VS Code remote-SSH port-forward will work)
    app.run(host='0.0.0.0', port=8080, threaded=True)
