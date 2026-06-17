/**
 * FORM → Garmin Sync: Gmail watcher (Google Apps Script)
 * =======================================================
 *
 * Watches your Gmail for FORM export emails and triggers the GitHub Actions
 * workflow the moment one arrives, via the repository_dispatch API.
 *
 * Why this exists:
 *   Scheduled (cron) GitHub workflows do NOT run on forked repositories, so we
 *   can't poll from GitHub itself. Instead this script polls Gmail (free, and
 *   can run as often as every minute) and fires the workflow only when there is
 *   actually a new export to process. GitHub Actions minutes are therefore only
 *   spent when there's real work to do.
 *
 * How it works:
 *   - Searches for UNREAD emails from FORM with the export subject.
 *   - If any exist, sends ONE repository_dispatch to GitHub. The pipeline
 *     processes every matching unread email in a single run and marks them read
 *     on success. A failed email stays unread, so the next time this script runs
 *     it dispatches again — giving automatic retries.
 *
 * SETUP (one time):
 *   1. Go to https://script.google.com and create a New Project.
 *   2. Paste this entire file in, replacing the default Code.gs contents.
 *   3. Project Settings (gear icon) → Script Properties → add two properties:
 *        GITHUB_PAT   = a GitHub fine-grained PAT for supplecoder/Fit-File-Faker
 *                       with "Contents: Read and write" permission
 *        GITHUB_REPO  = supplecoder/Fit-File-Faker
 *   4. Run the `installTrigger` function once (select it in the toolbar → Run).
 *      Approve the Gmail + external-request permissions when prompted.
 *      This creates a time-based trigger that runs `checkFormEmails` every
 *      5 minutes. (Change the interval inside installTrigger if you like;
 *      Apps Script supports down to every 1 minute.)
 *   5. Done. To test immediately, run `checkFormEmails` manually.
 *
 * Security note: the PAT lives only in this script's Script Properties (in your
 * Google account), never in the public GitHub repo.
 */

// Must match the sender/subject the pipeline searches for (see gmail.py).
var FORM_SENDER = 'community@formswim.com';
var FORM_SUBJECT = 'Your FORM Data is ready!';
var DISPATCH_EVENT_TYPE = 'form-export-ready';

/**
 * Main entry point — run on a time-based trigger.
 * Checks for unread FORM emails and dispatches the workflow if any are found.
 */
function checkFormEmails() {
  var query = 'is:unread from:(' + FORM_SENDER + ') subject:("' + FORM_SUBJECT + '")';
  var threads = GmailApp.search(query, 0, 10);

  if (threads.length === 0) {
    Logger.log('No unread FORM export emails — nothing to dispatch.');
    return;
  }

  Logger.log('Found ' + threads.length + ' unread FORM email(s) — dispatching workflow.');
  dispatchWorkflow();
}

/**
 * Sends a repository_dispatch event to GitHub to trigger the sync workflow.
 */
function dispatchWorkflow() {
  var props = PropertiesService.getScriptProperties();
  var pat = props.getProperty('GITHUB_PAT');
  var repo = props.getProperty('GITHUB_REPO');

  if (!pat || !repo) {
    throw new Error(
      'Missing Script Properties. Set GITHUB_PAT and GITHUB_REPO under ' +
      'Project Settings → Script Properties.'
    );
  }

  var url = 'https://api.github.com/repos/' + repo + '/dispatches';
  var payload = JSON.stringify({ event_type: DISPATCH_EVENT_TYPE });

  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'Authorization': 'token ' + pat,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: payload,
    muteHttpExceptions: true
  });

  var code = response.getResponseCode();
  if (code === 204) {
    Logger.log('Dispatched successfully (HTTP 204).');
  } else {
    throw new Error(
      'GitHub dispatch failed: HTTP ' + code + ' — ' + response.getContentText()
    );
  }
}

/**
 * One-time setup: install a time-based trigger to run checkFormEmails().
 * Run this manually once from the Apps Script editor.
 */
function installTrigger() {
  // Remove any existing triggers for this function to avoid duplicates.
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'checkFormEmails') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  ScriptApp.newTrigger('checkFormEmails')
    .timeBased()
    .everyMinutes(5) // change to 1, 5, 10, 15, or 30
    .create();

  Logger.log('Trigger installed: checkFormEmails will run every 5 minutes.');
}
