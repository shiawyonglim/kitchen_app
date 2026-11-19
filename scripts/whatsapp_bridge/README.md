# Local WhatsApp Web Bridge (Option 1 — Testing)

Listens silently to your **"Message Yourself"** chat via WhatsApp Linked Devices (QR code) and forwards messages to `kitchen_app` without sending any WhatsApp replies.

## Setup (3 Steps)
1. Add a shared secret (and optional free Groq API key) to your root `.env`:
   ```env
   WHATSAPP_BRIDGE_TOKEN=pick-any-long-random-secret
   GROQ_API_KEY=your-free-groq-key
   ```
2. Install Node dependencies:
   ```powershell
   cd scripts\whatsapp_bridge
   npm install
   ```
3. Start the bridge (while `kitchen_app` is running on port 8000):
   ```powershell
   npm start
   ```
   Scan the QR code in WhatsApp → **Linked Devices**. Forward any text/message to **Message Yourself** and it will appear in the **Assistant** tab of `kitchen_app`.
