from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def load_request(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_title(request_data: dict) -> str:
    configured = str(request_data.get("title", "")).strip()
    if configured:
        return configured[:100]

    topic = str(request_data.get("topic", "")).strip()
    match = re.search(
        r"exact YouTube title:\s*([^\n.]+)",
        topic,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()[:100]

    return "Azure AI Foundry - By Varun"


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "Usage: upload-latest-youtube.py TOKEN_JSON VIDEO_FILE REQUEST_JSON URL_OUTPUT"
        )

    token_path = Path(sys.argv[1])
    video_path = Path(sys.argv[2])
    request_path = Path(sys.argv[3])
    url_output_path = Path(sys.argv[4])

    if not token_path.is_file():
        raise FileNotFoundError("The YouTube OAuth token file is missing.")
    if not video_path.is_file() or video_path.stat().st_size == 0:
        raise FileNotFoundError("The generated video file is missing or empty.")

    request_data = load_request(request_path)
    title = resolve_title(request_data)
    privacy = str(request_data.get("privacy", "private")).strip() or "private"
    description = str(
        request_data.get(
            "description",
            "A concise Hinglish business explainer about Azure AI Foundry, including three practical business examples.",
        )
    ).strip()
    tags = request_data.get(
        "tags",
        [
            "Azure AI Foundry",
            "Microsoft Azure",
            "Artificial Intelligence",
            "Business AI",
            "Hinglish",
            "Varun Ashat",
        ],
    )

    credentials = Credentials.from_authorized_user_file(
        str(token_path),
        scopes=SCOPES,
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    if not credentials.valid:
        raise RuntimeError("The stored Google OAuth token is not valid.")

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )
    upload = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "tags": [str(tag) for tag in tags][:15],
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

    result = upload.execute()
    video_id = str(result["id"])
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    url_output_path.write_text(youtube_url, encoding="utf-8")
    print(youtube_url)


if __name__ == "__main__":
    main()
