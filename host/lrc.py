#!/usr/bin/env python3
"""
lrc.py — .lrc 가사 파일을 읽고, OLED 폭에 맞게 잘라서 "언제 뭘 띄울지" 목록으로 만든다.

이 파일이 존재하는 이유는 하나다. **화면에서 글자가 잘리면 안 된다.**
OLED 는 128픽셀뿐이라 한글은 한 줄에 12자가 한계다. 그래서 긴 가사 줄은
여기서 미리 여러 조각으로 나누고, 그 줄이 흐르는 시간을 조각들이 나눠 갖는다.

글자 폭 계산 규칙은 펌웨어의 measureMixedText() 와 **똑같아야 한다.**
  - 한글      : 10픽셀
  - 영문·숫자 : 6픽셀
"""

from __future__ import annotations

import re
from pathlib import Path

# 가사 상자는 글자 폭 + 좌우 여백 10픽셀이고, 상자는 화면(128)보다 2픽셀 작다.
# 그래서 글자가 쓸 수 있는 폭은 128 - 2 - 10 = 116 픽셀이다.
MAX_TEXT_W = 116

HANGUL_W = 10
ASCII_W = 6

# 한 조각이 화면에 머무는 최소·최대 시간(초). 너무 빨리 지나가거나
# 간주 구간에 가사가 계속 떠 있는 걸 막는다.
MIN_CHUNK_SEC = 0.45
MAX_LINE_SEC = 8.0

_TIME_RE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def load_font_codes(header: Path) -> set[int]:
    """펌웨어에 심긴 한글 글꼴 목록을 읽는다. 없는 글자를 미리 경고하기 위해서다."""
    if not header.exists():
        return set()
    text = header.read_text(errors="replace")
    m = re.search(r"HANGUL_CODES\[HANGUL_COUNT\]\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        return set()
    return {int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{4})", m.group(1))}


def char_width(ch: str) -> int:
    if "가" <= ch <= "힣":
        return HANGUL_W
    if " " <= ch < "\x7f":
        return ASCII_W
    return 0            # 펌웨어가 그리지 못하는 글자는 폭도 0이다


def text_width(s: str) -> int:
    return sum(char_width(c) for c in s)


def _atoms(text: str, max_w: int) -> list[str]:
    """띄어쓰기로 나눈 조각들. 화면보다 긴 단어 하나는 글자 단위로 미리 쪼갠다."""
    out: list[str] = []
    for word in text.split(" "):
        if text_width(word) <= max_w:
            out.append(word)
            continue
        piece = ""
        for ch in word:
            if text_width(piece + ch) > max_w:
                out.append(piece)
                piece = ch
            else:
                piece += ch
        if piece:
            out.append(piece)
    return out


def _min_chunks(atoms: list[str], max_w: int) -> int:
    """화면에 담으려면 최소 몇 조각이 필요한가. 탐욕적으로 세면 그게 최소값이다."""
    count, current = 1, ""
    for a in atoms:
        candidate = a if not current else current + " " + a
        if text_width(candidate) <= max_w:
            current = candidate
        else:
            count += 1
            current = a
    return count


def split_to_fit(text: str, max_w: int = MAX_TEXT_W) -> list[str]:
    """한 줄을 화면에 들어가는 조각들로 나눈다.

    **폭이 찰 때까지 밀어넣는 방식(탐욕적)을 쓰면 안 된다.**
    그러면 '꽉 찬 조각 + 남은 한 단어' 모양이 되어, 뒷조각에 한두 글자만 남는다.
    한글은 조사가 붙은 짧은 어절이 많아서 이게 특히 자주 터진다.
    실제로 이 곡에서 나뉜 17줄 중 7줄이 그랬고, 한 줄은 화면에 한 글자만 떴다.

    그래서 조각 수는 최소로 두되(읽을 시간을 벌어야 하므로),
    그 개수 안에서 **폭이 고르게** 나뉘도록 고른다.
    """
    text = " ".join(text.split())
    if not text:
        return []
    if text_width(text) <= max_w:
        return [text]

    atoms = _atoms(text, max_w)
    k = _min_chunks(atoms, max_w)
    if k <= 1:
        return [" ".join(atoms)]

    n = len(atoms)
    # width[i][j] = atoms[i:j] 를 한 조각으로 붙였을 때의 폭
    width = [[0] * (n + 1) for _ in range(n + 1)]
    for i in range(n):
        acc = 0
        for j in range(i + 1, n + 1):
            acc = text_width(atoms[i]) if j == i + 1 else acc + ASCII_W + text_width(atoms[j - 1])
            width[i][j] = acc

    target = text_width(text) / k          # 조각 하나가 가졌으면 하는 폭
    INF = float("inf")

    # dp[c][i] = atoms[i:] 를 c 조각으로 나눌 때의 최소 '고르지 못한 정도'
    dp = [[INF] * (n + 1) for _ in range(k + 1)]
    cut = [[-1] * (n + 1) for _ in range(k + 1)]
    dp[0][n] = 0.0

    for c in range(1, k + 1):
        for i in range(n - 1, -1, -1):
            best, best_j = INF, -1
            for j in range(i + 1, n + 1):
                w = width[i][j]
                if w > max_w:
                    break                  # 더 붙이면 더 넘친다
                rest = dp[c - 1][j]
                if rest == INF:
                    continue
                # 목표 폭에서 벗어난 만큼을 제곱해서 더한다.
                # 제곱이라서 '아주 짧은 조각 하나'가 크게 벌점을 받는다 = 고아가 사라진다.
                score = (w - target) ** 2 + rest
                if score < best:
                    best, best_j = score, j
            dp[c][i], cut[c][i] = best, best_j

    if dp[k][0] == INF:                    # 이론상 없지만, 나뉘지 않으면 원문 그대로
        return [" ".join(atoms)]

    chunks, i, c = [], 0, k
    while c > 0:
        j = cut[c][i]
        chunks.append(" ".join(atoms[i:j]))
        i, c = j, c - 1
    return chunks


def parse_time(tag_min: str, tag_rest: str) -> float:
    """[mm:ss.xx] 또는 [mm:ss:xx] 를 초로."""
    return int(tag_min) * 60 + float(tag_rest.replace(":", "."))


def parse_lrc(path: Path) -> tuple[list[tuple[float, str]], dict]:
    """.lrc 를 (시각, 가사) 목록과 메타정보로 읽는다.

    한 줄에 시간표가 여러 개 붙어 있는 형식([00:12.00][01:30.00]가사)도 받는다.
    """
    meta: dict[str, str] = {}
    entries: list[tuple[float, str]] = []

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue

        # [ar:...] [ti:...] 같은 메타 태그
        m = re.fullmatch(r"\[([a-zA-Z]{2,}):(.*)\]", line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
            continue

        stamps = _TIME_RE.findall(line)
        if not stamps:
            continue
        body = _TIME_RE.sub("", line).strip()
        for mm, rest in stamps:
            entries.append((parse_time(mm, rest), body))

    entries.sort(key=lambda e: e[0])
    return entries, meta


def build_cues(entries: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """(시각, 가사) 목록을 화면에 실제로 띄울 (시각, 조각) 목록으로 바꾼다.

    - 긴 줄은 조각으로 나누고, 그 줄이 차지한 시간을 글자 수 비율로 나눠 갖는다
    - 줄과 줄 사이가 비면 빈 조각("")을 넣어 간주 구간에는 상자를 지운다
    """
    cues: list[tuple[float, str]] = []

    for i, (t, body) in enumerate(entries):
        next_t = entries[i + 1][0] if i + 1 < len(entries) else t + MAX_LINE_SEC
        span = max(0.05, min(next_t - t, MAX_LINE_SEC))

        chunks = split_to_fit(body)
        if not chunks:
            cues.append((t, ""))          # 빈 줄 = 간주 시작
            continue

        # 조각이 하나면 그대로. 여러 개면 글자 폭 비율로 시간을 나눈다.
        #
        # 시작 시각은 반드시 "그 줄이 차지한 구간 안"에서만 나눈다.
        # 최소 표시시간을 억지로 확보하려고 뒤로 밀면 마지막 조각이 다음 줄을
        # 넘어가서 다음 가사를 덮어쓴다. 조금 빨리 지나가더라도 넘기지 않는 쪽이 맞다.
        if len(chunks) == 1:
            cues.append((t, chunks[0]))
        else:
            # 글자 폭에 비례하되, 완전한 비례는 쓰지 않는다.
            # "…줄 12" 처럼 꼬리가 짧으면 비례만으로는 0.3초만 스치고 사라진다.
            # 평균값과 반씩 섞어서 짧은 조각도 읽을 시간을 갖게 한다.
            raw = [max(1, text_width(c)) for c in chunks]
            mean = sum(raw) / len(raw)
            weights = [0.5 * w + 0.5 * mean for w in raw]
            total = sum(weights)
            acc = 0
            for chunk, w in zip(chunks, weights):
                cues.append((t + span * acc / total, chunk))
                acc += w

        # 다음 줄까지 한참 비면 그 사이에 화면을 비운다
        if next_t - t > MAX_LINE_SEC:
            cues.append((t + span, ""))

    cues.sort(key=lambda c: c[0])
    return cues


def missing_glyphs(cues: list[tuple[float, str]], codes: set[int]) -> list[str]:
    """펌웨어 글꼴에 없어서 화면에 안 나올 글자들."""
    if not codes:
        return []
    bad = []
    for _, text in cues:
        for ch in text:
            o = ord(ch)
            if 0xAC00 <= o <= 0xD7A3 and o not in codes:
                if ch not in bad:
                    bad.append(ch)
            elif ch == "\n":
                continue          # 두 줄로 나누라는 표시일 뿐이다
            elif not ("가" <= ch <= "힣") and not (" " <= ch < "\x7f"):
                if ch not in bad:
                    bad.append(ch)
    return bad
