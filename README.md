# Image Share

Simple image upload app:

- Upload or paste an image.
- Get a direct link back.
- See only the uploads from your current browser session on the page.
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
