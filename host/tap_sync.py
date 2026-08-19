#!/usr/bin/env python3
"""
tap_sync.py — 노래를 들으면서 엔터를 눌러 가사 타이밍(.lrc)을 만든다.

가사 "글자"는 직접 준비해야 한다(한 줄에 한 소절씩 적은 .txt).
이 도구는 그 글자에 **시각을 붙여주는** 일만 한다.

사용:
  ./tap_sync.py "<유튜브 링크>" --text ../lyrics/mysong.txt --out ../lyrics/mysong.lrc
  ./tap_sync.py ~/Music/song.m4a --text ... --out ...

조작:
  엔터      다음 줄이 시작되는 순간 (지금 시각을 그 줄에 찍는다)
  b + 엔터  방금 찍은 걸 취소하고 한 줄 뒤로
  q + 엔터  여기까지 저장하고 끝
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lyrics_oled as app   # noqa: E402


def fmt(t: float) -> str:
    if t < 0:
        t = 0.0
    return f"[{int(t) // 60:02d}:{int(t) % 60:02d}.{int(t * 100) % 100:02d}]"


def main() -> None:
    p = argparse.ArgumentParser(description="엔터를 눌러 가사 타이밍을 찍는다")
    p.add_argument("source", help="유튜브 링크 또는 로컬 오디오 파일")
    p.add_argument("--text", required=True, help="가사 글자 파일(.txt, 한 줄에 한 소절)")
    p.add_argument("--out", required=True, help="만들 .lrc 경로")
    p.add_argument("--latency", type=float, default=0.25,
                   help="afplay 시작 지연 보정(초)")
    p.add_argument("--lead", type=float, default=0.0,
                   help="찍은 시각을 앞당길 양(초). 손이 느리면 0.2 정도")
    args = p.parse_args()

    text_path = Path(args.text).expanduser()
    if not text_path.exists():
        sys.exit(f"가사 글자 파일이 없습니다: {text_path}")
    lines = [ln.strip() for ln in text_path.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        sys.exit("가사 글자 파일이 비어 있습니다.")

    src = args.source
    audio = (app.fetch_audio(src)[0] if src.startswith("http")
             else Path(src).expanduser())
    if not audio.exists():
        sys.exit(f"음원 파일이 없습니다: {audio}")

    print(f"\n  {len(lines)}줄. 소리가 나기 시작하면 각 줄이 나오는 순간 엔터를 누르세요.")
    print("  b=취소  q=저장하고 끝\n")

    proc = subprocess.Popen(["afplay", str(audio)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.monotonic() + args.latency

    stamps: list[float] = []
    try:
        i = 0
        while i < len(lines):
            nxt = lines[i + 1] if i + 1 < len(lines) else "(마지막)"
            sys.stdout.write(f"  [{i+1:3d}/{len(lines)}] 다음 → {nxt[:40]}\n")
            sys.stdout.flush()
            cmd = sys.stdin.readline().strip().lower()
            if cmd == "q":
                break
            if cmd == "b":
                if stamps:
                    stamps.pop()
                    i -= 1
                continue
            stamps.append(max(0.0, time.monotonic() - t0 - args.lead))
            i += 1
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()

    if not stamps:
        sys.exit("\n찍은 게 없어서 저장하지 않았습니다.")

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    body = [f"[ti:{text_path.stem}]", ""]
    body += [f"{fmt(t)}{line}" for t, line in zip(stamps, lines)]
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"\n  저장했습니다: {out}  ({len(stamps)}줄)")
    print("  어긋나면 lyrics_oled.py 의 --offset 으로 통째로 밀 수 있습니다.")


if __name__ == "__main__":
    main()
