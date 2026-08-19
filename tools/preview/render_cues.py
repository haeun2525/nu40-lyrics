#!/usr/bin/env python3
"""
render_cues.py — .lrc 를 실제 화면 배치로 렌더링해서 잘림이 없는지 눈으로 확인한다.

펌웨어의 drawScreen() 과 같은 규칙으로 그린다. 배경도 펌웨어와 같은 4가지다.

실행:
  python3 tools/preview/render_cues.py lyrics/노래.lrc          # 배경 전부 한 장씩
  python3 tools/preview/render_cues.py lyrics/노래.lrc --bg 0   # 별밤만
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "host"))

from oled import Canvas, W, measure, save_sheet   # noqa: E402
from design_preview import draw_bars, fake_levels, lyric_box   # noqa: E402
from bg_options import bg_stars, bg_ripple, bg_cassette, bg_skyline   # noqa: E402
import lrc as lrclib   # noqa: E402

# 펌웨어의 버튼 1~4 와 같은 순서. 외곽선 여부도 펌웨어의 bgUsesOutline() 과 같다.
BACKGROUNDS = [
    ("1 별밤",           bg_stars,    True),
    ("2 동심원 파문",     bg_ripple,   False),
    ("3 카세트 테이프",   bg_cassette, False),
    ("4 도시 스카이라인", bg_skyline,  True),
]


def screen(text, bg_index, phase, beat, seed):
    """펌웨어의 drawScreen() 과 같은 배치."""
    _, bg, outline = BACKGROUNDS[bg_index]
    c = Canvas()
    bg(c, phase, beat)
    if text:
        if outline:
            c.outline_text((W - measure(text, 1)) // 2, 22, text, 1)
        else:
            lyric_box(c, text, 1, 26)
    draw_bars(c, fake_levels(seed))
    return c


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = None
    if "--bg" in sys.argv:
        only = int(sys.argv[sys.argv.index("--bg") + 1])

    src = Path(args[0] if args else HERE.parent.parent / "lyrics/_테스트.lrc")
    entries, meta = lrclib.parse_lrc(src)
    cues = lrclib.build_cues(entries)

    over = [t for t in cues if lrclib.text_width(t[1]) > lrclib.MAX_TEXT_W]
    widths = [lrclib.text_width(t[1]) for t in cues]
    print(f"  {src.name}: 원본 {len(entries)}줄 → 화면 조각 {len(cues)}개")
    print(f"  최대 폭 {max(widths)}px / 한계 {lrclib.MAX_TEXT_W}px, 폭 초과 {len(over)}개")

    bad = lrclib.missing_glyphs(cues, lrclib.load_font_codes(
        HERE.parent.parent / "firmware/nu40_lyrics/hangul_font.h"))
    if bad:
        print(f"  [경고] 화면에 못 그리는 글자: {' '.join(bad)}")

    # 폭이 큰 조각부터 본다. 잘리는 건 여기서 다 드러난다.
    order = sorted(range(len(cues)), key=lambda i: -widths[i])[:6]
    targets = [only] if only is not None else range(len(BACKGROUNDS))

    for b in targets:
        canvases = [screen(cues[i][1], b, i * 0.37, 0.8 if i % 3 else 0.2, i)
                    for i in order]
        out = HERE / f"CUE_{src.stem}_배경{b + 1}.png"
        save_sheet(canvases, out, scale=4, gap=8)
        print(f"  저장: {out.name}  ({BACKGROUNDS[b][0]}, "
              f"{'외곽선' if BACKGROUNDS[b][2] else '상자'})")
