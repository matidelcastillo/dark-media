# Buffer ES automation

GitHub Actions replaces Cloudflare Cron for ES queue refill. It reads the private `automation/manifests/es.json`, verifies each selected image from the public `matidelcastillo/dark-media` repository, then uses the Buffer API. The workflow does not read or write Workers, D1, or R2.

The daily workflow runs at 15:17 UTC and schedules at most three approved posts per run, stopping at ten scheduled posts. It deduplicates against scheduled and sent Buffer posts using their media paths. Posts use the Tuesday/Thursday 14:00 and Sunday 19:00 `America/Mexico_City` cadence. GitHub cron is best-effort; it may start late.

Add only reviewed posts to `automation/manifests/es.json`. Each entry requires a unique `slug`, a caption of at most 150 words, a past timezone-aware `approvedAt`, and exactly five public `_es.jpg` URLs under that slug in `dark-media`; `dueAt` is optional but must be future and on the approved cadence. The committed manifest is currently empty, so scheduled runs create no posts.

The repository Actions secret `BUFFER_API_KEY` is configured. Never commit the key or print it in workflow logs. `workflow_dispatch` runs the same guarded queue-refill operation manually.

Cloudflare's `dark-buffer-pipeline` Worker must remain unused for scheduling; remove its Cron trigger after this workflow is merged and verified. Keep its data/resources intact until their deletion is separately reviewed.
