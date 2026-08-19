#!/usr/bin/env python3
"""
render_character.py — 형체를 어떤 캐릭터로 그릴지 시안을 뽑는다.

골반선은 그리지 않는다(상체만). 관절 좌표는 그대로 쓰고 '그리는 방식'만 바꾼다.
"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from oled import Canvas, save_sheet          # noqa: E402
from design_preview import draw_bars, fake_levels   # noqa: E402

HEAD, SL, SR, EL, ER, WL, WR, HL, HR = range(9)


def shoulders_mid(j):
    return ((j[SL][0] + j[SR][0]) / 2, (j[SL][1] + j[SR][1]) / 2)


def waist(j):
    """골반은 안 그리지만 허리 위치는 필요하다. 골반 두 점의 한가운데."""
    return ((j[HL][0] + j[HR][0]) / 2, (j[HL][1] + j[HR][1]) / 2)


def _torso(j, narrow=0.6):
    sm, wm = shoulders_mid(j), waist(j)
    dx, dy = j[SR][0] - j[SL][0], j[SR][1] - j[SL][1]
    L = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / L, dy / L
    h = L / 2
    return sm, wm, ux, uy, h, [
        (sm[0] - ux * h, sm[1] - uy * h), (sm[0] + ux * h, sm[1] + uy * h),
        (wm[0] + ux * h * narrow, wm[1] + uy * h * narrow),
        (wm[0] - ux * h * narrow, wm[1] - uy * h * narrow)]


def style_silhouette(c, j):
    """실루엣 — 몸을 통째로 채운다."""
    sm, wm, ux, uy, h, quad = _torso(j)
    hx, hy = j[HEAD][0], j[HEAD][1] - 1
    c.thick_seg(hx, hy + 5, sm[0], sm[1], 1.8, 2.4)
    c.fill_poly(quad)
    for sh, el, wr in ((SL, EL, WL), (SR, ER, WR)):
        c.thick_seg(j[sh][0], j[sh][1], j[el][0], j[el][1], 3.0, 2.2)
        c.thick_seg(j[el][0], j[el][1], j[wr][0], j[wr][1], 2.2, 1.4)
        c.fill_circle(j[wr][0], j[wr][1], 2)
    c.fill_circle(hx, hy, 7)


def style_shaded(c, j):
    """음영 — 빛을 왼쪽 위에서 받는다고 보고 반대쪽에 가로 빗금을 깐다.

    점을 흩뿌리는 방식은 20픽셀 몸통에서 얼룩으로 보였다. 가로 빗금은 연필 그림의
    해칭과 같은 원리라 같은 밀도에서도 매끄러운 회색으로 읽힌다.
    단계는 셋뿐 — 꽉 참 / 두 줄에 한 줄 / 세 줄에 한 줄.
    """
    LIT, MID, DARK = 1.0, 0.5, 0.3
    sm, wm, ux, uy, h, quad = _torso(j)
    hx, hy = j[HEAD][0], j[HEAD][1] - 1

    c.seg_tone(hx, hy + 5, sm[0], sm[1], 2.0, 2.6, MID)          # 목 — 턱 그늘
    mt = ((quad[0][0] + quad[1][0]) / 2, (quad[0][1] + quad[1][1]) / 2)
    mb = ((quad[2][0] + quad[3][0]) / 2, (quad[2][1] + quad[3][1]) / 2)
    c.fill_poly_tone([quad[0], mt, mb, quad[3]], LIT)            # 왼쪽 = 빛
    c.fill_poly_tone([mt, quad[1], quad[2], mb], MID)            # 오른쪽 = 그늘

    for sh, el, wr in ((SL, EL, WL), (SR, ER, WR)):
        tone = LIT if sh == SL else MID
        c.seg_tone(j[sh][0], j[sh][1], j[el][0], j[el][1], 3.0, 2.2, tone)
        c.seg_tone(j[el][0], j[el][1], j[wr][0], j[wr][1], 2.2, 1.4, tone)
        c.fill_ellipse_tone(j[wr][0], j[wr][1], 1.8, 1.8, LIT)

    # 머리 — 머리카락을 먼저 덮고 그 아래로 얼굴을 드러낸다.
    # 납작한 모자처럼 보이지 않게 이마 선을 곡선으로 파내고 가운데에 가르마를 준다.
    c.fill_ellipse_tone(hx, hy - 1, 8, 9, DARK)                  # 머리카락
    c.fill_ellipse_tone(hx, hy + 2, 6, 6, LIT)                   # 얼굴
    c.fill_ellipse_tone(hx + 2, hy + 3, 4, 4, MID)               # 얼굴 오른쪽 그늘
    c.fill_poly_tone([(hx - 1, hy - 6), (hx + 1, hy - 6),         # 가르마 앞머리
                      (hx + 2, hy - 1), (hx, hy + 1), (hx - 2, hy - 1)], DARK)
    for ex in (-4, 2):                                           # 눈
        c.pixel(hx + ex, hy + 3, 0)
        c.pixel(hx + ex + 1, hy + 3, 0)


STYLES = [("음영빗금", style_shaded), ("실루엣", style_silhouette)]

if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "motion/chestpop.motion")
    d = json.loads(src.read_text(encoding="utf-8"))
    F = [[(f[i * 2], f[i * 2 + 1]) for i in range(9)] for f in d["frames"]]
    picks = [int(len(F) * k / 4) for k in range(4)]
    for name, fn in STYLES:
        shots = []
        for idx in picks:
            c = Canvas(); fn(c, F[idx]); draw_bars(c, fake_levels(idx))
            shots.append(c)
        out = HERE / f"CHAR_{name}.png"
        save_sheet(shots, out, scale=4, gap=8)
        print(f"  저장: {out.name}")
