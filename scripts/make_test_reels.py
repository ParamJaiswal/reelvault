"""Synthesize realistic test Reels locally (TTS speech + text overlays + ffmpeg).

These are REAL media files with real speech and real on-screen text — used for
integration tests and the AI evaluation benchmark. No external services.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT = Path("D:/reel-knowledge/testmedia")

SCRIPTS = {
    # name -> (spoken script, [ (t, "ON SCREEN TEXT") ], caption)
    "job_reel": (
        "Hiring alert! Zylker Analytics is hiring Data Analyst interns in Bangalore. "
        "This is a fresher role, zero to one years experience. You need SQL, Python and Excel. "
        "Stipend is twenty five thousand per month, and top performers get a pre placement offer "
        "of eight to ten lakh per annum. Apply before September fifteenth at zylker dot example dot com "
        "slash careers. Link is in the caption.",
        [(0.5, "WE'RE HIRING"), (2.5, "Data Analyst Intern"), (5.0, "Location: Bangalore"),
         (7.0, "Skills: SQL Python Excel"), (9.5, "Apply by 15 Sept"),
         (12.0, "zylker.example.com/careers")],
        "Zylker Analytics Data Analyst Intern hiring! Bangalore. Freshers apply https://zylker.example.com/careers #hiring #dataanalyst",
    ),
    "edu_reel": (
        "RAG explained in sixty seconds. RAG means Retrieval Augmented Generation. "
        "Step one, chunk your documents. Step two, embed chunks into a vector database like FAISS or Qdrant. "
        "Step three, retrieve the top matching chunks at query time. Step four, stuff them into the LLM prompt. "
        "That's how language models answer questions about your private data without retraining.",
        [(1.0, "RAG in 60 seconds"), (4.0, "1. Chunk documents"),
         (6.0, "2. Embed -> vector DB"), (8.5, "FAISS · Qdrant · Chroma"),
         (11.0, "3. Retrieve top-k"), (13.5, "4. Prompt the LLM")],
        "RAG architecture explained simply #ai #machinelearning #llm",
    ),
    "tool_reel": (
        "Stop paying for transcription. Whisper runs completely offline on your laptop. "
        "It transcribes audio in over ninety languages, it's open source from OpenAI, "
        "and tools like faster whisper make it run five times faster on a normal GPU. "
        "Pricing? Free, forever. Link to the GitHub repo is on screen.",
        [(0.5, "FREE transcription tool"), (3.0, "Whisper — runs offline"),
         (6.0, "90+ languages"), (9.0, "github.com/openai/whisper")],
        "You don't need paid transcription. Whisper = free + local #aitools #productivity",
    ),
}

SILENT_TAIL = 1.5


def synth_speech(text: str, out_wav: str) -> None:
    """Windows SAPI TTS via PowerShell — real spoken audio, no cloud."""
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        f"$s.Rate=1; $s.Speak('{text.replace(chr(39), '')}'); $s.Dispose()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"SAPI TTS failed: {r.stderr[:300]}")


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True)
    return float(out.stdout.strip())


def build_reel(name: str) -> Path:
    speech_text, overlays, _caption = SCRIPTS[name]
    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / f"{name}_speech.wav"
    mp4 = OUT / f"{name}.mp4"

    if not wav.exists():
        print(f"  TTS: {name}…"); sys.stdout.flush()
        synth_speech(speech_text, str(wav))
    dur = probe_duration(str(wav)) + SILENT_TAIL

    # drawtext filters with enable-between windows, styled like reel captions
    parts = []
    for i, (t, txt) in enumerate(overlays):
        # ffmpeg single-quote sections have NO escapes: drop risky chars outright
        safe = (txt.replace("\\", " ").replace("'", "").replace("%", "pct")
                .replace(":", " "))
        safe = safe.replace(",", "\\,")
        end = t + 3.0
        parts.append(
            f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
            f"text='{safe}':fontsize=52:fontcolor=white:"
            f"borderw=4:bordercolor=black@0.85:x=(w-text_w)/2:y=h*{0.16 + 0.09*i:.2f}:"
            f"enable='between(t,{t},{end})'")
    vf = ",".join(parts) if parts else "null"
    bg = "gradients=size=720x1278:c0=0x0b1220:c1=0x101a30:speed=0.02"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", bg,
        "-i", str(wav),
        "-af", f"apad=pad_dur={SILENT_TAIL}",
        "-vf", vf,
        "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest", "-r", "24", str(mp4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {name}: {r.stderr[:400]}")
    print(f"  built {mp4.name} ({dur:.1f}s, {mp4.stat().st_size//1024} KB)")
    return mp4


def build_all() -> list[Path]:
    return [build_reel(n) for n in SCRIPTS]


if __name__ == "__main__":
    paths = build_all()
    print("TEST_MEDIA:", [str(p) for p in paths])
