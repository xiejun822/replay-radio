# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.20,<2"]
# ///
"""Local stdio tools for ReplayRadio. Run with uv run --script tools/clip_mcp.py."""
import base64
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP
from mcp.types import AudioContent, ImageContent

import serve

mcp = FastMCP("ReplayRadio Clips")
data = Path(os.environ.get("REPLAYRADIO_DATA") or
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ReplayRadio/www/data")
flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def media_tool(name):
    path = os.environ.get(name.upper()) or shutil.which(name)
    if not path:
        raise ValueError(f"未找到 {name}，请配置 {name.upper()} 或 PATH")
    return path


def catalog():
    stations = serve._read_json_file(data / "stations.json") or {}
    stations = stations.get("stations", []) if isinstance(stations, dict) else stations
    for station in stations:
        folder = data / "stations" / str(station["id"])
        library = (serve._read_json_file(folder / "programs_cache.json")
                   or serve._read_json_file(folder / "programs.json"))
        if not library and station.get("main"):
            library = (serve._read_json_file(data / "programs_cache.json")
                       or serve._read_json_file(data / "programs.json"))
        for program in (library or {}).get("programs", []):
            yield station, folder, program


@mcp.tool()
def find_recordings(query: str = "", limit: int = 20) -> list[dict]:
    """Search this PC's cached recordings by title, date, station name or BV ID, newest first."""
    if not 1 <= limit <= 100:
        raise ValueError("limit 须在 1 到 100 之间")
    rows = []
    for station, _, p in catalog():
        row = dict(station=station.get("name"), station_id=str(station["id"]),
                   bvid=p["bvid"], title=p["title"], date=p.get("date", ""))
        if all(word.casefold() in " ".join(str(v) for v in row.values()).casefold()
               for word in query.split()):
            rows.append(row)
    return sorted(rows, key=lambda r: r["date"], reverse=True)[:limit]


def recording(station_id, bvid):
    for st, folder, p in catalog():
        if str(st["id"]) == station_id and p["bvid"] == bvid:
            return st, folder, p
    raise ValueError("本机清单里找不到这场录播，请先在 ReplayRadio 刷新该主播的录播列表")


def chapters(folder, program):
    segments = serve._read_segments_file(folder / "segments.js")
    rows = []
    for part in program["parts"]:
        cid = str(part["cid"])
        for seg in segments.get(cid, []):
            rows.append(dict(number=len(rows) + 1, page=part["page"], cid=cid,
                             start=seg["start"], end=seg["end"], label=seg.get("label", "")))
    return rows


@mcp.tool()
def list_chapters(station_id: str, bvid: str) -> list[dict]:
    """List 本场分段 exactly in UI order. Start/end are seconds relative to each part."""
    _, folder, program = recording(station_id, bvid)
    return chapters(folder, program)


def radio_api(path, payload=None):
    port = int(os.environ.get("REPLAYRADIO_PORT", "8765"))
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/ping", timeout=3) as r:
            if json.load(r).get("app") != "replay-radio":
                raise ValueError(f"{base} 不是 ReplayRadio 服务")
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"ReplayRadio 返回 {exc.code}：" + exc.read(2048).decode(errors="replace")) from None
    except urllib.error.URLError:
        raise ValueError(f"无法连接 {base}，请打开 ReplayRadio；自定义端口用 REPLAYRADIO_PORT 设置") from None


@mcp.tool()
def prepare_chapter(station_id: str, bvid: str, number: int, directory: str = "") -> dict:
    """Return an existing chapter MP4 or start a ReplayRadio export. Poll export_status with its batch ID."""
    station, folder, program = recording(station_id, bvid)
    rows = chapters(folder, program)
    if not 1 <= number <= len(rows):
        raise ValueError(f"本场共有 {len(rows)} 段，请核对段号")
    row = rows[number - 1]
    outdir = Path(directory or serve._clip_default_dir()).expanduser().resolve()
    source = dict(bvid=bvid, cid=row["cid"], start=row["start"], end=row["end"])
    name = serve._clip_name(program["title"], number, row["start"], row["end"])
    if outdir.is_dir():
        base = Path(name).stem
        dur = max(0.5, min(serve.CLIP_MAX_SEC, row["end"] - row["start"]))
        found = serve._clip_existing(str(outdir), base, base, source, dur, media_tool("ffmpeg"))
        if found:
            return {"path": found[0], "reused": True}
    item = dict(source, title=program["title"], n=number, artist=station.get("name", ""))
    return radio_api("/api/clip/start", {"dir": str(outdir), "items": [item]})


@mcp.tool()
def export_status(batch_id: str) -> dict:
    """Read an active or completed ReplayRadio chapter export, including output path and errors."""
    state = radio_api("/api/clip/status")
    if state.get("batchId") != batch_id:
        raise ValueError("该任务已被另一批替代，可用 prepare_chapter 查找已保存的文件")
    return {k: state.get(k) for k in ("running", "stage", "percent", "filename", "done", "error")}


def input_media(path):
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".flv", ".mkv", ".mov", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        raise ValueError("请选择本地音视频文件")
    return source


def run_media(args, timeout):
    p = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    if p.returncode:
        raise ValueError(p.stderr.decode("utf-8", "replace")[-800:] or "读取素材失败")
    return p.stdout


@mcp.tool()
def inspect_media(path: str) -> dict:
    """Read duration in seconds and stream types of a saved clip or local recording."""
    source = input_media(path)
    raw = run_media([media_tool("ffprobe"), "-v", "error", "-show_entries",
                     "format=duration:stream=codec_type,codec_name,width,height", "-of", "json",
                     str(source)], 30)
    return json.loads(raw)


def sample_command(path, at):
    if not math.isfinite(at) or at < 0:
        raise ValueError("取样时间须是大于等于零的秒数")
    return [media_tool("ffmpeg"), "-v", "error", "-nostdin", "-ss", str(at), "-i", str(input_media(path))]


@mcp.tool()
def preview_frame(path: str, at: float) -> ImageContent:
    """See one video frame at local-file time in seconds to inspect titles, lyrics or a cut."""
    frame = run_media(sample_command(path, at) + ["-map", "0:V:0", "-frames:v", "1",
                      "-vf", "scale=960:-2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"], 30)
    if not frame:
        raise ValueError("该时间点没有画面")
    return ImageContent(type="image", mimeType="image/jpeg", data=base64.b64encode(frame).decode())


@mcp.tool()
def listen(path: str, at: float, seconds: float = 8) -> AudioContent:
    """Listen around a proposed boundary. Times use local-file seconds. Returns up to 20 seconds."""
    if not 0 < seconds <= 20:
        raise ValueError("试听时长须在 0 到 20 秒之间")
    audio = run_media(sample_command(path, at) + ["-map", "0:a:0", "-t", str(seconds),
                      "-ac", "1", "-ar", "22050", "-f", "mp3", "-write_xing", "0", "-id3v2_version", "0", "pipe:1"], 30)
    if not audio:
        raise ValueError("该时间点没有声音数据")
    return AudioContent(type="audio", mimeType="audio/mpeg", data=base64.b64encode(audio).decode())


@mcp.tool()
def cut_media(path: str, start: float, end: float, output: str) -> dict:
    """Export a precise range to a NEW .mp4 or .mp3 path. Times are seconds FROM THE LOCAL FILE START."""
    source = input_media(path)
    dest = Path(output).expanduser().resolve()
    if dest.suffix.lower() not in {".mp4", ".mp3"}:
        raise ValueError("输出文件扩展名须为 .mp4 或 .mp3")
    if dest.exists():
        raise ValueError(f"目标已存在：{dest}")
    duration = float(inspect_media(str(source))["format"]["duration"])
    if not 0 <= start < end <= duration or not math.isfinite(end):
        raise ValueError(f"起止时间须满足 0 ≤ 起点 < 终点 ≤ {duration:.3f} 秒")
    codec = (["-map", "0:V:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
              "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"] if dest.suffix.lower() == ".mp4"
             else ["-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".clip-") as tmp:
        staged = Path(tmp) / dest.name
        run_media(sample_command(source, start) + ["-t", str(end-start), *codec,
                  "-map_chapters", "-1", str(staged)], 3600)
        if os.name == "nt":
            staged.rename(dest)  # On Windows rename refuses an existing destination.
        else:
            os.link(staged, dest)
    return {"path": str(dest), "seconds": end - start}


if __name__ == "__main__":
    mcp.run()
