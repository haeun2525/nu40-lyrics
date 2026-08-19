#!/usr/bin/env python3
"""
extract_motion.py — 안무 영상에서 상반신 동작을 뽑아 보드가 그릴 좌표로 바꾼다.

영상 → MediaPipe 자세 추정 → 관절 9개 → 화면 좌표(.motion) → 재생 중 보드로 전송.
무거운 계산은 여기서 한 번만 하고, 재생할 때는 좌표만 흘려보낸다.

**전신이 아니라 상반신만 쓴다.** 가슴 튕기기 같은 숏폼 안무는 다리가 화면에 안 들어와서
발목·무릎 신뢰도가 바닥이다(실측 발목 0%). 억지로 다리를 그리면 없는 데이터를 지어내는 꼴이다.
머리·어깨·팔꿈치·손목·골반 9개면 저스트댄스풍 형체로 충분하다.

사용:
  ./extract_motion.py 영상.mp4 --out ../motion/춤.motion --start 12 --dur 10
  ./extract_motion.py 영상.mp4 --out ... --auto        # 움직임이 가장 큰 구간을 알아서 고름
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# MediaPipe 관절 번호. 우리가 쓰는 9개만.
NOSE = 0
SH_L, SH_R = 11, 12
EL_L, EL_R = 13, 14
WR_L, WR_R = 15, 16
HIP_L, HIP_R = 23, 24
USED = [NOSE, SH_L, SH_R, EL_L, EL_R, WR_L, WR_R, HIP_L, HIP_R]

# 보드 화면에서 형체가 쓰는 자리.
# 막대를 왼쪽 세로줄로 옮겨서(x0~21) 형체가 화면 높이를 거의 다 쓴다.
FIG_TOP, FIG_BOTTOM = 1, 58
FIG_LEFT = 26
SCREEN_W = 128

# 어깨 너비를 화면에서 몇 픽셀로 볼 것인가. 형체 전체 크기를 정하는 값이다.
# 막대를 왼쪽으로 옮겨 형체 자리가 y1~58 로 넓어졌다. 26픽셀이면 머리끝~허리가
# 약 52픽셀이라 그 안에 들어간다.
SHOULDER_PX = 26.0


def landmarks(video: Path, start: float, dur: float):
    """구간의 프레임마다 관절 좌표(정규화 0~1)를 뽑는다. 못 잡은 프레임은 None."""
    import mediapipe as mp

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    first = int(start * fps)
    WARMUP = 8                      # 추적기가 자리를 잡는 데 필요한 프레임 수
    last = total if dur <= 0 else min(total, int((start + dur) * fps))

    out = []
    with mp.solutions.pose.Pose(static_image_mode=False, model_complexity=1,
                                min_detection_confidence=0.5,
                                min_tracking_confidence=0.5) as pose:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok or i >= last:
                break
            if i >= first - WARMUP:
                r = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if r.pose_landmarks:
                    lm = r.pose_landmarks.landmark
                    got = np.array([[lm[j].x, lm[j].y, lm[j].visibility] for j in USED],
                                   dtype=np.float32)
                else:
                    got = None
                # 워밍업 구간은 추적기가 자리를 잡는 동안이라 버린다.
                # 첫 프레임은 관절이 덜 잡혀서 척추가 접힌 채로 나온다.
                if i >= first:
                    out.append(got)
            i += 1
    cap.release()
    return out, fps


def fill_gaps(frames: list) -> list:
    """못 잡거나 흐릿한 프레임은 앞뒤 값으로 메운다.

    한 프레임만 비어도 형체가 사라져 보인다. 몸통(어깨·골반)이 흐릿한 프레임도
    버린다 — 그런 프레임은 관절이 엉뚱한 데 찍혀서 형체가 일그러진다.
    """
    TORSO = [1, 2, 7, 8]                     # 어깨L/R, 골반L/R
    for i, f in enumerate(frames):
        if f is not None and f.shape[1] > 2 and min(f[j][2] for j in TORSO) < 0.6:
            frames[i] = None
    good = [i for i, f in enumerate(frames) if f is not None]
    if not good:
        sys.exit("이 구간에서 사람을 한 번도 찾지 못했습니다. 다른 구간을 골라보세요.")
    for i, f in enumerate(frames):
        if f is None:
            near = min(good, key=lambda g: abs(g - i))
            frames[i] = frames[near].copy()
    return frames


def smooth(seq: np.ndarray, window: int) -> np.ndarray:
    """시간 축으로 이동평균. 손목처럼 흔들리는 관절의 떨림을 줄인다."""
    if window <= 1:
        return seq
    pad = window // 2
    padded = np.concatenate([seq[:1]] * pad + [seq] + [seq[-1:]] * pad, axis=0)
    kernel = np.ones(window, dtype=np.float32) / window
    out = np.empty_like(seq)
    for j in range(seq.shape[1]):
        for c in range(seq.shape[2]):
            out[:, j, c] = np.convolve(padded[:, j, c], kernel, mode="valid")[:len(seq)]
    return out



def rigidify(rel: np.ndarray) -> np.ndarray:
    """뼈 길이를 구간 중앙값으로 고정한다. rel 은 골반 한가운데를 원점으로 한 화면 좌표.

    자세 추정은 프레임마다 팔 길이와 목 길이가 조금씩 다르게 나온다. 큰 화면에서는
    티가 안 나지만 128x64 에서는 형체가 흐물거리고, 관절이 잘못 잡힌 프레임에서는
    아예 사람으로 안 보인다. 방향은 관측값을 그대로 쓰고 **길이만** 고정하면
    춤 동작(각도 변화)은 전부 살아 있으면서 형체가 단단해진다.

    **척추만 예외로 길이 변화를 허용한다.** 가슴 튕기기는 어깨와 골반 사이가
    늘었다 줄었다 하는 동작이라, 척추까지 고정하면 표현하려는 움직임이 사라진다.
    대신 너무 접히거나 늘어나지 않게 묶어만 둔다.
    """
    HEAD, SH_L, SH_R, EL_L, EL_R, WR_L, WR_R, HIP_L, HIP_R = range(9)

    def unit(v):
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-6)

    def length(a, b):
        return np.linalg.norm(rel[:, a] - rel[:, b], axis=1)

    hip_mid = (rel[:, HIP_L] + rel[:, HIP_R]) * 0.5
    sh_mid = (rel[:, SH_L] + rel[:, SH_R]) * 0.5

    L = {name: float(np.median(v)) for name, v in {
        "hip_half": length(HIP_L, HIP_R) / 2,
        "sh_half": length(SH_L, SH_R) / 2,
        "neck": np.linalg.norm(rel[:, HEAD] - sh_mid, axis=1),
        "upper_l": length(SH_L, EL_L), "upper_r": length(SH_R, EL_R),
        "fore_l": length(EL_L, WR_L), "fore_r": length(EL_R, WR_R),
    }.items()}

    spine_len = np.linalg.norm(sh_mid - hip_mid, axis=1)
    spine_med = float(np.median(spine_len))
    # 0.55배까지 허용했더니 자세 추정이 덜 잡힌 프레임에서 척추가 절반으로 접혀,
    # 어깨선과 골반선이 붙어 상자처럼 보였다. 실측 분포상 실제 튕김은 0.75~1.15배
    # 안에서 일어나므로 그 밖은 잘라도 동작이 손상되지 않는다.
    spine_len = np.clip(spine_len, spine_med * 0.75, spine_med * 1.25)

    out = np.zeros_like(rel)
    hip_dir = unit(rel[:, HIP_R] - rel[:, HIP_L])
    out[:, HIP_L] = hip_mid - hip_dir * L["hip_half"]
    out[:, HIP_R] = hip_mid + hip_dir * L["hip_half"]

    new_sh_mid = hip_mid + unit(sh_mid - hip_mid) * spine_len[:, None]
    sh_dir = unit(rel[:, SH_R] - rel[:, SH_L])
    out[:, SH_L] = new_sh_mid - sh_dir * L["sh_half"]
    out[:, SH_R] = new_sh_mid + sh_dir * L["sh_half"]
    out[:, HEAD] = new_sh_mid + unit(rel[:, HEAD] - sh_mid) * L["neck"]

    for sh, el, wr, up, fo in ((SH_L, EL_L, WR_L, "upper_l", "fore_l"),
                               (SH_R, EL_R, WR_R, "upper_r", "fore_r")):
        out[:, el] = out[:, sh] + unit(rel[:, el] - rel[:, sh]) * L[up]
        out[:, wr] = out[:, el] + unit(rel[:, wr] - rel[:, el]) * L[fo]
    return out


def to_screen(seq: np.ndarray, aspect: float) -> np.ndarray:
    """정규화 좌표를 보드 화면 좌표로 바꾼다.

    **크기와 위치를 프레임마다 다시 맞춘다.** 구간 전체에 하나의 기준을 쓰면 안 된다.
    숏폼 안무 영상은 컷이 여러 개라 장면이 바뀔 때마다 사람 크기와 위치가 확 달라진다.
    한 번만 정한 기준으로는 컷이 바뀌는 순간 형체가 거대해지거나 화면 밖으로 나간다.

    기준을 **어깨 너비**로 삼는 게 요점이다. 가슴 튕기기는 어깨와 골반 사이 거리가
    변하는 동작이라, 어깨 너비로 맞추면 카메라 거리만 지워지고 동작은 그대로 남는다.
    (어깨~골반 거리로 맞추면 표현하려는 움직임이 통째로 사라진다.)

    골반을 화면의 고정된 자리에 못 박는다. 몸이 좌우로 걸어다니는 건 사라지지만,
    작은 화면에서는 그게 오히려 형체가 안정돼 보인다.
    """
    xy = seq.copy()
    xy[:, :, 1] *= aspect          # y 를 영상 비율만큼 늘려야 사람이 납작해지지 않는다

    # 어깨 너비를 그대로 쓰면 안 된다. 몸이 옆으로 돌면 2차원에서 어깨가 납작해 보여서
    # 나눗셈 결과가 폭발하고 형체가 화면을 뚫고 나간다. 실제로 그렇게 깨졌다.
    # 그래서 **주변 1.5초의 중앙값**을 쓰고, 구간 전체 중앙값에서 너무 벗어나면 묶는다.
    sh_w = np.maximum(np.linalg.norm(xy[:, 1] - xy[:, 2], axis=1), 1e-6)

    win = 45
    pad = win // 2
    padded = np.concatenate([sh_w[:1]] * pad + [sh_w] + [sh_w[-1:]] * pad)
    rolling = np.array([np.median(padded[i:i + win]) for i in range(len(sh_w))])

    base = float(np.median(sh_w))
    rolling = np.clip(rolling, base * 0.75, base * 1.35)
    scale = SHOULDER_PX / rolling

    hip_mid = (xy[:, 7] + xy[:, 8]) * 0.5
    rel = (xy - hip_mid[:, None, :]) * scale[:, None, None]
    rel = rigidify(rel)

    HIP_X = (FIG_LEFT + SCREEN_W) / 2                # 막대를 뺀 자리의 한가운데
    HIP_Y = FIG_BOTTOM - 4
    out = np.empty_like(rel)
    out[:, :, 0] = rel[:, :, 0] + HIP_X
    out[:, :, 1] = rel[:, :, 1] + HIP_Y

    # 그래도 팔을 크게 뻗으면 넘칠 수 있다. 넘친 만큼만 통째로 민다(잘라내지 않는다).
    for t in range(len(out)):
        x, y = out[t, :, 0], out[t, :, 1]
        x += max(0.0, FIG_LEFT - x.min()) - max(0.0, x.max() - (SCREEN_W - 2))
        y += max(0.0, FIG_TOP - y.min()) - max(0.0, y.max() - FIG_BOTTOM)

    np.clip(out[:, :, 0], FIG_LEFT, SCREEN_W - 1, out=out[:, :, 0])
    np.clip(out[:, :, 1], 0, FIG_BOTTOM, out=out[:, :, 1])
    return np.round(out).astype(np.uint8)


def liveliest(video: Path, dur: float) -> float:
    """어깨가 가장 크게 움직이는 구간의 시작 시각을 찾는다."""
    frames, fps = landmarks(video, 0.0, 0.0)
    frames = fill_gaps(frames)
    seq = np.stack(frames)
    sh_mid = (seq[:, 1] + seq[:, 2]) * 0.5
    move = np.abs(np.diff(sh_mid[:, 1], prepend=sh_mid[0, 1]))     # 위아래 움직임
    win = int(dur * fps)
    if win >= len(move):
        return 0.0
    total = np.convolve(move, np.ones(win), mode="valid")
    return float(np.argmax(total) / fps)


def main() -> None:
    p = argparse.ArgumentParser(description="안무 영상에서 상반신 동작을 뽑는다")
    p.add_argument("video", help="영상 파일 경로")
    p.add_argument("--out", required=True, help="만들 .motion 경로")
    p.add_argument("--start", type=float, default=0.0, help="시작 시각(초)")
    p.add_argument("--dur", type=float, default=10.0, help="길이(초)")
    p.add_argument("--auto", action="store_true", help="움직임이 가장 큰 구간을 알아서 고른다")
    p.add_argument("--smooth", type=int, default=3, help="떨림 줄이기 창 크기(프레임)")
    args = p.parse_args()

    video = Path(args.video).expanduser()
    if not video.exists():
        sys.exit(f"영상이 없습니다: {video}")

    cap = cv2.VideoCapture(str(video))
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1
    cap.release()
    aspect = h / w

    start = args.start
    if args.auto:
        print("  움직임이 큰 구간을 찾는 중 …")
        start = liveliest(video, args.dur)
        print(f"  고른 구간: {start:.1f}초부터 {args.dur:.0f}초")

    frames, fps = landmarks(video, start, args.dur)
    caught = sum(1 for f in frames if f is not None)
    frames = fill_gaps(frames)

    # 신뢰도는 흐릿한 프레임을 걸러내는 데까지만 쓰고 여기서 떼어낸다.
    # 좌표 계산과 저장은 전부 (x, y) 두 개 기준이라, 세 번째 값이 남아 있으면
    # 어깨 너비도 골반 위치도 엉뚱하게 계산되고 저장 개수까지 어긋난다.
    seq = np.stack(frames)[:, :, :2]
    seq = smooth(seq, args.smooth)
    px = to_screen(seq, aspect)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": video.name,
        "start": round(start, 2),
        "fps": round(fps, 3),
        "joints": ["머리", "어깨L", "어깨R", "팔꿈치L", "팔꿈치R",
                   "손목L", "손목R", "골반L", "골반R"],
        "frames": px.reshape(len(px), -1).tolist(),
    }, ensure_ascii=False), encoding="utf-8")

    print(f"  {len(px)}프레임 ({len(px)/fps:.1f}초), 사람 검출 {100*caught/max(len(frames),1):.0f}%")
    print(f"  저장: {out}  ({out.stat().st_size/1024:.0f}KB)")
    print(f"  확인: python3 tools/preview/render_motion.py {out}")


if __name__ == "__main__":
    main()
