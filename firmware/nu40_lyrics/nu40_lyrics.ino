/*
  NU40 DK 가사 디스플레이 — 보드 펌웨어
  ------------------------------------------------------------------
  이 보드가 하는 일은 딱 하나다. PC가 USB로 보내주는 두 가지를 화면에 그린다.
    1) 지금 나와야 할 가사 한 조각 (글자)
    2) 지금 음악의 주파수 막대 16개 (숫자)

  이 보드(nRF52840)에는 WiFi도 스피커도 마이크도 없다. 그래서
  유튜브 다운로드·음원 분석·가사 타이밍 계산·소리 재생은 전부 PC가 하고,
  보드는 "받은 대로 그리기"만 한다. 이 역할 분담을 바꾸지 말 것.
  (주식 티커 프로젝트와 같은 구조다.)

  화면 배치 (128 x 64)
    배경  : 4가지 중 하나. 버튼 1~4로 즉시 바꾼다. 음악의 비트에 반응한다
    가운데: 가사 (y 18~35). 글자가 잘리지 않게 PC가 미리 잘라서 보낸다
    아래  : 주파수 막대 16개 (y 44~63)

  배경 4가지와 가사 표시 방식
    버튼1 별밤          — 별이 세 겹으로 흐르고, 비트에 가까운 별만 반짝인다   → 외곽선 가사
    버튼2 동심원 파문    — 원이 퍼져나가고, 비트에 한가운데가 밝게 찬다        → 상자 가사
    버튼3 카세트 테이프  — 릴 두 개가 돈다                                  → 상자 가사
    버튼4 도시 스카이라인 — 건물이 흐르고 창문에 불이 들어온다                 → 외곽선 가사

  성긴 배경은 글자를 배경 위에 그대로 얹고(외곽선), 선이 촘촘한 배경은 상자로 가린다.
  촘촘한 배경에 외곽선을 쓰면 글자가 배경 선과 섞여서 안 읽힌다.

  용어 한 줄 설명
    - I2C   : 선 2가닥(SDA/SCL)으로 화면과 대화하는 통신 방식.
    - 시리얼 : USB 케이블로 PC와 값을 주고받는 통로. 여기선 115200 속도를 쓴다.
    - 프레임 : PC가 한 번에 보내는 값 묶음. 앞에 0xAA 0x55 를 붙여 시작점을 표시한다.
*/

#include <Adafruit_TinyUSB.h>   // 이게 없으면 Serial 에서 링크 에러가 난다 (이 보드의 규칙)
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <math.h>

// 한글 글꼴 데이터 (갈무리9, 9x9). 주식 티커 펌웨어와 같은 파일이다.
#include "hangul_font.h"

// ─────────────────────────────────────────────────────────────
// 하드웨어 설정 (회로도로 확정된 값 — 바꾸지 말 것)
// ─────────────────────────────────────────────────────────────

// OLED는 P0.26(SDA) / P0.27(SCL). NU40DK 의 variant.h 에 이미 지정돼 있어서
// Wire.begin() 기본값이 곧 P0.26/P0.27 이다. setPins() 를 부를 필요가 없다.
static const uint8_t BUTTON_PINS[4] = { 11, 12, 24, 25 };  // 눌리면 LOW
static const uint8_t LED_PINS[4]    = { 13, 14, 15, 16 };  // HIGH 면 켜짐

static const uint8_t OLED_ADDR_MAIN = 0x3C;
static const uint8_t OLED_ADDR_ALT  = 0x3D;

#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1        // NU40DK 에는 화면 리셋 전용 핀이 없다

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ─────────────────────────────────────────────────────────────
// 화면 배치
// ─────────────────────────────────────────────────────────────
static const int16_t LYRIC_CY = 26;   // 가사의 세로 한가운데
static const int16_t LYRIC_TOP = 22;  // 외곽선 방식일 때 글자 윗줄 위치

static const uint8_t BG_COUNT = 4;    // 배경 가짓수 (버튼 1~4에 하나씩)

static const uint8_t BAR_COUNT  = 16;
static const int16_t BAR_W      = 6;
static const int16_t BAR_GAP    = 2;
static const int16_t BAR_BOTTOM = 63;  // 막대가 서 있는 바닥
static const int16_t BAR_MAX    = 20;  // 막대 최대 높이

// 가사 한 조각의 최대 길이. 한글 21자(63바이트)면 충분하고도 남는다.
// PC가 화면 폭(116픽셀)에 맞춰 미리 잘라서 보내기 때문에 여기까지 올 일은 없다.
static const uint8_t LYRIC_MAX = 96;

// ─────────────────────────────────────────────────────────────
// 지금 화면에 그릴 값
// ─────────────────────────────────────────────────────────────
static char    lyricText[LYRIC_MAX + 1] = "";   // 빈 문자열이면 상자를 그리지 않는다(간주 구간)
static uint8_t barLevel[BAR_COUNT];             // 0~255. PC가 보내준 값
static uint8_t barShown[BAR_COUNT];             // 실제로 그리는 값. 떨어질 때만 천천히 내려온다
static uint8_t beatLevel = 0;                   // 0~255. 저역 타격 세기. 배경이 이걸 보고 반응한다
static uint8_t currentBg = 0;                   // 지금 배경 (0~3). 버튼으로 바꾼다

static bool     displayOk   = false;
static bool     linkActive  = false;   // PC에서 프레임을 한 번이라도 받았는지
static uint32_t lastFrameMs = 0;       // 마지막으로 프레임을 받은 시각
static uint32_t lastDrawMs  = 0;
static uint32_t lastErrorNoticeMs = 0;

// 화면을 다시 그리는 간격. OLED 전송에 25ms 안팎이 걸려서 30fps 가 현실적인 상한이다.
static const uint16_t DRAW_INTERVAL_MS = 33;

// PC가 3초 넘게 조용하면 대기 화면으로 돌아간다.
static const uint32_t LINK_TIMEOUT_MS = 3000;

// ─────────────────────────────────────────────────────────────
// 시리얼 프레임 해석
//   막대 : 0xAA 0x55 0x01 [16바이트] [체크섬]
//   가사 : 0xAA 0x55 0x02 [길이] [길이만큼의 UTF-8 바이트] [체크섬]
//   체크섬은 종류 바이트부터 마지막 값까지 전부 XOR 한 값이다.
// ─────────────────────────────────────────────────────────────
enum RxState { WAIT_SYNC1, WAIT_SYNC2, WAIT_KIND, WAIT_LEN, WAIT_BODY, WAIT_CHECK };

static RxState rxState = WAIT_SYNC1;
static uint8_t rxKind    = 0;
static uint8_t rxNeed    = 0;      // 본문으로 더 받아야 하는 바이트 수
static uint8_t rxGot     = 0;
static uint8_t rxBuf[LYRIC_MAX];
static uint8_t rxCheck   = 0;

static const uint8_t KIND_BARS  = 0x01;
static const uint8_t KIND_LYRIC = 0x02;

// ─────────────────────────────────────────────────────────────
// 준비 단계
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  for (uint8_t i = 0; i < 4; i++) {
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    pinMode(LED_PINS[i], OUTPUT);
    digitalWrite(LED_PINS[i], LOW);
  }

  for (uint8_t i = 0; i < BAR_COUNT; i++) { barLevel[i] = 0; barShown[i] = 0; }

  Wire.begin();
  // 기본 100kHz 로는 한 화면 보내는 데 85ms 가 걸려서 12fps 밖에 안 나온다.
  // 400kHz 로 올려야 막대가 음악처럼 움직인다. 이 줄을 지우지 말 것.
  Wire.setClock(400000);

  displayOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_MAIN);
  if (!displayOk) displayOk = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR_ALT);

  if (!displayOk) {
    Serial.println("ERR|OLED를 찾지 못했습니다. SDA=P0.26 / SCL=P0.27 배선과 3V3·GND를 확인하세요.");
    return;
  }

  display.clearDisplay();
  display.display();
  Serial.println("RDY|가사 디스플레이 준비됨");
}

// ─────────────────────────────────────────────────────────────
// 되풀이
// ─────────────────────────────────────────────────────────────
void loop() {
  readSerial();
  readButtons();

  if (!displayOk) {
    if (millis() - lastErrorNoticeMs > 5000) {
      lastErrorNoticeMs = millis();
      Serial.println("ERR|OLED 없음 — 배선 확인 후 보드를 다시 연결하세요.");
    }
    return;
  }

  if (linkActive && millis() - lastFrameMs > LINK_TIMEOUT_MS) {
    linkActive = false;
    lyricText[0] = '\0';
    for (uint8_t i = 0; i < BAR_COUNT; i++) barLevel[i] = 0;
  }

  uint32_t now = millis();
  if (now - lastDrawMs >= DRAW_INTERVAL_MS) {
    lastDrawMs = now;
    drawScreen(now);
  }
}

// ─────────────────────────────────────────────────────────────
// 시리얼 읽기 — 한 바이트씩 상태를 옮겨가며 프레임을 조립한다
// ─────────────────────────────────────────────────────────────
void readSerial() {
  while (Serial.available() > 0) {
    uint8_t b = (uint8_t)Serial.read();

    switch (rxState) {
      case WAIT_SYNC1:
        if (b == 0xAA) rxState = WAIT_SYNC2;
        break;

      case WAIT_SYNC2:
        // 0xAA 가 연달아 오면 그건 여전히 시작 신호의 첫 바이트다.
        rxState = (b == 0x55) ? WAIT_KIND : (b == 0xAA ? WAIT_SYNC2 : WAIT_SYNC1);
        break;

      case WAIT_KIND:
        rxKind  = b;
        rxCheck = b;
        rxGot   = 0;
        if (rxKind == KIND_BARS) {
          rxNeed  = BAR_COUNT + 1;   // 막대 16개 + 비트 1바이트
          rxState = WAIT_BODY;
        } else if (rxKind == KIND_LYRIC) {
          rxState = WAIT_LEN;
        } else {
          rxState = WAIT_SYNC1;   // 모르는 종류는 버린다
        }
        break;

      case WAIT_LEN:
        rxCheck ^= b;
        if (b > LYRIC_MAX) { rxState = WAIT_SYNC1; break; }   // 너무 길면 버린다
        rxNeed  = b;
        rxState = (rxNeed == 0) ? WAIT_CHECK : WAIT_BODY;
        break;

      case WAIT_BODY:
        rxBuf[rxGot++] = b;
        rxCheck ^= b;
        if (rxGot >= rxNeed) rxState = WAIT_CHECK;
        break;

      case WAIT_CHECK:
        if (b == rxCheck) applyFrame();     // 값이 맞을 때만 반영한다
        rxState = WAIT_SYNC1;
        break;
    }
  }
}

void applyFrame() {
  lastFrameMs = millis();
  linkActive  = true;

  if (rxKind == KIND_BARS) {
    for (uint8_t i = 0; i < BAR_COUNT; i++) barLevel[i] = rxBuf[i];
    beatLevel = rxBuf[BAR_COUNT];
  } else if (rxKind == KIND_LYRIC) {
    uint8_t n = rxGot > LYRIC_MAX ? LYRIC_MAX : rxGot;
    memcpy(lyricText, rxBuf, n);
    lyricText[n] = '\0';
  }
}

// ─────────────────────────────────────────────────────────────
// 한글 그리기 (주식 티커 펌웨어와 같은 방식)
// 아두이노 기본 글꼴에는 한글이 없어서 글자 모양을 비트맵으로 넣어뒀다.
// 영문·숫자는 기본 글꼴(5x7)을 쓰고, 한글만 이 비트맵으로 그린다.
// ─────────────────────────────────────────────────────────────

// UTF-8 로 적힌 글에서 글자 하나를 읽어 유니코드 번호로 바꾼다.
uint32_t utf8Next(const char* s, uint8_t* used) {
  uint8_t c = (uint8_t)s[0];
  if (c < 0x80)             { *used = 1; return c; }
  if ((c & 0xE0) == 0xC0)   { *used = 2; return ((uint32_t)(c & 0x1F) << 6) | (s[1] & 0x3F); }
  if ((c & 0xF0) == 0xE0)   { *used = 3; return ((uint32_t)(c & 0x0F) << 12)
                                                | ((uint32_t)(s[1] & 0x3F) << 6) | (s[2] & 0x3F); }
  *used = 1;
  return 0;
}

// 글자 번호로 글꼴 목록에서 몇 번째 글자인지 찾는다. 없으면 -1. (이진 탐색)
int16_t hangulIndex(uint32_t code) {
  if (code < 0xAC00 || code > 0xD7A3) return -1;
  int16_t low = 0, high = HANGUL_COUNT - 1;
  while (low <= high) {
    int16_t mid = (low + high) / 2;
    uint16_t here = HANGUL_CODES[mid];
    if (here == code) return mid;
    if (here < code) low = mid + 1;
    else             high = mid - 1;
  }
  return -1;
}

void drawHangulGlyph(int16_t x, int16_t y, int16_t index) {
  const uint8_t* glyph = HANGUL_BITS[index];
  for (uint8_t row = 0; row < HANGUL_H; row++) {
    uint8_t left = glyph[row * 2];
    uint8_t last = glyph[row * 2 + 1];
    for (uint8_t col = 0; col < 8; col++) {
      if (left & (0x80 >> col)) display.drawPixel(x + col, y + row, SSD1306_WHITE);
    }
    if (last & 0x80) display.drawPixel(x + 8, y + row, SSD1306_WHITE);
  }
}

void drawMixedText(int16_t x, int16_t y, const char* text) {
  display.setTextSize(1);
  while (*text != '\0') {
    uint8_t used;
    uint32_t code = utf8Next(text, &used);
    int16_t index = hangulIndex(code);

    if (index >= 0) {
      drawHangulGlyph(x, y, index);
      x += HANGUL_ADVANCE;
    } else if (code >= 0x20 && code < 0x80) {
      // 영문·숫자는 한글보다 2픽셀 작아서 1픽셀 내려 눈높이를 맞춘다.
      display.drawChar(x, y + 1, (char)code, SSD1306_WHITE, SSD1306_BLACK, 1);
      x += 6;
    }
    text += used;
  }
}

// 그렸을 때 가로로 몇 픽셀을 차지하는지. PC도 똑같은 규칙으로 계산해서 미리 자른다.
int16_t measureMixedText(const char* text) {
  int16_t width = 0;
  while (*text != '\0') {
    uint8_t used;
    uint32_t code = utf8Next(text, &used);
    if (hangulIndex(code) >= 0)            width += HANGUL_ADVANCE;
    else if (code >= 0x20 && code < 0x80)  width += 6;
    text += used;
  }
  return width;
}

// ─────────────────────────────────────────────────────────────
// 배경 4가지
//
// 어느 것이든 두 값만 받는다.
//   tsec : 켜진 뒤 흐른 시간(초). 이걸로 움직임을 만든다.
//   beat : 0~1. 지금 음악의 저역 타격 세기. 배경이 음악에 반응하게 만드는 값이다.
//
// 배경은 y 0~43 안에서만 그린다. 아래쪽 y 44~63 은 막대 자리다.
// PC 미리보기(tools/preview/bg_options.py)와 같은 식으로 그려야 화면이 일치한다.
// 난수는 고정된 씨앗에서 만든다. 그래야 별·건물 자리가 매 프레임 같다.
// ─────────────────────────────────────────────────────────────

// 파이썬 미리보기와 똑같은 난수. 값이 달라지면 그림도 달라진다.
static inline uint32_t nextRand(uint32_t* seed) {
  *seed = (*seed * 1103515245u + 12345u) & 0x7FFFFFFFu;
  return *seed;
}

// 음수도 파이썬처럼 0 이상으로 만드는 나머지. C 의 % 는 음수를 음수로 돌려준다.
static inline int32_t modFloor(int32_t a, int32_t m) {
  int32_t r = a % m;
  return (r < 0) ? r + m : r;
}

// 배경 0 — 별밤. 별이 세 겹으로 흐른다. 가까운 별일수록 빠르다.
void bgStars(float tsec, float beat) {
  uint32_t seed = 12345;
  for (uint8_t i = 0; i < 46; i++) {
    int16_t x0 = (int16_t)(nextRand(&seed) % 128);
    int16_t y  = (int16_t)(nextRand(&seed) % 44);
    uint8_t layer = i % 3;                       // 0=먼 별, 2=가까운 별
    float   speed = 3.0f + layer * 7.0f;
    int16_t x = (int16_t)modFloor(x0 - (int32_t)(tsec * speed), 128);

    display.drawPixel(x, y, SSD1306_WHITE);
    if (layer == 2 && beat > 0.6f) {             // 가까운 별만 비트에 십자로 커진다
      display.drawPixel(x - 1, y, SSD1306_WHITE);
      display.drawPixel(x + 1, y, SSD1306_WHITE);
      display.drawPixel(x, y - 1, SSD1306_WHITE);
      display.drawPixel(x, y + 1, SSD1306_WHITE);
    }
  }
}

// 배경 1 — 동심원 파문. 원이 계속 퍼져나가고, 비트에 한가운데가 밝게 찬다.
void bgRipple(float tsec, float beat) {
  for (uint8_t i = 0; i < 4; i++) {
    int16_t r = (int16_t)modFloor((int32_t)(tsec * 22.0f + i * 15.0f), 60);
    if (r > 2) display.drawCircle(64, 30, r, SSD1306_WHITE);
  }
  if (beat > 0.65f) {
    display.drawCircle(64, 30, 3, SSD1306_WHITE);
    display.drawCircle(64, 30, 4, SSD1306_WHITE);
  }
}

// 배경 2 — 카세트 테이프. 릴 두 개가 돈다.
void bgCassette(float tsec, float beat) {
  display.drawRoundRect(4, 2, 120, 42, 4, SSD1306_WHITE);
  const int16_t reelX[2] = { 36, 92 };
  for (uint8_t n = 0; n < 2; n++) {
    int16_t cx = reelX[n];
    display.drawCircle(cx, 23, 12, SSD1306_WHITE);
    display.drawCircle(cx, 23, 4, SSD1306_WHITE);
    for (uint8_t k = 0; k < 6; k++) {            // 릴 살 — 이게 돌아가는 게 보인다
      float a = tsec * 2.2f + k * (PI / 3.0f) + (n ? 0.5f : 0.0f);
      display.drawLine((int16_t)(cx + 4 * cosf(a)),  (int16_t)(23 + 4 * sinf(a)),
                       (int16_t)(cx + 11 * cosf(a)), (int16_t)(23 + 11 * sinf(a)),
                       SSD1306_WHITE);
    }
  }
  display.drawLine(36, 11, 92, 11, SSD1306_WHITE);   // 릴 사이를 잇는 테이프
  if (beat > 0.6f) display.drawLine(36, 10, 92, 10, SSD1306_WHITE);
}

// 배경 3 — 도시 야경. 건물이 흐르고 창문에 불이 들어온다. 위쪽엔 붙박이 별.
//
// 건물 높이를 y33 아래로 묶어 둔다. 그보다 높으면 지붕선이 가사(y22~32)를
// 가로질러서 외곽선 글씨가 읽히지 않는다. 미리보기로 확인하고 정한 값이다.
void bgSkyline(float tsec, float beat) {
  uint32_t seed = 555;                    // 별은 흐르지 않는다. 건물만 흐른다.
  for (uint8_t i = 0; i < 14; i++) {
    int16_t sx = (int16_t)(nextRand(&seed) % 128);
    int16_t sy = (int16_t)(nextRand(&seed) % 18);
    display.drawPixel(sx, sy, SSD1306_WHITE);
  }

  seed = 987;
  int16_t x = (int16_t)(modFloor(-(int32_t)(tsec * 9.0f), 24) - 24);
  uint8_t flick = (uint8_t)(beat * 4.0f);

  while (x < SCREEN_WIDTH) {
    int16_t w   = 10 + (int16_t)(nextRand(&seed) % 12);
    int16_t h   = 3 + (int16_t)(nextRand(&seed) % 9);    // 지붕은 y33~41 사이
    int16_t top = 44 - h;
    display.drawRect(x, top, w, h, SSD1306_WHITE);

    for (int16_t wy = top + 3; wy < 42; wy += 4) {
      for (int16_t wx = x + 2; wx < x + w - 2; wx += 4) {
        if (modFloor(wx * 7 + wy * 13 + flick, 5) < 2) {
          display.drawPixel(wx, wy, SSD1306_WHITE);
        }
      }
    }
    x += w + 3;
  }
  display.drawLine(0, 44, SCREEN_WIDTH - 1, 44, SSD1306_WHITE);
}

void drawBackground(uint8_t which, float tsec, float beat) {
  switch (which) {
    case 0: bgStars(tsec, beat);    break;
    case 1: bgRipple(tsec, beat);   break;
    case 2: bgCassette(tsec, beat); break;
    default: bgSkyline(tsec, beat); break;
  }
}

// 배경마다 가사를 어떻게 띄울지. true 면 배경 위에 그대로 얹는다(외곽선).
// 선이 촘촘한 배경(파문·카세트)에 외곽선을 쓰면 글자가 배경과 섞여서 안 읽힌다.
bool bgUsesOutline(uint8_t which) {
  return (which == 0 || which == 3);   // 별밤, 스카이라인만 성기다
}

// ─────────────────────────────────────────────────────────────
// 가사 그리기
//
// 두 가지 방식이 있고 배경에 따라 자동으로 고른다.
//   상자   : 상자 안을 검게 지우고 그 위에 글자. 어떤 배경에서도 또렷하다.
//   외곽선 : 글자 둘레만 검게 두르고 배경 위에 그대로 얹는다. 배경이 다 보인다.
//
// 글자가 잘리는 일은 없다. PC 가 화면 폭(116픽셀)에 맞춰 미리 잘라서 보내기 때문이다.
// ─────────────────────────────────────────────────────────────

// 한글 한 글자의 '둘레'를 검게 칠한다.
//
// 같은 글자를 여덟 방향으로 여덟 번 그리면 느리다. 대신 각 줄의 픽셀을
// 좌우로 한 칸씩 번지게 하고(비트 밀기), 위아래 줄끼리 합쳐서 한 번에 칠한다.
// 9x9 글자 하나가 두 번의 훑기로 끝난다.
void drawHangulHalo(int16_t x, int16_t y, int16_t index) {
  const uint8_t* glyph = HANGUL_BITS[index];
  uint16_t spread[HANGUL_H];

  for (uint8_t r = 0; r < HANGUL_H; r++) {
    // 한 줄을 9비트로 모은다. 그 다음 왼쪽 한 칸 여유를 두려고 한 칸 밀어 올린다.
    // 이러면 비트 (9 - 열번호) 가 그 열의 픽셀이 된다. 열 -1 은 비트 10, 열 9 는 비트 0.
    uint16_t raw  = ((uint16_t)glyph[r * 2] << 1) | (glyph[r * 2 + 1] >> 7);
    uint16_t base = raw << 1;
    spread[r] = base | (base << 1) | (base >> 1);   // 좌우로 한 칸씩 번지게
  }

  for (int8_t ry = -1; ry <= HANGUL_H; ry++) {
    uint16_t v = 0;                                  // 위아래 줄까지 합친다
    if (ry - 1 >= 0 && ry - 1 < HANGUL_H) v |= spread[ry - 1];
    if (ry     >= 0 && ry     < HANGUL_H) v |= spread[ry];
    if (ry + 1 >= 0 && ry + 1 < HANGUL_H) v |= spread[ry + 1];
    if (!v) continue;

    for (int8_t c = -1; c <= HANGUL_W; c++) {
      if (v & (1u << (9 - c))) display.drawPixel(x + c, y + ry, SSD1306_BLACK);
    }
  }
}

// 글 전체의 둘레를 검게 두른다. 그 다음 위에 흰 글자를 그리면 배경 위에서도 읽힌다.
void drawMixedTextHalo(int16_t x, int16_t y, const char* text) {
  display.setTextSize(1);
  while (*text != '\0') {
    uint8_t used;
    uint32_t code = utf8Next(text, &used);
    int16_t index = hangulIndex(code);

    if (index >= 0) {
      drawHangulHalo(x, y, index);
      x += HANGUL_ADVANCE;
    } else if (code >= 0x20 && code < 0x80) {
      // 영문은 글자가 작아서 여덟 방향으로 그려도 부담이 없다.
      // 앞뒤 색을 같게 주면 칸 전체가 아니라 글자 모양만 칠해진다.
      for (int8_t dx = -1; dx <= 1; dx++) {
        for (int8_t dy = -1; dy <= 1; dy++) {
          if (dx || dy) {
            display.drawChar(x + dx, y + 1 + dy, (char)code,
                             SSD1306_BLACK, SSD1306_BLACK, 1);
          }
        }
      }
      x += 6;
    }
    text += used;
  }
}

// 한 줄을 그린다. outline 이면 배경 위에 얹고, 아니면 상자는 바깥에서 이미 그렸다.
void drawLyricLine(const char* text, int16_t y, bool outline, int16_t clampLeft) {
  int16_t tw = measureMixedText(text);
  int16_t tx = (SCREEN_WIDTH - tw) / 2;
  if (tx < clampLeft) tx = clampLeft;

  if (outline) drawMixedTextHalo(tx, y, text);
  drawMixedText(tx, y, text);
}

// 가사를 그린다.
//
// 줄바꿈(\n)이 들어 있으면 두 줄로 그린다. 인트로에 곡 제목과 가수를 함께
// 띄우려고 만든 길이다. 무엇을 언제 띄울지는 PC 가 정하고, 보드는 받은 대로 그린다.
void drawLyric(const char* text, bool outline) {
  if (text[0] == '\0') return;      // 간주 구간 — 배경만 보여준다

  // 줄바꿈을 찾아 두 줄로 나눈다. 없으면 한 줄이다.
  char line1[LYRIC_MAX + 1];
  const char* line2 = NULL;
  const char* br = strchr(text, '\n');
  if (br != NULL) {
    uint8_t n = (uint8_t)(br - text);
    if (n > LYRIC_MAX) n = LYRIC_MAX;
    memcpy(line1, text, n);
    line1[n] = '\0';
    line2 = br + 1;
    if (line2[0] == '\0') line2 = NULL;   // 뒤가 비었으면 한 줄 취급
  } else {
    strncpy(line1, text, LYRIC_MAX);
    line1[LYRIC_MAX] = '\0';
  }

  // 글자 9픽셀 + 줄 사이 2픽셀. 두 줄이면 y16~36 을 쓴다(막대는 y44부터라 여유가 있다).
  const int16_t LINE_H = 11;
  int16_t y1 = line2 ? (LYRIC_CY - LINE_H + 1) : LYRIC_TOP;
  int16_t y2 = LYRIC_CY + 1;

  if (outline) {
    drawLyricLine(line1, y1, true, 1);
    if (line2) drawLyricLine(line2, y2, true, 1);
    return;
  }

  // 상자 방식 — 두 줄이면 상자를 그만큼 키운다.
  int16_t tw = measureMixedText(line1);
  if (line2) {
    int16_t w2 = measureMixedText(line2);
    if (w2 > tw) tw = w2;
  }
  int16_t bw = tw + 10;
  if (bw > SCREEN_WIDTH - 2) bw = SCREEN_WIDTH - 2;
  int16_t bh = line2 ? (LINE_H * 2 + 6) : 17;
  int16_t bx = (SCREEN_WIDTH - bw) / 2;
  int16_t by = LYRIC_CY - bh / 2;

  // 상자 안을 까맣게 지워서 뒤의 배경이 글자와 겹치지 않게 한다.
  display.fillRoundRect(bx, by, bw, bh, 3, SSD1306_BLACK);
  display.drawRoundRect(bx, by, bw, bh, 3, SSD1306_WHITE);

  if (line2) {
    drawLyricLine(line1, by + 4, false, bx + 2);
    drawLyricLine(line2, by + 4 + LINE_H, false, bx + 2);
  } else {
    drawLyricLine(line1, by + 4, false, bx + 2);
  }
}

// ─────────────────────────────────────────────────────────────
// 버튼 — 배경 바꾸기
// 버튼을 누르면 PC 와 상관없이 보드가 즉시 배경을 바꾼다.
// 지금 배경이 몇 번인지는 같은 번호의 LED 로 알려준다.
// ─────────────────────────────────────────────────────────────
static const uint32_t DEBOUNCE_MS = 30;
static bool     buttonWasDown[4]   = { false, false, false, false };
static uint32_t buttonChangedAt[4] = { 0, 0, 0, 0 };

void readButtons() {
  uint32_t now = millis();

  for (uint8_t i = 0; i < 4; i++) {
    bool isDown = (digitalRead(BUTTON_PINS[i]) == LOW);   // 눌리면 LOW

    if (isDown != buttonWasDown[i]) {
      if (now - buttonChangedAt[i] < DEBOUNCE_MS) continue;   // 흔들림은 무시
      buttonChangedAt[i] = now;
      buttonWasDown[i]   = isDown;

      if (isDown && currentBg != i) {     // 새로 눌린 순간에만 반응
        currentBg = i;
        Serial.print("BG|");
        Serial.println(i);
      }
    }
  }

  for (uint8_t i = 0; i < BG_COUNT; i++) {
    digitalWrite(LED_PINS[i], (i == currentBg) ? HIGH : LOW);
  }
}

// ─────────────────────────────────────────────────────────────
// 주파수 막대
// 올라갈 때는 즉시, 내려갈 때는 천천히. 그래야 눈에 타격감이 남는다.
// ─────────────────────────────────────────────────────────────
void drawBars() {
  for (uint8_t i = 0; i < BAR_COUNT; i++) {
    uint8_t target = barLevel[i];
    if (target >= barShown[i]) {
      barShown[i] = target;                       // 올라갈 때는 곧바로
    } else {
      uint8_t drop = (barShown[i] - target) / 3;  // 내려갈 때는 3분의 1씩
      barShown[i] = (drop == 0) ? target : barShown[i] - drop;
    }

    int16_t h = ((int32_t)barShown[i] * BAR_MAX) / 255;
    int16_t x = i * (BAR_W + BAR_GAP);

    // 막대가 설 자리의 배경을 지워서 지구본 선과 섞이지 않게 한다.
    display.fillRect(x, BAR_BOTTOM - BAR_MAX, BAR_W, BAR_MAX + 1, SSD1306_BLACK);
    if (h < 1) h = 1;                             // 조용해도 바닥선은 남긴다
    display.fillRect(x, BAR_BOTTOM - h + 1, BAR_W, h, SSD1306_WHITE);
  }
}

// ─────────────────────────────────────────────────────────────
// 화면 그리기
// ─────────────────────────────────────────────────────────────
void drawScreen(uint32_t now) {
  display.clearDisplay();

  float tsec = now / 1000.0f;
  float beat = beatLevel / 255.0f;

  drawBackground(currentBg, tsec, beat);

  if (linkActive) {
    drawLyric(lyricText, bgUsesOutline(currentBg));
  } else {
    drawLyric("대기중", bgUsesOutline(currentBg));
  }

  drawBars();
  display.display();
}
