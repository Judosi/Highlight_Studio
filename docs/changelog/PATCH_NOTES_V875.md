# v8.7.5 Metadata Safe

Fix for `HTTPConnectionPool(host='localhost', port=11434): Read timed out` during YouTube metadata generation.

## What changed

- Normal **Metadata** generation is now fast/offline and does not wait for Ollama.
- Added optional **AI Metadata** button for creative titles/descriptions through Ollama.
- Added dedicated `metadata_timeout` setting, default `90` seconds.
- AI Metadata now uses only a compact list of final clips instead of sending too much context.
- If Ollama times out, the app saves a usable fallback metadata package instead of showing a long scary failure.
- Added UI fields:
  - `AI Metadata`
  - `Metadata timeout`
  - `Metadata clips`
- Added backend endpoint:
  - `POST /api/projects/{project_id}/metadata-ai`
- Added smoke test that fast metadata works without Ollama.

## Recommended use

Use **Metadata быстро** after render. Use **AI Metadata** only when Ollama is idle and you want more creative names.
