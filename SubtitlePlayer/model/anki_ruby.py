import os
import re
import subprocess
import sys
import threading
import logging
from typing import Callable, List, Optional, Tuple

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
SJIS_ENCODING = "sjis"
UTF8_ENCODING = "utf-8"

DEFAULT_SUPPORT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "anki_reading_support")
)

BRACKET_RUBY_RE = re.compile(r"([^\[\]]+)\[([^\[\]]+)\]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
MECAB_NODE_RE = re.compile(r"(.+)\[(.*)\]")
ALNUM_RE = re.compile(r"^[A-Za-z0-9]+$")
JAPANESE_NUMERAL_CHARS = set("一二三四五六七八九十０１２３４５６７８９")

Segment = Tuple[str, Optional[str]]
SingleKanjiReader = Optional[Callable[[str], str]]
logger = logging.getLogger(__name__)


class RubyGeneratorError(RuntimeError):
    """Raised when the bundled MeCab/Kakasi ruby backend cannot be used."""


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


def _kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _split_ruby_base(base: str, ruby: str, single_kanji_reader: SingleKanjiReader = None) -> List[Segment]:
    if not base:
        return []
    if not ruby:
        return [(base, None)]
    return split_furigana(base, ruby, single_kanji_reader)


def strip_html(text: str) -> str:
    return HTML_TAG_RE.sub("", text or "")


def escape_text(text: str) -> str:
    text = text or ""
    text = HTML_BR_RE.sub("---newline---", text)
    text = text.replace("\n", " ")
    text = text.replace("\uff5e", "~")
    text = strip_html(text)
    text = text.replace("---newline---", "<br>")
    return text


def munge_for_platform(popen: List[str]) -> List[str]:
    popen = list(popen)
    if IS_WIN:
        popen = [os.path.normpath(x) for x in popen]
        popen[0] += ".exe"
    elif not IS_MAC:
        popen[0] += ".lin"
    return popen


def _ensure_executable(path: str, name: str) -> None:
    if not path or not os.path.exists(path):
        raise RubyGeneratorError(f"Bundled {name} executable was not found: {path}")
    if not IS_WIN:
        os.chmod(path, 0o755)


def _startupinfo():
    if not IS_WIN:
        return None
    si = subprocess.STARTUPINFO()
    try:
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except Exception:
        si.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW
    return si


def _support_env(support_dir: str, updates: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(updates)
    if IS_WIN:
        env["PATH"] = support_dir + os.pathsep + env.get("PATH", "")
    return env


def _process_is_open(proc: subprocess.Popen | None) -> bool:
    return (
        proc is not None
        and proc.poll() is None
        and proc.stdin is not None
        and proc.stdout is not None
    )


def _close_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            logger.debug("Failed to kill %s process", name, exc_info=True)


class KakasiController:
    def __init__(self, support_dir: str) -> None:
        self.support_dir = support_dir
        self.kakasi: subprocess.Popen | None = None
        self.kakasi_cmd: List[str] | None = None
        self.kakasi_env: dict[str, str] | None = None
        self._lock = threading.RLock()

    def setup(self) -> None:
        self.kakasi_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "kakasi")] + KAKASI_ARGS
        )
        _ensure_executable(self.kakasi_cmd[0], "kakasi")
        self.kakasi_env = _support_env(
            self.support_dir,
            {
                "ITAIJIDICT": os.path.join(self.support_dir, "itaijidict"),
                "KANWADICT": os.path.join(self.support_dir, "kanwadict"),
            },
        )

    def ensure_open(self) -> None:
        if not _process_is_open(self.kakasi):
            self.close()
            self.setup()
            try:
                self.kakasi = subprocess.Popen(
                    self.kakasi_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                    cwd=self.support_dir,
                    env=self.kakasi_env,
                )
            except OSError as exc:
                raise RubyGeneratorError("Failed to start bundled kakasi") from exc

    def close(self) -> None:
        with self._lock:
            _close_process(self.kakasi, "kakasi")
            self.kakasi = None

    def reading(self, expr: str) -> str:
        expr = escape_text(expr)
        with self._lock:
            for attempt in range(2):
                try:
                    self.ensure_open()
                    assert self.kakasi and self.kakasi.stdin and self.kakasi.stdout
                    self.kakasi.stdin.write(expr.encode(SJIS_ENCODING, "ignore") + b"\n")
                    self.kakasi.stdin.flush()
                    raw = self.kakasi.stdout.readline()
                    if not raw:
                        raise RubyGeneratorError("kakasi closed its output pipe")
                    return raw.rstrip(b"\r\n").decode(SJIS_ENCODING, "replace")
                except (BrokenPipeError, OSError, ValueError, RubyGeneratorError):
                    self.close()
                    if attempt:
                        raise
            return ""


class MecabController:
    def __init__(self, support_dir: str, kakasi: KakasiController) -> None:
        self.support_dir = support_dir
        self.kakasi = kakasi
        self.mecab: subprocess.Popen | None = None
        self.mecab_cmd: List[str] | None = None
        self.mecab_env: dict[str, str] | None = None
        self._lock = threading.RLock()

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
        _ensure_executable(self.mecab_cmd[0], "mecab")
        self.mecab_env = _support_env(
            self.support_dir,
            {
                "DYLD_LIBRARY_PATH": self.support_dir,
                "LD_LIBRARY_PATH": self.support_dir,
            },
        )

    def ensure_open(self) -> None:
        if not _process_is_open(self.mecab):
            self.close()
            self.setup()
            try:
                self.mecab = subprocess.Popen(
                    self.mecab_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                    cwd=self.support_dir,
                    env=self.mecab_env,
                )
            except OSError as exc:
                raise RubyGeneratorError("Failed to start bundled mecab") from exc

    def close(self) -> None:
        with self._lock:
            _close_process(self.mecab, "mecab")
            self.mecab = None

    def reading(self, expr: str) -> str:
        expr = escape_text(expr)
        with self._lock:
            expr = self._raw_reading(expr)
            return self._format_mecab_output(expr)

    def _raw_reading(self, expr: str) -> str:
        for attempt in range(2):
            try:
                self.ensure_open()
                assert self.mecab and self.mecab.stdin and self.mecab.stdout
                self.mecab.stdin.write(expr.encode(UTF8_ENCODING, "ignore") + b"\n")
                self.mecab.stdin.flush()
                raw = self.mecab.stdout.readline()
                if not raw:
                    raise RubyGeneratorError("mecab closed its output pipe")
                return raw.rstrip(b"\r\n").decode(UTF8_ENCODING, "replace")
            except (BrokenPipeError, OSError, ValueError, RubyGeneratorError):
                self.close()
                if attempt:
                    raise
        return ""

    def _format_mecab_output(self, expr: str) -> str:
        out = []
        for node in expr.split():
            m = MECAB_NODE_RE.fullmatch(node)
            if not m:
                logger.warning("Unexpected output from mecab: %r", expr)
                return ""

            (kanji, reading) = m.groups()
            if kanji == reading or not reading:
                out.append(kanji)
                continue
            reading = self.kakasi.reading(reading)
            if reading == kanji:
                out.append(kanji)
                continue
            if all(ch in JAPANESE_NUMERAL_CHARS for ch in kanji):
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
            if c < len(out) - 1 and ALNUM_RE.fullmatch(out[c + 1]):
                s += " "
            fin += s
        return fin.strip().replace("< br>", "<br>")


class AddonRubyGenerator:
    def __init__(self, support_dir: Optional[str] = None) -> None:
        self.support_dir = support_dir or DEFAULT_SUPPORT_DIR
        if not os.path.isdir(self.support_dir):
            raise RubyGeneratorError(f"Ruby support directory was not found: {self.support_dir}")
        self.kakasi = KakasiController(self.support_dir)
        self.mecab = MecabController(self.support_dir, self.kakasi)
        self._single_kanji_reading_cache: dict[str, str] = {}
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self.mecab.close()
        self.kakasi.close()

    def __enter__(self) -> "AddonRubyGenerator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise RubyGeneratorError("Ruby generator has been closed")

    def reading(self, text: str) -> str:
        self._ensure_open()
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

    def segments(self, text: str) -> List[Segment]:
        self._ensure_open()
        ruby_text = self.reading(text)
        return bracket_text_to_segments(ruby_text, self.single_kanji_reading)


def bracket_text_to_segments(text: str, single_kanji_reader: SingleKanjiReader = None) -> List[Segment]:
    segments: List[Segment] = []
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
