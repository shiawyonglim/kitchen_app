// Personal ERP — iPhone Scriptable Widget
// 1. Install "Scriptable" from the iOS App Store.
// 2. Create a new script, paste this file, and set BASE_URL + WIDGET_TOKEN below.
// 3. Add a Scriptable widget to your Home Screen or Lock Screen and select this script.

const BASE_URL = "https://YOUR-PC.YOUR-TAILNET.ts.net"; // e.g. Tailscale Serve HTTPS URL
const WIDGET_TOKEN = "PASTE_YOUR_WIDGET_API_TOKEN_HERE";  // matches WIDGET_API_TOKEN in .env

function fmtRM(val) {
  return `RM ${Number(val || 0).toFixed(2)}`;
}

async function fetchSummary() {
  const req = new Request(`${BASE_URL.replace(/\/$/, "")}/api/wealth/widget-summary`);
  req.headers = { Authorization: `Bearer ${WIDGET_TOKEN}` };
  req.timeoutInterval = 10;
  return await req.loadJSON();
}

function buildErrorWidget(msg) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#111216");
  w.setPadding(14, 14, 14, 14);
  const title = w.addText("Personal ERP");
  title.font = Font.boldSystemFont(13);
  title.textColor = new Color("#F7F8F8");
  w.addSpacer(4);
  const err = w.addText(msg);
  err.font = Font.systemFont(11);
  err.textColor = new Color("#F87171");
  return w;
}

function buildLockScreenWidget(data) {
  const w = new ListWidget();
  const top = w.addText(`Today -${fmtRM(data.today_expense_myr)}`);
  top.font = Font.boldSystemFont(12);
  const mid = w.addText(`Left ${fmtRM(data.budget_remaining_myr)}`);
  mid.font = Font.systemFont(11);
  const bot = w.addText(`Month ${data.budget_used_pct}% used`);
  bot.font = Font.systemFont(10);
  bot.textOpacity = 0.75;
  return w;
}

function buildHomeWidget(data, isMedium) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#111216");
  w.setPadding(14, 16, 14, 16);
  w.url = BASE_URL;

  const header = w.addStack();
  header.layoutHorizontally();
  header.centerAlignContent();
  const title = header.addText(`Spend · ${data.month}`);
  title.font = Font.semiboldSystemFont(11);
  title.textColor = new Color("#9CA3AF");
  header.addSpacer();
  const pct = header.addText(`${data.budget_used_pct}%`);
  pct.font = Font.boldSystemFont(11);
  pct.textColor = data.budget_used_pct > 90 ? new Color("#F87171") : new Color("#818CF8");

  w.addSpacer(6);

  const todayLbl = w.addText(`Today -${fmtRM(data.today_expense_myr)}`);
  todayLbl.font = Font.boldSystemFont(isMedium ? 18 : 16);
  todayLbl.textColor = new Color("#F7F8F8");

  w.addSpacer(2);

  const leftColor = data.budget_remaining_myr >= 0 ? "#34D399" : "#F87171";
  const leftLbl = w.addText(
    `${data.budget_remaining_myr >= 0 ? "Left" : "Over"} ${fmtRM(Math.abs(data.budget_remaining_myr))} / ${fmtRM(data.monthly_budget_myr)}`
  );
  leftLbl.font = Font.mediumSystemFont(11);
  leftLbl.textColor = new Color(leftColor);

  if (isMedium && Array.isArray(data.recent) && data.recent.length > 0) {
    w.addSpacer(8);
    for (const tx of data.recent.slice(0, 2)) {
      const row = w.addStack();
      row.layoutHorizontally();
      const merch = row.addText(tx.merchant);
      merch.font = Font.systemFont(11);
      merch.textColor = new Color("#D1D5DB");
      merch.lineLimit = 1;
      row.addSpacer();
      const sign = tx.tx_type === "expense" ? "-" : "+";
      const amt = row.addText(`${sign}${fmtRM(tx.amount_myr)}`);
      amt.font = Font.semiboldMonospacedSystemFont(11);
      amt.textColor = tx.tx_type === "expense" ? new Color("#F87171") : new Color("#34D399");
    }
  } else {
    w.addSpacer(6);
    const monthTxt = w.addText(`Month -${fmtRM(data.month_expense_myr)}`);
    monthTxt.font = Font.systemFont(11);
    monthTxt.textColor = new Color("#A6AEBB");
  }

  return w;
}

let widget;
try {
  if (BASE_URL.includes("YOUR-PC") || WIDGET_TOKEN.includes("PASTE_YOUR")) {
    widget = buildErrorWidget("Set BASE_URL and WIDGET_TOKEN at the top of this script.");
  } else {
    const data = await fetchSummary();
    if (config.widgetFamily === "accessoryRectangular" || config.widgetFamily === "accessoryInline") {
      widget = buildLockScreenWidget(data);
    } else {
      widget = buildHomeWidget(data, config.widgetFamily === "medium");
    }
  }
} catch (err) {
  widget = buildErrorWidget(`Offline: ${err.message || err}`);
}

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
