import streamlit as st
import tempfile
import os
import subprocess
import imageio_ffmpeg
from faster_whisper import WhisperModel

st.set_page_config(page_title="Neeraj AI Clipper", page_icon="🎬")

st.title("🎬 Neeraj AI Clipper")
st.write("Turn long videos into short vertical clips.")

video = st.file_uploader(
    "Upload your video",
    type=["mp4", "mov", "mkv"]
)

clip_length = st.slider("Clip duration (seconds)", 15, 60, 30)
number_of_clips = st.slider("Number of clips", 1, 5, 3)

if video:
    st.video(video)

    if st.button("✨ Find Viral Moments", type="primary"):
        with st.spinner("Preparing your video..."):
            with tempfile.TemporaryDirectory() as folder:
                video_path = os.path.join(folder, "input.mp4")
                audio_path = os.path.join(folder, "audio.wav")

                with open(video_path, "wb") as f:
                    f.write(video.getbuffer())

                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

                subprocess.run([
                    ffmpeg, "-y", "-i", video_path,
                    "-vn", "-ac", "1", "-ar", "16000",
                    audio_path
                ], check=True, capture_output=True)

                st.info("Loading speech recognition AI...")

                model = WhisperModel(
                    "tiny",
                    device="cpu",
                    compute_type="int8"
                )

                segments, info = model.transcribe(
                    audio_path,
                    vad_filter=True
                )

                words = list(segments)

                if not words:
                    st.error("No speech detected in this video.")
                    st.stop()

                st.info("Finding interesting moments...")

                moments = []
                duration = max(
                    1, words[-1].end - words[0].start
                )

                for start in range(
                    0, int(duration), clip_length // 2
                ):
                    end = start + clip_length
                    selected = [
                        s for s in words
                        if s.start < end and s.end > start
                    ]

                    if not selected:
                        continue

                    text = " ".join(s.text for s in selected)
                    lower = text.lower()

                    keywords = [
                        "wow", "amazing", "secret",
                        "best", "crazy", "important",
                        "why", "how", "never",
                        "finally", "unbelievable"
                    ]

                    score = len(text.split())
                    score += sum(
                        10 for word in keywords
                        if word in lower
                    )
                    score += text.count("!") * 3
                    score += text.count("?") * 2

                    moments.append({
                        "start": start,
                        "end": min(end, duration),
                        "text": text,
                        "score": score
                    })

                moments.sort(
                    key=lambda x: x["score"],
                    reverse=True
                )

                results = moments[:number_of_clips]

                if not results:
                    st.warning("No suitable clips found.")
                    st.stop()

                st.success("Your clips are ready!")

                for index, moment in enumerate(results):
                    output = os.path.join(
                        folder, f"clip_{index + 1}.mp4"
                    )

                    subprocess.run([
                        ffmpeg, "-y",
                        "-ss", str(moment["start"]),
                        "-i", video_path,
                        "-t", str(
                            moment["end"] - moment["start"]
                        ),
                        "-vf",
                        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "26",
                        "-c:a", "aac",
                        "-movflags", "+faststart",
                        output
                    ], check=True, capture_output=True)

                    st.subheader(f"Clip {index + 1}")
                    st.write(moment["text"])
                    st.video(output)

                    with open(output, "rb") as f:
                        st.download_button(
                            "⬇️ Download clip",
                            data=f.read(),
                            file_name=f"neeraj_clip_{index + 1}.mp4",
                            mime="video/mp4",
                            key=f"download_{index}"
                        )
else:
    st.info("Upload a video to get started.")
￼Enter
