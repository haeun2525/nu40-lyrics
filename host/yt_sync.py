#!/usr/bin/env python3
"""
yt_sync.py — 유튜브 플레이어의 재생 위치를 보드로 넘겨주는 다리.

영상을 내려받아 띄우는 대신 **정식 유튜브 플레이어를 그대로 띄운다.**
화면에 유튜브가 보이므로 촬영에도 자연스럽고, 무엇보다 IFrame API 가
재생 위치와 상태를 알려주기 때문에 **멈추면 보드도 멈추고 되감으면 같이 되감긴다.**

브라우저 자바스크립트는 파일에 쓸 수 없어서, 아주 작은 로컬 서버를 하나 띄우고
페이지가 0.1초마다 위치를 보고하게 한다. 서버는 이 스크립트 안에서 돌기 때문에
따로 켜 둘 게 없다.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8770

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>NU40 싱크</title>
<style>
 html,body{margin:0;height:100%%;background:#0b0d12;color:#dfe6f2;
   font-family:-apple-system,sans-serif;display:flex;flex-direction:column;
   align-items:center;justify-content:center;gap:12px}
 #hint{font-size:13px;opacity:.65}
</style></head><body>
<div id="p"></div>
<div id="hint">재생·일시정지·되감기를 하면 보드가 그대로 따라갑니다</div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
var player, ok = false;
function onYouTubeIframeAPIReady() {
  player = new YT.Player('p', {
    height: '640', width: '360', videoId: '%(vid)s',
    playerVars: {autoplay: 0, controls: 1, rel: 0, playsinline: 1},
    events: {onReady: function () { ok = true; setInterval(send, 100); }}
  });
}
function send() {
  if (!ok) return;
  try {
    var t = player.getCurrentTime();
    var playing = (player.getPlayerState() === 1) ? 1 : 0;
    fetch('/pos?t=' + t + '&r=' + playing);
  } catch (e) {}
}
</script></body></html>
"""


class YouTubeClock:
    """유튜브 플레이어가 보고해 준 재생 위치를 따라가는 시계.

    보고는 0.1초에 한 번뿐이라 그 사이는 자체 시계로 이어 붙인다.
    멈추면 rate 가 0 이 되어 그 자리에 머문다.
    """

    def __init__(self, video_id: str, port: int = PORT) -> None:
        self.video_id = video_id
        self.port = port
        self.media = 0.0
        self.rate = 0.0
        self.wall = time.monotonic()
        self.seen = False           # 페이지가 한 번이라도 보고했는지
        self.alive = True
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self):
        clock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 요청 로그를 터미널에 쏟지 않는다
                pass

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/pos":
                    q = parse_qs(u.query)
                    try:
                        clock.media = float(q.get("t", ["0"])[0])
                        clock.rate = float(q.get("r", ["0"])[0])
                        clock.wall = time.monotonic()
                        clock.seen = True
                    except ValueError:
                        pass
                    self.send_response(204)
                    self.end_headers()
                    return
                body = (PAGE % {"vid": clock.video_id}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def start(self, open_browser: bool = True) -> None:
        self._thread.start()
        if open_browser:
            # 크롬 앱 창으로 띄운다. 주소창 없이 영상만 보여서 촬영에 낫다.
            subprocess.Popen(
                ["open", "-na", "Google Chrome", "--args",
                 f"--app=http://127.0.0.1:{self.port}/"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def now(self) -> float:
        return self.media + (time.monotonic() - self.wall) * self.rate

    def stop(self) -> None:
        self.alive = False
        try:
            self._server.shutdown()
        except Exception:
            pass


def video_id(url: str) -> str:
    """유튜브 주소에서 영상 아이디만 뽑는다. shorts/watch/youtu.be 다 받는다."""
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    raise SystemExit(f"유튜브 영상 아이디를 찾지 못했습니다: {url}")
