import base64
import time
import tempfile
import os
import subprocess

from selenium import webdriver
from selenium.webdriver.firefox.options import Options


class BrowserVideoController:
    def __init__(self, start_url: str):
        self.start_url = start_url
        self.driver = None

    # ------------------------------------------------------------
    # Launch Firefox controlled by Selenium
    # ------------------------------------------------------------
    def start(self):
        options = Options()
        self.driver = webdriver.Firefox(options=options)
        self.driver.get(self.start_url)

    # ------------------------------------------------------------
    # Basic playback control
    # ------------------------------------------------------------
    def pause(self):
        self.driver.execute_script("""
            const v = document.querySelector('video');
            if (v) v.pause();
        """)

    def play(self):
        self.driver.execute_script("""
            const v = document.querySelector('video');
            if (v) v.play();
        """)

    def get_current_time(self) -> float:
        return self.driver.execute_script("""
            const v = document.querySelector('video');
            return v ? v.currentTime : 0;
        """)

    def seek(self, seconds: float):
        self.driver.execute_script("""
            const v = document.querySelector('video');
            if (v) v.currentTime = arguments[0];
        """, seconds)

    # ------------------------------------------------------------
    # Capture frame at exact timestamp
    # ------------------------------------------------------------
    def capture_frame(self, timestamp: float, output_path: str):
        self.seek(timestamp)
        time.sleep(0.15)  # allow frame to update

        data_url = self.driver.execute_script("""
            const v = document.querySelector('video');
            if (!v) return null;

            const canvas = document.createElement('canvas');
            canvas.width = v.videoWidth;
            canvas.height = v.videoHeight;
            canvas.getContext('2d').drawImage(v, 0, 0);

            return canvas.toDataURL('image/png');
        """)

        if not data_url:
            return None

        header, encoded = data_url.split(",", 1)
        image_bytes = base64.b64decode(encoded)

        with open(output_path, "wb") as f:
            f.write(image_bytes)

        return output_path

    # ------------------------------------------------------------
    # Record exact audio window between start and end
    # ------------------------------------------------------------
    def record_audio_segment(self, start: float, end: float, output_path: str):
        duration = end - start
        if duration <= 0:
            return None

        # Seek and pause before recording
        self.seek(start)
        time.sleep(0.1)

        data_url = self.driver.execute_async_script("""
            const start = arguments[0];
            const duration = arguments[1];
            const callback = arguments[arguments.length - 1];

            const v = document.querySelector('video');
            if (!v) { callback(null); return; }

            const stream = v.captureStream();
            const recorder = new MediaRecorder(stream);
            let chunks = [];

            recorder.ondataavailable = e => chunks.push(e.data);
            recorder.onstop = () => {
                const blob = new Blob(chunks, { type: 'audio/webm' });
                const reader = new FileReader();
                reader.onloadend = () => callback(reader.result);
                reader.readAsDataURL(blob);
            };

            v.currentTime = start;
            v.play();

            recorder.start();

            setTimeout(() => {
                recorder.stop();
                v.pause();
            }, duration * 1000);
        """, start, duration)

        if not data_url:
            return None

        header, encoded = data_url.split(",", 1)
        audio_bytes = base64.b64decode(encoded)

        temp_webm = tempfile.mktemp(suffix=".webm")
        with open(temp_webm, "wb") as f:
            f.write(audio_bytes)

        # Convert to mp3 (optional but recommended for Anki)
        subprocess.run([
            "ffmpeg",
            "-y",
            "-i", temp_webm,
            output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        os.remove(temp_webm)

        return output_path

    # ------------------------------------------------------------
    # Capture both screenshot + audio using subtitle timestamps
    # ------------------------------------------------------------
    def capture_subtitle_segment(self, start: float, end: float,
                                 image_path: str,
                                 audio_path: str):

        midpoint = start + (end - start) / 2

        img = self.capture_frame(midpoint, image_path)
        audio = self.record_audio_segment(start, end, audio_path)

        return img, audio
    

    def mine_current_subtitle(self, subtitle):
        start = subtitle["start"]
        end = subtitle["end"]

        image_path = "capture.png"
        audio_path = "audio.mp3"

        img, audio = self.browser.capture_subtitle_segment(
            start,
            end,
            image_path,
            audio_path
        )

        if img and audio:
            self.anki_client.add_from_selection(
                word=subtitle["text"],
                sentence=subtitle["text"],
                audio_path=audio,
                image_path=img
            )