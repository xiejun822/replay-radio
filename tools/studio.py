"""Host the pinned SongCut Studio on a separate loopback origin."""
import os
import shutil
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
SOURCE = BASE / "vendor" / "songcut-studio"
_server = None
_lock = threading.Lock()


def open_editor(main_port, ffmpeg, alive, bye, clip=None):
    global _server
    with _lock:
        if _server is None:
            if not (SOURCE / "server.py").is_file():
                raise FileNotFoundError("缺少歌切制作台资源。源码版请运行 git submodule update --init。")
            sys.path.insert(0, str(SOURCE))
            import server as studio
            if ffmpeg:
                os.environ.setdefault("SONGCUT_FFMPEG", ffmpeg)

            class Editor(studio.Handler):
                def do_GET(self):
                    path = urlsplit(self.path).path
                    if path in ("/", "/index.html"):
                        page = (SOURCE / "index.html").read_text(encoding="utf-8")
                        page = page.replace("</body>", '<script src="/rr/import.js"></script></body>')
                        return self.send_bytes(page.encode(), "text/html; charset=utf-8")
                    if path == "/rr/import.js":
                        script = (BASE / "assets" / "studio-import.js").read_bytes()
                        return self.send_bytes(script, "application/javascript")
                    # Each exported clip gets its own draft; keep the normal editor's draft
                    # and layout presets when two songs are open at the same time.
                    if path == "/pendant-storage.js":
                        script = (SOURCE / "pendant-storage.js").read_text(encoding="utf-8")
                        script = script.replace("DB='songcut-studio-drafts'",
                            "DB='songcut-studio-drafts'+(new URLSearchParams(location.search).get('clip') || '')")
                        return self.send_bytes(script.encode(), "application/javascript")
                    if path.startswith("/rr/clip/"):
                        item = self.server.clips.get(path.split("/")[-1])
                        if not item:
                            return self._send_json(404, {"error": "切片记录已过期，请从结果页重新打开"})
                        if path.startswith("/rr/clip/info/"):
                            return self._send_json(200, {k:item[k] for k in ("name", "song", "artist")})
                        if path.startswith("/rr/clip/media/"):
                            try:
                                stream = open(item["path"], "rb")
                            except OSError:
                                return self._send_json(404, {"error": "切片文件已移动或无法读取"})
                            with stream:
                                self.send_response(200)
                                self.send_header("Content-Type", "video/mp4")
                                self.send_header("Content-Length", str(os.fstat(stream.fileno()).st_size))
                                self.end_headers()
                                shutil.copyfileobj(stream, self.wfile)
                            return
                    return super().do_GET()

                def send_bytes(self, data, content_type):
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def do_POST(self):
                    if self.path in ("/rr/alive", "/rr/bye"):
                        if self.headers.get("Origin") != "http://127.0.0.1:%d" % self.server.server_port:
                            return self._send_json(403, {"error": "只接受制作台页面的请求"})
                        size = self.headers.get("Content-Length", "")
                        if not size.isdigit() or not 1 <= int(size) <= 64:
                            return self._send_json(400, {"error": "页面编号长度无效"})
                        value = self.rfile.read(int(size)).decode("ascii", "ignore")
                        if not value.isalnum():
                            return self._send_json(400, {"error": "页面编号无效"})
                        (alive if self.path == "/rr/alive" else bye)("studio-" + value)
                        return self._send_json(200, {"ok": True})
                    if self.path == "/api/shutdown":
                        return self._send_json(409, {"error": "请关闭制作台标签页，或从 ReplayRadio 停止本地服务"})
                    return super().do_POST()

            try:
                instance = studio.SingleInstanceServer(("127.0.0.1", 18766), Editor)
            except OSError:
                instance = studio.SingleInstanceServer(("127.0.0.1", 0), Editor)
            instance.clips = {}
            threading.Thread(target=instance.serve_forever, daemon=True).start()
            threading.Thread(target=studio.reap_stale_jobs, daemon=True).start()
            _server = instance
        if clip:
            _server.clips[clip["studioId"]] = clip
        url = "http://127.0.0.1:%d/?radio=%d" % (_server.server_port, main_port)
        return url + ("&clip=" + clip["studioId"] if clip else "")


def stop():
    if _server is not None:
        import server as studio
        studio.shutdown_studio(_server)
        _server.server_close()
