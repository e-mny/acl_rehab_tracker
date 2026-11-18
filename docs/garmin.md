# Garmin daily steps

The optional `garmin` worker uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), installed as `garminconnect`, to read daily step totals. It imports the current calendar day's count at **23:59 in the journal's time zone**, and backfills from a configured start date. It uses outbound connections only; no webhook or additional host port is required.

## What gets imported

Only the `steps` field is imported. An existing day's circumference measurements, motion, pain, exercise, notes and original entry source are preserved. A missing day gets an API-sourced entry containing its step count. Subsequent Garmin imports replace that day's steps with Garmin's latest count, including previously entered manual step counts. Other metrics are never inferred from steps.

The worker starts by catching up all due days since `GARMIN_START_DATE`. Before 23:59, the newest due date is yesterday; at 23:59, today's date becomes due. It checks the clock at least every minute while idle and wakes at the nightly boundary. Network availability, login and provider latency can delay an import; missed days are caught up automatically when service resumes.

Garmin totals reflect what the device has uploaded to Garmin Connect. The 23:59 import reads the current day's daily summary so it does not depend on historical statistics having closed for that day. The worker refreshes the most recent seven due days each night to pick up late uploads and corrected totals. Missing/null readings are left missing and retried hourly. An explicit numeric zero is imported as zero. Days without Garmin data do not clear existing steps or create empty entries.

The journal's **Settings → Time zone** controls the schedule. Garmin supplies calendar-day totals; keep the Garmin account's calendar time zone aligned with the journal for matching day boundaries. Changing the container's `TZ` alone does not change the journal time zone.

## Configure a deployment

Generate or reuse the existing Stride API token from **Settings & data → Integrations**. The Garmin and Telegram workers can share it. Creating a new token invalidates the previous one; update all affected workers together.

Set the first date to import in the server's untracked `.env`. This example date is fictional:

```dotenv
GARMIN_START_DATE=2020-01-15
STRIDE_API_TOKEN=REPLACE_WITH_EXISTING_STRIDE_API_TOKEN
```

Keep the real backfill date and credentials in deployment configuration. Garmin credentials do not need to be stored in `.env`. Authentication uses a one-time interactive login and a saved session in the `garmin_data` volume.

### Source-build deployment

From the updated repository on the Docker host:

```sh
docker compose --profile garmin build garmin
docker compose --profile garmin stop garmin
docker compose --profile garmin run --rm --no-deps garmin python -m integrations.garmin.worker --login
```

Enter the Garmin email address, password and MFA code if requested. Inputs are hidden. Successful login stores a session in the volume; passwords and MFA codes are not stored by the worker or printed to logs. Session loading and refresh use the library's [token-store authentication](https://github.com/cyberjunky/python-garminconnect#installation).

Update the application too: the worker requires the new steps-only API endpoint.

```sh
docker compose --profile garmin up -d --build
docker compose --profile garmin logs --tail=50 garmin
```

Retain any other enabled profiles, or add `garmin` to the server's existing `COMPOSE_PROFILES`, for example `pose,telegram,garmin` when all optional services are wanted. The initial backfill begins immediately after login when the worker starts.

### GHCR image deployment on Proxmox

Wait for the updated GitHub Actions run to publish all four images, then pull the deployment-file changes into the existing server checkout. Keep the same Compose project name and journal/bot volume names. The new image is `IMAGE_REPOSITORY-garmin:IMAGE_TAG`, using the same image prefix and tag as the other services.

Add `GARMIN_START_DATE` to `.env` and set an explicit `GARMIN_DATA_VOLUME` for the saved session and checkpoints. The default is `COMPOSE_PROJECT_NAME_garmin_data`. For the new integration only, create that new volume once; replace the placeholder with the exact value configured in `.env`:

```sh
sudo docker volume create YOUR_GARMIN_DATA_VOLUME
sudo docker compose -f compose.production.yaml pull garmin
sudo docker compose -f compose.production.yaml stop garmin
sudo docker compose -f compose.production.yaml run --rm --no-deps garmin python -m integrations.garmin.worker --login
```

Add `garmin` to `COMPOSE_PROFILES` after successful login. Then reconcile the complete release and install the updated timer script, which now recognizes the Garmin service and its volume:

```sh
sudo env STRIDE_DEPLOY_DIR="$PWD" bash deploy/update.sh
sudo bash deploy/install-auto-update.sh "$PWD"
```

Use the server's existing root registry login for private images. The existing journal and Telegram volumes are retained. See the [image deployment guide](deployment.md) for package access and update behavior.

## Verify imports and retry

Open **Settings & data → Integrations → Garmin steps**, then refresh connection status. The display shows connection state, imported-day count, the last import time, and days awaiting readings. Imported totals appear in the journal and steps charts alongside the existing rehab data.

For a source-build deployment, manually refresh all due history once:

```sh
docker compose --profile garmin stop garmin
docker compose --profile garmin run --rm --no-deps garmin python -m integrations.garmin.worker --sync
docker compose --profile garmin up -d garmin
```

For an image deployment, use the same commands with `-f compose.production.yaml` and root's Docker account. One-off commands take a lock and refuse to run alongside the continuous worker. `--sync` refreshes all days from the configured start date through the latest due date; it does not import a partial current day before 23:59. It is also useful after restoring an older journal backup while retaining newer worker checkpoints.

Authentication errors require stopping the Garmin worker, rerunning `--login` in the same volume and starting it again. Rate limits pause requests for 30 minutes. Connection or response errors retry after five minutes. Backoff and per-day checkpoints survive restarts. A partially completed backfill resumes safely; repeating an import assigns only that day's steps and does not duplicate entries.

## Storage and privacy

The existing `stride_data` volume stores imported steps and Garmin import status. The separate `garmin_data` volume stores the reusable Garmin session, synchronization checkpoints and a process lock. Back up these volumes privately. Local `garmin-data/` directories and `garmin_tokens.json` files are excluded from Git and Docker build contexts. Container images contain no Garmin credentials or account data.

Do not publish saved sessions, worker databases or volume archives. Logs and status messages use fixed error codes; upstream exception text, credentials and raw Garmin responses are not logged. Only login and read methods are called on Garmin; imported steps are written to the local Stride API.
