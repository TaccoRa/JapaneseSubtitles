from flask import Flask, request
import logging
import threading

app = Flask(__name__)
logger = logging.getLogger(__name__)

latest_time = 0.0
latest_duration = 0.0
lock = threading.Lock()


@app.route("/time", methods=["POST"])
def receive_time():
    global latest_time, latest_duration

    data = request.json
    if not data:
        return {"status": "no data"}

    with lock:
        latest_time = float(data.get("currentTime", 0.0))
        latest_duration = float(data.get("duration", 0.0))

    logger.debug("[VIDEO] %.2f / %.2f", latest_time, latest_duration)

    return {"status": "ok"}


def get_video_time():
    with lock:
        return latest_time, latest_duration


def start_server():
    logger.info("Video sync server started on 127.0.0.1:5001")
    app.run(port=5001, threaded=True)
