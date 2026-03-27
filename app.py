#!/usr/bin/env python3
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import cgi
import html
import json
import mimetypes
import os
import re
import secrets
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

APP_TITLE = os.getenv("APP_TITLE", "File Share")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent / "data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
METADATA_DIR = Path(os.getenv("METADATA_DIR", DATA_DIR / "metadata"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "photo_link_drop_session")
SESSION_COOKIE_MAX_AGE = int(os.getenv("SESSION_COOKIE_MAX_AGE", str(60 * 60 * 24 * 365)))
SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
VISIBLE_UPLOAD_LIMIT = 12

UPLOAD_ACCEPT_EXTENSIONS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".pdf",
    ".txt",
    ".csv",
    ".rtf",
    ".md",
    ".json",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".zip",
]
UPLOAD_ACCEPT = ",".join(UPLOAD_ACCEPT_EXTENSIONS)
ALLOWED_FILE_LABEL = ", ".join(extension.removeprefix(".") for extension in UPLOAD_ACCEPT_EXTENSIONS)

ALLOWED_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".rtf": "application/rtf",
    ".md": "text/markdown",
    ".json": "application/json",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".zip": "application/zip",
}

MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/rtf": ".rtf",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/zip": ".zip",
}

IMAGE_EXTENSIONS = {extension for extension, mime_type in ALLOWED_EXTENSIONS.items() if mime_type.startswith("image/")}
INLINE_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/json",
}


def ensure_storage_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)



def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"



def format_timestamp(timestamp_epoch: float) -> str:
    return datetime.fromtimestamp(timestamp_epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")



def metadata_path_for(filename: str) -> Path:
    return METADATA_DIR / f"{filename}.json"



def display_extension(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix[:8].upper() if suffix else "FILE"



def is_image_file(content_type: str, file_name: str) -> bool:
    normalized_content_type = (content_type or "").lower().strip()
    if normalized_content_type.startswith("image/"):
        return True
    return Path(file_name).suffix.lower() in IMAGE_EXTENSIONS



def build_content_disposition(disposition: str, file_name: str) -> str:
    normalized_name = Path(file_name).name or "download"
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "_", normalized_name).strip("._") or "download"
    encoded_name = quote(normalized_name)
    return f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'



def write_upload_record(
    filename: str,
    session_id: str,
    original_name: str,
    size_bytes: int,
    content_type: str,
) -> dict:
    uploaded_at = datetime.now(timezone.utc)
    record = {
        "filename": filename,
        "session_id": session_id,
        "original_name": original_name,
        "size_bytes": size_bytes,
        "content_type": content_type,
        "uploaded_at_iso": uploaded_at.isoformat(),
        "uploaded_at_epoch": uploaded_at.timestamp(),
    }
    metadata_path_for(filename).write_text(json.dumps(record), encoding="utf-8")
    return record



def load_upload_record(metadata_path: Path, require_file: bool = True) -> dict | None:
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    filename = Path(str(data.get("filename", ""))).name
    session_id = str(data.get("session_id", "")).lower()
    if not filename or not SESSION_ID_PATTERN.fullmatch(session_id):
        return None

    file_path = UPLOAD_DIR / filename
    file_exists = file_path.is_file()
    if require_file and not file_exists:
        return None

    file_stats = file_path.stat() if file_exists else None

    uploaded_at_epoch = data.get("uploaded_at_epoch")
    try:
        uploaded_at_epoch = float(uploaded_at_epoch)
    except (TypeError, ValueError):
        uploaded_at_epoch = file_stats.st_mtime if file_stats else datetime.now(timezone.utc).timestamp()

    size_bytes = data.get("size_bytes")
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError):
        size_bytes = file_stats.st_size if file_stats else 0

    original_name = Path(str(data.get("original_name", filename))).name or filename
    content_type = str(data.get("content_type", "")).strip()

    return {
        "filename": filename,
        "session_id": session_id,
        "original_name": original_name,
        "size_bytes": size_bytes,
        "content_type": content_type,
        "uploaded_at_epoch": uploaded_at_epoch,
    }



def serialize_upload_item(base_url: str, record: dict) -> dict:
    filename = record["filename"]
    original_name = record["original_name"]
    content_type = record["content_type"] or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    uploaded_at_epoch = float(record["uploaded_at_epoch"])
    return {
        "filename": filename,
        "original_name": original_name,
        "url": f"{base_url}/files/{quote(filename)}",
        "size_bytes": int(record["size_bytes"]),
        "size_label": format_size(int(record["size_bytes"])),
        "uploaded_at_epoch": uploaded_at_epoch,
        "uploaded_at_label": format_timestamp(uploaded_at_epoch),
        "content_type": content_type,
        "is_image": is_image_file(content_type, original_name or filename),
        "extension_label": display_extension(original_name or filename),
    }



def session_uploads(base_url: str, session_id: str, limit: int = VISIBLE_UPLOAD_LIMIT) -> list[dict]:
    if not METADATA_DIR.exists():
        return []

    items = []
    for current_metadata_path in METADATA_DIR.glob("*.json"):
        record = load_upload_record(current_metadata_path)
        if not record or record["session_id"] != session_id:
            continue
        items.append(serialize_upload_item(base_url, record))

    items.sort(key=lambda item: item["uploaded_at_epoch"], reverse=True)
    return items[:limit]



def render_upload_visual(item: dict) -> str:
    safe_url = html.escape(item["url"])
    safe_original_name = html.escape(item["original_name"])
    safe_extension = html.escape(item["extension_label"])
    if item["is_image"]:
        return f"""
      <a class="thumb-link" href="{safe_url}" target="_blank" rel="noreferrer">
        <img class="thumb-image" src="{safe_url}" alt="{safe_original_name}" loading="lazy">
      </a>
        """

    return f"""
      <a class="thumb-link file-thumb" href="{safe_url}" target="_blank" rel="noreferrer">
        <span class="file-thumb-label">{safe_extension}</span>
      </a>
    """



def render_upload_card(item: dict) -> str:
    safe_url = html.escape(item["url"])
    safe_filename = html.escape(item["filename"])
    safe_original_name = html.escape(item["original_name"])
    safe_size = html.escape(item["size_label"])
    safe_uploaded_at = html.escape(item["uploaded_at_label"])
    safe_extension = html.escape(item["extension_label"])
    return f"""
    <article class="upload-card" data-filename="{safe_filename}">
      {render_upload_visual(item)}
      <div class="upload-content">
        <div class="upload-topline">
          <strong class="upload-name">{safe_original_name}</strong>
          <span class="upload-time">{safe_uploaded_at}</span>
        </div>
        <div class="upload-subline">{safe_extension} - {safe_size}</div>
        <div class="upload-links">
          <a class="mini-link" href="{safe_url}" target="_blank" rel="noreferrer">Open</a>
          <button type="button" class="mini-button delete-button" data-filename="{safe_filename}">Delete</button>
        </div>
      </div>
    </article>
    """



def render_upload_grid(uploads: list[dict]) -> str:
    if not uploads:
        return """
        <div class="empty-state">
          <strong>No files.</strong>
        </div>
        """

    return "\n".join(render_upload_card(item) for item in uploads)



def render_home(base_url: str, uploads: list[dict]) -> str:
    upload_count = len(uploads)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(APP_TITLE)}</title>
  <style>
    :root {{
      --bg: #f3f4f6;
      --card: #ffffff;
      --line: #d9dde3;
      --text: #111827;
      --muted: #6b7280;
      --accent: #2563eb;
      --accent-dark: #1d4ed8;
      --danger: #b91c1c;
      --danger-bg: #fef2f2;
      --ok-bg: #effaf3;
      --ok-line: #b7e1c4;
      --error-bg: #fff1f1;
      --error-line: #f1c0c0;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}

    main {{
      width: min(980px, calc(100% - 1rem));
      margin: 0 auto;
      padding: 1rem 0 2rem;
    }}

    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      margin-bottom: 1rem;
    }}

    .topbar h1 {{
      margin: 0;
      font-size: 1.5rem;
    }}

    .topbar p {{
      margin: 0.25rem 0 0;
      color: var(--muted);
    }}

    .workspace {{
      display: grid;
      gap: 1rem;
      grid-template-columns: minmax(280px, 360px) 1fr;
      align-items: start;
    }}

    .upload-panel,
    .gallery-panel {{
      padding: 1rem;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--card);
    }}

    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      margin-bottom: 0.9rem;
    }}

    .section-head h2 {{
      margin: 0;
      font-size: 1.05rem;
    }}

    .count-chip {{
      white-space: nowrap;
      padding: 0.35rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #f8fafc;
    }}

    form {{
      display: grid;
      gap: 0.75rem;
    }}

    .dropzone {{
      display: block;
      padding: 0.9rem;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fafafa;
    }}

    .dropzone strong {{
      display: block;
      margin-bottom: 0.35rem;
      font-size: 0.95rem;
    }}

    .dropzone span {{
      display: block;
      color: var(--muted);
      font-size: 0.92rem;
    }}

    .dropzone input {{
      display: block;
      width: 100%;
      margin-top: 0.75rem;
      padding: 0.65rem;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: #fff;
    }}

    .paste-input {{
      display: block;
      width: 100%;
      min-height: 92px;
      margin-top: 0.75rem;
      padding: 0.65rem;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      resize: vertical;
      font: inherit;
    }}

    .paste-input.ready {{
      border-color: var(--accent);
      background: #eff6ff;
    }}

    .primary-button,
    .secondary-button,
    .mini-button {{
      appearance: none;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.8rem 1rem;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      background: #fff;
      color: var(--text);
    }}

    .primary-button {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}

    .mini-button {{
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--danger);
    }}

    .primary-button:disabled,
    .secondary-button:disabled,
    .mini-button:disabled {{
      cursor: progress;
      opacity: 0.75;
    }}

    .share-card,
    .error-card {{
      display: none;
      margin-top: 0.85rem;
      padding: 0.9rem;
      border-radius: 12px;
    }}

    .share-card.show {{
      display: block;
      background: var(--ok-bg);
      border: 1px solid var(--ok-line);
    }}

    .error-card.show {{
      display: block;
      background: var(--error-bg);
      border: 1px solid var(--error-line);
      color: #991b1b;
    }}

    .share-card strong {{
      display: block;
      margin-bottom: 0.6rem;
    }}

    .share-row {{
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 0.6rem;
      align-items: center;
    }}

    .share-input {{
      width: 100%;
      min-width: 0;
      padding: 0.75rem 0.9rem;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
    }}

    .share-meta {{
      margin-top: 0.6rem;
      color: var(--muted);
      font-size: 0.9rem;
      word-break: break-word;
    }}

    .upload-grid {{
      display: grid;
      gap: 0.75rem;
    }}

    .upload-card {{
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      gap: 0.8rem;
      align-items: center;
      padding: 0.75rem;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fcfcfd;
    }}

    .thumb-link {{
      display: block;
      width: 88px;
      height: 88px;
      overflow: hidden;
      border-radius: 10px;
      background: #e5e7eb;
    }}

    .thumb-image {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}

    .file-thumb {{
      display: flex;
      align-items: center;
      justify-content: center;
      background: #eff6ff;
      color: var(--accent-dark);
      text-decoration: none;
    }}

    .file-thumb-label {{
      font-size: 1rem;
      font-weight: 700;
      letter-spacing: 0.08em;
    }}

    .upload-content {{
      min-width: 0;
    }}

    .upload-topline {{
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: flex-start;
    }}

    .upload-name {{
      display: inline-block;
      font-size: 0.95rem;
      line-height: 1.4;
      word-break: break-word;
    }}

    .upload-time {{
      color: var(--muted);
      font-size: 0.84rem;
      white-space: nowrap;
    }}

    .upload-subline {{
      margin-top: 0.25rem;
      color: var(--muted);
      font-size: 0.9rem;
      word-break: break-word;
    }}

    .upload-links {{
      display: flex;
      gap: 0.85rem;
      margin-top: 0.55rem;
      align-items: center;
    }}

    .mini-link {{
      color: var(--accent-dark);
      font-weight: 600;
      text-decoration: none;
    }}

    .empty-state {{
      padding: 1rem;
      border-radius: 12px;
      border: 1px dashed var(--line);
      background: #fafafa;
      color: var(--muted);
      text-align: center;
    }}

    @media (max-width: 940px) {{
      .workspace {{
        grid-template-columns: 1fr;
      }}
    }}

    @media (max-width: 720px) {{
      main {{
        width: min(100%, calc(100% - 1rem));
      }}

      .topbar {{
        flex-direction: column;
      }}

      .share-row {{
        grid-template-columns: 1fr;
      }}

      .upload-card {{
        grid-template-columns: 1fr;
      }}

      .thumb-link {{
        width: 100%;
        height: 180px;
      }}

      .upload-topline {{
        flex-direction: column;
        gap: 0.35rem;
      }}

      .upload-time {{
        white-space: normal;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>{html.escape(APP_TITLE)}</h1>
        <p>{MAX_UPLOAD_MB} MB max</p>
      </div>
    </header>

    <section class="workspace">
      <section class="upload-panel">
        <div class="section-head">
          <div>
            <h2>Upload</h2>
          </div>
        </div>

        <form id="upload-form">
          <label class="dropzone">
            <strong>File</strong>
            <span>Select a file.</span>
            <input id="file-input" type="file" name="file" accept="{html.escape(UPLOAD_ACCEPT)}">
          </label>
          <label class="dropzone">
            <strong>Paste Image</strong>
            <span>Paste here.</span>
            <textarea id="paste-input" class="paste-input" rows="3" placeholder="Paste image here"></textarea>
          </label>
          <button id="submit-button" class="primary-button" type="submit">Upload</button>
        </form>

        <div id="result" class="share-card"></div>
        <div id="error" class="error-card"></div>
      </section>

      <section class="gallery-panel">
        <div class="section-head">
          <div>
            <h2>Your Files</h2>
          </div>
          <div class="count-chip"><span id="gallery-count">{upload_count}</span></div>
        </div>
        <div id="gallery-grid" class="upload-grid">
          {render_upload_grid(uploads)}
        </div>
      </section>
    </section>
  </main>

  <script>
    const form = document.getElementById("upload-form");
    const input = document.getElementById("file-input");
    const pasteInput = document.getElementById("paste-input");
    const submitButton = document.getElementById("submit-button");
    const result = document.getElementById("result");
    const error = document.getElementById("error");
    const galleryGrid = document.getElementById("gallery-grid");
    const galleryCount = document.getElementById("gallery-count");
    let pastedFile = null;

    function escapeHtml(value) {{
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }}

    function renderVisual(item) {{
      const safeUrl = escapeHtml(item.url);
      const safeOriginalName = escapeHtml(item.original_name);
      const safeExtension = escapeHtml(item.extension_label);

      if (item.is_image) {{
        return `
          <a class="thumb-link" href="${{safeUrl}}" target="_blank" rel="noreferrer">
            <img class="thumb-image" src="${{safeUrl}}" alt="${{safeOriginalName}}" loading="lazy">
          </a>
        `;
      }}

      return `
        <a class="thumb-link file-thumb" href="${{safeUrl}}" target="_blank" rel="noreferrer">
          <span class="file-thumb-label">${{safeExtension}}</span>
        </a>
      `;
    }}

    function renderCard(item) {{
      const safeUrl = escapeHtml(item.url);
      const safeFilename = escapeHtml(item.filename);
      const safeOriginalName = escapeHtml(item.original_name);
      const safeSize = escapeHtml(item.size_label);
      const safeUploadedAt = escapeHtml(item.uploaded_at_label);
      const safeExtension = escapeHtml(item.extension_label);

      return `
        <article class="upload-card" data-filename="${{safeFilename}}">
          ${{renderVisual(item)}}
          <div class="upload-content">
            <div class="upload-topline">
              <strong class="upload-name">${{safeOriginalName}}</strong>
              <span class="upload-time">${{safeUploadedAt}}</span>
            </div>
            <div class="upload-subline">${{safeExtension}} - ${{safeSize}}</div>
            <div class="upload-links">
              <a class="mini-link" href="${{safeUrl}}" target="_blank" rel="noreferrer">Open</a>
              <button type="button" class="mini-button delete-button" data-filename="${{safeFilename}}">Delete</button>
            </div>
          </div>
        </article>
      `;
    }}

    function activeUploadFile() {{
      if (pastedFile) {{
        return pastedFile;
      }}
      if (input.files.length) {{
        return input.files[0];
      }}
      return null;
    }}

    function clearPastedFile() {{
      pastedFile = null;
      pasteInput.value = "";
      pasteInput.classList.remove("ready");
    }}

    function fileNameForClipboard(file) {{
      if (file.name && file.name.trim()) {{
        return file.name;
      }}

      const mimeToExtension = {{
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/heic": ".heic",
        "image/heif": ".heif",
      }};
      const extension = mimeToExtension[(file.type || "").toLowerCase()] || ".png";
      return `pasted-${{Date.now()}}${{extension}}`;
    }}

    input.addEventListener("change", () => {{
      if (input.files.length) {{
        clearPastedFile();
      }}
    }});

    pasteInput.addEventListener("paste", (event) => {{
      event.preventDefault();

      const items = Array.from(event.clipboardData?.items || []);
      const imageItem = items.find((item) => item.type && item.type.startsWith("image/"));
      if (!imageItem) {{
        pastedFile = null;
        pasteInput.value = "";
        pasteInput.classList.remove("ready");
        error.textContent = "Paste an image.";
        error.className = "error-card show";
        return;
      }}

      const file = imageItem.getAsFile();
      if (!file) {{
        pastedFile = null;
        pasteInput.value = "";
        pasteInput.classList.remove("ready");
        error.textContent = "Paste an image.";
        error.className = "error-card show";
        return;
      }}

      const preparedFile = new File([file], fileNameForClipboard(file), {{
        type: file.type || "image/png",
      }});

      pastedFile = preparedFile;
      input.value = "";
      pasteInput.value = "Image ready.";
      pasteInput.classList.add("ready");
      error.className = "error-card";
      error.textContent = "";
    }});

    function updateGalleryCount() {{
      galleryCount.textContent = String(galleryGrid.querySelectorAll(".upload-card").length);
    }}

    function ensureEmptyState() {{
      if (galleryGrid.querySelector(".upload-card")) {{
        return;
      }}
      if (galleryGrid.querySelector(".empty-state")) {{
        return;
      }}
      galleryGrid.innerHTML = '<div class="empty-state"><strong>No files.</strong></div>';
      updateGalleryCount();
    }}

    function prependUpload(item) {{
      const emptyState = galleryGrid.querySelector(".empty-state");
      if (emptyState) {{
        emptyState.remove();
      }}

      galleryGrid.insertAdjacentHTML("afterbegin", renderCard(item));
      const cards = galleryGrid.querySelectorAll(".upload-card");
      if (cards.length > {VISIBLE_UPLOAD_LIMIT}) {{
        cards[cards.length - 1].remove();
      }}
      updateGalleryCount();
    }}

    function removeUpload(filename) {{
      const card = Array.from(galleryGrid.querySelectorAll(".upload-card")).find((item) => item.dataset.filename === filename);
      if (card) {{
        card.remove();
      }}
      ensureEmptyState();
      updateGalleryCount();
    }}

    function showResult(payload) {{
      const safeUrl = escapeHtml(payload.url);
      const safeOriginalName = escapeHtml(payload.item.original_name);
      const safeSize = escapeHtml(payload.item.size_label);
      const safeExtension = escapeHtml(payload.item.extension_label);

      result.innerHTML = `
        <strong>Link</strong>
        <div class="share-row">
          <input class="share-input" id="share-link-input" type="text" readonly value="${{safeUrl}}">
          <button type="button" id="copy-button" class="secondary-button">Copy</button>
          <a class="primary-button" href="${{safeUrl}}" target="_blank" rel="noreferrer" style="text-decoration:none;text-align:center;">Open</a>
        </div>
        <div class="share-meta">${{safeOriginalName}} - ${{safeExtension}} - ${{safeSize}}</div>
      `;
      result.className = "share-card show";

      const copyButton = document.getElementById("copy-button");
      const shareLinkInput = document.getElementById("share-link-input");
      copyButton.addEventListener("click", async () => {{
        try {{
          await navigator.clipboard.writeText(payload.url);
          shareLinkInput.focus();
          shareLinkInput.select();
          copyButton.textContent = "Copied";
          window.setTimeout(() => {{
            copyButton.textContent = "Copy";
          }}, 1400);
        }} catch (_error) {{
          shareLinkInput.focus();
          shareLinkInput.select();
          copyButton.textContent = "Select";
          window.setTimeout(() => {{
            copyButton.textContent = "Copy";
          }}, 1600);
        }}
      }});
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      result.className = "share-card";
      result.textContent = "";
      error.className = "error-card";
      error.textContent = "";

      const uploadFile = activeUploadFile();
      if (!uploadFile) {{
        error.textContent = "Pick or paste a file.";
        error.className = "error-card show";
        return;
      }}

      submitButton.disabled = true;
      submitButton.textContent = "Uploading...";

      try {{
        const formData = new FormData();
        formData.append("file", uploadFile, uploadFile.name);

        const response = await fetch("/upload", {{
          method: "POST",
          body: formData,
        }});

        const payload = await response.json();
        if (!response.ok || !payload.ok) {{
          throw new Error(payload.error || "Upload failed.");
        }}

        showResult(payload);
        prependUpload(payload.item);
        form.reset();
        clearPastedFile();
      }} catch (uploadError) {{
        error.textContent = uploadError.message;
        error.className = "error-card show";
      }} finally {{
        submitButton.disabled = false;
        submitButton.textContent = "Upload";
      }}
    }});

    galleryGrid.addEventListener("click", async (event) => {{
      const deleteButton = event.target.closest(".delete-button");
      if (!deleteButton) {{
        return;
      }}

      event.preventDefault();
      error.className = "error-card";
      error.textContent = "";
      deleteButton.disabled = true;
      deleteButton.textContent = "Deleting...";

      try {{
        const filename = deleteButton.dataset.filename;
        const response = await fetch(`/files/${{encodeURIComponent(filename)}}`, {{
          method: "DELETE",
        }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) {{
          throw new Error(payload.error || "Delete failed.");
        }}
        removeUpload(filename);
      }} catch (deleteError) {{
        deleteButton.disabled = false;
        deleteButton.textContent = "Delete";
        error.textContent = deleteError.message;
        error.className = "error-card show";
      }}
    }});
  </script>
</body>
</html>
"""


class FileShareHandler(BaseHTTPRequestHandler):
    server_version = "FileShare/1.2"

    def do_GET(self) -> None:
        self.route_request(head_only=False)

    def do_HEAD(self) -> None:
        self.route_request(head_only=True)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/upload":
            self.handle_upload()
            return

        self.respond_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/files/"):
            self.handle_delete(parsed.path.removeprefix("/files/"))
            return

        self.respond_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def route_request(self, head_only: bool) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            session_id, is_new_session = self.ensure_session_id()
            uploads = session_uploads(self.base_url(), session_id)
            self.respond_html(
                render_home(self.base_url(), uploads),
                head_only=head_only,
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        if parsed.path == "/healthz":
            self.respond_json(HTTPStatus.OK, {"ok": True, "service": "file-share"}, head_only=head_only)
            return

        if parsed.path.startswith("/files/"):
            self.serve_upload(parsed.path.removeprefix("/files/"), head_only=head_only)
            return

        self.respond_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}, head_only=head_only)

    def handle_upload(self) -> None:
        ensure_storage_dirs()
        session_id, is_new_session = self.ensure_session_id()

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.respond_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": "Use multipart/form-data."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Missing Content-Length header."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        if content_length <= 0:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "No upload body received."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        if content_length > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
            self.respond_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": f"Upload is larger than {MAX_UPLOAD_MB} MB."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )

        field_name = "file" if "file" in form else "photo" if "photo" in form else None
        if not field_name:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Field 'file' is required."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        file_field = form[field_name]
        if isinstance(file_field, list):
            file_field = file_field[0]

        if not getattr(file_field, "file", None):
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Field 'file' is empty."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        original_name = Path(file_field.filename or "upload").name
        file_bytes = file_field.file.read(MAX_UPLOAD_BYTES + 1)
        if not file_bytes:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Uploaded file is empty."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        if len(file_bytes) > MAX_UPLOAD_BYTES:
            self.respond_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": f"Upload is larger than {MAX_UPLOAD_MB} MB."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        extension, mime_type = self.resolve_file_type(original_name, file_field.type or "")
        if not extension or not mime_type:
            self.respond_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": f"Accepted files: {ALLOWED_FILE_LABEL}.",
                },
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        stored_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(6)}{extension}"
        destination = UPLOAD_DIR / stored_name
        destination.write_bytes(file_bytes)

        record = write_upload_record(
            filename=stored_name,
            session_id=session_id,
            original_name=original_name,
            size_bytes=len(file_bytes),
            content_type=mime_type,
        )
        item = serialize_upload_item(self.base_url(), record)

        self.respond_json(
            HTTPStatus.CREATED,
            {
                "ok": True,
                "filename": stored_name,
                "size": len(file_bytes),
                "content_type": mime_type,
                "url": item["url"],
                "item": item,
            },
            extra_headers=self.session_headers(session_id, is_new_session),
        )

    def handle_delete(self, file_name: str) -> None:
        ensure_storage_dirs()
        session_id, is_new_session = self.ensure_session_id()
        normalized = Path(file_name).name
        if normalized != file_name or not normalized:
            self.respond_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": "Not found"},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        metadata_path = metadata_path_for(normalized)
        record = load_upload_record(metadata_path, require_file=False)
        if not record:
            self.respond_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": "Not found"},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        if record["session_id"] != session_id:
            self.respond_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "Not your file."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        file_path = UPLOAD_DIR / normalized
        try:
            if file_path.exists():
                file_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()
        except OSError:
            self.respond_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Delete failed."},
                extra_headers=self.session_headers(session_id, is_new_session),
            )
            return

        self.respond_json(
            HTTPStatus.OK,
            {"ok": True, "deleted": normalized},
            extra_headers=self.session_headers(session_id, is_new_session),
        )

    def resolve_file_type(self, original_name: str, uploaded_mime_type: str) -> tuple[str | None, str | None]:
        extension = Path(original_name).suffix.lower()
        if extension in ALLOWED_EXTENSIONS:
            return extension, ALLOWED_EXTENSIONS[extension]

        normalized_mime_type = uploaded_mime_type.lower().strip()
        if normalized_mime_type in MIME_TO_EXTENSION:
            return MIME_TO_EXTENSION[normalized_mime_type], normalized_mime_type

        guessed_mime_type, _ = mimetypes.guess_type(original_name)
        if guessed_mime_type and guessed_mime_type in MIME_TO_EXTENSION:
            return MIME_TO_EXTENSION[guessed_mime_type], guessed_mime_type

        return None, None

    def ensure_session_id(self) -> tuple[str, bool]:
        raw_cookie = self.headers.get("Cookie", "")
        if raw_cookie:
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except Exception:
                cookie = SimpleCookie()
            current = cookie.get(SESSION_COOKIE_NAME)
            if current:
                session_id = current.value.strip().lower()
                if SESSION_ID_PATTERN.fullmatch(session_id):
                    return session_id, False

        return secrets.token_hex(16), True

    def session_headers(self, session_id: str, is_new_session: bool) -> list[tuple[str, str]]:
        if not is_new_session:
            return []

        parts = [
            f"{SESSION_COOKIE_NAME}={session_id}",
            "Path=/",
            f"Max-Age={SESSION_COOKIE_MAX_AGE}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if self.request_scheme() == "https":
            parts.append("Secure")
        return [("Set-Cookie", "; ".join(parts))]

    def serve_upload(self, file_name: str, head_only: bool = False) -> None:
        normalized = Path(file_name).name
        if normalized != file_name or not normalized:
            self.respond_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}, head_only=head_only)
            return

        path = UPLOAD_DIR / normalized
        if not path.is_file():
            self.respond_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}, head_only=head_only)
            return

        record = load_upload_record(metadata_path_for(normalized))
        if record:
            mime_type = record["content_type"] or mimetypes.guess_type(record["original_name"])[0] or "application/octet-stream"
            download_name = record["original_name"]
        else:
            mime_type = ALLOWED_EXTENSIONS.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            download_name = path.name

        payload = path.read_bytes()
        disposition = "inline" if mime_type.startswith("image/") or mime_type in INLINE_CONTENT_TYPES else "attachment"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", build_content_disposition(disposition, download_name))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def base_url(self) -> str:
        if BASE_URL:
            return BASE_URL

        forwarded_proto = self.headers.get("X-Forwarded-Proto")
        forwarded_host = self.headers.get("X-Forwarded-Host")
        host = forwarded_host or self.headers.get("Host") or f"{HOST}:{PORT}"
        proto = forwarded_proto or "http"
        return f"{proto}://{host}"

    def request_scheme(self) -> str:
        forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "").strip().lower()
        if forwarded_proto in {"http", "https"}:
            return forwarded_proto
        return "http"

    def respond_html(
        self,
        body: str,
        head_only: bool = False,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for header_name, header_value in extra_headers or []:
            self.send_header(header_name, header_value)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def respond_json(
        self,
        status: HTTPStatus,
        payload: dict,
        head_only: bool = False,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for header_name, header_value in extra_headers or []:
            self.send_header(header_name, header_value)
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        message = format % args
        print(f"[{timestamp}] {self.address_string()} {message}", flush=True)



def main() -> None:
    ensure_storage_dirs()
    server = ThreadingHTTPServer((HOST, PORT), FileShareHandler)
    print(f"File Share listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
