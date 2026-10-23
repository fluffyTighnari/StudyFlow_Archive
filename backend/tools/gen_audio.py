"""把讲稿合成音频（`V-SMP` 的听觉版）。

为什么用本机 TTS 而不是自己录：四版呈现比的是"同一内容用哪种媒介更舒服"，
所以四版必须**内容等价、语速一致**。人录音每次都不一样；同一个声音、同一语速只有 TTS 能保证。

流程：`*.md` 讲稿 → 去掉 Markdown 与"TTS 参数"节 → SAPI（Huihui，zh-CN）→ WAV → ffmpeg → m4a。
成品时长要求 3–5 分钟（清单 §3），不在区间就**报错**，不静默接受 ——
太短说明讲稿被截断了，太长说明语速不对，两种都会让"听觉版"与其他三版不可比。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "frontend" / "redesign" / "assets" / "items"
MIN_SECONDS, MAX_SECONDS = 150.0, 330.0  # 2.5–5.5 分钟（清单要求 3–5，留一点余量）

#: 语速。SAPI 的 rate 取值 -10..10；-1 比默认略慢，配合中文长句更稳。
RATE = -1
VOICE = "Microsoft Huihui Desktop"


def plain_text(markdown: str) -> str:
    """只留正文：去标题、引用、分隔线、加粗标记，砍掉「TTS 参数」那一节。"""
    if "## TTS 参数" in markdown:
        markdown = markdown.split("## TTS 参数")[0]
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">") or set(line) <= {"-"}:
            continue
        line = line.replace("**", "").replace("`", "")
        lines.append(line)
    return "\n\n".join(lines)


def synthesize(text_path: Path, wav_path: Path) -> None:
    script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice('{VOICE}')
$synth.Rate = {RATE}
$synth.SetOutputToWaveFile('{wav_path}')
$text = [System.IO.File]::ReadAllText('{text_path}', [System.Text.Encoding]::UTF8)
$synth.Speak($text)
$synth.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"TTS 失败：{result.stderr[:400]}")


def to_m4a(wav_path: Path, m4a_path: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-ac", "1", "-ar", "44100", "-b:a", "128k", str(m4a_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg 失败：{result.stderr[:400]}")


def duration_of(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe 失败：{result.stderr[:400]}")
    return float(result.stdout.strip())


def main() -> None:
    reports = []
    for folder in sorted(BASE.glob("SMP-*")):
        script_md = folder / f"{folder.name}-A.md"
        if not script_md.exists():
            continue
        text = plain_text(script_md.read_text(encoding="utf-8"))
        chars = len(re.sub(r"\s", "", text))
        text_path = folder / f"{folder.name}-A.txt"
        wav_path = folder / f"{folder.name}-A.wav"
        m4a_path = folder / f"{folder.name}-A.m4a"
        text_path.write_text(text, encoding="utf-8")

        synthesize(text_path, wav_path)
        to_m4a(wav_path, m4a_path)
        seconds = duration_of(m4a_path)
        wav_path.unlink(missing_ok=True)

        size_kb = m4a_path.stat().st_size / 1024
        ok = MIN_SECONDS <= seconds <= MAX_SECONDS
        print(
            f"  {m4a_path.name:14} {chars} 字  {seconds / 60:.2f} 分钟  "
            f"{size_kb:5.0f}KB  {'OK' if ok else '时长不合格'}"
        )
        reports.append({
            "file": f"assets/items/{folder.name}/{m4a_path.name}",
            "chars": chars,
            "seconds": round(seconds, 1),
            "kb": round(size_kb),
            "voice": VOICE,
            "rate": RATE,
            "text": f"assets/items/{folder.name}/{text_path.name}",
            "duration_ok": ok,
        })
        if not ok:
            print(
                f"      ✗ 时长 {seconds:.0f}s 不在 {MIN_SECONDS:.0f}–{MAX_SECONDS:.0f}s："
                "太短=讲稿被截断，太长=语速不对，两者都会让听觉版与其他三版不可比",
                file=sys.stderr,
            )

    manifest = BASE / "SMP-audio.json"
    manifest.write_text(json.dumps({"items": reports}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"清单写入 {manifest.relative_to(ROOT)}")
    if not all(report["duration_ok"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
