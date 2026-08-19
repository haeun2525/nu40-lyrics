#!/usr/bin/env python3
"""
radio_fx.py — 음원을 옛날 라디오에서 나오는 것처럼 바꾼다.

"지직거리는 아날로그 느낌"은 잡음 하나로는 안 나온다. 네 가지가 같이 있어야 한다.
하나만 넣으면 그냥 시끄러운 원곡처럼 들린다.

  1) 대역 제한  — 라디오 소리의 정체는 사실 이것이다. 저역과 고역을 잘라내면
                  스피커가 작아진 것처럼 들린다. 나머지 효과는 그 위의 장식이다.
  2) 잡음       — 쉭 하는 바닥 잡음(hiss)과 툭툭 튀는 소리(crackle). 둘은 다른 소리다.
  3) 회전 흔들림 — 테이프·판이 고르게 안 돌아 생기는 미세한 음정 흔들림(wow/flutter).
                  이게 있어야 '아날로그 장치'로 들린다. 없으면 그냥 필터 먹인 디지털 음원이다.
  4) 전파 세기  — 아주 느린 음량 출렁임. 라디오가 신호를 잡았다 놓쳤다 하는 느낌.

넘파이만 쓴다. 외부 라이브러리를 더 깔지 않는다.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np


def _bandpass(x: np.ndarray, sr: int, low: float, high: float) -> np.ndarray:
    """주파수 축에서 직접 잘라낸다.

    칼같이 자르면 '삐-' 하는 쇳소리가 남는다. 가장자리를 완만하게 기울여서
    옛날 스피커처럼 부드럽게 사라지게 한다.
    """
    n = len(x)
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)

    mask = np.ones(len(freqs), dtype=np.float32)
    # 저역: low 아래로 한 옥타브에 걸쳐 서서히 줄인다
    lo_edge = freqs < low
    mask[lo_edge] = np.clip(freqs[lo_edge] / max(low, 1.0), 0.0, 1.0) ** 3
    # 고역: high 위로 서서히 줄인다
    hi_edge = freqs > high
    mask[hi_edge] = np.clip(high / np.maximum(freqs[hi_edge], 1.0), 0.0, 1.0) ** 3

    return np.fft.irfft(spec * mask, n=n).astype(np.float32)


def _wobble(x: np.ndarray, sr: int, wow: float, flutter: float) -> np.ndarray:
    """읽는 위치를 아주 조금씩 밀었다 당겼다 해서 음정을 흔든다.

    wow 는 느린 흔들림(판이 살짝 휘어 있는 느낌), flutter 는 빠른 떨림이다.
    값이 크면 취한 것처럼 들리니 샘플 수십 개 수준으로만 민다.
    """
    if wow <= 0 and flutter <= 0:
        return x
    n = len(x)
    t = np.arange(n, dtype=np.float32)
    shift = (wow * np.sin(2 * np.pi * 0.55 * t / sr)
             + flutter * np.sin(2 * np.pi * 7.3 * t / sr + 1.3))
    return np.interp(t + shift, t, x).astype(np.float32)


def _crackle(n: int, sr: int, rate: float, level: float, rng) -> np.ndarray:
    """툭, 툭 튀는 소리. 짧게 솟았다가 금방 잦아든다."""
    out = np.zeros(n, dtype=np.float32)
    count = int(rate * n / sr)
    if count <= 0:
        return out

    for pos in rng.integers(0, n, size=count):
        length = int(rng.integers(int(sr * 0.001), int(sr * 0.006)))
        end = min(n, pos + length)
        if end <= pos:
            continue
        env = np.exp(-np.linspace(0, 6, end - pos, dtype=np.float32))
        amp = level * float(rng.uniform(0.3, 1.0)) * (1 if rng.random() > 0.5 else -1)
        out[pos:end] += amp * env * rng.standard_normal(end - pos).astype(np.float32)
    return out



def _moving_lowpass(x: np.ndarray, sr: int, base_hz: float,
                    depth_oct: float, rate_hz: float, resonance: float) -> np.ndarray:
    """차단 주파수가 천천히 오르내리는 저역통과. '물 먹은 소리'의 핵심이다.

    고역을 그냥 깎기만 하면 '이불 덮은 소리'가 된다. 물속처럼 들리려면
    **깎는 지점이 계속 움직여야** 한다. 그래서 짧은 토막으로 잘라 토막마다
    다른 차단 주파수를 걸고 다시 이어 붙인다(겹쳐 더하기).
    차단 지점을 살짝 부풀리면(resonance) 웅웅거리는 울림이 생긴다.
    """
    N, hop = 1024, 512
    win = np.hanning(N).astype(np.float32)
    n = len(x)
    xp = np.concatenate([x, np.zeros(N * 2, np.float32)])
    n_frames = 1 + (len(xp) - N) // hop

    out = np.zeros(len(xp), np.float32)
    norm = np.zeros(len(xp), np.float32)
    win_sq = (win * win).astype(np.float32)
    freqs = np.fft.rfftfreq(N, 1.0 / sr).astype(np.float32)

    CHUNK = 512                      # 한 번에 처리할 토막 수. 메모리를 묶어 둔다.
    for start in range(0, n_frames, CHUNK):
        end = min(n_frames, start + CHUNK)
        idx = np.arange(N)[None, :] + hop * np.arange(start, end)[:, None]
        spec = np.fft.rfft(xp[idx] * win, axis=1)

        t = (np.arange(start, end) * hop + N * 0.5) / sr
        cut = base_hz * (2.0 ** (depth_oct * np.sin(2 * np.pi * rate_hz * t)))
        ratio = freqs[None, :] / cut[:, None]

        mask = 1.0 / (1.0 + ratio ** 4)
        if resonance > 0:
            mask = mask + resonance * np.exp(-((ratio - 1.0) / 0.35) ** 2)
        spec *= mask

        rec = np.fft.irfft(spec, n=N, axis=1).astype(np.float32) * win
        for k in range(end - start):
            o = (start + k) * hop
            out[o:o + N] += rec[k]
            norm[o:o + N] += win_sq

    np.maximum(norm, 1e-6, out=norm)
    return (out / norm)[:n].astype(np.float32)


def _crush(x: np.ndarray, sr: int, amount: float, rng,
           dropout: float = 0.0) -> np.ndarray:
    """깨지는 소리.

    지글거림(계단 줄이기·샘플 솎기)과 끊김은 **다른 노브로 나눠 둔다.**
    끊김은 음악이 잠깐씩 사라지는 것이라, 지글거림은 마음에 들어도 끊김만
    거슬리는 경우가 많다. 하나로 묶여 있으면 그때 둘 다 포기해야 한다.
    """
    y = x

    # 1) 계단 수를 줄인다(비트 깎기). 8비트 → 4비트로 갈수록 지글거린다.
    bits = 9.0 - 5.0 * amount
    levels = float(2 ** bits)
    y = np.round(y * levels) / levels

    # 2) 샘플을 솎아 같은 값을 여러 번 쓴다. 원래 없던 높은 소리가 끼어들어(에일리어싱)
    #    싸구려 기계로 재생한 것 같은 거친 맛이 난다.
    hold = 1 + int(round(7 * amount))
    if hold > 1:
        n = len(y)
        y = y[(np.arange(n) // hold) * hold]

    # 3) 신호가 잠깐씩 끊긴다. 전파가 약해 툭툭 끊기는 느낌. 기본은 꺼져 있다.
    if dropout <= 0:
        return y.astype(np.float32)

    n = len(y)
    env = np.ones(n, dtype=np.float32)
    count = int(0.8 * dropout * n / sr)
    for pos in rng.integers(0, n, size=max(0, count)):
        length = int(rng.integers(int(sr * 0.02), int(sr * 0.13)))
        end = min(n, pos + length)
        env[pos:end] *= float(rng.uniform(0.05, 0.35))
    return (y * env).astype(np.float32)


def apply(x: np.ndarray, sr: int, strength: float = 0.6,
          crush: float = 0.0, water: float = 0.0,
          dropout: float = 0.0) -> np.ndarray:
    """strength 라디오 느낌, crush 지글거림, water 물 먹은 정도, dropout 끊김.

    모두 0~1. dropout 은 기본으로 꺼 둔다 — 음악이 잠깐씩 사라지는 효과라
    한 번 거슬리기 시작하면 계속 거슬린다. 원할 때만 켜는 쪽이 맞다.
    """
    s = float(np.clip(strength, 0.0, 1.0))
    cr = float(np.clip(crush, 0.0, 1.0))
    wa = float(np.clip(water, 0.0, 1.0))
    if s <= 0 and cr <= 0 and wa <= 0:
        return x

    rng = np.random.default_rng(20260819)     # 돌릴 때마다 같은 소리가 나게 고정
    n = len(x)
    y = x.astype(np.float32).copy()

    # 1) 회전 흔들림 — 음정을 미세하게 흔든다
    y = _wobble(y, sr, wow=45.0 * s, flutter=7.0 * s)

    # 2) 잡음을 먼저 다 얹는다. 쉭 하는 바닥 잡음과 툭툭 튀는 소리는 다른 소리다.
    y += (0.006 * s) * rng.standard_normal(n).astype(np.float32)
    y += _crackle(n, sr, 16.0 * s, 0.22 * s, rng)

    # 3) 대역 제한은 **맨 마지막에** 건다.
    #    실제 라디오는 잡음도 같은 스피커를 통과한다. 잡음을 필터 뒤에 얹으면
    #    광대역 성분이 다시 들어와서 대역 제한이 무의미해진다 (처음에 이렇게 만들었다가
    #    측정해보니 고역이 원음보다 오히려 늘어 있었다).
    #    상한은 곱셈으로 좁힌다. 빼기로 하면 0.6 정도에서도 8kHz라 사실상 안 잘린다.
    low = 60.0 + 340.0 * s                      # s=0.6 → 264Hz
    high = 16000.0 * (2800.0 / 16000.0) ** s    # s=0.6 → 약 5.4kHz
    y = _bandpass(y, sr, low, high)

    # 3-2) 물 먹은 소리 — 차단 주파수가 흔들리는 저역통과.
    #      대역 제한 뒤에 걸어야 '좁은 라디오' 위에 '물속'이 얹힌다.
    if wa > 0:
        y = _moving_lowpass(y, sr,
                            base_hz=1500.0 - 1000.0 * wa,   # wa=0.6 → 900Hz
                            depth_oct=0.40 * wa,            # 위아래로 흔들리는 폭
                            rate_hz=0.23,                   # 천천히 일렁인다
                            resonance=0.7 * wa)             # 웅웅거리는 울림

    # 3-3) 깨지는 소리 — 계단 줄이기·샘플 솎기·끊김
    if cr > 0 or dropout > 0:
        y = _crush(y, sr, cr, rng, dropout=float(np.clip(dropout, 0.0, 1.0)))

    # 4) 전파 세기가 흔들리는 느낌 — 아주 느린 음량 출렁임
    t = np.arange(n, dtype=np.float32)
    drift = 1.0 - (0.14 * s) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * t / sr))
    y *= drift

    # 5) 작은 스피커가 힘들어하는 느낌 — 큰 소리를 부드럽게 눌러준다
    drive = 1.0 + 3.0 * s
    y = np.tanh(drive * y) / math.tanh(drive)

    # 소리 크기를 원곡과 비슷하게 되돌린다. 안 하면 효과를 켤 때마다 볼륨이 뛴다.
    peak = float(np.max(np.abs(y))) or 1.0
    ref = float(np.max(np.abs(x))) or 1.0
    return (y * (min(ref, 0.98) / peak)).astype(np.float32)


def write_wav(path: Path, x: np.ndarray, sr: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(x, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((data * 32000).astype("<i2").tobytes())
    return path
