# Optional integrations

Both integrations run next to Stride in Docker. Only Stride publishes a host port. The Telegram bot makes outbound HTTPS requests, and photo analysis runs locally on the CPU. Each integration is enabled through its own Compose profile.

## Local photo analysis

```sh
docker compose --profile pose up -d --build
docker compose --profile pose ps
```

The first build downloads a versioned MediaPipe Pose Landmarker Lite model and verifies its SHA-256. It is then bundled inside the image; inference makes no external requests. Both Linux AMD64 (typical Proxmox) and ARM64 wheels are available for the pinned MediaPipe version.

Open **Photo journal**. New photos and previously unprocessed stored photos are queued automatically when a leg is known. The queue lives in SQLite, survives app restarts, and waits while the optional pose service is offline. Photos with no selected leg show **Select leg to analyse**; choose one in the photo editor or set your recovering knee in Settings.

To measure a photo:

1. Select **Flexion · bending** or **Extension · straightening**, and confirm the leg.
2. Use the proposed alignment, or align the two goniometer arms manually: green along the thigh, amber along the shin. Their ends represent directions; they do not have to reach a visible hip or ankle. Drag the knee pivot to move both arms together. **Place knee pivot** lets you tap its position; **Swap thigh / shin** reverses the arm labels.
3. Zoom from 100–300% and scroll the image for precision. Large drag targets, a magnifier, one-pixel nudge buttons, and arrow keys are available. Shift + arrow moves ten image pixels. Untouched starter guides cannot be logged as measurements.
4. For extension, explicitly choose **Short of straight** (positive deficit) or **Beyond straight** (negative hyperextension). The image supplies an angle magnitude; this direction is user-confirmed, not inferred by the model.
5. Review the measurement type, leg, alignment, date and direction, check the review box, and select **Save flexion measurement** or **Save extension measurement**. An existing different value requires an additional replacement confirmation. Concurrent edits invalidate the preview.

**Save alignment only** saves the photo's guides and type without changing a daily metric. Logging changes only the selected field: extension never overwrites flexion, and flexion never overwrites extension. Other metrics and notes are preserved. Evidence stores the selected metric, signed value, direction, reviewed alignment, photo/entry link, model provenance when used, and timestamp. Legacy photo measurements remain labelled as legacy flexion logs; choosing a new photo type never silently reclassifies or deletes them.

**Analyse all photos** queues unprocessed photos and retries failures, skipping ready proposals, active jobs and photos already logged. Select a batch leg to override saved defaults if needed. **Review photos** steps through photos that have not been logged. Photos that cannot be detected offer manual arm alignment. Existing saved alignments are never replaced by a background proposal. In the editor, a new proposal replaces starter guides only if you have not adjusted them; **Use detected alignment** explicitly applies a proposal when you have already edited an alignment.

The model still works best on a side-on image containing one person with hip, knee and ankle visible. Cropped leg-only images can fail its person-detection step. Failures now explain whether no body pose, multiple people or hidden landmarks caused rejection. Manual thigh/shin axis alignment works without an off-screen hip. Confidence/visibility scores are not measures of clinical angle accuracy.

Angles are recomputed on the server in image pixels to account for image aspect ratio. Knee bend is `180° − angle(thigh arm, knee pivot, shin arm)`. Flexion uses this non-negative value. Extension uses the same magnitude with your explicitly selected sign, in the existing supported range −30° to +90°. These 2D estimates are not clinically validated; check your technique against your physio's measurement and use a consistent setup. Automated circumference estimation is not included.

No GPU passthrough or extra host ports are required. Manual alignment continues to work when the pose service is offline. To leave the service enabled for ordinary Compose commands, set `COMPOSE_PROFILES=pose` in `.env`.

## Telegram setup

1. In Telegram, create a bot through **@BotFather → /newbot**. Copy the bot token into your local `.env` file. Do not commit that file.
2. In Stride, open **Settings & data → Integrations → Create token**. Copy it to the same `.env` file.
3. Configure:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USER_ID=your-numeric-telegram-user-id
STRIDE_API_TOKEN=your-stride-token
```

To find your own numeric ID without using a third-party bot, send `/start` to your new bot and run this read-only helper:

```sh
docker compose --profile telegram build telegram
docker compose --profile telegram run --rm --no-deps telegram python -m integrations.telegram.bot --whoami
```

The helper reads pending private-chat updates and prints sender IDs; it sends no messages. Do this before starting polling, and remove any previously configured webhook if you reused an existing bot. Set `TELEGRAM_ALLOWED_USER_ID` to your own ID, then start:

```sh
docker compose --profile telegram up -d --build
docker compose --profile telegram logs --tail=50 telegram
```

To run both integrations:

```sh
docker compose --profile telegram --profile pose up -d --build
```

Optionally set `COMPOSE_PROFILES=telegram,pose` in `.env` so both stay enabled in subsequent Compose commands. A service already started keeps running until stopped. Stop optional services explicitly when disabling them:

```sh
docker compose --profile telegram stop telegram
docker compose --profile pose stop pose
```

## Sending check-ins

Send one date per message. Your existing format is supported:

```text
7/11/26
bad side:
just above knee cap: 38.3
5cm above knee cap: 38.0
15cm above knee cap: 44.2

good side:
37cm
39.8cm
49cm
```

The shorter format is also supported:

```text
7/11/26
knee: 38.3
+5: 38.0
+15: 44.2
pain: 2
exercise: 30
notes: Morning check-in
```

These are syntax examples, not new records to import. Only include measurements actually taken on that date. `pain` means pain at rest; use `pain_activity` for pain with activity. The parser also accepts `flexion`, `extension`, `stiffness`, `steps`, `sleep`, `instability`, and explicitly named `other_knee`, `other_above5`, `other_above15`. Use decimal points and the labelled units. Telegram notes are limited to 2,000 characters so the full new note fits in the confirmation preview. Invalid, duplicate or unknown metric labels are rejected rather than guessed.

Dates use day/month/year, or ISO `YYYY-MM-DD`. If omitted, the message timestamp is converted using your journal's time zone. The bot shows a preview and **Save / Cancel** buttons. Existing values that change are shown as old → new. Only the supplied fields change. Notes you do not supply remain untouched; if you supply `notes:`, that replaces the existing note after confirmation.

If an entry changes after the preview, the bot presents an updated preview and requires Save again. Entries use `source: telegram`. Zero values are preserved. Repeated deliveries and retries use API idempotency keys to avoid duplicate records. Drafts and polling offsets live in the separate `telegram_data` volume and survive container restarts. Drafts expire after 24 hours and are cleaned up after 30 days.

Commands: `/help`, `/log` (optional prefix for a multiline check-in), `/cancel` (latest pending draft), and `/whoami`. The worker silently ignores other users and group chats. One polling worker should run per bot token.

## Telegram photos

Send a photo or JPEG/PNG/WebP document, optionally with a caption:

```text
7/11/26
Side-on flexion reference
```

After you confirm Save, the bot downloads the image and uploads it to Stride. The same 12 MB upload limit, decoding, resizing and metadata removal apply. Image documents retain more original quality than Telegram-compressed photo messages. Photo cards show their Telegram source. The uploaded photo is automatically queued for local analysis when your recovering side is configured. Open it in Stride, select Flexion or Extension, and review before logging.

Messages and images sent to the bot pass through Telegram. Direct browser uploads and local model inference stay on your own server. Confirmed Telegram uploads are analysed through the same durable server queue. The bot never automatically logs an angle.

## API additions

- `POST /api/entries/preview`: body `{ "date": "YYYY-MM-DD", "values": { ...partial fields... } }`. Returns the proposed merged entry, existing entry and a version string. Writes nothing.
- `PATCH /api/entries/by-date/{date}`: partial fields, creates a date if absent. Optional `If-Match` must equal the preview's version (`new` for an absent date). A conflict returns 409.
- `Idempotency-Key` on PATCH, photo upload and photo apply: a repeated identical request returns the original response. Reusing a key for different data returns 409.
- `POST /api/photos/{id}/queue`: queues a photo for background analysis using `{ "side": "left" }` or `right`. Existing ready proposals and failures on the same leg are not retried by this route.
- `POST /api/photos/analyze-batch`: optional `photo_ids`, optional `side`, and `retry_failed` (default true). Returns queued, skipped and needs-side IDs.
- `POST /api/photos/{id}/analyze`: body `{ "side": "left" }` or `right`. Stores a proposal with model/version, points, visibility and computed angle. Daily entries remain unchanged.
- `POST /api/photos/{id}/apply`: body includes `date`, `caption`, `side`, three normalized `points`, `reviewed: true`, and `expected_version` from an entry preview. Also pass `metric` (`flexion` or `extension`, default flexion for legacy clients), `extension_direction` (`deficit` or `hyperextension`, required for extension), and optional `analysis_id` when the reviewed alignment originated from a proposal. The server recomputes signed geometry; an arbitrary supplied angle is never used.
- `GET /api/integrations`: authenticated pose availability and Telegram heartbeat status. The Telegram worker sends an authenticated heartbeat every polling cycle; the UI considers it online for 120 seconds after the last heartbeat.

All journal routes retain normal authentication. Workers do not need access to password hashes or journal files. Only the pose worker receives a stored image through Stride's internal request.

## Credentials, maintenance and testing

A new Stride API token replaces the old one. After changing `.env`, recreate the Telegram service with `docker compose --profile telegram up -d telegram`; `docker compose restart` alone does not reload environment values. Revoking a token stops API access immediately. No credentials or token-bearing request URLs are logged by the bot's error handlers.

`stride_data` still contains your journal and photos. `telegram_data` contains offsets and drafts; include it in server backups if you need pending drafts restored. Losing bot state does not delete journal data. Avoid restoring bot state independently to a point earlier than the journal; keep both volumes together in a VM backup. `.env` is separate and must be saved privately if you want credentials restored.

Tests exercise parsing, confirmation, allowlisting, cancellation/conflicts, retry idempotency, restart state, uploads, proposal isolation and reviewed photo application using a mocked Telegram API and disposable SQLite databases:

```sh
.venv/bin/python -m pytest backend/tests -q
npm run build --prefix frontend
```

Browser tests require the disposable QA container described in the root README, with the pose service reachable over its Docker network to exercise successful automatic detection. They never use the live journal. Actual live Telegram delivery requires your own configured credentials and a private message to your bot.

For a successful model/browser integration check using a disposable QA instance, enable the pose service first and start QA on the same network:

```sh
docker run -d --name stride-qa --network acl_rehab_tracker_default -p 127.0.0.1:18080:8000 -e POSE_ANALYZER_URL=http://pose:8001 --mount type=volume,src=stride_qa_data,dst=/data stride-rehab:local
cd frontend
STRIDE_POSE_TEST_IMAGE=/absolute/path/to/a/test-photo.jpg npm run test:e2e
```

Use the actual Compose network name if you renamed the project. Without `STRIDE_POSE_TEST_IMAGE`, the successful pose browser test is skipped; backend isolation/review tests still run. A full-body test image must have the selected hip, knee and ankle visible for detection. Test data is disposable and is edited by the browser test.
