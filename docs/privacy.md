# Privacy and publication notes

Stride stores health measurements, free-text notes, photographs and account information. Treat deployment data separately from application source when sharing a repository, container image, screenshot or support log.

Documentation examples use fictional values and generic placeholders. This does not establish that datasets, test fixtures, images or Git history have been sanitized.

## Files to review before publication

| Material | Handling |
| --- | --- |
| `.env` and local configuration copies | Keep private. Commit only examples with empty values or explicit placeholders. |
| SQLite databases and their WAL/SHM files | Keep outside the source repository. |
| Uploaded photos and thumbnails | Exclude personal images and identifying metadata. |
| CSV, ZIP and volume backups | Keep private; archives can contain notes, photos, credentials or sessions. |
| Initialization data | Use synthetic records for public distribution. |
| Test fixtures and assertions | Replace real names, dates, measurements and message transcripts with fictional data. |
| Browser test output | Exclude screenshots, traces, reports and downloads containing private information. |
| Logs and pasted terminal output | Review for credentials, account identifiers, local paths and private records. |

The initialization file `backend/seed_history.json` is loaded on first database setup when present during source development. Container builds exclude this file and test fixtures; new containers start with an empty journal. Review and sanitize the tracked file before distributing source. Related assertions in `backend/tests/` and browser fixtures in `frontend/tests/` require the same review. Previously built images may still contain their original initialization data.

Do not rely solely on `.gitignore`: it does not remove files already tracked by Git, sanitize fixture values, or change previously built images.

## Git history

Check the files staged for publication and the commit history as well as the current working tree. Deleting a private file in a later commit does not remove its earlier contents from history.

History cleanup is a separate operation from editing documentation. Preserve a private backup and deliberately review the publication history before changing repository visibility or pushing to a public remote. If credentials were committed, revoke or rotate them; removing their text alone does not invalidate them.

## Screenshots and examples

Use synthetic records and consented or synthetic images for demonstrations. Check names, dates, captions, browser tabs, file paths, account identifiers and visible background details before publishing screenshots. Use neutral hostnames and placeholder IDs in command examples.

## Runtime data boundaries

The core application stores its database and photos in the deployment volume. Local pose inference processes photos on the deployment server. Model assets are downloaded when building the pose image.

Messages and photos sent to the Telegram bot pass through Telegram's service before reaching the deployment. Bot drafts and polling state live in a separate volume. Both journal and bot data should be included in private backup planning.

Optional group access also makes submissions and bot replies visible to group members, including previous measurements shown in confirmation previews. The sender allowlist restricts who can write; it does not hide messages from group members. Keep real user and group IDs in the deployment's untracked configuration, and use private chat for private submissions.

Exports and server backups contain private information even when credentials are excluded from an individual export format. Keep them outside public repositories and restrict access to their storage.
