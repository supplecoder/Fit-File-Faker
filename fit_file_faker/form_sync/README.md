# FORM → Garmin Sync

Automatically syncs your [FORM swim goggles](https://www.formswim.com/) workouts
to Garmin Connect — with full Training Effect / Training Status credit — by
rewriting each FORM `.fit` file to appear as your own Garmin device, then
uploading it.

The whole thing runs on free infrastructure: a **GitHub Actions** workflow does
the work, and a small **Google Apps Script** watches your Gmail and triggers the
workflow whenever FORM emails you a new export.

```
FORM app  ──email──▶  Gmail  ──Apps Script──▶  GitHub Actions
                                                    │
                                   download S3 zip  │  rewrite device metadata
                                                    ▼
                                            Garmin Connect (with Training Effect)
```

---

## How it works

1. You request a data export in the FORM app. FORM emails you a link to a ZIP
   archive of `.fit` files (hosted on S3, signed with short-lived credentials).
2. A Google Apps Script polls Gmail for that email and, when it appears, fires
   the GitHub workflow via the `repository_dispatch` API.
3. The workflow ([`pipeline.py`](pipeline.py)):
   - finds the unread FORM email over IMAP,
   - downloads and unzips the export,
   - rewrites each `.fit` file's manufacturer/product/unit-ID so Garmin treats
     it as your real device,
   - uploads to Garmin Connect,
   - refreshes and re-stores the Garmin session token,
   - marks the email read (only on success).

Failures leave the email **unread** and fail the workflow run, so you get a
GitHub failure notification and the email is retried on the next trigger.

---

## Module layout

| File | Responsibility |
|------|----------------|
| [`gmail.py`](gmail.py) | IMAP connect, search unread FORM emails, extract the S3 URL, mark read |
| [`downloader.py`](downloader.py) | Download the S3 ZIP, extract `.fit` files (with redacted error logging) |
| [`processor.py`](processor.py) | Rewrite a `.fit` file to the target Garmin device via `FitEditor` |
| [`garmin.py`](garmin.py) | Authenticate to Garmin Connect and upload; manage the session token string |
| [`github.py`](github.py) | Persist the refreshed Garmin token back to a GitHub Secret (encrypted) |
| [`pipeline.py`](pipeline.py) | Orchestrates all of the above; the workflow entry point |

Supporting files (repo root):
- [`.github/workflows/form_sync.yml`](../../.github/workflows/form_sync.yml) — the workflow
- [`scripts/seed_garmin_tokens.py`](../../scripts/seed_garmin_tokens.py) — one-time local Garmin auth
- [`scripts/form_gmail_trigger.gs`](../../scripts/form_gmail_trigger.gs) — the Gmail watcher

---

## Setup

You'll need: a FORM account, a Garmin Connect account, a Gmail account, and a
GitHub account. Budget ~20 minutes.

### 1. Fork (or create) the repository

Fork this repo to your own GitHub account, **or** create a standalone copy.

> **Important — forks can't use cron.** GitHub silently disables *scheduled*
> (`schedule:`/cron) workflows on forked repositories. This project works around
> that with the Apps Script + `repository_dispatch` trigger (steps 5–6), which
> works fine on forks. If you instead create a **standalone** (non-fork) repo,
> the `schedule:` fallback already in the workflow will activate automatically
> and you can skip the Apps Script entirely if you prefer cron polling.

### 2. Find your Garmin device IDs

You need two numbers from your physical Garmin device:

- **Unit ID** — Settings → System → About → Unit ID. This is a long number
  (e.g. `3490329847`). Despite the FIT spec calling this field "serial_number",
  it is **not** the printed serial on the back of the watch.
- **Product (device) ID** — the numeric model ID for your device. The easiest
  way to find your device is the main tool's interactive picker, which lets you
  choose by name (e.g. "Forerunner 965") rather than hunting for a number:

  ```bash
  fit-file-faker --config-menu
  ```

  Create/edit a profile, select your device from the list, and note the product
  ID it shows (Forerunner 965 = `4315`). Alternatively, look it up directly in
  the supplemental registry in [`../config.py`](../config.py)
  (`SUPPLEMENTAL_GARMIN_DEVICES`). Either way, the matching firmware version is
  resolved automatically, so you don't set it.

  > Future improvement: integrate this device selection directly into the
  > form_sync setup so the product ID is discovered the same way as the main CLI.

> Matching the Unit ID to your real device is what makes Garmin Connect grant
> Training Effect / Training Status. A random Unit ID may upload but won't be
> recognized as a real device. See `../../CLAUDE.md` for details.

### 3. Create a Gmail App Password

IMAP login needs an App Password (not your normal password):

1. Enable 2-Step Verification on your Google account.
2. Go to <https://myaccount.google.com/apppasswords>, create one named
   `fit-file-faker`, and copy the 16-character code.

### 4. Seed your Garmin token

Run the seeder locally once to capture a Garmin session token (handles MFA
interactively, which GitHub Actions can't):

```bash
pip install -e ".[form_sync]"
python scripts/seed_garmin_tokens.py
```

Copy the long token string it prints — you'll store it as `FFF_GARMIN_TOKENS`.

> If you hit a 429 (rate limit), wait a few minutes and retry — Garmin
> throttles repeated login attempts from the same IP.

### 5. Add the GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add all of these:

| Secret | Value |
|--------|-------|
| `FFF_GMAIL_ADDRESS` | Your Gmail address |
| `FFF_GMAIL_APP_PASSWORD` | The App Password from step 3 |
| `FFF_GARMIN_EMAIL` | Your Garmin Connect email |
| `FFF_GARMIN_PASSWORD` | Your Garmin Connect password |
| `FFF_GARMIN_TOKENS` | The token string from step 4 |
| `FFF_GARMIN_UNIT_ID` | Your device Unit ID (step 2) |
| `FFF_GARMIN_DEVICE_ID` | Your device product ID (step 2) |
| `FFF_GH_PAT` | A PAT used to refresh the token secret — see below |

**`FFF_GH_PAT`**: a fine-grained Personal Access Token
(<https://github.com/settings/personal-access-tokens/new>) scoped to *only this
repository* with **Secrets: Read and write**. The pipeline uses it to write the
refreshed Garmin token back to `FFF_GARMIN_TOKENS` after each run.

> Tip: test the pipeline now by triggering the workflow manually
> (**Actions → FORM → Garmin Sync → Run workflow**) with an unread FORM email
> in your inbox, before wiring up the automatic trigger.

### 6. Wire up the automatic Gmail trigger (Apps Script)

This is what makes it hands-off. The script lives at
[`scripts/form_gmail_trigger.gs`](../../scripts/form_gmail_trigger.gs).

1. **Create a second PAT for the script** — fine-grained, scoped to only this
   repo, with **Contents: Read and write** (this is what `repository_dispatch`
   requires; it's a different permission than `FFF_GH_PAT`).
2. Go to <https://script.google.com> → **New project**. Replace the default
   `Code.gs` contents with the entire `form_gmail_trigger.gs` file.
3. **Enable the Gmail advanced service**: in the editor, click **Services (+)**
   in the left sidebar → choose **Gmail API** → **Add** (identifier `Gmail`).
4. **Pin the minimal scopes**: Project Settings (⚙️) → tick *"Show
   'appsscript.json' manifest file in editor"*. Open `appsscript.json` and
   replace its contents with [`scripts/appsscript.json`](../../scripts/appsscript.json)
   from this repo. This restricts the script to **read-only** Gmail access
   (`gmail.readonly`) instead of full mail access.
5. **Project Settings (⚙️) → Script Properties** → add:
   - `GITHUB_PAT` = the PAT from step 6.1
   - `GITHUB_REPO` = `your-username/your-repo`
6. In the editor, select **`installTrigger`** → **Run**. Approve permissions
   when prompted — the consent screen should now ask only to *"Read your email
   messages and settings"*. This creates a trigger that runs every 5 minutes
   (`everyMinutes()` accepts 1, 5, 10, 15, or 30).
7. Test: select **`checkFormEmails`** → **Run**, then check **View → Logs**.
   With an unread FORM email present it logs "dispatching workflow" and a run
   appears in your Actions tab.

That's it. Request an export in the FORM app and within ~5 minutes the swim
should appear on Garmin Connect.

---

## Operations & troubleshooting

**Where do runs show up?** Your repo's **Actions** tab → "FORM → Garmin Sync".
GitHub emails you on failure (configurable under your GitHub notification
settings).

**An activity already exists (HTTP 409).** Treated as success — Garmin
deduplicates uploads, so re-runs won't create duplicates.

**`ExpiredToken` / HTTP 400 on download.** FORM's S3 links are signed with
temporary AWS credentials that expire in roughly an hour — much sooner than the
48h the URL claims. If a link is too old, re-export from the FORM app. Frequent
polling (every 5 min via Apps Script) normally fetches links well within the
window.

**Garmin login fails / MFA required.** The stored token has expired or been
rejected and a fresh login hit MFA, which can't be completed headlessly. Re-run
`scripts/seed_garmin_tokens.py` locally and update the `FFF_GARMIN_TOKENS`
secret.

**Nothing happens when an email arrives.** Check the Apps Script **Executions**
log. Common causes: the PAT lacks Contents:write, `GITHUB_REPO` is wrong, or the
email subject/sender doesn't match the constants in `gmail.py` /
`form_gmail_trigger.gs`.

**Secrets safety.** On a public repo, Actions logs are world-readable. This
module never logs presigned URLs, security tokens, or credentials — only
redacted host/paths and parsed error codes. Don't add logging that prints secret
values.
