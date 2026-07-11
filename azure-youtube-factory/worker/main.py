from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as google_build
from googleapiclient.http import MediaFileUpload
from openai import AzureOpenAI
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("youtube-video-worker")

STORAGE_ACCOUNT = os.environ["STORAGE_ACCOUNT"]
TOPIC_QUEUE = os.getenv("TOPIC_QUEUE", "youtube-topics")
STATUS_QUEUE = os.getenv("STATUS_QUEUE", "youtube-status")
DEADLETTER_QUEUE = os.getenv("DEADLETTER_QUEUE", "youtube-deadletter")
KEY_VAULT_URL = os.environ["KEY_VAULT_URL"]
AOAI_ENDPOINT = os.environ["AOAI_ENDPOINT"].rstrip("/")
AOAI_DEPLOYMENT = os.environ["AOAI_DEPLOYMENT"]
AOAI_API_VERSION = os.getenv("AOAI_API_VERSION", "2024-10-21")
SPEECH_REGION = os.environ["SPEECH_REGION"]
VOICE = os.getenv("VOICE", "en-IN-PrabhatNeural")
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "private")
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1920"))
MAX_SCENES = int(os.getenv("MAX_SCENES", "6"))

credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
blob_service = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=credential,
)


def queue_client(name: str) -> QueueClient:
    return QueueClient(
        account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
        queue_name=name,
        credential=credential,
    )


topic_queue = queue_client(TOPIC_QUEUE)
status_queue = queue_client(STATUS_QUEUE)
deadletter_queue = queue_client(DEADLETTER_QUEUE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_status(job_id: str, stage: str, **extra: Any) -> None:
    payload = {"jobId": job_id, "stage": stage, "timestamp": utc_now(), **extra}
    status_queue.send_message(json.dumps(payload, ensure_ascii=False))
    LOGGER.info("status=%s job=%s", stage, job_id)


def safe_secret(name: str) -> str | None:
    try:
        return secret_client.get_secret(name).value
    except Exception:
        LOGGER.info("Secret %s is not configured.", name)
        return None


def run(command: list[str]) -> str:
    LOGGER.info("Running: %s", " ".join(command))
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def parse_topic(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            topic = str(data.get("topic", "")).strip()
            if not topic:
                raise ValueError("Topic is missing.")
            return {
                "topic": topic,
                "language": str(data.get("language", "hinglish")).strip() or "hinglish",
                "privacy": str(data.get("privacy", YOUTUBE_PRIVACY)).strip() or YOUTUBE_PRIVACY,
            }
    except json.JSONDecodeError:
        pass

    topic = content.strip()
    if not topic:
        raise ValueError("Queue message is empty.")
    return {"topic": topic, "language": "hinglish", "privacy": YOUTUBE_PRIVACY}


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model did not return a JSON object.")
    return json.loads(cleaned[start : end + 1])


def generate_plan(topic: str, language: str) -> dict[str, Any]:
    key = secret_client.get_secret("aoai-key").value
    client = AzureOpenAI(
        api_key=key,
        azure_endpoint=AOAI_ENDPOINT,
        api_version=AOAI_API_VERSION,
    )

    prompt = f"""
Create a concise vertical YouTube Short about: {topic}

Language style: {language}.
If language is Hinglish, use simple English with a small amount of easy Roman Hindi.
The narration must sound natural with a soft Indian male voice.
Audience: business and technology professionals.
Target duration: 60 to 90 seconds.
Create between 5 and {MAX_SCENES} scenes.

Return one JSON object with:
- title: engaging YouTube title, maximum 90 characters
- description: 2 short paragraphs
- tags: array of 8 to 12 tags without hash symbols
- scenes: array where every item has:
  - heading: maximum 7 words
  - bullets: array of 2 or 3 short bullets
  - narration: 2 to 4 natural spoken sentences
  - visual_hint: a short visual design idea

Do not include markdown or code fences.
""".strip()

    messages = [
        {
            "role": "system",
            "content": (
                "You are a factual enterprise technology video writer. "
                "Avoid unsupported claims and keep wording simple."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = client.chat.completions.create(
            model=AOAI_DEPLOYMENT,
            messages=messages,
            temperature=0.6,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(
            model=AOAI_DEPLOYMENT,
            messages=messages,
            temperature=0.6,
        )

    plan = extract_json(response.choices[0].message.content or "")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("The generated plan contains no scenes.")

    normalized_scenes: list[dict[str, Any]] = []
    for scene in scenes[:MAX_SCENES]:
        if not isinstance(scene, dict):
            continue
        normalized_scenes.append(
            {
                "heading": str(scene.get("heading", "Key point")).strip(),
                "bullets": [
                    str(item).strip()
                    for item in (scene.get("bullets") or [])
                    if str(item).strip()
                ][:3],
                "narration": str(scene.get("narration", "")).strip(),
                "visual_hint": str(scene.get("visual_hint", "")).strip(),
            }
        )

    if not normalized_scenes:
        raise ValueError("No valid scenes were generated.")

    return {
        "title": str(plan.get("title", topic)).strip()[:100],
        "description": str(plan.get("description", "")).strip(),
        "tags": [str(tag).strip() for tag in (plan.get("tags") or []) if str(tag).strip()][:15],
        "scenes": normalized_scenes,
    }


def synthesize_speech(text: str, output_path: Path) -> None:
    speech_key = secret_client.get_secret("speech-key").value
    endpoint = (
        f"https://{SPEECH_REGION}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    ssml = (
        "<speak version='1.0' xml:lang='en-IN'>"
        f"<voice name='{escape(VOICE)}'>"
        "<prosody rate='-3%' pitch='-2%'>"
        f"{escape(text)}"
        "</prosody></voice></speak>"
    )
    response = requests.post(
        endpoint,
        headers={
            "Ocp-Apim-Subscription-Key": speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
            "User-Agent": "VarunAzureYouTubeFactory",
        },
        data=ssml.encode("utf-8"),
        timeout=120,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    filename = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    return ImageFont.truetype(filename, size)


def fit_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        width = draw.textbbox((0, 0), trial, font=font)[2]
        if width <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_scene(
    scene: dict[str, Any],
    scene_index: int,
    scene_count: int,
    output_path: Path,
) -> None:
    image = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (11, 27, 49))
    draw = ImageDraw.Draw(image)

    for y in range(VIDEO_HEIGHT):
        ratio = y / max(VIDEO_HEIGHT - 1, 1)
        color = (
            int(11 + 18 * ratio),
            int(27 + 28 * ratio),
            int(49 + 42 * ratio),
        )
        draw.line((0, y, VIDEO_WIDTH, y), fill=color)

    accent = (65, 176, 255)
    accent_soft = (27, 82, 132)
    text_main = (245, 249, 255)
    text_soft = (190, 218, 239)

    draw.rounded_rectangle(
        (55, 70, VIDEO_WIDTH - 55, 215),
        radius=38,
        fill=accent_soft,
        outline=accent,
        width=4,
    )

    heading_font = load_font(58, bold=True)
    bullet_font = load_font(40)
    label_font = load_font(28, bold=True)
    footer_font = load_font(24)

    heading_lines = fit_wrapped_lines(
        draw,
        scene["heading"],
        heading_font,
        VIDEO_WIDTH - 160,
    )[:2]

    heading_y = 98
    for line in heading_lines:
        draw.text((85, heading_y), line, font=heading_font, fill=text_main)
        heading_y += 66

    draw.text(
        (70, 260),
        f"SCENE {scene_index} OF {scene_count}",
        font=label_font,
        fill=accent,
    )

    y = 345
    for bullet in scene.get("bullets", []):
        draw.ellipse((85, y + 12, 112, y + 39), fill=accent)
        lines = fit_wrapped_lines(
            draw,
            bullet,
            bullet_font,
            VIDEO_WIDTH - 210,
        )
        for line in lines[:3]:
            draw.text((140, y), line, font=bullet_font, fill=text_main)
            y += 56
        y += 34

    visual_hint = scene.get("visual_hint", "")
    if visual_hint:
        draw.rounded_rectangle(
            (70, 1410, VIDEO_WIDTH - 70, 1690),
            radius=32,
            fill=(15, 41, 70),
            outline=(47, 105, 158),
            width=3,
        )
        draw.text((105, 1450), "VISUAL IDEA", font=label_font, fill=accent)
        visual_font = load_font(32)
        visual_y = 1510
        for line in fit_wrapped_lines(
            draw,
            visual_hint,
            visual_font,
            VIDEO_WIDTH - 210,
        )[:4]:
            draw.text((105, visual_y), line, font=visual_font, fill=text_soft)
            visual_y += 45

    draw.text(
        (70, VIDEO_HEIGHT - 95),
        "Varun Azure AI Video Factory",
        font=footer_font,
        fill=text_soft,
    )
    image.save(output_path, quality=95)


def audio_duration(path: Path) -> float:
    output = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return max(float(output), 1.0)


def build_video(plan: dict[str, Any], workdir: Path) -> tuple[Path, list[Path], list[Path]]:
    clip_paths: list[Path] = []
    image_paths: list[Path] = []
    audio_paths: list[Path] = []

    scenes = plan["scenes"]
    for index, scene in enumerate(scenes, start=1):
        image_path = workdir / f"scene-{index:02d}.png"
        audio_path = workdir / f"scene-{index:02d}.mp3"
        clip_path = workdir / f"scene-{index:02d}.mp4"

        render_scene(scene, index, len(scenes), image_path)
        synthesize_speech(scene["narration"], audio_path)
        duration = audio_duration(audio_path) + 0.35

        run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-i",
                str(audio_path),
                "-t",
                f"{duration:.3f}",
                "-vf",
                (
                    f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
                    "format=yuv420p,"
                    "fade=t=in:st=0:d=0.25,"
                    f"fade=t=out:st={max(duration - 0.25, 0.1):.3f}:d=0.25"
                ),
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                str(clip_path),
            ]
        )

        clip_paths.append(clip_path)
        image_paths.append(image_path)
        audio_paths.append(audio_path)

    concat_file = workdir / "clips.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in clip_paths),
        encoding="utf-8",
    )

    final_path = workdir / "final-video.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(final_path),
        ]
    )
    return final_path, image_paths, audio_paths


def upload_file(container: str, blob_name: str, path: Path) -> str:
    client = blob_service.get_blob_client(container=container, blob=blob_name)
    with path.open("rb") as handle:
        client.upload_blob(handle, overwrite=True)
    return client.url


def upload_text(container: str, blob_name: str, text: str) -> str:
    client = blob_service.get_blob_client(container=container, blob=blob_name)
    client.upload_blob(text.encode("utf-8"), overwrite=True)
    return client.url


def upload_to_youtube(
    video_path: Path,
    plan: dict[str, Any],
    privacy: str,
) -> str | None:
    client_secret_json = safe_secret("youtube-client-secret-json")
    token_json = safe_secret("youtube-token-json")
    if not client_secret_json or not token_json:
        return None

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    credentials = Credentials.from_authorized_user_info(
        json.loads(token_json),
        scopes=scopes,
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleRequest())

    youtube = google_build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": plan["title"],
                "description": plan["description"],
                "tags": plan["tags"],
                "categoryId": "28",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=-1,
            resumable=True,
        ),
    )
    result = request.execute()
    video_id = result["id"]
    return f"https://www.youtube.com/watch?v={video_id}"


def process_message(message: Any) -> None:
    payload = parse_topic(message.content)
    job_id = uuid.uuid4().hex[:12]
    prefix = f"{datetime.now(timezone.utc):%Y/%m/%d}/{job_id}"

    try:
        send_status(job_id, "RECEIVED", topic=payload["topic"])
        with tempfile.TemporaryDirectory(prefix="youtube-worker-") as temp:
            workdir = Path(temp)
            send_status(job_id, "SCRIPT_GENERATION")
            plan = generate_plan(payload["topic"], payload["language"])

            plan_path = workdir / "script.json"
            plan_path.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            send_status(job_id, "VOICE_AND_RENDERING")
            video_path, image_paths, audio_paths = build_video(plan, workdir)

            send_status(job_id, "UPLOADING_ASSETS")
            script_url = upload_file("scripts", f"{prefix}/script.json", plan_path)
            video_url = upload_file("videos", f"{prefix}/video.mp4", video_path)
            thumbnail_url = upload_file(
                "thumbnails",
                f"{prefix}/thumbnail.png",
                image_paths[0],
            )

            for index, image_path in enumerate(image_paths, start=1):
                upload_file("images", f"{prefix}/scene-{index:02d}.png", image_path)
            for index, audio_path in enumerate(audio_paths, start=1):
                upload_file("audio", f"{prefix}/scene-{index:02d}.mp3", audio_path)

            metadata = {
                "jobId": job_id,
                "topic": payload["topic"],
                "language": payload["language"],
                "privacy": payload["privacy"],
                "title": plan["title"],
                "scriptUrl": script_url,
                "videoUrl": video_url,
                "thumbnailUrl": thumbnail_url,
                "createdAt": utc_now(),
            }

            send_status(job_id, "YOUTUBE_UPLOAD")
            youtube_url = upload_to_youtube(
                video_path,
                plan,
                payload["privacy"],
            )
            metadata["youtubeUrl"] = youtube_url
            upload_text(
                "metadata",
                f"{prefix}/metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )

            if youtube_url:
                send_status(
                    job_id,
                    "COMPLETED",
                    title=plan["title"],
                    youtubeUrl=youtube_url,
                    storagePath=f"videos/{prefix}/video.mp4",
                )
            else:
                send_status(
                    job_id,
                    "OAUTH_PENDING",
                    title=plan["title"],
                    storagePath=f"videos/{prefix}/video.mp4",
                    message="Video created. One-time YouTube OAuth is still required.",
                )

        topic_queue.delete_message(message.id, message.pop_receipt)
    except Exception as exc:
        LOGGER.exception("Video job failed.")
        deadletter_queue.send_message(
            json.dumps(
                {
                    "originalMessage": message.content,
                    "error": str(exc),
                    "timestamp": utc_now(),
                },
                ensure_ascii=False,
            )
        )
        try:
            topic_queue.delete_message(message.id, message.pop_receipt)
        except Exception:
            LOGGER.exception("Could not delete failed queue message.")
        send_status(job_id, "FAILED", error=str(exc))
        raise


def main() -> None:
    LOGGER.info("Checking queue %s", TOPIC_QUEUE)
    messages = topic_queue.receive_messages(
        messages_per_page=1,
        visibility_timeout=3600,
    )
    message = next(iter(messages), None)
    if message is None:
        LOGGER.info("No pending topic was found.")
        return
    process_message(message)


if __name__ == "__main__":
    main()
