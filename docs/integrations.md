# Optional integrations

Stride supports a Telegram input worker and a local pose-analysis service. Both run alongside the core application through Docker Compose profiles. Only the core application publishes a host port.

## Local photo analysis

```sh
docker compose --profile pose up -d --build
docker compose --profile pose ps
```

The image build downloads a versioned MediaPipe Pose Landmarker Lite model and verifies its SHA-256. The model is then bundled into the image; inference runs locally on the CPU and makes no external image-analysis requests.

New photos and unprocessed stored photos are queued when a leg is known. The SQLite queue survives application restarts and waits while the optional pose service is offline. Select a leg in the photo editor or configure the recovering knee in **Settings & data**.

### Reviewing a measurement

1. Select **Flexion · bending** or **Extension · straightening**, and confirm the leg.
2. Review the proposed alignment, or align the two arms manually: green follows the thigh and amber follows the shin. Their ends indicate directions and do not have to reach a visible hip or ankle.
3. Drag the knee pivot to move both arms together. Use **Place knee pivot** to tap its position, or **Swap thigh / shin** to reverse the arm labels.
4. Use zoom, the magnifier, one-pixel nudge buttons or arrow keys for precision. Shift + arrow moves ten image pixels. Untouched starter guides cannot be logged.
5. For extension, select **Short of straight** for a positive deficit or **Beyond straight** for negative hyperextension. The model does not infer this sign.
6. Review the type, leg, alignment, date and direction, then save the selected measurement. Replacing a different existing value requires confirmation. Concurrent edits invalidate an outdated preview.

**Save alignment only** stores annotations without updating daily measurements. Logging changes only the selected metric and preserves other values and notes. The evidence records the metric, signed value, direction, reviewed points, photo/entry link, available model provenance and review time.

Changing a photo's type does not move or delete a previously logged measurement. Older logs without an explicit metric remain labelled as legacy flexion records.

### Batch analysis and manual fallback

**Analyse all photos** queues unprocessed photos and retries failures, skipping ready proposals, active jobs and photos already logged. A batch leg selection can override saved defaults. **Review photos** steps through photos that have not been logged.

Background proposals do not replace saved alignments. In an open editor, a proposal replaces starter guides only if no manual adjustments have been made. **Use detected alignment** explicitly applies a proposal over an edited alignment.

Detection works best with one person in a side-on image and the hip, knee and ankle visible. Cropped leg-only images may fail the model's person-detection step. Failure messages distinguish missing body poses, multiple people and hidden landmarks. Manual thigh/shin alignment remains available without an off-screen hip point.

Angles are recomputed on the server using image pixels to account for aspect ratio:

```text
knee bend = 180° − angle(thigh arm, knee pivot, shin arm)
```

Flexion uses the non-negative angle. Extension uses the same magnitude with the explicitly selected sign, within the supported range of −30° to +90°. Visibility scores are not measures of clinical accuracy. These 2D estimates are not clinically validated; use a consistent setup and check measurement technique with a qualified clinician. Circumference estimation from photos is not implemented.

Manual alignment works without the pose service. To enable the service for subsequent Compose commands, set `COMPOSE_PROFILES=pose` in `.env`.

## Telegram setup

1. Create a bot through [@BotFather](https://t.me/BotFather) using `/newbot`.
2. Generate a Stride token in **Settings & data → Integrations → Create token**.
3. Add the credentials and allowed numeric Telegram user ID to the deployment's untracked `.env` file:

```dotenv
TELEGRAM_BOT_TOKEN=REPLACE_WITH_BOT_TOKEN
TELEGRAM_ALLOWED_USER_ID=REPLACE_WITH_NUMERIC_USER_ID
# Optional: allow the same user to log from one group as well.
TELEGRAM_ALLOWED_GROUP_ID=
STRIDE_API_TOKEN=REPLACE_WITH_STRIDE_API_TOKEN
```

These are placeholders, not working credentials. Do not commit real values.

### Finding the allowed user ID

Send `/start` to the new bot, then run the read-only setup helper before starting the worker:

```sh
docker compose --profile telegram build telegram
docker compose --profile telegram run --rm --no-deps telegram python -m integrations.telegram.bot --whoami
```

The helper reads pending private-chat updates and prints sender IDs; it sends no messages. Set `TELEGRAM_ALLOWED_USER_ID` to the intended owner's numeric ID. If reusing a bot with a webhook, remove that webhook before using the helper.

### Using the bot in a group

Add the bot to the intended group and set `TELEGRAM_ALLOWED_GROUP_ID` in the deployment's untracked `.env` to that group's negative numeric ID. Leave it empty for private-chat access only. Recreate the worker after changing configuration:

```sh
docker compose --profile telegram up -d --build telegram
```

`TELEGRAM_ALLOWED_USER_ID` still controls who can submit measurements, upload photos, and press Save or Cancel. Other group members cannot write to the journal. Both the owner's private chat and the configured group remain available. Entries from both chats go into the same journal.

Keep [Telegram group privacy mode](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get) enabled. Replace `YourBotUsername` below with the bot's actual username, and send this as one message (the measurements are fictional):

```text
/log@YourBotUsername
knee: 41.2
+5: 43.7
+15: 53.6
```

Alternatively, send `/help@YourBotUsername`, then reply directly to the bot's response with measurements or a photo. Replies let the bot receive photo messages while privacy mode is enabled. Ordinary group conversation is ignored. You can also prefix a photo caption with `/log@YourBotUsername`, with its optional date on the following line.

Previews, Save/Cancel buttons, and confirmations appear in the originating chat and forum topic. Drafts and `/cancel` are scoped to that chat/topic. Only the configured user can act on buttons, and a draft cannot be saved from another chat or topic. `/whoami@YourBotUsername` displays the user and current chat IDs after the group is configured.

Group members can see the submitted measurements, photos, previews, and replies, including previous values shown in an update preview. Use private chat when those details should stay out of the group. Anonymous administrator messages cannot be matched to the allowed user; send as your personal account.

Start the worker:

```sh
docker compose --profile telegram up -d --build
docker compose --profile telegram logs --tail=50 telegram
```

To enable both integrations:

```sh
docker compose --profile telegram --profile pose up -d --build
```

Optionally set `COMPOSE_PROFILES=telegram,pose` in `.env`. Stop optional services explicitly when disabling them:

```sh
docker compose --profile telegram stop telegram
docker compose --profile pose stop pose
```

Only one polling worker should run per bot token. The worker ignores other users and group chats.

## Message formats

The following examples use **fictional dates, measurements and notes** to demonstrate syntax. They are not rehabilitation targets or records to import.

Labelled circumference measurements:

```text
15/1/2020
bad side:
just above knee cap: 41.2
5cm above knee cap: 43.7
15cm above knee cap: 53.6

good side:
40.0cm
42.9cm
52.7cm
```

A shorter check-in:

```text
2020-01-15
knee: 41.2
+5: 43.7
+15: 53.6
pain: 3
exercise: 12
notes: Example check-in for format demonstration.
```

Send one date per message and only include measurements taken on that date. The parser also accepts `flexion`, `extension`, `pain_activity`, `stiffness`, `steps`, `sleep`, `instability`, `other_knee`, `other_above5` and `other_above15`. `pain` means pain at rest. Use decimal points and the labelled units. Unknown labels, duplicate metrics and invalid values are rejected.

Dates use day/month/year or ISO `YYYY-MM-DD`. If omitted, the message timestamp is converted using the journal's configured time zone. Telegram notes are limited to 2,000 characters so the full new note fits in the confirmation preview.

The bot displays **Save / Cancel** buttons before writing data. Existing values that change appear as old → new. Omitted fields remain unchanged. Providing `notes:` replaces the existing note after confirmation.

If an entry changes after the preview, the worker presents an updated preview and requests confirmation again. Submitted entries use `source: telegram`. API idempotency keys protect against duplicate writes during retries. Drafts and polling offsets persist in the `telegram_data` volume. Drafts expire after 24 hours and are cleaned up after 30 days.

Available commands:

| Command | Action |
| --- | --- |
| `/help` | Show accepted formats. |
| `/log` | Optional prefix for a multiline check-in. |
| `/cancel` | Cancel the latest pending draft in the current chat/topic. |
| `/whoami` | Show the allowed user's numeric Telegram ID and current chat ID. |

## Telegram photos

Send a photo or JPEG, PNG or WebP document with an optional caption. This example is fictional:

```text
2020-01-15
Example side-on image caption.
```

After confirmation, the worker downloads the image and uploads it to Stride. The application's 12 MB limit, image decoding, resizing and metadata removal apply. Image documents can retain more original quality than Telegram-compressed photo messages.

Confirmed uploads use the same background analysis queue as browser uploads. Select the measurement type and review the alignment in **Photo journal** before logging an angle. Telegram does not automatically save a derived measurement.

Messages and images sent through the bot pass through Telegram. Direct browser uploads and local inference remain on the deployment server.

## API routes

The authenticated reference is at `/api/docs`, with the OpenAPI schema at `/api/openapi.json`. Send `Authorization: Bearer TOKEN` for token-based access. The token is shown once when generated and only its hash is stored.

| Route | Purpose |
| --- | --- |
| `POST /api/entries/preview` | Validate and merge partial fields without writing data. |
| `PATCH /api/entries/by-date/{date}` | Update supplied fields or create a missing date. |
| `POST /api/photos` | Upload a private photo. |
| `POST /api/photos/{id}/queue` | Queue analysis of a stored photo. |
| `POST /api/photos/analyze-batch` | Queue a batch of proposals. |
| `POST /api/photos/{id}/analyze` | Request a landmark proposal for a selected leg. |
| `POST /api/photos/{id}/apply` | Log an explicitly reviewed photo measurement. |
| `GET /api/integrations` | Report pose availability and Telegram heartbeat status. |

Entry previews accept `date` and `values`, and return the merged entry, existing entry and a version string. Supply that version in `If-Match` when patching to reject conflicting edits; `new` identifies an absent entry. Conflicts return HTTP 409.

PATCH, photo upload and photo application support `Idempotency-Key`. Repeating an identical request with the same key returns the original result. Reusing the key for a different request returns HTTP 409.

Photo uploads use multipart fields `file`, `photo_date`, optional `caption`, optional `side`, and optional `measurement_type`. Analysis and queue requests use `side: left` or `right`. The queue route does not retry an existing ready proposal or failure on the same leg. Batch requests accept optional `photo_ids`, optional `side` and `retry_failed` (default `true`), and return queued, skipped and needs-side IDs.

Reviewed photo application requires `date`, `caption`, `side`, three normalized `points`, `reviewed: true` and `expected_version`. Include `metric` as `flexion` or `extension`; legacy clients default to flexion. Extension also requires `extension_direction` as `deficit` or `hyperextension`. Optional `analysis_id` links model provenance. The server recomputes geometry rather than trusting a supplied angle.

Only the selected daily metric changes. The server records the evidence with the photo in the same transaction. A proposal alone never changes a daily entry.

## Credentials and maintenance

Creating a new Stride API token replaces the previous one. After editing `.env`, recreate the Telegram service:

```sh
docker compose --profile telegram up -d telegram
```

A container restart alone does not reload environment values. Revoking a token removes API access immediately. The connection indicator uses the worker's authenticated heartbeat and considers it online for 120 seconds after the last heartbeat.

Run only one polling worker per bot token, including across development machines and production servers. Repeated Telegram HTTP 409 errors with no webhook configured indicate competing polling instances. Stop the duplicate worker, then rebuild and recreate the worker on the intended host. The heartbeat confirms journal connectivity; check worker logs separately for Telegram polling errors.

The `stride_data` volume contains journal records, photos and analysis jobs. `telegram_data` contains drafts and polling state. Back up both together when pending drafts must be restored consistently. Save configuration separately and privately. See the [privacy and publication notes](privacy.md) before sharing logs, screenshots, repositories or images.

## Browser tests

Browser tests create an account and modify records. Run them only against disposable data. Review initialization files and test fixtures before publishing or distributing a test image.

Build the application image, then start an isolated QA instance from the repository root:

```sh
docker compose build stride
docker network create stride-qa-net
docker run -d --name stride-qa --network stride-qa-net -p 127.0.0.1:18080:8000 --mount type=volume,src=stride_qa_data,dst=/data stride-rehab:local
cd frontend
npx playwright install chromium
npm run test:e2e
```

Pose-dependent browser tests are skipped unless `STRIDE_POSE_TEST_IMAGE` is provided. For a full check, first build and run a local pose worker on the same QA network:

```sh
# From the repository root:
docker build -f integrations/pose/Dockerfile -t stride-pose:qa .
docker run -d --name stride-pose-qa --network stride-qa-net stride-pose:qa
```

Recreate only the disposable application container with the QA worker URL, then run the full browser checks:

```sh
# From the repository root:
docker rm -f stride-qa
docker run -d --name stride-qa --network stride-qa-net -p 127.0.0.1:18080:8000 -e POSE_ANALYZER_URL=http://stride-pose-qa:8001 --mount type=volume,src=stride_qa_data,dst=/data stride-rehab:local
cd frontend
STRIDE_POSE_TEST_IMAGE=/path/to/synthetic-or-consented-test-image.jpg npm run test:e2e
```

Use a test image with visible hip, knee and ankle landmarks. Do not publish private photos or generated screenshots. `STRIDE_TEST_URL` can override the test endpoint; never set it to a live journal.

After testing, remove only the disposable resources:

```sh
docker rm -f stride-qa
# If the optional QA pose worker was started:
docker rm -f stride-pose-qa
docker volume rm stride_qa_data
docker network rm stride-qa-net
```

The backend tests use temporary databases and a mocked Telegram API. Live Telegram delivery requires separately configured credentials and a private message to the bot.
