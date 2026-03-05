# Slack App Setup Guide

This guide walks you through creating the **ACB Pipeline Bot** Slack app and
wiring it into the ACB pipeline so it can post notifications to the
`#forge_reports` channel.

---

## Prerequisites

- You have admin or owner access to the target Slack workspace.
- You have collaborator/admin access to the GitHub repository.

---

## Step 1 — Delete Old Slack App (if exists)

If a previous version of the bot already exists:

1. Go to <https://api.slack.com/apps>.
2. Find **ACB Pipeline Bot** and click it.
3. Scroll to the bottom → **Delete App** → confirm.

---

## Step 2 — Generate a Slack App Configuration Token

1. Go to <https://api.slack.com/apps>.
2. Scroll down to **Your App Configuration Tokens**.
3. Click **Generate Token**.
4. Select the target workspace.
5. Copy the token (starts with `xoxe-1-…`). Keep it handy for Step 3.

> **Security note:** This token is only passed as a workflow input and is
> never stored as a repository secret. It appears only in the workflow run
> log, visible only to the person who triggered it.

---

## Step 3 — Run the Setup Workflow

1. Open the **Actions** tab of this repository.
2. In the left sidebar, click **Setup Slack App**.
3. Click **Run workflow** (top-right of the run list).
4. Paste your configuration token (from Step 2) into the **config_token** field.
5. Click **Run workflow**.
6. Wait ~30 seconds for the workflow to complete.

---

## Step 4 — Copy the Bot Token from the Workflow Summary

1. Click the completed workflow run.
2. Click the **create_app** job.
3. Open the **Step Summary** section at the bottom.
4. Copy the **Bot Token** (starts with `xoxb-…`).

---

## Step 5 — Update the GitHub Secret

1. Go to **Settings → Secrets and variables → Actions** in this repository.
2. Find **SLACK_BOT_TOKEN** and click **Edit** (or **New repository secret** if
   it doesn't exist yet).
3. Paste the bot token copied in Step 4.
4. Click **Save**.
5. While here, confirm that `SLACK_ACK_CHANNEL_ID` is set to `C0A3CGW9ECS`
   (the `#forge_reports` channel ID). Create it if missing.

---

## Step 6 — Invite the Bot to the Channel

1. Open **#forge_reports** in Slack.
2. In the message box, type:

   ```
   /invite @ACB Pipeline Bot
   ```

3. Press **Enter** and confirm.

---

## Step 7 — Test the Integration

Retrigger the ACB pipeline in one of the following ways:

- Push a change to the `specs/` directory.
- Go to **Actions → ACB Pipeline** → **Run workflow**.

You should see a notification appear in `#forge_reports` within a few seconds
of the workflow starting.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Workflow fails with "Invalid configuration token" | Token expired or copied incorrectly | Regenerate token (Step 2) and re-run |
| Workflow fails with "Manifest not found" | `slack_app_manifest.json` missing | Ensure the file exists at the repo root |
| No message in `#forge_reports` | Bot not invited | Repeat Step 6 |
| `SLACK_BOT_TOKEN` env error in pipeline | Secret not updated | Repeat Step 5 |
