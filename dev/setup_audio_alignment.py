"""One-time setup. Recognition and captured audio stay local after model setup."""
from pathlib import Path
from faster_whisper.utils import download_model

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "voice_models" / "whisper-base"
    download_model("base", output_dir=str(target))
    print(f"Local CPU model ready: {target}")
