#!/usr/bin/env python3
"""
oled.py — SSD1306 128x64 화면을 PC에서 픽셀 단위로 똑같이 그리는 작은 엔진

펌웨어가 쓰는 글꼴을 그대로 읽어오기 때문에, 여기서 보이는 그림이 실제 화면과 같다.
  - 영문·숫자 : 아두이노에 설치된 Adafruit GFX 기본 글꼴 (glcdfont.c, 5x7)
  - 한글       : 갈무리9 픽셀 글꼴 (9x9) — 주식 티커 펌웨어에 심은 것과 같은 글꼴
"""

import math
import re
import struct
import zlib
from pathlib import Path

W, H = 128, 64
HANGUL_ADVANCE = 10     # 한글 글자 간격 (1배 기준)
ASCII_ADVANCE = 6       # 영문·숫자 글자 간격 (1배 기준)

GFX_FONT_PATHS = [
    Path.home() / "Documents/Arduino/libraries/Adafruit_GFX_Library/glcdfont.c",
    Path.home() / "Library/Arduino15/libraries/TFT/src/utility/glcdfont.c",
]
GALMURI_PATH = Path.home() / "Library/Fonts/Galmuri9Bitmap-Regular-2.40.3.ttf"
GALMURI_EM = 12         # 이 글꼴은 크기 12에서만 열린다. 실제 글자 높이는 9픽셀.


def load_gfx_font():
    for p in GFX_FONT_PATHS:
        if p.exists():
            text = p.read_text(errors="replace")
            body = text[text.index("{"): text.rindex("}")]
            data = [int(x, 16) for x in re.findall(r"0[xX]([0-9a-fA-F]{2})", body)]
            if len(data) >= 256 * 5:
                return data[: 256 * 5]
    raise SystemExit("[오류] Adafruit GFX 글꼴 파일(glcdfont.c)을 찾지 못했습니다.")


FONT = load_gfx_font()
_hangul_cache = {}


def is_hangul(ch):
    return "가" <= ch <= "힣"


def hangul_dots(ch):
    """한글 한 글자를 '켜진 픽셀 좌표' 목록으로. (9x9 안쪽)"""
    if ch in _hangul_cache:
        return _hangul_cache[ch]
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(GALMURI_PATH), GALMURI_EM)
    img = Image.new("1", (48, 48), 0)
    ImageDraw.Draw(img).text((12, 12), ch, font=font, fill=1)
    box = img.getbbox()
    dots = []
    if box:
        crop = img.crop(box)
        pix = crop.load()
        w, h = crop.size
        dots = [(x, y) for y in range(min(h, 9)) for x in range(min(w, 9)) if pix[x, y]]
    _hangul_cache[ch] = dots
    return dots


def measure(text, size=1):
    """그렸을 때 가로로 몇 픽셀을 차지하는지."""
    return sum((HANGUL_ADVANCE if is_hangul(c) else ASCII_ADVANCE) * size for c in text)


class Canvas:
    def __init__(self):
        self.px = [[0] * W for _ in range(H)]

    # ── 기본 도형 (Adafruit_GFX 와 같은 규칙) ──
    def pixel(self, x, y, on=1):
        x, y = int(x), int(y)
        if 0 <= x < W and 0 <= y < H:
            self.px[y][x] = on

    def line(self, x0, y0, x1, y1, on=1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        steep = abs(y1 - y0) > abs(x1 - x0)
        if steep:
            x0, y0, x1, y1 = y0, x0, y1, x1
        if x0 > x1:
            x0, x1, y0, y1 = x1, x0, y1, y0
        dx, dy = x1 - x0, abs(y1 - y0)
        err = dx // 2
        ystep = 1 if y0 < y1 else -1
        y = y0
        for x in range(x0, x1 + 1):
            self.pixel(y, x, on) if steep else self.pixel(x, y, on)
            err -= dy
            if err < 0:
                y += ystep
                err += dx

    def rect(self, x, y, w, h, fill=False, on=1):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.pixel(xx, yy, on)
        else:
            for xx in range(x, x + w):
                self.pixel(xx, y, on); self.pixel(xx, y + h - 1, on)
            for yy in range(y, y + h):
                self.pixel(x, yy, on); self.pixel(x + w - 1, yy, on)

    def round_rect(self, x, y, w, h, r=3, fill=False, on=1):
        """Adafruit_GFX 의 drawRoundRect / fillRoundRect 와 같은 모양."""
        x, y, w, h = int(x), int(y), int(w), int(h)
        if fill:
            self.rect(x + r, y, w - 2 * r, h, True, on)
            self._circle_quads(x + r, y + r, w - 2 * r - 1, h - 2 * r - 1, r, True, on)
        else:
            for xx in range(x + r, x + w - r):
                self.pixel(xx, y, on); self.pixel(xx, y + h - 1, on)
            for yy in range(y + r, y + h - r):
                self.pixel(x, yy, on); self.pixel(x + w - 1, yy, on)
            self._circle_quads(x + r, y + r, w - 2 * r - 1, h - 2 * r - 1, r, False, on)

    def _circle_quads(self, cx, cy, dw, dh, r, fill, on):
        f, ddF_x, ddF_y = 1 - r, 1, -2 * r
        xx, yy = 0, r
        while xx < yy:
            if f >= 0:
                yy -= 1; ddF_y += 2; f += ddF_y
            xx += 1; ddF_x += 2; f += ddF_x
            if fill:
                for k in range(-yy, yy + 1):
                    self.pixel(cx + xx + dw, cy + k + (dh if k > 0 else 0), on)
                    self.pixel(cx - xx, cy + k + (dh if k > 0 else 0), on)
                for k in range(-xx, xx + 1):
                    self.pixel(cx + yy + dw, cy + k + (dh if k > 0 else 0), on)
                    self.pixel(cx - yy, cy + k + (dh if k > 0 else 0), on)
            else:
                self.pixel(cx + xx + dw, cy + yy + dh, on); self.pixel(cx + yy + dw, cy + xx + dh, on)
                self.pixel(cx - xx, cy + yy + dh, on);      self.pixel(cx - yy, cy + xx + dh, on)
                self.pixel(cx + xx + dw, cy - yy, on);      self.pixel(cx + yy + dw, cy - xx, on)
                self.pixel(cx - xx, cy - yy, on);           self.pixel(cx - yy, cy - xx, on)

    # ── 글자 ──
    def draw_char(self, x, y, ch, size=1, on=1):
        code = ord(ch)
        if code > 255:
            code = ord("?")
        for col in range(5):
            bits = FONT[code * 5 + col]
            for row in range(8):
                if bits & (1 << row):
                    for dx in range(size):
                        for dy in range(size):
                            self.pixel(x + col * size + dx, y + row * size + dy, on)

    def mixed_text(self, x, y, s, size=1, on=1):
        """한글·영문 섞인 글. size 2면 픽셀을 2배로 키운다."""
        for ch in s:
            if is_hangul(ch):
                for dx, dy in hangul_dots(ch):
                    for ox in range(size):
                        for oy in range(size):
                            self.pixel(x + dx * size + ox, y + dy * size + oy, on)
                x += HANGUL_ADVANCE * size
            else:
                self.draw_char(x, y + size, ch, size, on)   # 영문은 한 칸 내려 눈높이를 맞춘다
                x += ASCII_ADVANCE * size
        return x

    def center_text(self, y, s, size=1, on=1):
        return self.mixed_text((W - measure(s, size)) // 2, y, s, size, on)


    def circle(self, cx, cy, r, on=1):
        """Adafruit_GFX 의 drawCircle 과 같은 브레젠험 원."""
        cx, cy, r = int(cx), int(cy), int(r)
        f, ddF_x, ddF_y = 1 - r, 1, -2 * r
        x, y = 0, r
        self.pixel(cx, cy + r, on); self.pixel(cx, cy - r, on)
        self.pixel(cx + r, cy, on); self.pixel(cx - r, cy, on)
        while x < y:
            if f >= 0:
                y -= 1; ddF_y += 2; f += ddF_y
            x += 1; ddF_x += 2; f += ddF_x
            for sx, sy in ((x, y), (-x, y), (x, -y), (-x, -y),
                           (y, x), (-y, x), (y, -x), (-y, -x)):
                self.pixel(cx + sx, cy + sy, on)

    def outline_text(self, x, y, s, size=1):
        """배경 위에 그대로 얹는 글씨. 글자 둘레를 검게 두른 뒤 흰 글자를 올린다.

        상자로 배경을 가리지 않아도 글자가 읽힌다. 대신 그리는 횟수가 9배가 된다.
        """
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    self.mixed_text(x + dx, y + dy, s, size, on=0)
        return self.mixed_text(x, y, s, size, on=1)

    def center_outline(self, y, s, size=1):
        return self.outline_text((W - measure(s, size)) // 2, y, s, size)

def save_png(canvas, path, scale=6):
    """켜진 픽셀은 흰색, 꺼진 픽셀은 진한 남색으로 (실제 OLED 느낌)."""
    on, off = (235, 245, 255), (8, 12, 24)
    rows = []
    for y in range(H):
        row = bytearray([0])
        for x in range(W):
            row += bytes(on if canvas.px[y][x] else off) * scale
        for _ in range(scale):
            rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def save_sheet(items, path, scale=5, gap=10, label_h=0):
    """여러 화면을 세로로 이어 붙여 한 장으로 저장한다. items = [(canvas, ...), ...]"""
    on, off, bg = (235, 245, 255), (8, 12, 24), (26, 26, 30)
    tile_w, tile_h = W * scale, H * scale
    total_h = len(items) * tile_h + (len(items) + 1) * gap
    total_w = tile_w + gap * 2
    rows = []
    blank = bytes(bg) * total_w
    for _ in range(gap):
        rows.append(blank)
    for canvas in items:
        for y in range(H):
            line = bytearray()
            for x in range(W):
                line += bytes(on if canvas.px[y][x] else off) * scale
            full = bytes(bg) * gap + bytes(line) + bytes(bg) * gap
            for _ in range(scale):
                rows.append(full)
        for _ in range(gap):
            rows.append(blank)
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", total_w, len(rows), 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)
