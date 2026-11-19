import os
import re
import tempfile
import subprocess
from collections import Counter
from urllib.parse import urlparse

import streamlit as st
import imageio_ffmpeg
from faster_whisper import WhisperModel

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

st.set_page_config(page_title="Neeraj AI Clipper", page_icon="🎬", layout="wide")
st.title("🎬 Neeraj AI Clipper")
st.caption("Turn videos you own or have permission to use into vertical Shorts, with subtitles and suggested YouTube SEO.")
st.info("Use only videos you own or are authorized to edit. Processing runs on the hosting machine and may be limited by its CPU, memory, time, and storage.")

STOP_WORDS = {
    "about", "after", "again", "also", "among", "because", "been", "before", "being", "between", "both", "could", "does", "doing", "down", "each", "even", "every", "from", "further", "have", "having", "here", "hers", "himself", "into", "itself", "just", "like", "more", "most", "much", "must", "only", "other", "over", "same", "should", "some", "such", "than", "that", "their", "them", "then", "there", "these", "they", "this", "those", "through", "under", "until", "very", "what", "when", "where", "which", "while", "with", "would", "your", "you", "the", "and", "for", "are", "was", "were", "has", "had", "his", "her", "she", "him", "our", "out", "not", "but", "can", "all", "any", "who", "why", "how", "its", "it's", "get", "got", "one", "two", "now", "well", "yeah", "yes", "okay", "um", "uh"
}
HOOK_TERMS = {"secret", "mistake", "never", "always", "best", "worst", "shocking", "surprising", "important", "truth", "reason", "because", "discover", "amazing", "powerful", "simple", "easy", "change", "learn", "tip", "problem", "solution", "money", "free", "success", "fail", "imagine"}


def run_command(args):
    try:
        return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc))[-4000:]
        raise RuntimeError(f"Media processing failed:\n{details}") from exc


def validate_youtube_url(url):
    parsed = urlparse(url.strip())
    allowed = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in allowed:
        raise ValueError("Enter a valid YouTube video URL.")
    return url.strip()


def download_youtube_video(url, folder):
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is missing. Add 'yt-dlp[default]' to requirements.txt and redeploy.")

    template = os.path.join(folder, "source.%(ext)s")

    # Ask yt-dlp for the best single-file format first. This avoids forcing
    # MP4 when a particular YouTube video does not offer an MP4 format.
    options = {
        "format": "best",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            title = info.get("title") or "YouTube video"
    except Exception as exc:
        raise RuntimeError(
            "YouTube could not provide a downloadable format for this link. "
            "The video may be private, restricted, unavailable, or YouTube may "
            "have changed its playback requirements. Try another video or use "
            "the Upload video file option. Details: " + str(exc)
        ) from exc

    files = [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.startswith("source.") and os.path.isfile(os.path.join(folder, name))
    ]
    if not files:
        raise RuntimeError(
            "yt-dlp finished without creating a video file. Try uploading the "
            "video directly instead."
        )

    return files[0], title


@st.cache_resource(show_spinner="Loading speech recognition model...")
def load_whisper_model():
    return WhisperModel("tiny", device="cpu", compute_type="int8")


def transcribe_video(video_path, folder):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    audio_path = os.path.join(folder, "audio.wav")
    run_command([ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", audio_path])
    segments, _ = load_whisper_model().transcribe(audio_path, vad_filter=True, word_timestamps=False)
    return list(segments)


def score_segment(text):
    lower = text.lower()
    words = re.findall(r"[a-zA-Z0-9']+", lower)
    if not words:
        return 0.0
    score = min(len(words), 110) * 0.15
    score += sum(2.5 for term in HOOK_TERMS if re.search(r"\b" + re.escape(term) + r"\b", lower))
    score += min(text.count("!"), 3) * 1.5 + min(text.count("?"), 3)
    if len(words) >= 35:
        score += 3
    if len(words) < 12:
        score -= 5
    return score


def choose_clips(segments, clip_count=5, min_seconds=30, max_seconds=60):
    if not segments:
        return []
    total_end = max(float(s.end) for s in segments)
    if total_end < min_seconds:
        return []
    candidates = []
    for start in range(0, max(1, int(total_end - min_seconds) + 1), 5):
        end = min(start + max_seconds, total_end)
        if end - start < min_seconds:
            continue
        selected = [s for s in segments if float(s.start) < end and float(s.end) > start]
        text = " ".join(s.text.strip() for s in selected).strip()
        if not text:
            continue
        duration = end - start
        density = min(len(re.findall(r"\w+", text)) / max(duration, 1), 2.5)
        candidates.append({"start": float(start), "end": float(end), "text": text, "score": score_segment(text) + density * 2})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    chosen = []
    for candidate in candidates:
        distinct = all(candidate["end"] <= item["start"] + 8 or candidate["start"] >= item["end"] - 8 for item in chosen)
        if distinct:
            chosen.append(candidate)
        if len(chosen) >= clip_count:
            break
    if len(chosen) < min(clip_count, len(candidates)):
        for candidate in candidates:
            if candidate not in chosen:
                chosen.append(candidate)
            if len(chosen) >= clip_count:
                break
    return sorted(chosen, key=lambda item: item["score"], reverse=True)


def timestamp_srt(seconds):
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def make_srt(segments, start, end, output_path):
    entries = []
    index = 1
    for segment in segments:
        seg_start = max(float(segment.start), float(start))
        seg_end = min(float(segment.end), float(end))
        text = segment.text.strip()
        if seg_end > seg_start and text:
            entries.append(f"{index}\n{timestamp_srt(seg_start-start)} --> {timestamp_srt(seg_end-start)}\n{text}\n")
            index += 1
    with open(output_path, "w", encoding="utf-8") as file:
        file.write("\n".join(entries))


def seo_package(title, transcript):
    words = re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", transcript.lower())
    common = [word for word, _ in Counter(words).most_common(12) if word not in STOP_WORDS]
    topic = ", ".join(common[:4]) if common else "this topic"
    clean_title = re.sub(r"\s+", " ", title).strip()
    short_title = clean_title[:65].rstrip(" -:|") if clean_title else "Interesting moment"
    hashtags = ["#Shorts"] + ["#" + re.sub(r"[^A-Za-z0-9]", "", word).capitalize() for word in common[:4]]
    description = f"A short highlight about {topic}. Watch the key moment and share your thoughts.\n\nSubscribe for more clips. This Short is an excerpt from the original video."
    return {"title": f"{short_title} | Key Insight"[:100], "description": description, "hashtags": " ".join(hashtags), "keywords": ", ".join(common[:10]), "thumbnail_text": (common[0].upper() if common else "KEY MOMENT")[:28]}


def render_vertical_clip(ffmpeg, source, output, start, end):
    duration = max(1.0, float(end) - float(start))
    video_filter = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[background];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[foreground];"
        "[background][foreground]overlay=(W-w)/2:(H-h)/2,setsar=1[outv]"
    )
    run_command([ffmpeg, "-y", "-ss", str(start), "-i", source, "-t", str(duration), "-filter_complex", video_filter, "-map", "[outv]", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output])


# ---------------- Streamlit interface ----------------
st.subheader("1. Add your source video")
source_mode = st.radio("Choose how to provide your video", ["YouTube link", "Upload video file"], horizontal=True)
youtube_url = ""
uploaded_video = None
if source_mode == "YouTube link":
    youtube_url = st.text_input("YouTube video URL", placeholder="https://www.youtube.com/watch?v=...", help="Only use a video you own or have permission to process.")
else:
    uploaded_video = st.file_uploader("Upload a video you own or are authorized to edit", type=["mp4", "mov", "mkv", "webm"])

st.subheader("2. Clip settings")
clip_count = st.slider("Number of clips", min_value=1, max_value=5, value=5)
clip_duration = st.select_slider("Clip duration (seconds)", options=[30, 35, 40, 45, 50, 55, 60], value=45)
st.caption("The app finds spoken highlights and creates up to five vertical clips. A source that is too short may produce fewer clips.")
generate = st.button("✨ Analyze video and create Shorts", type="primary", use_container_width=True)

if generate:
    if source_mode == "YouTube link" and not youtube_url.strip():
        st.error("Paste a YouTube link first.")
        st.stop()
    if source_mode == "Upload video file" and uploaded_video is None:
        st.error("Upload a video first.")
        st.stop()

    with tempfile.TemporaryDirectory() as workdir:
        try:
            with st.status("Preparing your source video...", expanded=True) as status:
                if source_mode == "YouTube link":
                    st.write("Checking the YouTube link...")
                    source_path, video_title = download_youtube_video(validate_youtube_url(youtube_url), workdir)
                else:
                    extension = os.path.splitext(uploaded_video.name)[1] or ".mp4"
                    source_path = os.path.join(workdir, "uploaded_video" + extension)
                    with open(source_path, "wb") as file:
                        file.write(uploaded_video.getbuffer())
                    video_title = os.path.splitext(uploaded_video.name)[0] or "Uploaded video"

                st.write("Transcribing the audio...")
                transcript_segments = transcribe_video(source_path, workdir)
                if not transcript_segments:
                    raise RuntimeError("No speech was detected. Try a video with clear spoken audio.")

                st.write("Finding the best moments...")
                selected_clips = choose_clips(transcript_segments, clip_count=clip_count, min_seconds=clip_duration, max_seconds=clip_duration)
                if not selected_clips:
                    raise RuntimeError(f"No spoken section at least {clip_duration} seconds long was found. Try a longer video or choose a shorter clip duration.")
                status.update(label="Analysis complete", state="complete", expanded=False)

            st.success(f"Found {len(selected_clips)} clip(s).")
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            for index, clip in enumerate(selected_clips, start=1):
                st.divider()
                st.subheader(f"Clip {index}")
                st.write(f"**Time:** {int(clip['start'])}s – {int(clip['end'])}s")
                st.write(f"**Highlight score:** {round(min(100, clip['score'] * 2.5))}/100")
                st.caption("This is an automatic transcript-based estimate, not a prediction of views.")
                st.write(clip["text"][:1200] + ("..." if len(clip["text"]) > 1200 else ""))

                clip_path = os.path.join(workdir, f"neeraj_short_{index}.mp4")
                srt_path = os.path.join(workdir, f"neeraj_short_{index}.srt")
                with st.spinner(f"Rendering clip {index}..."):
                    render_vertical_clip(ffmpeg, source_path, clip_path, clip["start"], clip["end"])
                    make_srt(transcript_segments, clip["start"], clip["end"], srt_path)

                st.video(clip_path)
                with open(clip_path, "rb") as file:
                    st.download_button("⬇️ Download vertical video", data=file.read(), file_name=f"neeraj_short_{index}.mp4", mime="video/mp4", key=f"video_{index}")
                with open(srt_path, "rb") as file:
                    st.download_button("Download subtitles (.srt)", data=file.read(), file_name=f"neeraj_short_{index}.srt", mime="application/x-subrip", key=f"srt_{index}")

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
            st.info("Possible causes include an unavailable or restricted video, a large file, a hosting time or memory limit, or a missing dependency. Try a shorter video or upload a video you are authorized to use.")
else:
    st.markdown("""
    **How it works**
    1. Paste a YouTube link for a video you own or upload your source video.
    2. The app transcribes the audio and finds promising moments.
    3. It creates vertical clips, subtitle files, and suggested YouTube SEO text.
    """)
