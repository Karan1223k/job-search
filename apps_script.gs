// Paste into the Google Sheet: Extensions -> Apps Script, then Deploy -> Manage deployments ->
// edit (pencil) -> Version: New version -> Deploy. Receives company names from main.py and
// appends each company to column A only if it is not already listed.
const TOKEN = 'PASTE_SHEET_TOKEN_HERE';  // same value as SHEET_TOKEN in .env / GitHub secrets
const HEADER = 'Company Name';

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (body.token !== TOKEN) {
    return reply({ error: 'unauthorized' });
  }
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  if (body.clearAll) {
    // Manual reset: wipe the sheet before a fresh run.
    sheet.clearContents();
  } else if (body.clearColumnB) {
    // One-time cleanup of the job links column written by an older version.
    sheet.getRange('B:B').clearContent();
  }
  if (sheet.getLastRow() === 0) sheet.appendRow([HEADER]);
  sheet.getRange(1, 1).setValue(HEADER);

  const existing = new Set(
    sheet.getRange(1, 1, sheet.getLastRow(), 1).getValues()
      .map(r => String(r[0]).trim().toLowerCase())
  );
  const fresh = [];
  for (const name of body.companies || []) {
    const key = String(name).trim().toLowerCase();
    if (key && !existing.has(key)) {
      existing.add(key);
      fresh.push([name]);
    }
  }
  if (fresh.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, fresh.length, 1).setValues(fresh);
  }
  return reply({ added: fresh.length });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// --- Daily timer: GitHub's own scheduler is unreliable, so Google starts the run instead. ---
// One-time setup: Project Settings -> Script properties -> add GITHUB_TOKEN (a fine-grained token
// with Actions: Read and write on the job-search repo), then run setupDailyTrigger once.
const REPO = 'Karan1223k/job-search';

function setupDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'triggerDailyRun')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('triggerDailyRun').timeBased()
    .everyDays(1).atHour(9).nearMinute(0).inTimezone('Asia/Kolkata').create();
}

function triggerDailyRun() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  const res = UrlFetchApp.fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/daily.yml/dispatches`, {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main', inputs: { force: false } }),
      muteHttpExceptions: true,
    });
  if (res.getResponseCode() !== 204) {
    throw new Error(`GitHub dispatch failed: ${res.getResponseCode()} ${res.getContentText()}`);
  }
}
