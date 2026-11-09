# Stride

Stride is a self-hosted rehabilitation journal for tracking ACL, LCL and meniscus recovery. Record measurements from a phone or computer, compare trends over time, and keep notes and progress photos alongside daily entries.

The core application runs locally with Docker and SQLite. Telegram input and local photo analysis are optional services.

## Features

- Circumference measurements just above the kneecap, 5 cm above it and 15 cm above it, with optional same-day comparison-leg measurements.
- Flexion, signed extension deficit, pain, stiffness, activity, sleep and daily notes.
- Interactive charts with 30-day, 90-day, one-year and all-time views.
- A private photo journal with automatic alignment proposals, batch analysis and a manual two-arm measurement tool.
- Explicit review before saving photo-derived flexion or extension estimates.
- Password-protected access, API tokens, CSV exports and portable journal archives.
- Optional Telegram logging with confirmation buttons and protection against duplicate submissions.

Stride records observations and does not prescribe exercises, diagnose conditions or determine readiness to progress rehabilitation. Photo-derived measurements are estimates from a 2D image and are not clinically validated. Follow an individual care plan from a qualified clinician.

## Quick start

Requirements: Docker Engine and the Docker Compose plugin.

From the repository root:

```sh
docker compose up -d --build
docker compose ps
```

Open [http://localhost:8080](http://localhost:8080). The first visitor creates the journal's owner password. The application currently supports one shared journal and one owner account.

For another device on the same network, use `http://SERVER-LAN-IP:8080`, replacing the placeholder with the Docker host's address.

### Configuration

For a new deployment, copy the example configuration and edit it locally:

```sh
cp .env.example .env
```

Preserve an existing `.env` when updating a deployment. Keep credentials in that untracked file.

| Variable | Purpose |
| --- | --- |
| `PORT` | Host port for the web application; defaults to `8080`. |
| `BIND_ADDRESS` | Host interface to bind; defaults to `0.0.0.0`. |
| `TZ` | Container time zone. Set an IANA zone appropriate for the deployment. |
| `COOKIE_SECURE` | Set to `true` when accessing the application through HTTPS. Leave `false` for plain HTTP on a trusted LAN. |
| `COMPOSE_PROFILES` | Optional services to enable, such as `pose` or `telegram,pose`. |

Configure the journal's time zone and recovering knee separately in **Settings & data**.

### Proxmox and LAN hosting

Run Docker in a Linux VM on Proxmox, with a bridged network adapter and a reserved LAN address. Copy the repository into the VM and use the same Compose commands.

Allow the configured application port from trusted LAN devices. For access beyond the LAN, use an appropriate VPN or HTTPS reverse proxy. Binding to `127.0.0.1` limits access to the Docker host; binding to the host's LAN address limits the service to that interface.

## Optional integrations

Enable local CPU photo analysis:

```sh
docker compose --profile pose up -d --build
```

Enable both photo analysis and Telegram after configuring the bot credentials:

```sh
docker compose --profile telegram --profile pose up -d --build
```

The Telegram worker uses outbound long polling and does not require a public webhook or an additional host port. The pose service runs locally and has no published host port.

See the [integration guide](docs/integrations.md) for credential setup, message examples, photo review, API routes and troubleshooting. Messages and images sent through Telegram pass through Telegram's service. Direct browser uploads and local model inference remain on the deployment server.

## Using the journal

Measurement fields are optional. An entry can contain measurements, a note, or both. There is one entry per date; edit the existing entry to add information later. The main logging button opens the current day's entry when one exists. Use **Daily journal → Add entry** for a historical date.

For circumference measurements, keep the landmarks, leg position, tape tension and time of day consistent. Same-day comparison measurements provide context without carrying older reference values forward. Circumference can reflect swelling, muscle changes and measurement variation.

For photos, select **Flexion** or **Extension**, review or adjust the thigh and shin arms, and explicitly save the selected measurement. Extension requires a direction choice: positive values indicate a deficit, zero is straight, and negative values indicate hyperextension. Saving an alignment alone does not update a daily metric. Existing values require confirmation before replacement.

Background analysis prepares proposals and never logs measurements automatically. Cropped leg photos can require manual alignment when the model cannot locate a full-body pose. Manual alignment uses the visible thigh and shin directions without requiring an off-screen hip point.

The one-year timeline represents elapsed tracking time, not medical clearance. General background on rehabilitation metrics is available from [AOSSM](https://www.sportsmed.org/the-injured-acl) and the [EU–US Meniscus Rehabilitation Consensus](https://pmc.ncbi.nlm.nih.gov/articles/PMC12099113/).

## Storage and backups

The `stride_data` Docker volume contains the SQLite database and uploaded photos. The optional `telegram_data` volume contains bot drafts and polling state. These volumes persist when containers are replaced.

**`docker compose down -v` removes the deployment volumes and their data.** Use ordinary `docker compose down` when the volumes should be retained.

The application offers two exports:

| Export | Contents |
| --- | --- |
| CSV | Daily measurements and notes, suitable for a spreadsheet. |
| Portable ZIP | Profile, entries, photos and photo annotations. Owner credentials and sessions are excluded. |

The portable ZIP is a data archive, not a one-click restore format.

For a consistent backup of the core application volume:

```sh
docker compose stop stride
docker compose run --rm --no-deps -T --entrypoint tar stride -czf - -C /data . > stride-volume-backup.tar.gz
docker compose start stride
```

Restore into an empty deployment volume:

```sh
docker compose stop stride
docker compose run --rm --no-deps -T --entrypoint tar stride --no-same-owner -xzf - -C /data < stride-volume-backup.tar.gz
docker compose start stride
```

Volume backups contain private journal data and may include credentials and sessions. Keep them outside the public repository. Include the Telegram volume and configuration in a coordinated server backup when pending bot drafts must also be recoverable. See the [privacy and publication notes](docs/privacy.md).

## API

The authenticated API reference is available at `/api/docs`; the OpenAPI schema is at `/api/openapi.json`.

Generate a token in **Settings & data → Integrations** for trusted server-side tools. The API supports daily entries, partial updates, private photo uploads, analysis proposals and reviewed measurements. See [API integration details](docs/integrations.md#api-routes).

## Development

Requirements: Python 3.12+ and Node.js 22+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
```

Start the API and frontend in separate terminals:

```sh
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

```sh
npm run dev --prefix frontend
```

Open [http://localhost:5173](http://localhost:5173). Vite proxies `/api` to the local API process. Development data is stored in `data/`; Docker uses its named volume. `DATA_DIR` and `STATIC_DIR` override the server's storage and static-asset paths.

Run the standard checks:

```sh
.venv/bin/python -m pytest backend/tests -q
npm run build --prefix frontend
docker compose config --quiet
```

Browser tests require a disposable QA deployment. They create an owner account and modify journal data. See [browser test setup](docs/integrations.md#browser-tests); never point them at a live journal.

## Maintenance

```sh
docker compose logs --tail=100 stride
docker compose up -d --build
```

Retain any enabled profiles when updating optional services, or configure `COMPOSE_PROFILES` in `.env`.

`/api/health` checks process and database availability without returning journal data. The application container runs as a non-root user with a read-only root filesystem and a persistent writable data volume.

## Privacy and publication

The documentation's example check-ins use fictional values and dates. Application records, photos, exports, credentials, initialization data and test fixtures must be reviewed separately before publishing a repository or container image.

The application supports an initialization file at `backend/seed_history.json`, and the Dockerfile currently includes that file in the image. Treat initialization records as potentially private; sanitize them and any related test expectations before distribution. Updating documentation alone does not sanitize application data or Git history.

See [privacy and publication notes](docs/privacy.md) for the files and artifacts to review.
