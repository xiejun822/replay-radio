"""Host the pinned SongCut Studio on a separate loopback origin."""
import asyncio
import math
import os
import shutil
import sys
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
SOURCE = BASE / "vendor" / "songcut-studio"
_server = None
_lock = threading.Lock()


MEDIA_FORMATS = "mov,mp3,wav,flac,ogg,aac,matroska,webm,avi,aiff"


def receive_media(handler, source, ffmpeg):
    length = handler.headers.get("Content-Length", "")
    if not length.isdigit() or not 0 < int(length) <= 4 * 1024**3:
        raise ValueError("素材为空或超过 4 GB")
    if not ffmpeg:
        raise ValueError("未找到处理素材所需的 FFmpeg")
    remaining = int(length)
    with source.open("wb") as stream:
        while remaining:
            block = handler.rfile.read(min(remaining, 1024**2))
            if not block:
                raise ValueError("素材传输中断，请重试")
            stream.write(block)
            remaining -= len(block)


async def identify_sample(sample):
    from shazamio import Shazam
    async with Shazam() as client:
        return await asyncio.wait_for(client.recognize(sample), timeout=20)


def recognize_media(handler, ffmpeg, lyrics):
    if handler.headers.get("Origin") != "http://127.0.0.1:%d" % handler.server.server_port:
        return handler._send_json(403, {"error": "请从本地歌切制作台发起识别"})
    try:
        start = float(handler.headers.get("X-Sample-Start", ""))
    except ValueError:
        raise ValueError("取样位置必须是秒数") from None
    if not math.isfinite(start) or start < 0:
        raise ValueError("取样位置必须是非负的有限秒数")
    with tempfile.TemporaryDirectory(prefix="replayradio-identify-") as temp:
        source = Path(temp) / "source"
        receive_media(handler, source, ffmpeg)
        result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file",
            "-format_whitelist", MEDIA_FORMATS, "-ss", str(start), "-i", str(source),
            "-map", "0:a:0", "-t", "12", "-ar", "16000", "-ac", "1", "-f", "flac", "pipe:1"],
            capture_output=True, timeout=40, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not result.stdout:
        raise ValueError("无法读取该位置的音轨，请检查素材或移动播放位置")
    try:
        answer = asyncio.run(identify_sample(result.stdout))
    except ImportError:
        return handler._send_json(503, {"error": "缺少 ShazamIO。源码版请安装 shazamio==0.9.0"})
    except asyncio.TimeoutError:
        return handler._send_json(504, {"error": "识曲服务超时，请稍后重试"})
    except Exception as exc:
        return handler._send_json(502, {"error": "识曲服务请求失败：" + str(exc)[:140]})
    track = answer.get("track") or {}
    if not track.get("title"):
        return handler._send_json(200, {"matched": False})
    title, artist = track["title"], track.get("subtitle", "")
    found = lyrics(title, artist)[1] if lyrics else {}
    return handler._send_json(200, {"matched": True, "title": title, "artist": artist, "lyrics": found})


def trim_media(handler, ffmpeg):
    headers = handler.headers
    if headers.get("Origin") != "http://127.0.0.1:%d" % handler.server.server_port:
        return handler._send_json(403, {"error": "请从本地歌切制作台发起裁剪"})
    kind = headers.get("X-Media-Kind")
    if kind not in ("audio", "video"):
        raise ValueError("请选择音频或视频素材")
    try:
        start = float(headers.get("X-Trim-Start", ""))
        end = float(headers.get("X-Trim-End", ""))
    except ValueError:
        raise ValueError("起点和终点必须是秒数") from None
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
        raise ValueError("起点必须大于等于零，终点必须晚于起点")
    with tempfile.TemporaryDirectory(prefix="replayradio-trim-") as temp:
        source = Path(temp) / "source"
        receive_media(handler, source, ffmpeg)
        # Accept self-contained media containers, including MP4 and MP3.
        input_options = ["-protocol_whitelist", "file", "-format_whitelist",
                         MEDIA_FORMATS]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        probe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        probe = Path(ffmpeg).with_name(probe_name)
        probe = str(probe) if probe.is_file() else shutil.which("ffprobe")
        if not probe:
            raise ValueError("未找到读取素材时长所需的 ffprobe")
        result = subprocess.run([probe, "-v", "error", *input_options, "-show_entries",
                                 "format=duration", "-of", "csv=p=0", str(source)],
                                capture_output=True, timeout=60, creationflags=flags)
        try:
            duration = float(result.stdout.strip())
        except ValueError:
            raise ValueError("无法读取素材时长，请检查文件格式") from None
        # Browser and container durations can differ by one frame or MP3 padding.
        if not math.isfinite(duration) or start >= duration or end > duration + 0.1:
            raise ValueError("裁剪范围超出素材时长")
        if kind == "video":
            output = Path(temp) / "clip.mp4"
            mime = "video/mp4"
            codec = ["-map", "0:V:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        else:
            output = Path(temp) / "clip.mp3"
            mime = "audio/mpeg"
            codec = ["-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2"]
        result = subprocess.run([ffmpeg, "-nostdin", "-v", "error", *input_options,
                                 "-ss", str(start), "-i", str(source), "-t", str(min(end, duration) - start),
                                 *codec, "-map_chapters", "-1", str(output)],
                                capture_output=True, timeout=3600, creationflags=flags)
        if result.returncode or not output.stat().st_size:
            detail = result.stderr.decode("utf-8", "replace").strip()[-360:]
            raise ValueError("裁剪失败，请检查素材的音视频轨道。" + detail)
        with output.open("rb") as stream:
            handler.send_response(200)
            handler.send_header("Content-Type", mime)
            handler.send_header("Content-Length", str(output.stat().st_size))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            shutil.copyfileobj(stream, handler.wfile)


def open_editor(main_port, ffmpeg, alive, bye, clip=None, lyrics=None):
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
                        page = page.replace("</body>", '<script src="/rr/recognize.js"></script><script src="/rr/trim.js"></script><script src="/rr/import.js"></script></body>')
                        return self.send_bytes(page.encode(), "text/html; charset=utf-8")
                    if path == "/rr/import.js":
                        script = (BASE / "assets" / "studio-import.js").read_bytes()
                        return self.send_bytes(script, "application/javascript")
                    if path == "/rr/recognize.js":
                        return self.send_bytes((BASE / "assets" / "studio-recognize.js").read_bytes(), "application/javascript")
                    if path == "/rr/trim.js":
                        return self.send_bytes((BASE / "assets" / "studio-trim.js").read_bytes(), "application/javascript")
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
                    if self.path in ("/rr/trim", "/rr/recognize"):
                        try:
                            if self.path == "/rr/recognize":
                                return recognize_media(self, studio.find_ffmpeg(), lyrics)
                            return trim_media(self, studio.find_ffmpeg())
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            return
                        except ValueError as exc:
                            return self._send_json(400, {"error": str(exc)})
                        except subprocess.TimeoutExpired:
                            return self._send_json(504, {"error": "处理素材超时，请换一个位置或较短的素材重试"})
                        except OSError as exc:
                            return self._send_json(500, {"error": "处理素材失败：" + str(exc)})
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

            class EditorServer(studio.SingleInstanceServer):
                # Chromium opens several connections while it discovers the editor's scripts.
                request_queue_size = 128

            try:
                instance = EditorServer(("127.0.0.1", 18766), Editor)
            except OSError:
                instance = EditorServer(("127.0.0.1", 0), Editor)
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
