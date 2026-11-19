import os
import re
import json
import math
import shutil
import tempfile
import subprocess
from collections import Counter
from urllib.parse import urlparse, parse_qs

import streamlit as st
import imageio_ffmpeg
from faster_whisper import WhisperModel

# Optional dependency used only for user-authorized YouTube videos.
try:
    import yt_dlp
except ImportError:
    yt_dlp = None


st.set_page_config(
    page_title="Neeraj AI Clipper",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 Neeraj AI Clipper")
st.caption("Turn videos you own or have permission to use into Shorts, with clip ideas and YouTube SEO.")

st.info(
    "Free-first prototype: processing runs on the hosting machine and is subject to its CPU, memory, "
    "time and storage limits. Use YouTube links only for videos you own or are authorized to process. "
    "This app does not bypass private, paid, age-restricted or other access controls."
)

# ---------- Helpers ----------

STOP_WORDS = {
    "about", "after", "again", "also", "among", "because", "been", "before",
    "being", "between", "both", "could", "does", "doing", "down", "each",
    "even", "every", "from", "further", "have", "having", "here", "hers",
    "himself", "into", "itself", "just", "like", "more", "most", "much",
    "must", "only", "other", "over", "same", "should", "some", "such",
    "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "until", "very", "what", "when",
    "where", "which", "while", "with", "would", "your", "you", "the",
    "and", "for", "are", "was", "were", "has", "had", "his", "her",
    "she", "him", "our", "out", "not", "but", "can", "all", "any",
    "who", "why", "how", "its", "it's", "get", "got", "one", "two",
    "now", "well", "yeah", "yes", "okay", "um", "uh",
}

HOOK_TERMS = {
    "secret", "mistake", "never", "always", "best", "worst", "shocking",
    "surprising", "important", "truth", "reason", "because", "discover",
    "amazing", "powerful", "simple", "easy", "change", "learn", "tip",
    "problem", "solution", "money", "free", "success", "fail", "imagine",
}


def run_command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def validate_youtube_url(url):
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    allowed = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "www.youtu.be",
    }
    if parsed.scheme not in ("http", "https") or host not in allowed:
        raise ValueError("Please enter a valid youtube.com or youtu.be URL.")
    return url.strip()


def download_youtube_video(url, folder):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Add yt-dlp to requirements.txt and redeploy.")

    output_template = os.path.join(folder, "source.%(ext)s")
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "socket_timeout": 20,
        "retries": 1,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "YouTube video"
        duration = info.get("duration")
    candidates = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("source.") and os.path.isfile(os.path.join(folder, name))
    ]
    if not candidates:
        raise RuntimeError("The video could not be downloaded. It may be unavailable, restricted, or unsupported.")
    return candidates[0], title, duration


def transcribe_video(video_path, folder):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    audio_path = os.path.join(folder, "audio.wav")
    run_command([
        ffmpeg, "-y", "-i", video_path, "-vn",
        "-ac", "1", "-ar", "16000", audio_path
    ])
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, vad_filter=True, word_timestamps=False)
    return list(segments)


def score_segment(text):
    lower = text.lower()
    words = re.findall(r"[a-zA-Z0-9']+", lower)
    if not words:
        return 0
    score = min(len(words), 110) * 0.15
    score += sum(2.5 for term in HOOK_TERMS if re.search(r"\b" + re.escape(term) + r"\b", lower))
    score += min(text.count("!"), 3) * 1.5
    score += min(text.count("?"), 3) * 1.0
    # Reward a reasonably complete spoken thought, not just a tiny fragment.
    if len(words) >= 35:
        score += 3
    if len(words) < 12:
        score -= 5
    return score


def choose_clips(segments, clip_count=5, min_seconds=30, max_seconds=60):
    if not segments:
        return []

    total_end = max(float(s.end) for s in segments)
    # Candidate windows every 5 seconds. Score transcript content inside each window.
    candidates = []
    step = 5
    for start in range(0, max(1, int(total_end) - min_seconds + 1), step):
        end = min(start + max_seconds, total_end)
        if end - start < min_seconds:
            continue
        selected = [s for s in segments if float(s.start) < end and float(s.end) > start]
        if not selected:
            continue
        text = " ".join(s.text.strip() for s in selected).strip()
        duration = end - start
        if not text:
            continue
        score = score_segment(text)
        # Prefer windows with sufficient speech, without claiming this predicts virality.
        words = len(re.findall(r"\w+", text))
        speech_density = min(words / max(duration, 1), 2.5)
        score += speech_density * 2
        candidates.append({
            "start": float(start),
            "end": float(end),
            "text": text,
            "score": score,
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    chosen = []
    for candidate in candidates:
        # Avoid heavily overlapping clips.
        if all(
            candidate["end"] <= item["start"] + 8 or candidate["start"] >= item["end"] - 8
            for item in chosen
        ):
            chosen.append(candidate)
        if len(chosen) == clip_count:
            break
import os
import re
import json
import math
import shutil
import tempfile
import subprocess
from collections import Counter
from urllib.parse import urlparse, parse_qs

import streamlit as st
import imageio_ffmpeg
from faster_whisper import WhisperModel

# Optional dependency used only for user-authorized YouTube videos.
try:
    import yt_dlp
except ImportError:
    yt_dlp = None


st.set_page_config(
    page_title="Neeraj AI Clipper",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 Neeraj AI Clipper")
st.caption("Turn videos you own or have permission to use into Shorts, with clip ideas and YouTube SEO.")

st.info(
    "Free-first prototype: processing runs on the hosting machine and is subject to its CPU, memory, "
    "time and storage limits. Use YouTube links only for videos you own or are authorized to process. "
    "This app does not bypass private, paid, age-restricted or other access controls."
)

# ---------- Helpers ----------

STOP_WORDS = {
    "about", "after", "again", "also", "among", "because", "been", "before",
    "being", "between", "both", "could", "does", "doing", "down", "each",
    "even", "every", "from", "further", "have", "having", "here", "hers",
    "himself", "into", "itself", "just", "like", "more", "most", "much",
    "must", "only", "other", "over", "same", "should", "some", "such",
    "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "until", "very", "what", "when",
    "where", "which", "while", "with", "would", "your", "you", "the",
    "and", "for", "are", "was", "were", "has", "had", "his", "her",
    "she", "him", "our", "out", "not", "but", "can", "all", "any",
    "who", "why", "how", "its", "it's", "get", "got", "one", "two",
    "now", "well", "yeah", "yes", "okay", "um", "uh",
}

HOOK_TERMS = {
    "secret", "mistake", "never", "always", "best", "worst", "shocking",
    "surprising", "important", "truth", "reason", "because", "discover",
    "amazing", "powerful", "simple", "easy", "change", "learn", "tip",
    "problem", "solution", "money", "free", "success", "fail", "imagine",
}


def run_command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def validate_youtube_url(url):
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    allowed = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "www.youtu.be",
    }
    if parsed.scheme not in ("http", "https") or host not in allowed:
        raise ValueError("Please enter a valid youtube.com or youtu.be URL.")
    return url.strip()


def download_youtube_video(url, folder):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Add yt-dlp to requirements.txt and redeploy.")

    output_template = os.path.join(folder, "source.%(ext)s")
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "socket_timeout": 20,
        "retries": 1,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "YouTube video"
        duration = info.get("duration")
    candidates = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("source.") and os.path.isfile(os.path.join(folder, name))
    ]
    if not candidates:
        raise RuntimeError("The video could not be downloaded. It may be unavailable, restricted, or unsupported.")
    return candidates[0], title, duration


def transcribe_video(video_path, folder):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    audio_path = os.path.join(folder, "audio.wav")
    run_command([
        ffmpeg, "-y", "-i", video_path, "-vn",
        "-ac", "1", "-ar", "16000", audio_path
    ])
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, vad_filter=True, word_timestamps=False)
    return list(segments)


def score_segment(text):
    lower = text.lower()
    words = re.findall(r"[a-zA-Z0-9']+", lower)
    if not words:
        return 0
    score = min(len(words), 110) * 0.15
    score += sum(2.5 for term in HOOK_TERMS if re.search(r"\b" + re.escape(term) + r"\b", lower))
    score += min(text.count("!"), 3) * 1.5
    score += min(text.count("?"), 3) * 1.0
    # Reward a reasonably complete spoken thought, not just a tiny fragment.
    if len(words) >= 35:
        score += 3
    if len(words) < 12:
        score -= 5
    return score


def choose_clips(segments, clip_count=5, min_seconds=30, max_seconds=60):
    if not segments:
        return []

    total_end = max(float(s.end) for s in segments)
    # Candidate windows every 5 seconds. Score transcript content inside each window.
    candidates = []
    step = 5
    for start in range(0, max(1, int(total_end) - min_seconds + 1), step):
        end = min(start + max_seconds, total_end)
        if end - start < min_seconds:
            continue
        selected = [s for s in segments if float(s.start) < end and float(s.end) > start]
        if not selected:
            continue
        text = " ".join(s.text.strip() for s in selected).strip()
        duration = end - start
        if not text:
            continue
        score = score_segment(text)
        # Prefer windows with sufficient speech, without claiming this predicts virality.
        words = len(re.findall(r"\w+", text))
        speech_density = min(words / max(duration, 1), 2.5)
        score += speech_density * 2
        candidates.append({
            "start": float(start),
            "end": float(end),
            "text": text,
            "score": score,
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    chosen = []
    for candidate in candidates:
        # Avoid heavily overlapping clips.
        if all(
            candidate["end"] <= item["start"] + 8 or candidate["start"] >= item["end"] - 8
            for item in chosen
        ):
            chosen.append(candidate)
        if len(chosen) == clip_count:
            break

    # If the source is short, allow the best distinct windows even if overlap is unavoidable.
    if len(chosen) < min(clip_count, len(candidates)):
        for candidate in candidates:
            if candidate not in chosen:
                chosen.append(candidate)
            if len(chosen) == clip_count:
                break
    return sorted(chosen, key=lambda item: item["score"], reverse=True)


def make_srt(segments, start, end, output_path):
    def stamp(seconds):
        seconds = max(0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    entries = []
    idx = 1
    for seg in segments:
        seg_start = max(float(seg.start), start)
        seg_end = min(float(seg.end), end)
        text = seg.text.strip()
        if seg_end > seg_start and text:
            entries.append(
                f"{idx}\n{stamp(seg_start - start)} --> {stamp(seg_end - start)}\n{text}\n"
            )
            idx += 1
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(entries))


def seo_package(title, transcript):
    words = re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", transcript.lower())
    common = [
        word for word, count in Counter(words).most_common(12)
        if word not in STOP_WORDS and count >= 1
    ]
    topic = ", ".join(common[:4]) if common else "this topic"
    clean_title = re.sub(r"\s+", " ", title).strip()
    short_title = clean_title[:65].rstrip(" -:|") if clean_title else "Interesting moment"
    seo_title = f"{short_title} | Key Insight"
    hashtags = ["#Shorts"] + [
        "#" + re.sub(r"[^A-Za-z0-9]", "", word).capitalize()
        for word in common[:4]
    ]
    description = (
        f"A short highlight about {topic}. Watch the key moment and share your thoughts.\n\n"
        "Subscribe for more clips. This Short is an excerpt from the original video."
    )
    return {
        "title": seo_title[:100],
        "description": description,
        "hashtags": " ".join(hashtags),
        "keywords": ", ".join(common[:10]),
        "thumbnail_text": (common[0].upper() if common else "KEY MOMENT")[:28],
    }


def render_vertical_clip(ffmpeg, source, output, start, end):
    duration = max(1, end - start)
    # Blurred background keeps the full original frame visible in a vertical canvas.
    vf = (
        "split[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:10[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    run_command([
        ffmpeg, "-y", "-ss", str(start), "-i", source, "-t", str(duration),
        "-filter_complex", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output
    ])


# ---------- Interface ----------

st.subheader("1. Add your source video")
source_mode = st.radio(
    "Choose how to provide your video",
    ["YouTube link", "Upload video file"],
    horizontal=True,
)

youtube_url = ""
uploaded_video = None
if source_mode == "YouTube link":
    youtube_url = st.text_input(
        "YouTube video URL",
        placeholder="https://www.youtube.com/watch?v=...",
        help="Use only videos you own or have permission to download and edit.",
    )
else:
    uploaded_video = st.file_uploader(
        "Upload a video you own or are authorized to edit",
        type=["mp4", "mov", "mkv", "webm"],
    )

st.subheader("2. Clip settings")
col1, col2 = st.columns(2)
with col1:
    clip_count = st.number_input("Number of clips", min_value=5, max_value=5, value=5, step=1)
with col2:
    st.selectbox("Clip duration", ["30–60 seconds"], index=0, disabled=True)

st.caption("The app selects up to five distinct 30–60 second highlights. If the source is too short, fewer clips may be possible.")

generate = st.button("✨ Analyze video and create 5 Shorts", type="primary", use_container_width=True)

if generate:
    if source_mode == "YouTube link" and not youtube_url.strip():
        st.error("Paste a YouTube link first.")
        st.stop()
    if source_mode == "Upload video file" and uploaded_video is None:
        st.error("Upload a video first.")
        st.stop()

    with tempfile.TemporaryDirectory() as workdir:
        try:
            video_title = "Your video"
            source_path = None

            with st.status("Preparing your source video...", expanded=True) as status:
                if source_mode == "YouTube link":
                    valid_url = validate_youtube_url(youtube_url)
                    st.write("Checking the supplied YouTube link...")
                    source_path, video_title, _ = download_youtube_video(valid_url, workdir)
                else:
                    source_path = os.path.join(workdir, "uploaded_video")
                    with open(source_path, "wb") as f:
                        f.write(uploaded_video.getbuffer())
                    video_title = getattr(uploaded_video, "name", "Uploaded video")

                st.write("Transcribing speech with the open-source Whisper model...")
                transcript_segments = transcribe_video(source_path, workdir)
                if not transcript_segments:
                    raise RuntimeError("No speech was detected. Try a video with clear spoken audio.")

                st.write("Scoring candidate moments and selecting up to five clips...")
                selected_clips = choose_clips(transcript_segments, clip_count=5)

                if not selected_clips:
                    raise RuntimeError("Could not find enough spoken content for clips of 30–60 seconds.")

                status.update(label="Analysis complete", state="complete", expanded=False)

            st.success(f"Created {len(selected_clips)} clip plan(s). The score is a heuristic highlight score, not a guarantee of virality.")

            for index, clip in enumerate(selected_clips, start=1):
                st.divider()
                st.subheader(f"Clip {index}")
                st.write(f"**Time:** {int(clip['start'])}s – {int(clip['end'])}s")
                st.write(f"**Highlight potential:** {round(min(100, clip['score'] * 2.5))}/100")
                st.caption("This is a transcript-based estimate using hooks, speech density and punctuation; it is not a prediction of actual views.")
                st.write(clip["text"][:1200] + ("..." if len(clip["text"]) > 1200 else ""))

                clip_path = os.path.join(workdir, f"neeraj_short_{index}.mp4")
                srt_path = os.path.join(workdir, f"neeraj_short_{index}.srt")
                with st.spinner(f"Rendering clip {index} in vertical format..."):
                    render_vertical_clip(
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        source_path,
                        clip_path,
                        clip["start"],
                        clip["end"],
                    )
                    make_srt(transcript_segments, clip["start"], clip["end"], srt_path)

                st.video(clip_path)
                with open(clip_path, "rb") as f:
                    st.download_button(
                        "⬇️ Download vertical video",
                        data=f.read(),
                        file_name=f"neeraj_short_{index}.mp4",
                        mime="video/mp4",
                        key=f"video_{index}",
                    )
                with open(srt_path, "rb") as f:
                    st.download_button(
                        "Download subtitles (.srt)",
                        data=f.read(),
                        file_name=f"neeraj_short_{index}.srt",
                        mime="application/x-subrip",
                        key=f"srt_{index}",
                    )

                seo = seo_package(video_title, clip["text"])
                with st.expander(f"📈 YouTube SEO package – Clip {index}"):
                    st.text_input("Suggested title", seo["title"], key=f"title_{index}")
                    st.text_area("Description", seo["description"], key=f"desc_{index}")
                    st.text_input("Hashtags", seo["hashtags"], key=f"hashtags_{index}")
                    st.text_input("Keywords / tags", seo["keywords"], key=f"keywords_{index}")
                    st.text_input("Thumbnail text idea", seo["thumbnail_text"], key=f"thumb_{index}")

        except Exception as exc:
            st.error("The video could not be processed.")
            st.exception(exc)
            st.info(
                "Common causes: unsupported/restricted YouTube video, hosting memory or time limit, "
                "large video, or a missing dependency. Try a shorter video or upload a video file you are authorized to use."
            )

else:
    st.markdown(
        """
        **Your workflow**
        1. Paste a YouTube link for a video you own or have permission to use, or upload the source file.
        2. The app transcribes the audio and searches for promising moments.
        3. It renders up to five vertical clips, subtitle files and a suggested SEO package for each.
        """
    )
￼EnterIf the source is short, allow the best distinct windows even if overlap is unavoidable.
    if len(chosen) < min(clip_count, len(candidates)):
        for candidate in candidates:
            if candidate not in chosen:
                chosen.append(candidate)
            if len(chosen) == clip_count:
                break
    return sorted(chosen, key=lambda item: item["score"], reverse=True)


def make_srt(segments, start, end, output_path):
    def stamp(seconds):
        seconds = max(0, seconds)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    entries = []
    idx = 1
    for seg in segments:
        seg_start = max(float(seg.start), start)
        seg_end = min(float(seg.end), end)
        text = seg.text.strip()
        if seg_end > seg_start and text:
            entries.append(
                f"{idx}\n{stamp(seg_start - start)} --> {stamp(seg_end - start)}\n{text}\n"
            )
            idx += 1
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(entries))


def seo_package(title, transcript):
    words = re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", transcript.lower())
    common = [
        word for word, count in Counter(words).most_common(12)
        if word not in STOP_WORDS and count >= 1
    ]
    topic = ", ".join(common[:4]) if common else "this topic"
    clean_title = re.sub(r"\s+", " ", title).strip()
    short_title = clean_title[:65].rstrip(" -:|") if clean_title else "Interesting moment"
    seo_title = f"{short_title} | Key Insight"
    hashtags = ["#Shorts"] + [
        "#" + re.sub(r"[^A-Za-z0-9]", "", word).capitalize()
        for word in common[:4]
    ]
    description = (
        f"A short highlight about {topic}. Watch the key moment and share your thoughts.\n\n"
        "Subscribe for more clips. This Short is an excerpt from the original video."
    )
    return {
        "title": seo_title[:100],
        "description": description,
        "hashtags": " ".join(hashtags),
        "keywords": ", ".join(common[:10]),
        "thumbnail_text": (common[0].upper() if common else "KEY MOMENT")[:28],
    }


def render_vertical_clip(ffmpeg, source, output, start, end):
    duration = max(1, end - start)
    # Blurred background keeps the full original frame visible in a vertical canvas.
    vf = (
        "split[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:10[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    run_command([
        ffmpeg, "-y", "-ss", str(start), "-i", source, "-t", str(duration),
        "-filter_complex", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output
    ])


# ---------- Interface ----------

st.subheader("1. Add your source video")
source_mode = st.radio(
    "Choose how to provide your video",
    ["YouTube link", "Upload video file"],
    horizontal=True,
)

youtube_url = ""
uploaded_video = None
if source_mode == "YouTube link":
    youtube_url = st.text_input(
        "YouTube video URL",
        placeholder="https://www.youtube.com/watch?v=...",
        help="Use only videos you own or have permission to download and edit.",
    )
else:
    uploaded_video = st.file_uploader(
        "Upload a video you own or are authorized to edit",
        type=["mp4", "mov", "mkv", "webm"],
    )

st.subheader("2. Clip settings")
col1, col2 = st.columns(2)
with col1:
    clip_count = st.number_input("Number of clips", min_value=5, max_value=5, value=5, step=1)
with col2:
