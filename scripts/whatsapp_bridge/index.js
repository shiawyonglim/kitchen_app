/**
 * Option 1: Local WhatsApp Web Bridge (Baileys)
 * Listens silently (never sends replies) and forwards messages to kitchen_app.
 */
const fs = require("fs");
const path = require("path");
const pino = require("pino");
const qrcode = require("qrcode-terminal");
const QRCode = require("qrcode");
const {
  default: makeWASocket,
  DisconnectReason,
  downloadMediaMessage,
  useMultiFileAuthState,
} = require("@whiskeysockets/baileys");

const STATUS_FILE = path.join(__dirname, "bridge_status.json");

function writeStatus(patch) {
  try {
    const payload = {
      status: "starting",
      qr_data_url: "",
      user: "",
      updated_at: new Date().toISOString(),
      ...patch,
    };
    fs.writeFileSync(STATUS_FILE, JSON.stringify(payload, null, 2), "utf8");
  } catch (err) {
    console.error("[Bridge] Failed to write status file:", err.message);
  }
}

// Load root .env if present
const envPath = path.resolve(__dirname, "../../.env");
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) {
      process.env[m[1]] = m[2].replace(/^['"]|['"]$/g, "");
    }
  }
}

const API_URL = process.env.KITCHEN_APP_URL || "http://127.0.0.1:8000/api/assistant/whatsapp-ingest";
const BRIDGE_TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const LISTEN_JID = process.env.WHATSAPP_LISTEN_JID || ""; // Optional: restrict to a specific chat/group JID

if (!BRIDGE_TOKEN) {
  console.error("ERROR: WHATSAPP_BRIDGE_TOKEN is not set in .env or environment.");
  process.exit(1);
}

function extractText(msg) {
  const m = msg.message || {};
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.documentMessage?.caption ||
    ""
  ).trim();
}

async function forwardToApp(rawMessage, sender, imageBase64 = "", imageMime = "image/jpeg") {
  const res = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Bridge-Token": BRIDGE_TOKEN,
    },
    body: JSON.stringify({
      raw_message: rawMessage,
      source: "whatsapp",
      sender: sender || "WhatsApp",
      image_base64: imageBase64,
      image_mime: imageMime,
    }),
  });
  if (!res.ok) {
    const errText = await res.text();
    console.error(`[Bridge] API error ${res.status}: ${errText}`);
    return;
  }
  const savedList = await res.json();
  const items = Array.isArray(savedList) ? savedList : [savedList];
  for (const saved of items) {
    console.log(`[Bridge] Planned [${saved.category}] -> ${saved.title}${saved.scheduled_for ? ` (${saved.scheduled_for})` : ""}`);
  }
}

const pendingBuffers = new Map();
const BATCH_WINDOW_MS = 5000;

function queueMessageForBatch(chatKey, text, sender, imageBase64 = "", imageMime = "image/jpeg") {
  const existing = pendingBuffers.get(chatKey);
  if (existing) {
    clearTimeout(existing.timer);
    if (text) existing.texts.push(text);
    if (imageBase64) {
      existing.imageBase64 = imageBase64;
      existing.imageMime = imageMime;
    }
  } else {
    pendingBuffers.set(chatKey, {
      texts: text ? [text] : [],
      sender,
      imageBase64,
      imageMime,
      timer: null,
    });
  }
  const entry = pendingBuffers.get(chatKey);
  entry.timer = setTimeout(async () => {
    pendingBuffers.delete(chatKey);
    const combined = entry.texts.join("\n---\n");
    try {
      await forwardToApp(combined, entry.sender, entry.imageBase64, entry.imageMime);
    } catch (err) {
      console.error("[Bridge] Failed to reach kitchen_app:", err.message);
    }
  }, BATCH_WINDOW_MS);
}

let reconnectAttempts = 0;
let reconnectTimer = null;

process.on("unhandledRejection", (reason) => {
  console.error("[Bridge] Unhandled rejection (recovering):", reason);
});
process.on("uncaughtException", (err) => {
  console.error("[Bridge] Uncaught exception (recovering):", err.message);
});

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delayMs = Math.min(30000, 3000 * Math.pow(2, Math.min(reconnectAttempts, 3)));
  reconnectAttempts += 1;
  console.log(`[Bridge] Reconnecting in ${delayMs / 1000}s...`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startBridge().catch((err) => {
      console.error("[Bridge] Reconnect failed:", err.message);
      scheduleReconnect();
    });
  }, delayMs);
}

async function startBridge() {
  const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, "auth_info"));
  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: "silent" }),
    printQRInTerminal: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      reconnectAttempts = 0;
      console.log("\nScan this QR code in Settings -> WhatsApp Listener (or terminal):\n");
      qrcode.generate(qr, { small: true });
      try {
        const qrDataUrl = await QRCode.toDataURL(qr, { width: 240, margin: 1 });
        writeStatus({ status: "qr_ready", qr_data_url: qrDataUrl });
      } catch (err) {
        console.error("[Bridge] QR data URL error:", err.message);
      }
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      writeStatus({ status: shouldReconnect ? "reconnecting" : "logged_out" });
      console.log("[Bridge] Connection closed. Reconnecting:", shouldReconnect);
      if (shouldReconnect) scheduleReconnect();
    } else if (connection === "open") {
      reconnectAttempts = 0;
      const myJid = sock.user?.id || "";
      const myLid = sock.user?.lid || "";
      writeStatus({ status: "connected", qr_data_url: "", user: myJid || myLid });
      console.log(`[Bridge] Connected silently (id=${myJid}, lid=${myLid || "none"}). Forward messages to 'Message Yourself' to plan.`);
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    const myPhone = (sock.user?.id || "").split(":")[0].split("@")[0];
    const myLid = (sock.user?.lid || "").split(":")[0].split("@")[0];

    for (const msg of messages) {
      const remoteJid = msg.key.remoteJid || "";
      if (!remoteJid || remoteJid === "status@broadcast") continue;

      // Match "Message Yourself" by either phone JID (@s.whatsapp.net) or WhatsApp's newer @lid ID
      const remoteBase = remoteJid.split(":")[0].split("@")[0];
      const isSelfChat =
        (myPhone && remoteBase === myPhone) ||
        (myLid && remoteBase === myLid);

      if (LISTEN_JID ? remoteJid !== LISTEN_JID : !isSelfChat) {
        if (msg.key.fromMe) {
          console.log(`[Bridge] Skipped fromMe chat remoteJid=${remoteJid} (expected phone=${myPhone} or lid=${myLid || "none"})`);
        }
        continue;
      }

      const text = extractText(msg);
      let imageBase64 = "";
      let imageMime = "image/jpeg";
      if (msg.message?.imageMessage) {
        try {
          const buf = await downloadMediaMessage(msg, "buffer", {});
          imageBase64 = buf.toString("base64");
          imageMime = msg.message.imageMessage.mimetype || "image/jpeg";
        } catch (err) {
          console.error("[Bridge] Failed to download image:", err.message);
        }
      }
      if (!text && !imageBase64) continue;

      const sender = msg.pushName || (msg.key.fromMe ? "Me" : remoteJid);
      queueMessageForBatch(remoteJid, text, sender, imageBase64, imageMime);
    }
  });
}

startBridge().catch((err) => {
  console.error("[Bridge] Initial start error:", err.message);
  scheduleReconnect();
});
