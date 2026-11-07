# Stride

A private, self-hosted ACL, LCL and meniscus rehab journal. Built for manual input on a phone or computer over your local network, with one Docker service and SQLite storage. No cloud account, analytics, external fonts or external API is required.

## Run on Proxmox

Run Docker Engine and Docker Compose in a Linux VM on Proxmox (for example, Debian or Ubuntu). Give the VM a bridged network adapter and a reserved LAN IP. Copy this project to the VM, then run from the project directory:

```sh
docker compose up -d --build
docker compose ps
```

Open **http://YOUR-VM-IP:8080** from any device on the same network. On the computer running Docker, use **http://localhost:8080**. The first visitor creates the journal's password. Choose a password of at least 10 characters. There is one shared journal and one owner password.

If your VM has a firewall, permit TCP port 8080 from your LAN. The default binds to all host interfaces; restrict the VM firewall to your trusted subnet. Keep the service on your LAN, or access remotely over a VPN. An HTTPS reverse proxy can provide encryption; set `COOKIE_SECURE=true` in that case. Do not enable secure cookies for plain HTTP because browsers will not send them.

Optional configuration:

```sh
cp .env.example .env
# Edit PORT, BIND_ADDRESS, TZ or COOKIE_SECURE if needed.
docker compose up -d
```

`BIND_ADDRESS` is the Docker host's address, not the container's address. Use `127.0.0.1` when only a reverse proxy on the same host should reach it; use the VM's LAN IP to bind to that interface.

## Included history

Your 32 entries from **7 October through 7 November 2026** are included in `backend/seed_history.json` and imported into SQLite on first start. Dates use the day/month/year interpretation of your messages. The first entry is dated **7 October**, even though it was sent on 8 October. Message timestamps are retained in notes as source context, not assumed to be measurement times.

The three measurements are **just above the kneecap**, **5 cm above the kneecap**, and **15 cm above the kneecap**, in centimetres. The first site's label matches your existing measurement technique. Optional other-leg measurements are populated only on **7 and 14 October**; no reference values are carried forward to other dates. No range-of-motion, symptom or activity values are inferred. The rehab start date and affected side are left for you to set in Settings.

The import is transactional and runs once per database. Existing dates are preserved. A completed import marker prevents entries you later delete from reappearing on restart. A fresh Docker volume imports the history again. This seed contains personal data: remove or replace it before sharing the project or image with others.

## What you can track

- Three circumference measurements for the recovering leg and optional same-day other-leg comparisons.
- Flexion and signed extension deficit: 0° is straight; positive is short of straight; negative is hyperextension.
- Pain at rest and with activity, morning stiffness, giving-way episodes, exercise minutes, daily steps and sleep.
- Daily notes and clinician instructions in your settings.
- Progress photos with automatic analysis, batch proposals, and a two-arm thigh/shin alignment tool with zoom and a magnifier. Choose Flexion or Extension; extension requires an explicit deficit/hyperextension direction. A reviewed estimate can be logged to the selected metric while preserving all other fields.

All measurement fields are optional. You can save notes alone, or log a zero value when appropriate. There is one entry per date; edit an existing entry to add more information. Entries can be edited and deleted. Trends offer 30-day, 90-day, one-year and all-time views. Sample data is explicitly labelled and exists only in the browser.

The main button opens today's existing entry when you have already logged it. To enter a different historical date, use **Daily journal → Add entry** and change the date.

Circumference changes may reflect swelling, muscle size and measurement variation. The app does not equate a smaller thigh with recovery or prescribe targets, exercises or activity progression. The one-year timeline tracks elapsed calendar time; it is not medical clearance. Combined ligament and meniscus injuries need an individual care plan. The optional metrics are informed by [AOSSM's ACL overview](https://www.sportsmed.org/the-injured-acl) and the [2024 EU–US Meniscus Rehabilitation Consensus](https://pmc.ncbi.nlm.nih.gov/articles/PMC12099113/).

## Storage and backups

The named volume `stride_data` is mounted at `/data` and contains `stride.sqlite3` plus `photos/`. Restarting, rebuilding or replacing the container keeps it. **`docker compose down -v` deletes the volume and its data.** Normal `docker compose down` preserves it.

Settings provides:

- **CSV:** all daily entries, suitable for a spreadsheet or a physio visit.
- **Portable ZIP:** `journal.json` (profile, entries and photo annotations) and the photos. Credentials and browser sessions are excluded. This is a portable data archive, not a one-click restore format.

For a full server backup, stop the service and archive the volume consistently:

```sh
docker compose stop stride
docker compose run --rm --no-deps -T --entrypoint tar stride -czf - -C /data . > stride-volume-backup.tar.gz
docker compose start stride
```

Store the archive privately. To restore it into this deployment, stop the service and use the same service volume:

```sh
docker compose stop stride
docker compose run --rm --no-deps -T --entrypoint tar stride --no-same-owner -xzf - -C /data < stride-volume-backup.tar.gz
docker compose start stride
```

Restore onto an empty volume for an exact replacement. Extraction runs as the app user (UID 10001), so restored files belong to that user without requiring root privileges. The volume backup includes owner credentials and sessions, so protect it accordingly. VM backups are another useful layer when taken with the app stopped.

## Telegram input and local photo analysis

Both optional integrations are implemented. Enable local CPU pose analysis with:

```sh
docker compose --profile pose up -d --build
```

In Photo journal, new and unprocessed photos are analysed automatically when a leg is known. Choose Flexion or Extension, review or adjust the two alignment arms, and save the selected measurement. Extension sign is explicitly chosen by you. Proposals never automatically modify daily entries. Batch analysis retries failures without replacing saved alignments or measurements.

For Telegram, create a bot through @BotFather, create a Stride API token in **Settings → Integrations**, and set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID` and `STRIDE_API_TOKEN` in `.env`. Then run:

```sh
docker compose --profile telegram --profile pose up -d --build
```

The bot accepts your existing message format, previews each entry/photo with Save / Cancel buttons, preserves omitted fields, and handles retries and concurrent edits. It uses outbound long polling, so it needs no public webhook or forwarded port. Keep one worker per bot token.

See [the integration guide](docs/integrations.md) for finding your Telegram user ID, message examples, photo review, credentials, API additions and troubleshooting. Messages and photos sent through Telegram pass through Telegram; browser uploads and local analysis stay on your server.

The authenticated API reference is at `/api/docs`, with the OpenAPI schema at `/api/openapi.json`.

## Local development

Requirements: Python 3.12+ and Node 22+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
```

Run the API and frontend in separate terminals:

```sh
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

```sh
npm run dev --prefix frontend
```

Open `http://localhost:5173`. Vite proxies `/api` to port 8000. Local data is in the ignored `data/` directory; Docker uses its own persistent volume. `DATA_DIR` and `STATIC_DIR` can override the server's paths.

Validation:

```sh
.venv/bin/python -m pytest backend/tests -q
npm run build --prefix frontend
docker compose config --quiet
```

The Playwright browser test uses a separate test instance at port 18080, never your live journal. Create it from the built image:

```sh
docker run -d --name stride-qa -p 127.0.0.1:18080:8000 --mount type=volume,src=stride_qa_data,dst=/data stride-rehab:local
cd frontend
npx playwright install chromium
npm run test:e2e
```

The test creates a test password, edits test data, exercises photo uploads and downloads, and checks the mobile layout. Use only a disposable database. `STRIDE_TEST_URL` can override the base URL. Remove the `stride-qa` container and `stride_qa_data` volume when finished.

## Maintenance

```sh
docker compose logs --tail=100 stride
docker compose up -d --build
```

`/api/health` checks process and database availability without exposing journal data. The container runs as a non-root user with a read-only root filesystem, a writable data volume, and no additional Linux capabilities.

If you forget the password, stop the service, back up the data, and remove the `password` setting plus all `sessions` rows from the SQLite database using an SQLite tool. Restart and set a new password. Your entries and photos are unaffected; only use this procedure with access to your own server.
