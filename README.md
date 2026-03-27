# File Share

Simple file sharing app:

- Upload a file or paste an image.
- Get a direct link back.
- Delete files from your current browser session.
- See only the files from your current browser session on the page.
- Store files in `data/uploads/` and session metadata in `data/metadata/`.

## Run

```bash
python3 app.py
```

## Docker

```bash
docker compose up -d --build
```

## Public URL

Set `BASE_URL` if you want returned links to use a specific public host.
