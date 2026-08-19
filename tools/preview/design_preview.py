#!/usr/bin/env python3
"""
design_preview.py — 가사 화면 디자인 시안을 PC에서 렌더링한다.

보드에 굽기 전에 여기서 배치를 확정한다. 실제 화면과 픽셀 단위로 같다.
실행:  python3 tools/preview/design_preview.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from oled import Canvas, W, H, measure, save_png, save_sheet   # noqa: E402

OUT = Path(__file__).parent

# ── 배경: 지구본 ──────────────────────────────────────────
GLOBE_CX, GLOBE_CY, GLOBE_R = 64, 30, 30


def draw_globe(c, phase):
    """경선이 도는 지구본. phase 가 커지면 회전한다."""
    # 바깥 원
    for i in range(48):
        t0 = 2 * math.pi * i / 48
        t1 = 2 * math.pi * (i + 1) / 48
        c.line(GLOBE_CX + GLOBE_R * math.cos(t0), GLOBE_CY + GLOBE_R * math.sin(t0),
               GLOBE_CX + GLOBE_R * math.cos(t1), GLOBE_CY + GLOBE_R * math.sin(t1))
    # 경선 3개 — 가로 반지름이 cos 로 줄었다 늘었다 하면서 회전해 보인다
    for k in range(3):
        rx = GLOBE_R * math.cos(phase + k * math.pi / 3)
        pts = []
        for i in range(25):
            t = math.pi * i / 24 - math.pi / 2
            pts.append((GLOBE_CX + rx * math.cos(t), GLOBE_CY + GLOBE_R * math.sin(t)))
        for a, b in zip(pts, pts[1:]):
            c.line(a[0], a[1], b[0], b[1])
            c.line(2 * GLOBE_CX - a[0], a[1], 2 * GLOBE_CX - b[0], b[1])
    # 위선 2개
    for lat in (-0.6, 0.6):
        ry = GLOBE_R * math.cos(lat) * 0.22
        rx = GLOBE_R * math.cos(lat)
        cy = GLOBE_CY - GLOBE_R * math.sin(lat)
        pts = [(GLOBE_CX + rx * math.cos(2 * math.pi * i / 32),
                cy + ry * math.sin(2 * math.pi * i / 32)) for i in range(33)]
        for a, b in zip(pts, pts[1:]):
            c.line(a[0], a[1], b[0], b[1])
    # 십자선 — 화면 끝까지
    c.line(0, GLOBE_CY, W - 1, GLOBE_CY)
    c.line(GLOBE_CX, 0, GLOBE_CX, H - 1)
    for t in range(-2, 3):
        c.line(GLOBE_CX + t * 12, GLOBE_CY - 2, GLOBE_CX + t * 12, GLOBE_CY + 2)


# ── 배경: 흐르는 스캔라인 ────────────────────────────────
def draw_scanlines(c, phase):
    off = int(phase * 6) % 8
    for y in range(-8 + off, 44, 8):
        for x in range(0, W, 2):
            c.pixel(x + (y // 8) % 2, y)


# ── 아래쪽 스펙트럼 막대 ────────────────────────────────
BAR_COUNT = 16
BAR_W, BAR_GAP = 6, 2
BAR_BOTTOM = 63
BAR_MAX = 20


def draw_bars(c, levels):
    """levels: 0~15 값 16개. 막대는 배경 위에 덮어 그린다."""
    for i, v in enumerate(levels):
        h = max(1, round(v * BAR_MAX / 15))
        x = i * (BAR_W + BAR_GAP)
        # 막대 뒤 배경을 지워서 지구본 선과 겹치지 않게 한다
        c.rect(x, BAR_BOTTOM - BAR_MAX, BAR_W, BAR_MAX + 1, fill=True, on=0)
        c.rect(x, BAR_BOTTOM - h + 1, BAR_W, h, fill=True, on=1)


def fake_levels(seed):
    return [int(8 + 7 * math.sin(seed * 0.9 + i * 0.7) * math.cos(seed * 0.3 + i * 0.4))
            for i in range(BAR_COUNT)]


# ── 가사 상자 ────────────────────────────────────────────
def lyric_box(c, text, size, cy):
    """가운데 상자 안에 글자. 상자 안은 검게 지워서 배경과 겹치지 않게 한다."""
    tw = measure(text, size)
    th = 9 * size
    bw = min(W - 2, tw + 10)
    bh = th + 8
    bx = (W - bw) // 2
    by = cy - bh // 2
    c.round_rect(bx, by, bw, bh, 3, fill=True, on=0)
    c.round_rect(bx, by, bw, bh, 3, fill=False, on=1)
    c.mixed_text((W - tw) // 2, by + 4, text, size)


# ── 시안 ─────────────────────────────────────────────────
def option_a(text, phase, seed):
    """A — 레퍼런스형: 지구본 + 한 단어(2배 크기)"""
    c = Canvas()
    draw_globe(c, phase)
    lyric_box(c, text, 2, 26)
    draw_bars(c, fake_levels(seed))
    return c


def option_b(text, phase, seed):
    """B — 지구본 + 한 줄 전체(1배 크기)"""
    c = Canvas()
    draw_globe(c, phase)
    lyric_box(c, text, 1, 26)
    draw_bars(c, fake_levels(seed))
    return c


def option_c(text, phase, seed, sub=""):
    """C — 배경 없이 담백하게: 위 곡 정보 + 가운데 가사 + 아래 막대"""
    c = Canvas()
    draw_scanlines(c, phase)
    c.rect(0, 0, W, 11, fill=True, on=0)
    c.line(0, 11, W - 1, 11)
    c.mixed_text(2, 1, sub, 1)
    tw = measure(text, 1)
    c.rect((W - tw) // 2 - 3, 20, tw + 6, 15, fill=True, on=0)
    c.mixed_text((W - tw) // 2, 23, text, 1)
    draw_bars(c, fake_levels(seed))
    return c


if __name__ == "__main__":
    # 미리보기용 예시 문구 (실제 가사는 사용자가 .lrc 파일로 넣는다)
    SHORT = "hold"
    KO_SHORT = "그대로"
    KO_LINE = "오늘 밤은 조금 길어"          # 10자 — 보통 길이
    KO_LONG = "우리가 걸어온 길을 기억해줘"   # 14자 — 어려운(긴) 구간

    save_sheet([option_a(SHORT, 0.0, 0),
                option_a(KO_SHORT, 1.1, 3),
                option_a("우리", 2.2, 6)],
               OUT / "A_지구본_한단어.png")

    save_sheet([option_b(KO_LINE, 0.0, 0),
                option_b(KO_LONG, 1.1, 3),
                option_b("Perfect - Ed Sheeran", 2.2, 6)],
               OUT / "B_지구본_한줄.png")

    save_sheet([option_c(KO_LINE, 0.0, 0, "NOW PLAYING"),
                option_c(KO_LONG, 1.1, 3, "NOW PLAYING"),
                option_c("Perfect", 2.2, 6, "NOW PLAYING")],
               OUT / "C_담백_한줄.png")

    # 회전이 실제로 보이는지 — A안을 위상만 바꿔가며
    save_sheet([option_a(KO_SHORT, p, i) for i, p in enumerate([0.0, 0.5, 1.0, 1.5])],
               OUT / "R_회전_4단계.png")

    print("완료:", *[p.name for p in sorted(OUT.glob('*.png'))], sep="\n  ")
