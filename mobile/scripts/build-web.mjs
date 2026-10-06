// Собирает www/ для Capacitor: интерфейс из server/app/webapp (тот же, что открывается в браузере),
// config.js с адресом сервера, native.js с плагинами и фоновую проверку уведомлений.
//
//   API_URL=https://schedule.example.ru APP_NAME="Расписание МПГУ" npm run build
//
// Без API_URL приложение при первом запуске спросит адрес сервера.

import { build } from "esbuild";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const webapp = path.resolve(root, "../server/app/webapp");
const out = path.join(root, "www");

const config = {
  server: (process.env.API_URL || "").trim().replace(/\/+$/, ""),
  appName: (process.env.APP_NAME || "").trim() || "Расписание МПГУ",
};

await fs.rm(out, { recursive: true, force: true });
await fs.cp(webapp, out, { recursive: true });
// Service worker нужен только сайту: в приложении файлы и так лежат на телефоне
await fs.rm(path.join(out, "sw.js"), { force: true });
await fs.rm(path.join(out, "manifest.webmanifest"), { force: true });

await fs.writeFile(path.join(out, "config.js"), `window.APP_CONFIG = ${JSON.stringify(config)};\n`);

await build({
  entryPoints: [path.join(root, "src/native.js")],
  bundle: true,
  format: "iife",
  minify: true,
  target: ["es2020", "safari15", "chrome90"],
  outfile: path.join(out, "native.js"),
  logLevel: "warning",
});

await fs.mkdir(path.join(out, "runners"), { recursive: true });
await fs.copyFile(path.join(root, "runners/background.js"), path.join(out, "runners/background.js"));

const indexPath = path.join(out, "index.html");
let html = await fs.readFile(indexPath, "utf8");
if (!html.includes('<script src="app.js"></script>')) throw new Error("index.html: не нашёл <script src=\"app.js\">");
html = html
  .replace('<script src="app.js"></script>', '<script src="config.js"></script>\n  <script src="native.js"></script>\n  <script src="app.js"></script>')
  .replace(/\s*<link rel="manifest"[^>]*>/, "");
await fs.writeFile(indexPath, html);

console.log(`www/ готов. Сервер: ${config.server || "спросит при первом запуске"}`);
