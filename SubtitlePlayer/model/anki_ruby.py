import os
import re
import subprocess
import sys
import threading
import logging
from typing import Callable, List, Optional, Tuple

try:
    from SubtitlePlayer.furigana_splitter import (
        iter_number_counter_matches,
        iter_ordinal_number_matches,
        split_furigana,
    )
except ImportError:
    try:
        from furigana_splitter import iter_number_counter_matches, iter_ordinal_number_matches, split_furigana
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import iter_number_counter_matches, iter_ordinal_number_matches, split_furigana

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

KAKASI_ARGS = ["-isjis", "-osjis", "-u", "-JH", "-KH"]
MECAB_ARGS = ["--node-format=%m[%f[7]] ", "--eos-format=\n", "--unk-format=%m[] "]
SJIS_ENCODING = "sjis"
UTF8_ENCODING = "utf-8"

DEFAULT_SUPPORT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "anki_reading_support")
)

BRACKET_RUBY_RE = re.compile(r"([^\s\[\]]+)\[([^\[\]]+)\]")
SOURCE_RUBY_RE = re.compile(r"([\u3400-\u9fff\u3005]+)[(\uff08]([\u3040-\u309f\u30a0-\u30ff\u30fc]+)[)\uff09]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
ALNUM_RE = re.compile(r"^[A-Za-z0-9]+$")
ARABIC_NUMERAL_CHARS = set("0123456789０１２３４５６７８９")
WHOLE_RUBY_OVERRIDES = {
    "十分": "じゅうぶん",
}

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


def _is_katakana_ruby_base(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    has_katakana = False
    for ch in value:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30FA or 0x30FD <= code <= 0x30FF:
            has_katakana = True
            continue
        if ch in {"ー", "・", "･"}:
            continue
        return False
    return has_katakana


def _is_kana_text(text: str) -> bool:
    text = text or ""
    return bool(text) and all(0x3040 <= ord(ch) <= 0x30FF or ch == "\u30fc" for ch in text)


def _has_kanji_text(text: str) -> bool:
    return any(_is_kanji_char(ch) for ch in text or "")


def _katakana_run_segments(text: str) -> List[Segment] | None:
    source = text or ""
    if not source:
        return None
    out: List[Segment] = []
    cursor = 0
    changed = False
    for match in re.finditer(r"[\u30a0-\u30ff\u30fc]+", source):
        if match.start() > cursor:
            out.append((source[cursor:match.start()], None))
        base = match.group(0)
        hira = _kata_to_hira(base)
        if _is_katakana_ruby_base(base) and hira and hira != base:
            out.append((base, hira))
            changed = True
        else:
            out.append((base, None))
        cursor = match.end()
    if cursor < len(source):
        out.append((source[cursor:], None))
    return out if changed else None


def _split_ruby_base(
    base: str,
    ruby: str,
    single_kanji_reader: SingleKanjiReader = None,
    split_kanji_compounds: bool = False,
) -> List[Segment]:
    if not base:
        return []
    if base in WHOLE_RUBY_OVERRIDES:
        return [(base, WHOLE_RUBY_OVERRIDES[base])]
    if not ruby:
        return [(base, None)]
    if _is_katakana_ruby_base(base):
        hira = _kata_to_hira(ruby)
        return [(base, hira if hira and hira != base else None)]
    return split_furigana(
        base,
        ruby,
        single_kanji_reader,
        split_kanji_compounds=split_kanji_compounds,
    )


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
    def __init__(self, support_dir: str, extra_args: Optional[List[str]] = None) -> None:
        self.support_dir = support_dir
        self.extra_args = list(extra_args or [])
        self.kakasi: subprocess.Popen | None = None
        self.kakasi_cmd: List[str] | None = None
        self.kakasi_env: dict[str, str] | None = None
        self._lock = threading.RLock()

    def setup(self) -> None:
        self.kakasi_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "kakasi")] + KAKASI_ARGS + self.extra_args
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

    @staticmethod
    def _parse_mecab_nodes(expr: str) -> Optional[List[Tuple[str, str]]]:
        nodes: List[Tuple[str, str]] = []
        i = 0
        n = len(expr or "")
        while i < n:
            while i < n and expr[i] in " \t\r\n":
                i += 1
            if i >= n:
                break
            bracket = expr.find("[", i)
            if bracket < 0:
                return None
            close = expr.find("]", bracket + 1)
            if close < 0:
                return None
            surface = expr[i:bracket]
            reading = expr[bracket + 1:close]
            if not surface:
                return None
            nodes.append((surface, reading))
            i = close + 1
        return nodes

    def _format_mecab_output(self, expr: str) -> str:
        out = []
        nodes = self._parse_mecab_nodes(expr)
        if nodes is None:
            logger.warning("Unexpected output from mecab: %r", expr)
            return ""

        for kanji, reading in nodes:
            if not reading:
                hira = _kata_to_hira(kanji)
                if _is_katakana_ruby_base(kanji) and hira != kanji:
                    out.append(" %s[%s]" % (kanji, hira))
                else:
                    out.append(kanji)
                continue
            if kanji == reading:
                hira = _kata_to_hira(reading)
                if _is_katakana_ruby_base(kanji) and hira != kanji:
                    out.append(" %s[%s]" % (kanji, hira))
                else:
                    out.append(kanji)
                continue
            reading = self.kakasi.reading(reading)
            if reading == kanji:
                out.append(kanji)
                continue
            if all(ch in ARABIC_NUMERAL_CHARS for ch in kanji):
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
    def __init__(self, support_dir: Optional[str] = None, split_kanji_compounds: bool = False) -> None:
        self.support_dir = support_dir or DEFAULT_SUPPORT_DIR
        if not os.path.isdir(self.support_dir):
            raise RubyGeneratorError(f"Ruby support directory was not found: {self.support_dir}")
        self.split_kanji_compounds = bool(split_kanji_compounds)
        self.kakasi = KakasiController(self.support_dir)
        self.kakasi_readings = KakasiController(self.support_dir, extra_args=["-p"])
        self.mecab = MecabController(self.support_dir, self.kakasi)
        self._single_kanji_reading_cache: dict[str, str] = {}
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self.mecab.close()
        self.kakasi.close()
        self.kakasi_readings.close()

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
        value = ""
        try:
            raw = self.kakasi_readings.reading(ch).strip()
        except Exception:
            raw = ""
        if raw and raw != ch and "[" not in raw and "]" not in raw:
            value = _kata_to_hira(raw)
        if not value:
            raw = self.reading(ch).strip()
            match = BRACKET_RUBY_RE.fullmatch(raw)
            if match and match.group(1) == ch:
                value = _kata_to_hira(match.group(2))
            elif raw and raw != ch and "[" not in raw and "]" not in raw:
                value = _kata_to_hira(raw)
        self._single_kanji_reading_cache[ch] = value
        return value

    def segments(self, text: str, split_kanji_compounds: bool | None = None) -> List[Segment]:
        self._ensure_open()
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        return self._segments_with_source_ruby(text, split_kanji_compounds=split_flag)

    def _generated_segments(
        self,
        text: str,
        collapse_inline: bool = True,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment]:
        if not text:
            return []
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        ruby_text = self.reading(text)
        segments = bracket_text_to_segments(
            ruby_text,
            self.single_kanji_reading,
            split_kanji_compounds=split_flag,
        )
        segments = self._align_generated_spacing_to_source(segments, text)
        segments = self._apply_ruby_overrides(segments)
        if not split_flag:
            segments = self._merge_adjacent_kanji_ruby_compounds(segments)
        if collapse_inline:
            return self._collapse_repeated_inline_readings(segments)
        return segments

    @staticmethod
    def _align_generated_spacing_to_source(segments: List[Segment], source_text: str) -> List[Segment]:
        source = source_text or ""
        if not source or not segments:
            return segments
        out: List[Segment] = []
        pos = 0
        for base, ruby in segments:
            text = base or ""
            if not text:
                continue
            if not text.strip():
                if source.startswith(text, pos):
                    out.append((base, ruby))
                    pos += len(text)
                continue
            if pos < len(source) and source[pos].isspace() and text and not text[0].isspace():
                ws_end = pos
                while ws_end < len(source) and source[ws_end].isspace():
                    ws_end += 1
                if source.startswith(text, ws_end):
                    out.append((source[pos:ws_end], None))
                    out.append((base, ruby))
                    pos = ws_end + len(text)
                    continue
            if source.startswith(text, pos):
                out.append((base, ruby))
                pos += len(text)
                continue
            stripped = text.lstrip()
            if stripped != text and source.startswith(stripped, pos):
                out.append((stripped, ruby))
                pos += len(stripped)
                continue
            stripped = text.rstrip()
            if stripped != text and source.startswith(stripped, pos):
                out.append((stripped, ruby))
                pos += len(stripped)
                continue
            stripped = text.strip()
            if stripped != text and source.startswith(stripped, pos):
                out.append((stripped, ruby))
                pos += len(stripped)
                continue
            out.append((base, ruby))
        return out

    def _plain_segments(self, text: str, split_kanji_compounds: bool | None = None) -> List[Segment]:
        if not text:
            return []
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        ordinal_segments = self._segments_with_ordinal_readings(text, split_kanji_compounds=split_flag)
        if ordinal_segments is not None:
            return ordinal_segments
        counter_segments = self._segments_with_counter_readings(text, split_kanji_compounds=split_flag)
        if counter_segments is not None:
            return counter_segments
        return self._plain_segments_without_counters(text, split_kanji_compounds=split_flag)

    def _segments_with_ordinal_readings(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment] | None:
        matches = list(iter_ordinal_number_matches(text))
        if not matches:
            return None

        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        segments: List[Segment] = []
        cursor = 0
        for match, reading in matches:
            if match.start() > cursor:
                segments.extend(self._plain_segments(text[cursor:match.start()], split_kanji_compounds=split_flag))
            surface = match.group(0)
            segments.append((surface, WHOLE_RUBY_OVERRIDES.get(surface, reading)))
            cursor = match.end()
        if cursor < len(text):
            segments.extend(self._plain_segments(text[cursor:], split_kanji_compounds=split_flag))
        return segments

    def _plain_segments_without_counters(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment]:
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        if not _has_kanji_text(text):
            katakana_segments = _katakana_run_segments(text)
            if katakana_segments is not None:
                return katakana_segments
            return [(text, None)]
        if text in WHOLE_RUBY_OVERRIDES:
            return [(text, WHOLE_RUBY_OVERRIDES[text])]
        inline = self._segments_with_inline_source_readings(text, split_kanji_compounds=split_flag)
        if inline is not None:
            return self._apply_ruby_overrides(inline)
        return self._generated_segments(text, split_kanji_compounds=split_flag)

    def _generated_or_plain_segments(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment]:
        if not text:
            return []
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        if not _has_kanji_text(text):
            katakana_segments = _katakana_run_segments(text)
            if katakana_segments is not None:
                return katakana_segments
            return [(text, None)]
        return self._generated_segments(text, split_kanji_compounds=split_flag)

    def _segments_with_counter_readings(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment] | None:
        matches = list(iter_number_counter_matches(text))
        if not matches:
            return None

        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        segments: List[Segment] = []
        cursor = 0
        for match, reading in matches:
            if match.start() > cursor:
                segments.extend(
                    self._plain_segments_without_counters_preserve_edges(
                        text[cursor:match.start()],
                        split_kanji_compounds=split_flag,
                    )
                )
            surface = match.group(0)
            segments.append((surface, WHOLE_RUBY_OVERRIDES.get(surface, reading)))
            cursor = match.end()
        if cursor < len(text):
            segments.extend(
                self._plain_segments_without_counters_preserve_edges(
                    text[cursor:],
                    split_kanji_compounds=split_flag,
                )
            )
        return segments

    def _plain_segments_without_counters_preserve_edges(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment]:
        if not text:
            return []
        leading_len = len(text) - len(text.lstrip())
        trailing_len = len(text) - len(text.rstrip())
        leading = text[:leading_len]
        trailing = text[len(text) - trailing_len :] if trailing_len else ""
        middle_end = len(text) - trailing_len if trailing_len else len(text)
        middle = text[leading_len:middle_end]

        segments: List[Segment] = []
        if leading:
            segments.append((leading, None))
        if middle:
            segments.extend(
                self._plain_segments_without_counters(
                    middle,
                    split_kanji_compounds=split_kanji_compounds,
                )
            )
        if trailing:
            segments.append((trailing, None))
        return segments

    def _segments_with_source_ruby(self, text: str, split_kanji_compounds: bool | None = None) -> List[Segment]:
        if not text:
            return []
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)

        segments: List[Segment] = []
        last = 0
        found_source_ruby = False
        for match in SOURCE_RUBY_RE.finditer(text):
            found_source_ruby = True
            plain = text[last:match.start()]
            if plain:
                segments.extend(self._plain_segments(plain, split_kanji_compounds=split_flag))
            base = match.group(1)
            ruby = _kata_to_hira(match.group(2))
            segments.extend(
                split_furigana(
                    base,
                    ruby,
                    self.single_kanji_reading,
                    split_kanji_compounds=split_flag,
                )
            )
            last = match.end()
        if not found_source_ruby:
            return self._plain_segments(text, split_kanji_compounds=split_flag)
        tail = text[last:]
        if tail:
            segments.extend(self._plain_segments(tail, split_kanji_compounds=split_flag))
        return self._collapse_repeated_inline_readings(self._apply_ruby_overrides(segments))

    def _segments_with_inline_source_readings(
        self,
        text: str,
        split_kanji_compounds: bool | None = None,
    ) -> List[Segment] | None:
        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        out: List[Segment] = []
        cursor = 0
        i = 0
        changed = False
        while i < len(text):
            if not _is_kanji_char(text[i]):
                i += 1
                continue

            run_start = i
            while i < len(text) and _is_kanji_char(text[i]):
                i += 1
            run_end = i
            if run_end >= len(text) or not _is_kana_text(text[run_end]):
                continue

            base_text = text[run_start:run_end]
            following_plain = text[run_end:]
            override = self._source_inline_reading_override_for_base(
                base_text,
                following_plain,
                split_kanji_compounds=split_flag,
            )
            if override is None:
                continue

            override_segments, consumed = override
            if run_start > cursor:
                out.extend(
                    self._generated_or_plain_segments(
                        text[cursor:run_start],
                        split_kanji_compounds=split_flag,
                    )
                )
            out.extend(override_segments)
            cursor = run_end + consumed
            i = cursor
            changed = True

        if not changed:
            return None
        if cursor < len(text):
            out.extend(self._generated_or_plain_segments(text[cursor:], split_kanji_compounds=split_flag))
        return self._apply_ruby_overrides(out)

    def _collapse_repeated_inline_readings(self, segments: List[Segment]) -> List[Segment]:
        collapsed: List[Segment] = []
        i = 0
        while i < len(segments):
            base, ruby = segments[i]
            if not ruby or not any(_is_kanji_char(ch) for ch in base or ""):
                collapsed.append((base, ruby))
                i += 1
                continue

            group: List[Segment] = []
            j = i
            while j < len(segments):
                group_base, group_ruby = segments[j]
                if not group_ruby or not any(_is_kanji_char(ch) for ch in group_base or ""):
                    break
                group.append((group_base, group_ruby))
                j += 1

            if j < len(segments):
                next_base, next_ruby = segments[j]
                if next_base and next_ruby is None and _is_kana_text(next_base[0]):
                    group_ruby = "".join(part_ruby or "" for _part_base, part_ruby in group)
                    if group_ruby and next_base.startswith(group_ruby):
                        collapsed.extend(group)
                        trimmed = next_base[len(group_ruby):]
                        if trimmed:
                            collapsed.append((trimmed, None))
                        i = j + 1
                        continue

                    override = self._source_inline_reading_override(group, next_base)
                    if override is not None:
                        override_segments, consumed = override
                        collapsed.extend(override_segments)
                        trimmed = next_base[consumed:]
                        if trimmed:
                            collapsed.append((trimmed, None))
                        i = j + 1
                        continue

            collapsed.extend(group)
            i = j
        return collapsed

    @staticmethod
    def _apply_ruby_overrides(segments: List[Segment]) -> List[Segment]:
        if not segments:
            return segments
        return [
            (base, WHOLE_RUBY_OVERRIDES.get(base, ruby))
            for base, ruby in segments
        ]

    @staticmethod
    def _merge_adjacent_kanji_ruby_compounds(segments: List[Segment]) -> List[Segment]:
        if not segments:
            return segments
        out: List[Segment] = []
        i = 0
        while i < len(segments):
            base, ruby = segments[i]
            if ruby and base and all(_is_kanji_char(ch) for ch in base):
                merged_base = base
                merged_ruby = ruby
                j = i + 1
                while j < len(segments):
                    next_base, next_ruby = segments[j]
                    if not (
                        next_ruby
                        and next_base
                        and all(_is_kanji_char(ch) for ch in next_base)
                    ):
                        break
                    merged_base += next_base
                    merged_ruby += next_ruby
                    j += 1
                if j > i + 1:
                    out.append((merged_base, WHOLE_RUBY_OVERRIDES.get(merged_base, merged_ruby)))
                    i = j
                    continue
            out.append((base, ruby))
            i += 1
        return out

    def _source_inline_reading_override(
        self,
        ruby_group: List[Segment],
        following_plain: str,
    ) -> tuple[List[Segment], int] | None:
        base_text = "".join(base or "" for base, _ruby in ruby_group)
        return self._source_inline_reading_override_for_base(base_text, following_plain)

    def _source_inline_reading_override_for_base(
        self,
        base_text: str,
        following_plain: str,
        split_kanji_compounds: bool | None = None,
    ) -> tuple[List[Segment], int] | None:
        if not base_text or not following_plain:
            return None

        kana_len = 0
        for ch in following_plain:
            if not _is_kana_text(ch):
                break
            kana_len += 1
        if kana_len <= 0:
            return None

        split_flag = self.split_kanji_compounds if split_kanji_compounds is None else bool(split_kanji_compounds)
        kana_run = following_plain[:kana_len]
        next_after_kana = following_plain[kana_len : kana_len + 1]
        if (
            len(base_text) == 1
            and 1 <= kana_len <= 4
            and next_after_kana
            and _is_kanji_char(next_after_kana)
            and self._single_kanji_inline_reading_matches(base_text, kana_run)
        ):
            return (
                split_furigana(
                    base_text,
                    kana_run,
                    self.single_kanji_reading,
                    split_kanji_compounds=split_flag,
                ),
                kana_len,
            )

        max_take = min(8, kana_len)
        for take in range(max_take, 0, -1):
            candidate_reading = following_plain[:take]
            candidate_word = base_text + following_plain[take:]
            try:
                candidate_segments = self._generated_segments(
                    candidate_word,
                    collapse_inline=False,
                    split_kanji_compounds=split_flag,
                )
            except Exception:
                continue
            leading = self._leading_ruby_segments_for(candidate_segments, base_text, candidate_reading)
            if leading:
                return leading, take
        return None

    def _single_kanji_inline_reading_matches(self, base_text: str, kana_run: str) -> bool:
        if len(base_text or "") != 1 or not _is_kanji_char(base_text):
            return False
        target = _kata_to_hira(kana_run or "").strip()
        if not target:
            return False

        choices: set[str] = set()
        try:
            choices.update(self._expand_inline_reading_choices(self.single_kanji_reading(base_text)))
        except Exception:
            pass
        try:
            choices.update(
                ruby
                for _base, ruby in self._generated_segments(base_text, collapse_inline=False)
                if ruby
            )
        except Exception:
            pass
        return target in choices

    @staticmethod
    def _expand_inline_reading_choices(value: str) -> set[str]:
        value = _kata_to_hira(str(value or "").strip())
        if not value:
            return set()
        if "{" not in value:
            return {part for part in value.split("|") if part}

        expanded = [""]
        i = 0
        while i < len(value):
            ch = value[i]
            if ch != "{":
                expanded = [prefix + ch for prefix in expanded]
                i += 1
                continue
            end = value.find("}", i + 1)
            if end < 0:
                return {value}
            parts = [part for part in value[i + 1 : end].split("|") if part]
            if not parts:
                return {value}
            expanded = [prefix + part for prefix in expanded for part in parts]
            i = end + 1
        return {part for part in expanded if part}

    @staticmethod
    def _leading_ruby_segments_for(
        segments: List[Segment],
        base_text: str,
        reading: str,
    ) -> List[Segment] | None:
        base_acc = ""
        ruby_acc = ""
        leading: List[Segment] = []
        for base, ruby in segments:
            if not ruby or not any(_is_kanji_char(ch) for ch in base or ""):
                return None
            base_acc += base or ""
            ruby_acc += ruby or ""
            leading.append((base, ruby))
            if base_acc == base_text:
                return leading if ruby_acc == reading else None
            if not base_text.startswith(base_acc):
                return None
        return None


def bracket_text_to_segments(
    text: str,
    single_kanji_reader: SingleKanjiReader = None,
    split_kanji_compounds: bool = False,
) -> List[Segment]:
    segments: List[Segment] = []
    last = 0
    for m in BRACKET_RUBY_RE.finditer(text or ""):
        plain = text[last:m.start()]
        if plain:
            segments.append((plain, None))
        base = m.group(1)
        ruby = m.group(2)
        if base:
            for sub_base, sub_ruby in _split_ruby_base(
                base,
                ruby,
                single_kanji_reader,
                split_kanji_compounds=split_kanji_compounds,
            ):
                if (
                    sub_ruby is not None
                    and not any(_is_kanji_char(ch) for ch in sub_base)
                    and not _is_katakana_ruby_base(sub_base)
                ):
                    segments.append((sub_base, None))
                else:
                    segments.append((sub_base, sub_ruby))
        last = m.end()
    tail = (text or "")[last:]
    if tail:
        segments.append((tail, None))
    return segments
