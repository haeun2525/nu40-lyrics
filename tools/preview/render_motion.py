#!/usr/bin/env python3
"""
render_motion.py — 뽑아낸 동작(.motion)이 보드 화면에서 어떻게 보이는지 미리 본다.

펌웨어의 drawFigure() 와 같은 규칙으로 그린다. 굽기 전에 여기서 형체를 확정한다.

실행:  python3 tools/preview/render_motion.py motion/chestpop.motion
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from oled import Canvas, save_sheet   # noqa: E402
from design_preview import draw_bars, fake_levels   # noqa: E402
from bg_options import bg_stars, bg_ripple   # noqa: E402

# 관절 번호 (extract_motion.py 와 같은 순서)
HEAD, SH_L, SH_R, EL_L, EL_R, WR_L, WR_R, HIP_L, HIP_R = range(9)
HEAD_R = 5


def thick_line(c, x0, y0, x1, y1):
    """저스트댄스풍으로 굵게. 얇은 선은 128x64 에서 형체로 안 읽힌다."""
    c.line(x0, y0, x1, y1)
    c.line(x0 + 1, y0, x1 + 1, y1)
    c.line(x0, y0 + 1, x1, y1 + 1)


def draw_figure(c, j):
    """관절 9개로 상반신 형체를 그린다. 펌웨어와 같은 순서·같은 규칙."""
    shx = (j[SH_L][0] + j[SH_R][0]) // 2
    shy = (j[SH_L][1] + j[SH_R][1]) // 2
    hpx = (j[HIP_L][0] + j[HIP_R][0]) // 2
    hpy = (j[HIP_L][1] + j[HIP_R][1]) // 2

    c.circle(j[HEAD][0], j[HEAD][1], HEAD_R)          # 머리 (얼굴 없음)
    c.circle(j[HEAD][0], j[HEAD][1], HEAD_R - 1)      # 두 겹으로 굵게
    thick_line(c, j[HEAD][0], j[HEAD][1] + HEAD_R, shx, shy)      # 목
    thick_line(c, j[SH_L][0], j[SH_L][1], j[SH_R][0], j[SH_R][1])  # 어깨선
    thick_line(c, shx, shy, hpx, hpy)                              # 척추
    thick_line(c, j[HIP_L][0], j[HIP_L][1], j[HIP_R][0], j[HIP_R][1])  # 골반선
    for sh, el, wr in ((SH_L, EL_L, WR_L), (SH_R, EL_R, WR_R)):    # 양팔
        thick_line(c, j[sh][0], j[sh][1], j[el][0], j[el][1])
        thick_line(c, j[el][0], j[el][1], j[wr][0], j[wr][1])
        c.circle(j[wr][0], j[wr][1], 1)                            # 손


def screen(j, bg, phase, seed):
    c = Canvas()
    if bg:
        bg(c, phase, 0.8)
    draw_figure(c, j)
    draw_bars(c, fake_levels(seed))
    return c


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "motion/chestpop.motion")
    data = json.loads(src.read_text(encoding="utf-8"))
    frames = [[(f[i * 2], f[i * 2 + 1]) for i in range(9)] for f in data["frames"]]
    n = len(frames)
    print(f"  {src.name}: {n}프레임, {data['fps']:.0f}fps, {data['start']}초부터")

    # 동작이 이어지는 게 보이게 고르게 8장
    picks = [int(n * k / 8) for k in range(8)]
    save_sheet([screen(frames[i], None, 0, i) for i in picks],
               HERE / "MOTION_형체.png", scale=4, gap=8)
    save_sheet([screen(frames[i], bg_stars, i * 0.3, i) for i in picks[:4]] +
               [screen(frames[i], bg_ripple, i * 0.3, i) for i in picks[4:]],
               HERE / "MOTION_배경위.png", scale=4, gap=8)

    ys = [(f[SH_L][1] + f[SH_R][1]) // 2 for f in frames]
    print(f"  어깨 높이 변화: {min(ys)}~{max(ys)}px (튕기는 폭 {max(ys)-min(ys)}px)")
    print("  저장: MOTION_형체.png, MOTION_배경위.png")
