# testing_morphological_segmentation.py

import srt
import chardet
from fugashi import Tagger

SRT_PATH = r"C:/Tobi/JapaneseSubtitles/subs/onepiece/ワンピース.S22E611.WEBRip.Netflix.ja[cc].srt"
AN_PATTERN = r'\{\\an[58]\}'  # strip {\\an5} and {\\an8}

def load_subtitles(path):
    raw = open(path, "rb").read()
    enc = chardet.detect(raw)["encoding"] or "utf-8"
    text = raw.decode(enc, errors="replace")
    return list(srt.parse(text))

def clean_text(txt):
    # remove alignment tags and trim
    return srt.re.sub(AN_PATTERN, "", txt).strip()

def main():
    subs = load_subtitles(SRT_PATH)
    tagger = Tagger()  # uses UniDic by default if installed

    for idx, sub in enumerate(subs[:5], 1):
        print(f"[{idx:2}] {sub.start} --> {sub.end}")
        cleaned = clean_text(sub.content)
        for line in cleaned.splitlines():
            tokens = tagger(line)
            for tok in tokens:
                # UniDic exposes the kana reading as `.feature.reading`
                reading = getattr(tok.feature, "reading", "")
                print(f"{tok.surface}({reading})", end=" ")
            print()
        print()

if __name__ == "__main__":
    main()
