import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

try:
    from SubtitlePlayer.furigana_splitter import split_furigana
except ImportError:
    try:
        from furigana_splitter import split_furigana
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import split_furigana

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

KAKASI_ARGS = ["-isjis", "-osjis", "-u", "-JH", "-KH"]
MECAB_ARGS = ["--node-format=%m[%f[7]] ", "--eos-format=\n", "--unk-format=%m[] "]

DEFAULT_SUPPORT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "anki_reading_support")
)

BRACKET_RUBY_RE = re.compile(r"([^\[\]]+)\[([^\[\]]+)\]")


def _is_kanji_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        (code == 0x3005)  # 々 (ideographic iteration mark) should follow kanji ruby too
        or (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
        or (0x20000 <= code <= 0x2A6DF)
        or (0x2A700 <= code <= 0x2B73F)
        or (0x2B740 <= code <= 0x2B81F)
        or (0x2B820 <= code <= 0x2CEAF)
    )


def _is_kana_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


def _kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _split_kanji_kana_core(base: str, ruby: str) -> Optional[List[Tuple[str, Optional[str]]]]:
    if not base or not ruby:
        return None
    has_kanji = any(_is_kanji_char(ch) for ch in base)
    has_kana = any(_is_kana_char(ch) for ch in base)
    if not (has_kanji and has_kana):
        return None

    reading = _kata_to_hira(ruby)
    r_pos = 0
    out: List[Tuple[str, Optional[str]]] = []
    i = 0
    n = len(base)
    while i < n:
        ch = base[i]
        if _is_kana_char(ch):
            hira = _kata_to_hira(ch)
            if reading.startswith(hira, r_pos):
                r_pos += len(hira)
            out.append((ch, None))
            i += 1
            continue

        if _is_kanji_char(ch):
            j = i
            while j < n and _is_kanji_char(base[j]):
                j += 1
            kanji_chunk = base[i:j]

            next_kana = None
            k = j
            while k < n:
                if _is_kana_char(base[k]):
                    next_kana = _kata_to_hira(base[k])
                    break
                if _is_kanji_char(base[k]):
                    break
                k += 1

            if next_kana:
                idx = reading.find(next_kana, r_pos)
                if idx <= r_pos:
                    ruby_chunk = reading[r_pos:]
                    r_pos = len(reading)
                else:
                    ruby_chunk = reading[r_pos:idx]
                    r_pos = idx
            else:
                ruby_chunk = reading[r_pos:]
                r_pos = len(reading)

            if ruby_chunk:
                out.append((kanji_chunk, ruby_chunk))
            else:
                out.append((kanji_chunk, None))
            i = j
            continue

        out.append((ch, None))
        i += 1

    if r_pos < len(reading):
        for idx in range(len(out) - 1, -1, -1):
            base_chunk, ruby_chunk = out[idx]
            if ruby_chunk:
                out[idx] = (base_chunk, ruby_chunk + reading[r_pos:])
                break
        else:
            return None
    return out or None


def _split_ruby_base(base: str, ruby: str, single_kanji_reader=None) -> List[Tuple[str, Optional[str]]]:
    if not base:
        return []
    if not ruby:
        return [(base, None)]
    return split_furigana(base, ruby, single_kanji_reader)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def escape_text(text: str) -> str:
    text = (text or "").replace("\n", " ")
    text = text.replace("\uff5e", "~")
    text = re.sub(r"<br( /)?>", "---newline---", text)
    text = strip_html(text)
    text = text.replace("---newline---", "<br>")
    return text


def munge_for_platform(popen: List[str]) -> List[str]:
    if IS_WIN:
        popen = [os.path.normpath(x) for x in popen]
        popen[0] += ".exe"
    elif not IS_MAC:
        popen[0] += ".lin"
    return popen


def _startupinfo():
    if not IS_WIN:
        return None
    si = subprocess.STARTUPINFO()
    try:
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except Exception:
        si.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW
    return si


class KakasiController:
    def __init__(self, support_dir: str) -> None:
        self.support_dir = support_dir
        self.kakasi: subprocess.Popen | None = None
        self.kakasi_cmd: List[str] | None = None

    def setup(self) -> None:
        self.kakasi_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "kakasi")] + KAKASI_ARGS
        )
        os.environ["ITAIJIDICT"] = os.path.join(self.support_dir, "itaijidict")
        os.environ["KANWADICT"] = os.path.join(self.support_dir, "kanwadict")
        if not IS_WIN:
            os.chmod(self.kakasi_cmd[0], 0o755)

    def ensure_open(self) -> None:
        if not self.kakasi:
            self.setup()
            try:
                self.kakasi = subprocess.Popen(
                    self.kakasi_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                )
            except OSError as exc:
                raise Exception("Please install kakasi") from exc

    def reading(self, expr: str) -> str:
        self.ensure_open()
        expr = escape_text(expr)
        assert self.kakasi
        self.kakasi.stdin.write(expr.encode("sjis", "ignore") + b"\n")
        self.kakasi.stdin.flush()
        res = self.kakasi.stdout.readline().rstrip(b"\r\n").decode("sjis", "replace")
        return res


class MecabController:
    def __init__(self, support_dir: str, kakasi: KakasiController) -> None:
        self.support_dir = support_dir
        self.kakasi = kakasi
        self.mecab: subprocess.Popen | None = None
        self.mecab_cmd: List[str] | None = None

    def setup(self) -> None:
        self.mecab_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "mecab")]
            + MECAB_ARGS
            + [
                "-d",
                self.support_dir,
                "-r",
                os.path.join(self.support_dir, "mecabrc"),
                "-u",
                os.path.join(self.support_dir, "user_dic.dic"),
            ]
        )
        os.environ["DYLD_LIBRARY_PATH"] = self.support_dir
        os.environ["LD_LIBRARY_PATH"] = self.support_dir
        if not IS_WIN:
            os.chmod(self.mecab_cmd[0], 0o755)

    def ensure_open(self) -> None:
        if not self.mecab:
            self.setup()
            try:
                self.mecab = subprocess.Popen(
                    self.mecab_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                )
            except OSError as exc:
                raise Exception(
                    "Please ensure your system has 64 bit binary support."
                ) from exc

    def reading(self, expr: str) -> str:
        self.ensure_open()
        expr = escape_text(expr)
        assert self.mecab
        self.mecab.stdin.write(expr.encode("utf-8", "ignore") + b"\n")
        self.mecab.stdin.flush()
        expr = self.mecab.stdout.readline().rstrip(b"\r\n").decode("utf-8", "replace")
        out = []
        for node in expr.split(" "):
            if not node:
                break
            m = re.match(r"(.+)\[(.*)\]", node)
            if not m:
                sys.stderr.write(
                    "Unexpected output from mecab: {}\n".format(repr(expr))
                )
                return ""

            (kanji, reading) = m.groups()
            if kanji == reading or not reading:
                out.append(kanji)
                continue
            if kanji == self.kakasi.reading(reading):
                out.append(kanji)
                continue
            reading = self.kakasi.reading(reading)
            if reading == kanji:
                out.append(kanji)
                continue
            if kanji in "一二三四五六七八九十０１２３４５６７８９":
                out.append(kanji)
                continue

            place_l = 0
            place_r = 0
            for i in range(1, len(kanji)):
                if kanji[-i] != reading[-i]:
                    break
                place_r = i
            for i in range(0, len(kanji) - 1):
                if kanji[i] != reading[i]:
                    break
                place_l = i + 1

            if place_l == 0:
                if place_r == 0:
                    out.append(" %s[%s]" % (kanji, reading))
                else:
                    out.append(
                        " %s[%s]%s"
                        % (kanji[:-place_r], reading[:-place_r], reading[-place_r:])
                    )
            else:
                if place_r == 0:
                    out.append(
                        "%s %s[%s]"
                        % (reading[:place_l], kanji[place_l:], reading[place_l:])
                    )
                else:
                    out.append(
                        "%s %s[%s]%s"
                        % (
                            reading[:place_l],
                            kanji[place_l:-place_r],
                            reading[place_l:-place_r],
                            reading[-place_r:],
                        )
                    )

        fin = ""
        for c, s in enumerate(out):
            if c < len(out) - 1 and re.match(r"^[A-Za-z0-9]+$", out[c + 1]):
                s += " "
            fin += s
        return fin.strip().replace("< br>", "<br>")


class AddonRubyGenerator:
    def __init__(self, support_dir: Optional[str] = None) -> None:
        self.support_dir = support_dir or DEFAULT_SUPPORT_DIR
        self.kakasi = KakasiController(self.support_dir)
        self.mecab = MecabController(self.support_dir, self.kakasi)
        self._single_kanji_reading_cache: dict[str, str] = {}

    def reading(self, text: str) -> str:
        return self.mecab.reading(text)

    def single_kanji_reading(self, ch: str) -> str:
        if ch in self._single_kanji_reading_cache:
            return self._single_kanji_reading_cache[ch]
        if not _is_kanji_char(ch) or ch == "々":
            return ""
        raw = self.reading(ch).strip()
        match = BRACKET_RUBY_RE.fullmatch(raw)
        if match and match.group(1) == ch:
            value = _kata_to_hira(match.group(2))
        elif raw and raw != ch and "[" not in raw and "]" not in raw:
            value = _kata_to_hira(raw)
        else:
            value = ""
        self._single_kanji_reading_cache[ch] = value
        return value

    def segments(self, text: str) -> List[Tuple[str, Optional[str]]]:
        ruby_text = self.reading(text)
        return bracket_text_to_segments(ruby_text, self.single_kanji_reading)


def bracket_text_to_segments(text: str, single_kanji_reader=None) -> List[Tuple[str, Optional[str]]]:
    segments: List[Tuple[str, Optional[str]]] = []
    last = 0
    for m in BRACKET_RUBY_RE.finditer(text or ""):
        plain = text[last:m.start()]
        if plain:
            segments.append((plain, None))
        base = m.group(1)
        ruby = m.group(2)
        if base:
            for sub_base, sub_ruby in _split_ruby_base(base, ruby, single_kanji_reader):
                if sub_ruby is not None and not any(_is_kanji_char(ch) for ch in sub_base):
                    segments.append((sub_base, None))
                else:
                    segments.append((sub_base, sub_ruby))
        last = m.end()
    tail = (text or "")[last:]
    if tail:
        segments.append((tail, None))
    return segments
