#!/usr/bin/env python3
"""
bg_options.py — 배경 시안. 레퍼런스(지구본)와 다르게 갈 수 있는 후보들을 렌더링한다.

핵심 차별점은 모양이 아니라 **배경이 음악에 반응한다**는 점이다.
레퍼런스의 지구본은 그냥 돈다. 여기 후보들은 beat 값(0~1)을 받아 함께 움직인다.
그 값은 이미 host 쪽에서 계산하고 있는 저역 에너지를 쓰면 된다.

실행:  python3 tools/preview/bg_options.py
"""

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from oled import Canvas, W, H, measure, save_sheet   # noqa: E402
from design_preview import draw_bars, fake_levels, lyric_box   # noqa: E402
from design_preview import draw_globe   # noqa: E402


# ──────────────────────────────────────────────────────────────
# 배경 후보들.  phase = 시간(초), beat = 0~1 (비트 세기)
# ──────────────────────────────────────────────────────────────

def bg_stars(c, phase, beat):
    """별밤 — 별이 왼쪽으로 흐른다. 가까운 별일수록 빠르고, 비트에 크게 반짝인다."""
    seed = 12345
    for i in range(46):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x0 = seed % 128
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = seed % 44
        layer = i % 3                      # 0=먼 별, 2=가까운 별
        speed = 3 + layer * 7
        x = int(x0 - phase * speed) % 128
        c.pixel(x, y)
        if layer == 2 and beat > 0.6:      # 가까운 별만 비트에 십자로 커진다
            c.pixel(x - 1, y); c.pixel(x + 1, y)
            c.pixel(x, y - 1); c.pixel(x, y + 1)


def bg_tunnel(c, phase, beat):
    """원근 격자 터널 — 격자가 앞으로 밀려온다. 비트에 지평선이 살짝 올라간다."""
    horizon = 8 - int(beat * 3)
    vx, vy = W // 2, horizon
    for k in range(-7, 8):                 # 소실점에서 뻗어나가는 세로선
        c.line(vx + k * 3, vy, vx + k * 46, H - 1)
    for i in range(7):                     # 다가오는 가로선
        z = ((i + phase * 0.9) % 7) / 7.0
        y = vy + (H - vy) * (z * z)
        c.line(0, y, W - 1, y)
    c.line(0, horizon, W - 1, horizon)     # 지평선


def bg_ripple(c, phase, beat):
    """동심원 파문 — 비트가 칠 때마다 원이 새로 퍼져나간다."""
    for i in range(4):
        r = int((phase * 22 + i * 15) % 60)
        if r > 2:
            c.circle(W // 2, 30, r)
    if beat > 0.65:                        # 비트 순간에 한가운데가 밝게 찬다
        c.circle(W // 2, 30, 3)
        c.circle(W // 2, 30, 4)


def bg_cassette(c, phase, beat):
    """카세트 테이프 — 릴 두 개가 돈다. 음악 장치라는 게 한눈에 읽힌다."""
    c.round_rect(4, 2, 120, 42, 4)
    for cx in (36, 92):
        c.circle(cx, 23, 12)
        c.circle(cx, 23, 4)
        for k in range(6):                 # 릴 살 — 이게 돌아가는 게 보인다
            a = phase * 2.2 + k * math.pi / 3 + (0.5 if cx > 64 else 0)
            c.line(cx + 4 * math.cos(a), 23 + 4 * math.sin(a),
                   cx + 11 * math.cos(a), 23 + 11 * math.sin(a))
    c.line(36, 11, 92, 11)                 # 릴 사이를 잇는 테이프
    if beat > 0.6:
        c.line(36, 10, 92, 10)


def bg_vinyl(c, phase, beat):
    """LP판 — 판이 돌고 톤암이 얹혀 있다. 홈이 비트에 굵어진다."""
    cx, cy = 64, 26
    for r in (25, 21, 17, 13):
        c.circle(cx, cy, r)
    if beat > 0.6:
        c.circle(cx, cy, 21 + 1)
    c.circle(cx, cy, 3)
    for k in range(3):                     # 판 위의 표시 — 회전이 눈에 보이게
        a = phase * 1.6 + k * 2.09
        c.line(cx + 5 * math.cos(a), cy + 5 * math.sin(a),
               cx + 12 * math.cos(a), cy + 12 * math.sin(a))
    c.line(112, 2, 96, 18)                 # 톤암
    c.line(96, 18, 92, 22)


def bg_skyline(c, phase, beat):
    """도시 야경 — 건물이 흘러가고 창문에 불이 들어온다. 위쪽엔 별.

    건물 높이는 y33 아래로 묶어 둔다. 그보다 높으면 지붕선이 가사(y22~32)를
    가로질러서 외곽선 글씨가 읽히지 않는다. 렌더링해서 확인한 값이다.
    """
    seed = 555                                   # 붙박이 별 — 건물과 달리 흐르지 않는다
    for _ in range(14):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        sx = seed % 128
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        c.pixel(sx, seed % 18)

    seed = 987
    x = -int(phase * 9) % 24 - 24
    while x < W:
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        w = 10 + seed % 12
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        h = 3 + seed % 9                         # 지붕은 y33~41 사이
        top = 44 - h
        c.rect(x, top, w, h)
        for wy in range(top + 3, 44 - 2, 4):     # 창문
            for wx in range(x + 2, x + w - 2, 4):
                if (wx * 7 + wy * 13 + int(beat * 4)) % 5 < 2:
                    c.pixel(wx, wy)
        x += w + 3
    c.line(0, 44, W - 1, 44)


BACKGROUNDS = [
    ("0 지구본(레퍼런스)", lambda c, p, b: draw_globe(c, p)),
    ("1 별밤",            bg_stars),
    ("2 격자 터널",        bg_tunnel),
    ("3 동심원 파문",      bg_ripple),
    ("4 카세트 테이프",     bg_cassette),
    ("5 LP판",            bg_vinyl),
    ("6 도시 스카이라인",   bg_skyline),
]


# ──────────────────────────────────────────────────────────────
# 가사 표시 방식 두 가지
# ──────────────────────────────────────────────────────────────

def compose(bg, text, phase, beat, seed, style):
    c = Canvas()
    bg(c, phase, beat)
    if text:
        if style == "box":
            lyric_box(c, text, 1, 26)          # 상자로 배경을 가린다 (지금 방식)
        else:
            tw = measure(text, 1)
            c.outline_text((W - tw) // 2, 22, text, 1)   # 배경 위에 그대로 얹는다
    draw_bars(c, fake_levels(seed))
    return c


if __name__ == "__main__":
    LINE = "오늘 밤은 조금 길어"       # 보통 길이
    LONG = "나뉘어 이어서 보여야"       # 화면 폭에 꽉 차는 어려운 구간

    for style, tag in (("box", "상자"), ("outline", "외곽선")):
        canvases, names = [], []
        for name, bg in BACKGROUNDS:
            canvases.append(compose(bg, LINE, 1.4, 0.8, 3, style))
            names.append(name)
        save_sheet(canvases, HERE / f"BG_{tag}.png", scale=4, gap=8)
        print(f"  BG_{tag}.png :", ", ".join(names))

    # 폭이 꽉 찬 어려운 구간에서 두 방식 비교
    hard = []
    for name, bg in BACKGROUNDS[1:5]:
        hard.append(compose(bg, LONG, 2.1, 0.9, 6, "box"))
        hard.append(compose(bg, LONG, 2.1, 0.9, 6, "outline"))
    save_sheet(hard, HERE / "BG_어려운구간_상자vs외곽선.png", scale=4, gap=8)
    print("  BG_어려운구간_상자vs외곽선.png : 배경마다 상자/외곽선 한 쌍씩")
