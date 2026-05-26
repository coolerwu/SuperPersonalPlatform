import readline from "node:readline";
import QRCode from "qrcode";
import { WechatyBuilder } from "wechaty";

const profile = process.env.SPP_WECHAT_PROFILE || "super-personal-platform";
const puppet = process.env.SPP_WECHAT_PUPPET || "";
const token = process.env.SPP_WECHAT_PUPPET_SERVICE_TOKEN || "";

const botOptions = { name: profile };
if (puppet) {
  botOptions.puppet = puppet;
}
if (token) {
  botOptions.puppetOptions = { token };
}

const bot = WechatyBuilder.build(botOptions);
const recentMessages = new Map();

function emit(event) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), ...event })}\n`);
}

function remember(message) {
  recentMessages.set(message.id, message);
  if (recentMessages.size > 200) {
    const firstKey = recentMessages.keys().next().value;
    recentMessages.delete(firstKey);
  }
}

bot
  .on("scan", async (qrcode, status) => {
    let qrcodeDataUrl = "";
    try {
      qrcodeDataUrl = await QRCode.toDataURL(qrcode, { margin: 1, width: 280 });
    } catch (error) {
      emit({ type: "error", error: error.message || String(error) });
    }
    emit({
      type: "scan",
      status,
      qrcode,
      qrcode_data_url: qrcodeDataUrl,
      qrcode_url: `https://wechaty.js.org/qrcode/${encodeURIComponent(qrcode)}`
    });
  })
  .on("login", (user) => {
    emit({ type: "login", user: user.name() });
  })
  .on("logout", (user) => {
    emit({ type: "logout", user: user.name() });
  })
  .on("error", (error) => {
    emit({ type: "error", error: error.message || String(error) });
  })
  .on("message", async (message) => {
    try {
      if (message.self()) return;
      const text = message.text();
      if (!text || !text.trim()) return;
      const talker = message.talker();
      const room = message.room();
      remember(message);
      emit({
        type: "message",
        message_id: message.id,
        text,
        talker_name: talker?.name?.() || "",
        room_topic: room ? await room.topic() : ""
      });
    } catch (error) {
      emit({ type: "error", error: error.message || String(error) });
    }
  });

readline
  .createInterface({ input: process.stdin })
  .on("line", async (line) => {
    try {
      const command = JSON.parse(line);
      if (command.type !== "reply") return;
      const message = recentMessages.get(command.message_id);
      if (!message) {
        emit({ type: "error", error: `message not found: ${command.message_id}` });
        return;
      }
      await message.say(String(command.text || ""));
      emit({ type: "reply_sent", message_id: command.message_id });
    } catch (error) {
      emit({ type: "error", error: error.message || String(error) });
    }
  });

bot
  .start()
  .then(() => emit({ type: "started", profile }))
  .catch((error) => {
    emit({ type: "error", error: error.message || String(error) });
    process.exitCode = 1;
  });
