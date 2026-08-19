#!/usr/bin/env python3
"""
lyrics_oled.py — 노래를 노트북에서 재생하면서, 가사와 주파수 막대를 NU40DK OLED로 보낸다.

파이프라인 (뮤직 LED 프로젝트와 같은 방식):
  YouTube --yt-dlp--> m4a --afconvert--> WAV --numpy STFT--> 16밴드 막대
                       |
                       +-- afplay --> 노트북 스피커
  .lrc 가사 --lrc.py--> 화면 폭에 맞게 자른 조각들
                       |
                       +----------- USB 시리얼 ---> NU40DK OLED

보드는 소리도 인터넷도 없다. PC가 다 하고 보드는 받은 대로 그린다.

사용:
  ./lyrics_oled.py "<유튜브 링크>" --lrc ../lyrics/mysong.lrc
  ./lyrics_oled.py ~/Music/song.m4a --lrc ../lyrics/mysong.lrc
  ./lyrics_oled.py "<링크>" --lrc ... --preview      # 보드 없이 터미널에서 확인
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import subprocess
import sys
import threading
import time
import json
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lrc as lrclib   # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
FONT_HEADER = HERE.parent / "firmware" / "nu40_lyrics" / "hangul_font.h"

FPS = 30.0            # 보드가 화면을 다시 그리는 속도와 맞춘다 (펌웨어 DRAW_INTERVAL_MS = 33)
FFT_SIZE = 2048
BAR_COUNT = 16

SYNC1, SYNC2 = 0xAA, 0x55
KIND_BARS, KIND_LYRIC, KIND_POSE = 0x01, 0x02, 0x03
JOINT_COUNT = 9


# ──────────────────────────────────────────────────────────────
# 1. 음원 확보
# ──────────────────────────────────────────────────────────────

def fetch_audio(url: str) -> tuple[Path, str]:
    """유튜브에서 오디오를 받아 캐시에 저장하고 (파일경로, 제목)을 반환."""
    CACHE.mkdir(exist_ok=True)
    ytdlp = HERE / "bin" / "yt-dlp"
    if not ytdlp.exists():
        sys.exit(f"yt-dlp 가 없습니다. README 의 내려받기 명령을 참고하세요: {ytdlp}")

    # 오디오는 반드시 AAC 로 받는다. macOS 의 afconvert 는 WebM/Opus 를 못 읽는데
    # yt-dlp 기본값 bestaudio 는 webm/opus 를 고르기 때문이다.
    #
    # 그런데 오디오 전용 AAC 가 아예 없는 영상이 있다. 그런 영상은 opus 하나만 주고
    # AAC 는 '영상+소리가 합쳐진' 포맷 안에만 들어 있다. m4a 만 고집하면
    # "해당 포맷 없음"으로 멈춰버리므로, 마지막에 합쳐진 mp4 를 받는 길을 열어 둔다.
    # 화면은 안 쓰고 버리지만 몇 MB 더 받는 것뿐이라 문제되지 않는다.
    fmt = ("bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/"
           "best[acodec^=mp4a][ext=mp4]/best[ext=mp4]")

    ident = subprocess.run([str(ytdlp), "--no-warnings", "--print", "%(id)s\t%(title)s", url],
                           capture_output=True, text=True)
    if ident.returncode != 0:
        sys.exit(f"유튜브 정보를 읽지 못했습니다:\n{ident.stderr.strip()}")
    vid, _, title = ident.stdout.strip().partition("\t")

    out = CACHE / f"{vid}.m4a"
    if not out.exists():
        print(f"  내려받는 중: {title}")
        r = subprocess.run([str(ytdlp), "-f", fmt, "-o", str(out), "--no-warnings", url],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            sys.exit(f"음원 내려받기 실패:\n{r.stderr.strip()}")
    else:
        print(f"  캐시 사용: {title}")
    return out, title


def decode_wav(src: Path) -> tuple[np.ndarray, int]:
    """macOS 내장 afconvert 로 모노 16bit WAV 디코딩. ffmpeg 불필요."""
    # 원본 옆에 만들지 않는다. 남의 폴더(예: ~/Downloads)에 파일을 흘리게 된다.
    CACHE.mkdir(exist_ok=True)
    wav_path = CACHE / (src.stem + ".wav")
    if not wav_path.exists():
        r = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1",
                            str(src), str(wav_path)], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"afconvert 디코딩 실패:\n{r.stderr.strip()}")
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, sr


# ──────────────────────────────────────────────────────────────
# 2. 주파수 막대 16개 만들기
# ──────────────────────────────────────────────────────────────

def bar_levels(samples: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """프레임마다 막대 16개의 높이(0~255)와 비트 세기(0~255)를 미리 계산한다.

    비트 값은 보드의 배경이 음악에 반응하는 데 쓴다 (별이 반짝이고, 파문이 퍼진다).

    막대가 "음악에 맞춰 움직인다"고 느껴지려면 원시 진폭을 그대로 쓰면 안 된다.
    상용 음원은 이미 압축돼 있어서 진폭이 거의 안 움직이고, 밴드별 절대 에너지도
    20~30dB 씩 차이 난다(베이스는 늘 세고 고역은 늘 약하다).
    그래서 dB 변환 -> 밴드별 정규화 -> 대비 확장 순서를 거친다.
    """
    hop = int(round(sr / FPS))
    n_frames = max(1, (len(samples) - FFT_SIZE) // hop)
    window = np.hanning(FFT_SIZE).astype(np.float32)

    idx = np.arange(FFT_SIZE)[None, :] + hop * np.arange(n_frames)[:, None]
    spec = np.abs(np.fft.rfft(samples[idx] * window, axis=1)).astype(np.float32)
    freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / sr)

    # 사람 귀에 맞게 로그 간격으로 16칸을 나눈다
    edges = np.geomspace(40.0, 15000.0, BAR_COUNT + 1)
    power = np.zeros((n_frames, BAR_COUNT), dtype=np.float32)
    for i in range(BAR_COUNT):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if not mask.any():
            mask = np.zeros(freqs.shape, dtype=bool)
            mask[np.argmin(np.abs(freqs - edges[i]))] = True
        power[:, i] = (spec[:, mask] ** 2).mean(axis=1)

    db = 10.0 * np.log10(power + 1e-12)

    # 칸별 정규화: 곡 전체 기준 하위 15% ~ 상위 97% 를 0..1 로 편다.
    # 이게 없으면 왼쪽(저역) 막대만 늘 꽉 차고 오른쪽은 늘 바닥에 붙어 있는다.
    lo = np.percentile(db, 15.0, axis=0)
    hi = np.percentile(db, 97.0, axis=0)
    y = np.clip((db - lo) / np.maximum(hi - lo, 6.0), 0.0, 1.0)
    y = np.power(y, 1.25)          # 중간값을 위아래로 벌려 대비를 키운다

    # 비트는 저역의 '세기'가 아니라 '갑자기 커지는 순간'에서 뽑는다.
    #
    # 세기를 쓰면 안 된다. 요즘 음원은 저역이 곡 내내 차 있어서, 정규화하면 값이
    # 늘 높은 쪽에 붙는다. 실제로 세기로 뽑아봤더니 프레임의 78%가 임계를 넘어서
    # 배경 반응이 껐다 켰다가 아니라 그냥 계속 켜진 상태가 됐다.
    # 앞 프레임보다 커진 만큼만 보면(온셋) 북이 칠 때만 값이 솟는다.
    low = db[:, :3].mean(axis=1)
    rise = np.diff(low, prepend=low[0])
    rise = np.maximum(rise, 0.0)                 # 작아지는 건 비트가 아니다
    scale = max(np.percentile(rise, 97.0), 1e-6)
    beat = np.clip(rise / scale, 0.0, 1.0)

    # 타격 뒤 잠깐 남겨서 눈에 보이게 한다. 너무 길면 다시 상시 점등이 된다.
    beat_decay = math.exp(-1.0 / (FPS * 0.06))
    for t in range(1, n_frames):
        beat[t] = max(beat[t], beat[t - 1] * beat_decay)

    # 짧은 잔광으로 프레임 사이 떨림을 없앤다 (보드에서도 한 번 더 완만하게 만든다)
    decay = math.exp(-1.0 / (FPS * 0.09))
    for t in range(1, n_frames):
        np.maximum(y[t], y[t - 1] * decay, out=y[t])

    return (np.clip(y * 255.0, 0, 255).astype(np.uint8),
            np.clip(beat * 255.0, 0, 255).astype(np.uint8))


# ──────────────────────────────────────────────────────────────
# 3. 보드로 보내기
# ──────────────────────────────────────────────────────────────


class VideoClock:
    """QuickTime 재생 위치를 따라가는 시계.

    영상을 멈추면 보드도 멈추고, 뒤로 감으면 보드도 같이 뒤로 간다.
    다만 **매 프레임 물어볼 수는 없다** — 애플스크립트 한 번이 50~100ms 걸려서
    30fps(33ms)를 못 맞춘다. 그래서 0.25초마다 물어보고 그 사이는 자체 시계로
    이어 붙인다. 멈춤이나 되감기가 잡히면 그 순간 다시 맞춘다.
    """

    POLL_SEC = 0.25

    def __init__(self) -> None:
        self.media = 0.0          # 마지막으로 확인한 영상 위치(초)
        self.rate = 0.0           # 0이면 멈춘 상태, 1이면 재생 중
        self.wall = time.monotonic()
        self.alive = True
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._poll()
        self._thread.start()

    def _poll(self) -> None:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "QuickTime Player" to tell front document to '
             'return ((current time) as string) & "|" & ((rate) as string)'],
            capture_output=True, text=True)
        if r.returncode != 0:
            self.alive = False
            return
        try:
            t, rate = r.stdout.strip().split("|")
            self.media, self.rate = float(t), float(rate)
            self.wall = time.monotonic()
        except ValueError:
            pass

    def _loop(self) -> None:
        while self.alive:
            time.sleep(self.POLL_SEC)
            self._poll()

    def now(self) -> float:
        """지금 영상이 어디를 재생 중인지(초). 멈춰 있으면 그 자리에 머문다."""
        return self.media + (time.monotonic() - self.wall) * self.rate


def find_port() -> str | None:
    for pat in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def warn_if_port_busy(port: str) -> None:
    """다른 프로그램이 같은 포트를 잡고 있으면 화면이 엉킨다. 미리 알려준다."""
    r = subprocess.run(["lsof", port], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        print("  [경고] 다른 프로그램이 이미 보드를 쓰고 있습니다:")
        for line in r.stdout.strip().splitlines()[1:]:
            print("         " + line)
        print("         뮤직 LED 서버나 메트로놈이 켜져 있다면 먼저 끄세요.")


def frame_bars(levels: np.ndarray, beat: int) -> bytes:
    body = bytes([*(int(v) for v in levels), int(beat)])
    check = KIND_BARS
    for b in body:
        check ^= b
    return bytes([SYNC1, SYNC2, KIND_BARS]) + body + bytes([check])


def frame_lyric(text: str) -> bytes:
    # 보드가 받을 수 있는 길이까지만 자른다.
    #
    # 자르다가 UTF-8 글자 중간이 잘리면 보드가 깨진 글자를 그린다. 그래서 끝을 다듬어야
    # 하는데, **다듬는 규칙을 길이와 상관없이 항상 돌리면 안 된다.**
    # 한글 한 글자는 3바이트이고 뒤 두 바이트가 '이어짐' 표시라, 온전한 글자도
    # '잘린 조각'으로 오인해서 통째로 벗겨진다. 실제로 이 버그로 한글로 끝나는 가사가
    # 전부 마지막 한 글자씩 사라졌다 (영문으로 끝나면 안 걸려서 늦게 발견됐다).
    #
    # 한 번 디코딩했다 되돌리면 끝이 불완전할 때만 그 부분이 떨어져 나간다.
    body = text.encode("utf-8")[:96]
    body = body.decode("utf-8", "ignore").encode("utf-8")
    check = KIND_LYRIC ^ len(body)
    for b in body:
        check ^= b
    return bytes([SYNC1, SYNC2, KIND_LYRIC, len(body)]) + body + bytes([check])


def frame_pose(joints: list) -> bytes:
    """관절 9개의 화면 좌표(x, y)를 보드로 보낸다. 좌표 계산은 여기서 끝나 있다."""
    body = bytes(joints)
    check = KIND_POSE
    for b in body:
        check ^= b
    return bytes([SYNC1, SYNC2, KIND_POSE]) + body + bytes([check])


def load_motion(path: Path) -> list:
    """extract_motion.py 가 만든 .motion 을 읽는다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    frames = data["frames"]
    bad = [i for i, f in enumerate(frames) if len(f) != JOINT_COUNT * 2]
    if bad:
        sys.exit(f"동작 파일이 손상됐습니다: {len(bad)}개 프레임의 좌표 개수가 "
                 f"{JOINT_COUNT * 2}개가 아닙니다.")
    print(f"  동작: {path.name} — {len(frames)}프레임 "
          f"({len(frames)/data.get('fps', 30):.1f}초, 반복 재생)")
    return frames


BLOCKS = " ▁▂▃▄▅▆▇█"


def render_row(levels: np.ndarray, text: str, t: float) -> str:
    bars = "".join(BLOCKS[min(8, int(v) * 9 // 256)] for v in levels)
    return f"\r{t:6.1f}s |{bars}| {text[:22]:<22}"


def play(levels: np.ndarray, beats: np.ndarray,
         cues: list[tuple[float, str]], audio: Path, args,
         motion: list | None = None) -> None:
    ser = None
    if not args.preview:
        import serial
        port = args.port or find_port()
        if not port:
            sys.exit("보드를 찾을 수 없습니다. USB 연결을 확인하거나 --preview 를 쓰세요.")
        warn_if_port_busy(port)
        try:
            ser = serial.Serial(port, 115200, timeout=0)
        except serial.SerialException as e:
            # 분석까지 다 끝낸 뒤 여기서 막히면 원인을 알아보기 어렵다.
            # 실제로 한 번 겪었다: 재생 직전에 보드가 빠져 포트가 사라졌는데
            # 파이썬 역추적만 나와서 무슨 일인지 바로 안 읽혔다.
            if not Path(port).exists():
                sys.exit(f"\n  보드를 찾을 수 없습니다: {port}\n"
                         "  USB 케이블이 빠졌거나 보드가 다시 연결되며 포트 이름이 바뀐 것입니다.\n"
                         "  케이블을 다시 꽂고 `ls /dev/cu.usbmodem*` 로 확인한 뒤 다시 실행하세요.\n"
                         "  (--port 를 생략하면 알아서 찾습니다.)\n"
                         "  포트가 아예 안 보이면 시스템 설정 → 개인정보 보호 및 보안 →\n"
                         "  액세서리 연결 허용 을 확인하세요.")
            sys.exit(f"\n  포트를 열지 못했습니다: {port}\n  {e}")
        print(f"  시리얼 연결: {port}")
        time.sleep(0.4)          # CDC 포트가 열리고 안정될 때까지

    # 영상을 같이 틀 때는 소리도 영상에서 난다. 두 벌을 동시에 틀면 겹쳐 들린다.
    video = Path(args.video).expanduser().resolve() if args.video else None
    if video:
        if not video.exists():
            sys.exit(f"영상이 없습니다: {video}")
        # 앱을 띄우고 문서를 여는 데 시간이 걸린다. 미리 열어 두고 맨 앞으로 되감아
        # 멈춰 놓은 다음, 시작 순간에 재생 명령만 보내야 보드와 어긋나지 않는다.
        subprocess.run(["osascript",
                        "-e", f'tell application "QuickTime Player" to open POSIX file "{video}"',
                        "-e", 'tell application "QuickTime Player" to pause front document',
                        "-e", 'tell application "QuickTime Player" to set current time of '
                              'front document to 0',
                        "-e", 'tell application "QuickTime Player" to activate'],
                       capture_output=True)
        time.sleep(1.0)          # 창이 뜨고 첫 프레임이 그려질 때까지

    yt = None
    if args.youtube:
        import yt_sync
        yt = yt_sync.YouTubeClock(yt_sync.video_id(args.youtube))
        yt.start()
        print("  유튜브 플레이어를 띄웠습니다. 창에서 재생을 누르면 보드가 따라갑니다.")
        for _ in range(120):                 # 페이지가 첫 보고를 보낼 때까지 기다린다
            if yt.seen:
                break
            time.sleep(0.25)
        if not yt.seen:
            print("  [주의] 페이지에서 아무 보고가 없습니다. 크롬 창이 떴는지 확인하세요.")

    proc = None
    if not args.no_audio and not video and not yt:
        proc = subprocess.Popen(["afplay", str(audio)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # afplay 는 실제로 소리가 나기까지 조금 걸린다. 그만큼 화면을 늦춰 입을 맞춘다.
    if video:
        subprocess.Popen(["osascript", "-e",
                          'tell application "QuickTime Player" to play front document'])

    clock = yt
    if video:
        subprocess.Popen(["osascript", "-e",
                          'tell application "QuickTime Player" to play front document'])
        clock = VideoClock()
        clock.start()

    # 유튜브를 직접 틀 때 쓰는 카운트다운.
    # 임베드가 막힌 영상은 재생 위치를 물어볼 방법이 없어서, 사람이 맞추는 수밖에 없다.
    # 터미널을 안 보고도 맞출 수 있게 마지막 3초는 소리로 센다.
    if args.countdown > 0:
        n = int(args.countdown)
        print(f"  {n}초 뒤 시작합니다. 0 에 맞춰 유튜브 재생을 누르세요.")
        while n > 0:
            print(f"\r  {n} …   ", end="", flush=True)
            if n <= 3:
                subprocess.Popen(["say", "-r", "250", str(n)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            n -= 1
        print("\r  시작!        ")
        subprocess.Popen(["say", "-r", "250", "스타트"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.monotonic() + args.latency
    cue_i = 0
    shown = None
    last_index = -1

    try:
        if clock:
            # 영상을 따라가는 모드 — 프레임 번호를 영상 위치에서 뽑는다.
            while clock.alive:
                time.sleep(1.0 / FPS)
                i = int(clock.now() * FPS)
                if i < 0:
                    i = 0
                if i >= len(levels):
                    i = len(levels) - 1       # 끝까지 가면 마지막 화면을 유지한다
                if ser:
                    if motion:
                        ser.write(frame_pose(motion[i % len(motion)]))
                    else:
                        while cue_i < len(cues) and cues[cue_i][0] <= i / FPS:
                            cue_i += 1
                        text = cues[cue_i - 1][1] if cue_i > 0 else ""
                        if text != shown:
                            shown = text
                            ser.write(frame_lyric(text))
                    ser.write(frame_bars(levels[i], beats[i]))
                if (args.preview or args.verbose) and i != last_index:
                    sys.stdout.write(render_row(levels[i], shown or "", i / FPS))
                    sys.stdout.flush()
                last_index = i
            print("\n영상이 닫혔습니다.")
        else:
            for i, level in enumerate(levels):
                target = t0 + i / FPS
                delay = target - time.monotonic()
                if delay < -0.1:
                    continue          # 밀렸으면 이 프레임은 버린다. 소리와 어긋나는 것보다 낫다.
                if delay > 0:
                    time.sleep(delay)

                now = i / FPS + args.offset

                if motion:
                    # 댄스 모드 — 가사 대신 관절 좌표를 보낸다. 클립은 곡 내내 반복한다.
                    if ser:
                        ser.write(frame_pose(motion[i % len(motion)]))
                else:
                    # 지금 시각에 해당하는 가사 조각을 찾는다
                    while cue_i < len(cues) and cues[cue_i][0] <= now:
                        cue_i += 1
                    text = cues[cue_i - 1][1] if cue_i > 0 else ""

                    if text != shown:
                        shown = text
                        if ser:
                            ser.write(frame_lyric(text))
                if ser:
                    ser.write(frame_bars(level, beats[i]))

                if args.preview or args.verbose:
                    sys.stdout.write(render_row(level, shown or "", now))
                    sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n중단됨")
    finally:
        if yt:
            yt.stop()
        elif clock:
            clock.alive = False
        if ser:
            ser.write(frame_lyric(""))
            ser.write(frame_bars(np.zeros(BAR_COUNT, np.uint8), 0))
            ser.close()
        if proc:
            proc.terminate()
        if video:
            subprocess.run(["osascript", "-e",
                            'tell application "QuickTime Player" to pause front document'],
                           capture_output=True)
        print()


# ──────────────────────────────────────────────────────────────


def intro_cue(meta: dict, fallback_title: str) -> str | None:
    """인트로에 띄울 글. 첫 줄은 '제목', 둘째 줄은 가수.

    화면 폭(116픽셀)을 넘으면 보드에서 넘쳐버리므로 여기서 미리 검사한다.
    제목이 너무 길면 가수 줄을 빼고, 그래도 길면 아예 띄우지 않는다.
    """
    title = (meta.get("ti") or fallback_title or "").strip()
    artist = (meta.get("ar") or "").strip()
    if not title:
        return None

    first = f"'{title}'"
    if lrclib.text_width(first) > lrclib.MAX_TEXT_W:
        return None                       # 제목만으로도 안 들어간다 — 조용히 포기
    if artist and lrclib.text_width(artist) <= lrclib.MAX_TEXT_W:
        return f"{first}\n{artist}"
    return first


def main() -> None:
    p = argparse.ArgumentParser(description="유튜브/로컬 음원의 가사를 NU40DK OLED에 띄운다")
    p.add_argument("source", help="유튜브 링크 또는 로컬 오디오 파일 경로")
    p.add_argument("--lrc", help="가사 타이밍 파일(.lrc). --motion 을 쓰면 없어도 된다")
    p.add_argument("--motion", help="댄스 모드. extract_motion.py 가 만든 .motion 파일")
    p.add_argument("--preview", action="store_true", help="보드 없이 터미널에서만 확인")
    p.add_argument("--no-audio", action="store_true", help="소리 없이 화면만")
    p.add_argument("--port", help="시리얼 포트를 직접 지정")
    p.add_argument("--latency", type=float, default=0.25,
                   help="afplay 시작 지연 보정(초). 기본 0.25")
    p.add_argument("--offset", type=float, default=0.0,
                   help="가사만 앞뒤로 밀기(초). 양수면 가사가 빨라진다")
    p.add_argument("--radio", nargs="?", type=float, const=0.6, default=0.0,
                   metavar="세기",
                   help="옛날 라디오처럼 지직거리게 만든다. 0~1, 그냥 --radio 만 쓰면 0.6")
    p.add_argument("--crush", nargs="?", type=float, const=0.6, default=0.0,
                   metavar="세기",
                   help="소리를 더 깨지게. 0~1, 그냥 --crush 만 쓰면 0.6")
    p.add_argument("--water", nargs="?", type=float, const=0.6, default=0.0,
                   metavar="세기",
                   help="물 먹은 소리(웅웅거리는 저역통과). 0~1, 그냥 --water 만 쓰면 0.6")
    p.add_argument("--dropout", nargs="?", type=float, const=0.4, default=0.0,
                   metavar="세기",
                   help="소리가 툭툭 끊기는 효과. 기본은 꺼짐. --dropout 만 쓰면 0.4")
    p.add_argument("--video", help="로컬 영상을 화면에 같이 재생한다(소리는 영상 쪽에서 난다)")
    p.add_argument("--youtube", help="유튜브 영상을 띄우고 보드가 그 재생 위치를 따라간다. "
                                     "멈추면 같이 멈추고 되감으면 같이 되감긴다. "
                                     "단 임베드를 막아둔 영상에는 쓸 수 없다")
    p.add_argument("--countdown", type=float, default=0.0, metavar="초",
                   help="세고 나서 시작한다. 유튜브를 직접 틀 때 박자를 맞추는 용도. "
                        "마지막 3초는 소리로 세어준다")
    p.add_argument("--verbose", action="store_true", help="보드로 보내면서 터미널에도 표시")
    args = p.parse_args()

    motion = None
    if args.motion:
        mpath = Path(args.motion).expanduser()
        if not mpath.exists():
            sys.exit(f"동작 파일이 없습니다: {mpath}")
        motion = load_motion(mpath)

    entries: list = []
    meta: dict = {}
    cues: list = []
    if args.lrc:
        lrc_path = Path(args.lrc).expanduser()
        if not lrc_path.exists():
            sys.exit(f"가사 파일이 없습니다: {lrc_path}")
        entries, meta = lrclib.parse_lrc(lrc_path)
        if not entries:
            sys.exit(f"가사 파일에서 시간표([mm:ss.xx])를 찾지 못했습니다: {lrc_path}")
        cues = lrclib.build_cues(entries)
    elif not motion:
        sys.exit("--lrc 또는 --motion 중 하나는 있어야 합니다.")

    # 펌웨어 글꼴에 없는 글자는 화면에서 빈칸이 된다. 미리 알려준다.
    bad = lrclib.missing_glyphs(cues, lrclib.load_font_codes(FONT_HEADER)) if cues else []
    if bad:
        print(f"  [경고] 화면에 못 그리는 글자가 있습니다: {' '.join(bad)}")
        print("         (펌웨어 글꼴은 KS X 1001 한글 2350자 + 영문·숫자입니다)")

    src = args.source
    if src.startswith("http://") or src.startswith("https://"):
        audio, title = fetch_audio(src)
    else:
        audio = Path(src).expanduser()
        if not audio.exists():
            sys.exit(f"음원 파일이 없습니다: {audio}")
        title = audio.stem

    # 첫 가사가 나오기 전 빈 구간에 곡 정보를 띄운다. (댄스 모드에는 가사가 없다)
    intro = None if motion else intro_cue(meta, title)
    if intro and cues and cues[0][0] > 1.5:
        cues.insert(0, (0.0, intro))
        shown = intro.replace("\n", " / ")
        print(f"  인트로: {shown}  (첫 가사 {cues[1][0]:.1f}초까지)")

    print(f"  곡  : {meta.get('ti', title)}")
    if cues:
        print(f"  가사: {len(entries)}줄 → 화면 조각 {len(cues)}개 (한 줄이 길면 나눠서 이어 보여줍니다)")

    samples, sr = decode_wav(audio)
    print(f"  분석: {len(samples)/sr:.0f}초 …")

    # 재생은 **디코딩해 둔 WAV** 로 한다.
    # afplay 는 오디오 파일 재생기라 영상 트랙이 든 mp4 를 못 읽는다.
    # 안무 영상을 음원으로 쓸 때 소리가 아예 안 나던 원인이 이것이었다.
    audio = CACHE / (audio.stem + ".wav")

    # 막대와 비트는 **원음**으로 계산한다.
    # 라디오 효과는 저역·고역을 잘라내므로, 가공한 소리로 분석하면
    # 양끝 막대가 늘 바닥에 붙고 비트도 죽는다. 효과는 귀로 듣는 쪽에만 건다.
    levels, beats = bar_levels(samples, sr)

    if args.radio > 0 or args.crush > 0 or args.water > 0 or args.dropout > 0:
        import radio_fx
        print(f"  소리 가공 중 (라디오 {args.radio:.2f} / 깨짐 {args.crush:.2f} "
              f"/ 물먹음 {args.water:.2f} / 끊김 {args.dropout:.2f}) …")
        audio = radio_fx.write_wav(
            CACHE / f"{audio.stem}_fx.wav",
            radio_fx.apply(samples, sr, args.radio, args.crush, args.water,
                           args.dropout), sr)
    print(f"  준비 완료 — {len(levels)}프레임")
    print("  배경은 보드 버튼 1~4로 바꿉니다 (별밤 / 파문 / 카세트 / 스카이라인)\n")

    play(levels, beats, cues, audio, args, motion)


if __name__ == "__main__":
    main()
