#!/usr/bin/env python3
"""
fetch_lyrics.py — 유튜브 링크만 주면 타이밍이 붙은 가사(.lrc)를 자동으로 구해온다.

가사 페이지를 검색해서 긁어오지 않는다. 그렇게 얻는 건 시간표가 없는 맨 글자라
이 프로젝트에는 쓸모가 없다. 대신 **처음부터 타이밍이 붙어 있는 출처** 두 곳을 쓴다.

  1) LRCLIB (https://lrclib.net) — 음악 재생기용 동기화 가사 공개 API. 키가 필요 없고
     [mm:ss.xx] 형식을 그대로 내려준다. 곡 길이로 대조해서 다른 곡을 잡는 걸 막는다.
  2) 유튜브 자막 — 그 영상에 달린 자막. 업로더가 넣은 원본 자막이 있으면 가장 정확하고,
     없으면 자동 자막(음성 인식)을 쓴다. 자동 자막은 글자가 틀릴 수 있다.

둘 다 실패하면 tap_sync.py 로 직접 찍으면 된다.

사용:
  ./fetch_lyrics.py "<유튜브 링크>" --out ../lyrics/노래.lrc
  ./fetch_lyrics.py "<유튜브 링크>" --out ... --source youtube   # 자막만 쓰기
  ./fetch_lyrics.py --artist "가수" --track "곡" --out ...        # 링크 없이 찾기
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
YTDLP = HERE / "bin" / "yt-dlp"
UA = "nu40-lyrics/0.1 (https://github.com/, NU40DK OLED lyrics display)"


# ──────────────────────────────────────────────────────────────
# 유튜브에서 곡 정보 알아내기
# ──────────────────────────────────────────────────────────────

def video_info(url: str) -> dict:
    """제목·업로더·길이와, 있으면 유튜브가 인식한 아티스트/곡명을 가져온다."""
    fields = "%(id)s\t%(title)s\t%(uploader)s\t%(duration)s\t%(artist)s\t%(track)s"
    r = subprocess.run([str(YTDLP), "--no-warnings", "--print", fields, url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"유튜브 정보를 읽지 못했습니다:\n{r.stderr.strip()}")
    parts = (r.stdout.strip().split("\n")[0] + "\t\t\t\t\t").split("\t")
    vid, title, uploader, dur, artist, track = parts[:6]
    return {
        "id": vid,
        "title": title,
        "uploader": uploader,
        "duration": float(dur) if dur.replace(".", "").isdigit() else None,
        # yt-dlp 가 "NA" 를 내놓는 경우가 있어서 걸러낸다
        "artist": artist if artist and artist != "NA" else "",
        "track": track if track and track != "NA" else "",
    }


def guess_artist_track(info: dict) -> tuple[str, str]:
    """유튜브가 아티스트/곡명을 안 알려주면 제목에서 짐작한다.

    음악 영상 제목은 대개 '가수 - 곡명 (Official ...)' 꼴이다.
    """
    if info["artist"] and info["track"]:
        return info["artist"], info["track"]

    title = info["title"]
    # 괄호·대괄호 안의 홍보 문구를 걷어낸다: (Official Video), [MV], (Lyrics) 등
    cleaned = re.sub(r"[\(\[][^\)\]]*"
                     r"(official|video|audio|mv|lyric|verse|live|feat|ver\.|가사|뮤직비디오)"
                     r"[^\)\]]*[\)\]]", "", title, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—|")

    for sep in (" - ", " – ", " — ", " | "):
        if sep in cleaned:
            left, _, right = cleaned.partition(sep)
            return left.strip(), right.strip()
    return info["uploader"], cleaned


def strip_alias(name: str) -> str:
    """이름 뒤에 괄호로 병기한 다른 표기를 뗀다.

    한국 음악 영상은 'The Poles (더 폴스)' 처럼 영문·한글을 나란히 적는 일이 많다.
    가사 데이터베이스에는 보통 한쪽 표기로만 올라가 있어서, 병기한 채로 물어보면
    있는 곡도 못 찾는다. 실제로 이것 때문에 한 번 놓쳤다.
    """
    return re.sub(r"\s*[\(\［\[][^\)\］\]]*[\)\］\]]", "", name).strip()


def name_candidates(artist: str, track: str) -> list[tuple[str, str]]:
    """조회에 써볼 (가수, 곡명) 조합들. 앞에 있는 것부터 시도한다."""
    out = []
    for a in (artist, strip_alias(artist)):
        for t in (track, strip_alias(track)):
            if a and t and (a, t) not in out:
                out.append((a, t))
    return out


# ──────────────────────────────────────────────────────────────
# 출처 1 — LRCLIB
# ──────────────────────────────────────────────────────────────

def lrclib_get(artist: str, track: str, duration: float | None) -> str | None:
    """정확히 일치하는 곡의 동기화 가사를 받는다. 없으면 None."""
    q = {"artist_name": artist, "track_name": track}
    if duration:
        q["duration"] = str(int(round(duration)))
    url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(q)
    data = _get_json(url)
    if data and data.get("syncedLyrics"):
        return data["syncedLyrics"]
    return None


def lrclib_search(artist: str, track: str, duration: float | None) -> str | None:
    """정확히 일치하는 게 없을 때 검색해서 가장 그럴듯한 것을 고른다.

    곡 길이가 가장 가까운 것을 쓴다. 같은 제목의 라이브·리믹스를 잘못 집는 걸 막는다.
    """
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(
        {"artist_name": artist, "track_name": track})
    data = _get_json(url)
    if not isinstance(data, list):
        return None

    hits = [d for d in data if d.get("syncedLyrics")]
    if not hits:
        return None
    if duration:
        hits.sort(key=lambda d: abs((d.get("duration") or 0) - duration))
        # 길이가 15초 넘게 차이 나면 다른 버전일 가능성이 크다
        if abs((hits[0].get("duration") or 0) - duration) > 15:
            print(f"  [주의] 길이가 {abs((hits[0].get('duration') or 0) - duration):.0f}초 "
                  f"차이 납니다. 다른 버전일 수 있습니다.")
    print(f"  LRCLIB 검색 결과 사용: {hits[0].get('artistName')} — {hits[0].get('trackName')}")
    return hits[0]["syncedLyrics"]


def _get_json(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 출처 2 — 유튜브 자막
# ──────────────────────────────────────────────────────────────

def youtube_subs(url: str, vid: str, lang: str | None) -> str | None:
    """영상에 달린 자막을 받아 .lrc 형식으로 바꾼다. 원본 자막을 자동 자막보다 먼저 쓴다."""
    tmp = HERE / ".cache" / "subs"
    tmp.mkdir(parents=True, exist_ok=True)

    langs = lang or "ko,en,ko-orig,en-orig"
    for auto in (False, True):
        cmd = [str(YTDLP), "--no-warnings", "--skip-download",
               "--sub-langs", langs, "--sub-format", "vtt",
               "-o", str(tmp / "%(id)s"), url]
        cmd.insert(3, "--write-auto-subs" if auto else "--write-subs")
        subprocess.run(cmd, capture_output=True, text=True)

        found = sorted(tmp.glob(f"{vid}*.vtt"))
        if found:
            kind = "자동 자막(음성 인식 — 글자가 틀릴 수 있습니다)" if auto else "원본 자막"
            print(f"  유튜브 {kind} 사용: {found[0].name}")
            text = vtt_to_lrc(found[0].read_text(encoding="utf-8", errors="replace"))
            for f in found:
                f.unlink()
            if text:
                return text
    return None


_VTT_TIME = re.compile(
    r"(\d+):(\d+):(\d+[.,]\d+)\s+-->\s+(\d+):(\d+):(\d+[.,]\d+)")


def vtt_to_lrc(vtt: str) -> str:
    """VTT 자막을 .lrc 로. 자동 자막은 같은 줄이 계속 되풀이되므로 걸러낸다."""
    out: list[tuple[float, str]] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        m = _VTT_TIME.search(lines[i])
        if not m:
            i += 1
            continue
        h, mi, s = m.group(1), m.group(2), m.group(3).replace(",", ".")
        start = int(h) * 3600 + int(mi) * 60 + float(s)

        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() and not _VTT_TIME.search(lines[i]):
            # 자동 자막의 낱말 단위 태그(<00:00:01.234><c>단어</c>)를 걷어낸다
            clean = re.sub(r"<[^>]+>", "", lines[i]).strip()
            if clean:
                body.append(clean)
            i += 1
        text = " ".join(body).strip()
        if text:
            out.append((start, text))

    # 자동 자막은 앞 줄을 그대로 다시 내보내며 굴러간다. 바로 앞과 같으면 버린다.
    deduped: list[tuple[float, str]] = []
    for t, text in out:
        if deduped and (text == deduped[-1][1] or text.startswith(deduped[-1][1])):
            deduped[-1] = (deduped[-1][0], text)   # 더 길어진 쪽으로 갱신
            continue
        deduped.append((t, text))

    if not deduped:
        return ""
    return "\n".join(f"[{int(t)//60:02d}:{int(t)%60:02d}.{int(t*100)%100:02d}]{txt}"
                     for t, txt in deduped)


# ──────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="타이밍이 붙은 가사(.lrc)를 자동으로 구해온다")
    p.add_argument("url", nargs="?", help="유튜브 링크")
    p.add_argument("--artist", help="링크 없이 찾을 때 가수 이름")
    p.add_argument("--track", help="링크 없이 찾을 때 곡 이름")
    p.add_argument("--duration", type=float, help="곡 길이(초). 대조에 쓴다")
    p.add_argument("--out", required=True, help="만들 .lrc 경로")
    p.add_argument("--source", choices=["auto", "lrclib", "youtube"], default="auto",
                   help="어디서 가져올지. 기본 auto (LRCLIB 먼저, 없으면 유튜브 자막)")
    p.add_argument("--lang", help="유튜브 자막 언어 (예: ko 또는 en)")
    args = p.parse_args()

    info = {"id": "", "duration": args.duration}
    artist, track = args.artist or "", args.track or ""

    if args.url:
        info = video_info(args.url)
        if not (artist and track):
            artist, track = guess_artist_track(info)
        print(f"  영상: {info['title']}")
        print(f"  추정: {artist} — {track}  ({info['duration'] or 0:.0f}초)")
    elif not (artist and track):
        sys.exit("링크를 주거나 --artist 와 --track 을 함께 지정하세요.")

    text = None

    if args.source in ("auto", "lrclib") and artist and track:
        for a, t in name_candidates(artist, track):
            if (a, t) != (artist, track):
                print(f"  다시 조회: {a} — {t}")
            text = lrclib_get(a, t, info.get("duration"))
            if text:
                print("  LRCLIB 정확히 일치")
                break
            text = lrclib_search(a, t, info.get("duration"))
            if text:
                break

    if not text and args.source in ("auto", "youtube") and args.url:
        text = youtube_subs(args.url, info["id"], args.lang)

    if not text:
        print("\n  타이밍이 붙은 가사를 찾지 못했습니다.")
        print("  가사를 한 줄에 한 소절씩 적은 .txt 를 만든 뒤 tap_sync.py 로 직접 찍으세요:")
        print("    ./.venv/bin/python tap_sync.py \"<링크>\" --text 가사.txt --out 가사.lrc")
        sys.exit(1)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    header = []
    if track:
        header.append(f"[ti:{track}]")
    if artist:
        header.append(f"[ar:{artist}]")
    out.write_text("\n".join(header + ["", text]) + "\n", encoding="utf-8")

    n = len([l for l in text.splitlines() if l.strip()])
    print(f"\n  저장했습니다: {out}  ({n}줄)")
    print("  화면에서 잘리는 게 없는지 확인:")
    print(f"    python3 tools/preview/render_cues.py {out}")


if __name__ == "__main__":
    main()
