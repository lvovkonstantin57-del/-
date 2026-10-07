"use strict";

// Приложение на телефоне (Capacitor): нативные плагины собирает mobile/scripts/build-web.mjs в native.js,
// адрес сервера — из настроек сборки или введённый на экране входа.
// В браузере — тот же сервер, что отдал страницу.
const NATIVE = !!window.Capacitor?.isNativePlatform?.();
const PLATFORM = window.Capacitor?.getPlatform?.() || "web";
const Plugins = window.NativePlugins || {};
const CONFIG = window.APP_CONFIG || {};
const TOKEN_KEY = "mpgu_token";
const SERVER_KEY = "mpgu_server";

// Настройки на устройстве: в приложении — Preferences (их не сотрёт система), в браузере — localStorage
const store = {
  async get(key) {
    try {
      if (Plugins.Preferences) return (await Plugins.Preferences.get({ key })).value;
      return localStorage.getItem(key);
    } catch (_) { return null; }
  },
  async set(key, value) {
    try {
      if (Plugins.Preferences) {
        if (value === null || value === undefined) await Plugins.Preferences.remove({ key });
        else await Plugins.Preferences.set({ key, value: String(value) });
      } else if (value === null || value === undefined) localStorage.removeItem(key);
      else localStorage.setItem(key, String(value));
    } catch (_) { /* приватный режим */ }
  },
};

let TOKEN = null;
// "" — тот же адрес, что у страницы (браузер); в приложении — https://сервер
let SERVER = "";


async function setToken(token) {
  TOKEN = token || null;
  await store.set(TOKEN_KEY, TOKEN);
  bgConfigure();
}

// «schedule.example.ru» → «https://schedule.example.ru»
function normalizeServer(text) {
  let v = (text || "").trim().replace(/\/+$/, "");
  if (!v) return "";
  if (!/^https?:\/\//i.test(v)) v = "https://" + v;
  return v;
}

const serverLabel = () => (SERVER || location.origin).replace(/^https?:\/\//, "");
const WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
  "сентября", "октября", "ноября", "декабря"];
const PARITY = { odd: "нечётная", even: "чётная", every: "каждая" };

// Свои номера недель, как их хранит сервер: «1-4,6», «2/3» (со 2-й каждую 3-ю), «1-16/2»
function weeksMatch(spec, n) {
  return (spec || "").split(",").some((item) => {
    const m = /^(\d+)(?:-(\d+))?(?:\/(\d+))?$/.exec(item.trim());
    if (!m) return false;
    const first = Number(m[1]), step = Number(m[3] || 1);
    const last = m[2] ? Number(m[2]) : m[3] ? Infinity : first;
    return n >= first && n <= last && (n - first) % step === 0;
  });
}
const lessonOnWeek = (l, n) => (l.week === "custom" ? weeksMatch(l.weeks, n)
  : l.week === "every" || l.week === (n % 2 ? "odd" : "even"));
const KINDS = ["лекция", "практика", "лабораторная", "семинар", "экзамен", "зачёт"];
const WINDOW_MIN = 60; // перерыв от часа — уже «окно»
const ROLE_LABELS = { owner: "главный админ", admin: "админ", starosta: "староста", user: "студент" };
const isAdminRole = (role) => role === "admin" || role === "owner";

// Тонкие линейные иконки (в стиле Lucide)
const ICONS = {
  book: '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>',
  flask: '<path d="M10 2v7.5L4.7 20.5A1 1 0 0 0 5.6 22h12.8a1 1 0 0 0 .9-1.5L14 9.5V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>',
  pen: '<path d="M12 20h9"/><path d="M16.4 3.6a2.1 2.1 0 0 1 3 3L7.4 18.6a2 2 0 0 1-.9.5l-2.9.9.9-2.9a2 2 0 0 1 .5-.9z"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  check: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>',
  edit: '<path d="M16.4 3.6a2.1 2.1 0 0 1 3 3L7.4 18.6 3 20l1.4-4.4z"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>',
  upload: '<path d="M12 15V3"/><path d="m7 8 5-5 5 5"/><path d="M20 15v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  shield: '<path d="M12 21.5s7.5-3.6 7.5-9.6V5.3L12 2.5 4.5 5.3v6.6c0 6 7.5 9.6 7.5 9.6z"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
  alarm: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6M22 6l-3-3"/>',
  bell: '<path d="M6 8.5a6 6 0 0 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15 6 8.5"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-5.1A8 8 0 1 1 21 12z"/>',
  phone: '<rect x="6" y="2.5" width="12" height="19" rx="3"/><path d="M11 18h2"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5"/><path d="M5 12h11"/>',
  userPlus: '<circle cx="10" cy="8" r="4"/><path d="M3 21a7 7 0 0 1 12.5-4.3"/><path d="M19 15v6M16 18h6"/>',
  calPlus: '<rect x="3" y="4.5" width="18" height="17" rx="4"/><path d="M8 2.5v4M16 2.5v4M3 10h18"/><path d="M12 13.5v5M9.5 16h5"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  hash: '<path d="M5 9h15M4 15h15M10 3 8 21M16 3l-2 18"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5.5"/>',
  tick: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v4.5h-4.5"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  camera: '<path d="M4 8.5A2.5 2.5 0 0 1 6.5 6H8l1.5-2.5h5L16 6h1.5A2.5 2.5 0 0 1 20 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z"/><circle cx="12" cy="12.5" r="3.5"/>',
  calEdit: '<path d="M11 21.5H7a4 4 0 0 1-4-4v-9a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v3"/><path d="M8 2.5v4M16 2.5v4M3 10h18"/><path d="M19.4 14.6a1.6 1.6 0 0 1 2.3 2.3L17 21.5l-3 .7.7-3z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
};

const state = {
  me: null,
  date: null,        // любая дата внутри показываемой недели (YYYY-MM-DD)
  selectedDay: 0,    // 0..6
  week: null,
  group: null,       // выбранная группа у админа; MINE — пары преподавателя
  groups: [],
  config: null,
  att: [],           // пары с отметкой сегодня у группы студента
  teacher: null,     // «Я учитель»: подгруппы и журнал
  teacherNext: null, // что открыть на «Я учитель» после загрузки
  unread: 0,         // непрочитанные уведомления — кружок на вкладке
  lastNoteId: 0,     // самое новое уведомление, которое уже видели
  notesView: "inbox",
};

// --- утилиты ---------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// replaceChildren, который пропускает null/false (иначе на экране появляется «null»)
function setChildren(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

function icon(name) {
  const span = document.createElement("span");
  span.innerHTML = `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
  return span.firstChild;
}

function kindIcon(kind) {
  const k = (kind || "").toLowerCase();
  if (k.startsWith("лек")) return "book";
  if (k.startsWith("лаб")) return "flask";
  if (k.startsWith("прак") || k.startsWith("сем")) return "pen";
  if (k.startsWith("экз") || k.startsWith("зач")) return "check";
  return null;
}

const $ = (id) => document.getElementById(id);

function toast(text, ms = 2500) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function request(path, { method = "GET", body, form } = {}) {
  const headers = {};
  if (TOKEN) headers.Authorization = "Bearer " + TOKEN;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(SERVER + path, { method, headers, body: payload });
  } catch (_) {
    const err = new Error(navigator.onLine === false ? "Нет сети" : `Сервер ${serverLabel()} не отвечает`);
    err.status = 0;
    throw err;
  }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch (_) { /* пустой ответ */ }
    let msg = data?.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("\n");
    const err = new Error(msg || `Ошибка ${res.status}`);
    err.status = res.status;
    if (res.status === 401 && TOKEN && !path.startsWith("/api/auth/")) { await setToken(null); showLogin(); }
    throw err;
  }
  return res;
}

async function api(path, options) {
  const res = await request(path, options);
  try { return await res.json(); } catch (_) { return null; }
}

// Файл с сервера (журнал в Excel, бэкап): имя — из Content-Disposition
async function apiFile(path) {
  const res = await request(path);
  const cd = res.headers.get("Content-Disposition") || "";
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  const name = star ? decodeURIComponent(star[1]) : plain ? plain[1] : "file";
  return { blob: await res.blob(), name };
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });
}

// В приложении — сохраняем во временную папку и открываем «Поделиться» (сохранить, отправить);
// в браузере — обычное скачивание
async function saveFile({ blob, name }) {
  if (NATIVE && Plugins.Filesystem && Plugins.Share) {
    const written = await Plugins.Filesystem.writeFile({
      path: name, data: await blobToBase64(blob), directory: "CACHE", recursive: true,
    });
    await Plugins.Share.share({ title: name, url: written.uri, dialogTitle: name });
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// Поделиться текстом: в приложении — системное меню, в браузере — Web Share или копирование
async function shareText(text, title) {
  try {
    if (Plugins.Share) { await Plugins.Share.share({ title, text, dialogTitle: title }); return; }
    if (navigator.share) { await navigator.share({ title, text }); return; }
  } catch (e) {
    if (/cancel|abort/i.test(String(e?.message || e?.name || e))) return;
  }
  copyText(text);
}

async function copyText(text, done = "Скопировано") {
  try {
    if (Plugins.Clipboard) await Plugins.Clipboard.write({ string: text });
    else await navigator.clipboard.writeText(text);
  } catch (_) {
    // Запасной путь для WebView без доступа к буферу обмена
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.append(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (_) { /* не вышло — хотя бы покажем текст */ }
    ta.remove();
  }
  haptic("light");
  toast(done);
}

function openExternal(url) {
  if (Plugins.Browser && /^https?:/.test(url)) Plugins.Browser.open({ url }).catch(() => window.open(url, "_blank"));
  else window.open(url, "_blank");
}

// @ник или ссылка t.me — откроем чат; сайт — откроем; телефон и почту — скопируем
function contactLink(contact) {
  const v = (contact || "").trim();
  const nick = /^@([A-Za-z][A-Za-z0-9_]{3,31})$/.exec(v);
  if (nick) return "https://t.me/" + nick[1];
  if (/^https?:\/\/\S+$/.test(v)) return v;
  return null;
}

// Лёгкая отдача на телефоне; в браузере её нет
function haptic(kind = "select") {
  const h = Plugins.Haptics;
  if (!h) return;
  const style = { select: "LIGHT", light: "LIGHT", medium: "MEDIUM", heavy: "HEAVY" }[kind] || "LIGHT";
  h.impact({ style }).catch(() => {});
}

function hapticResult(kind) {
  Plugins.Haptics?.notification({ type: kind.toUpperCase() }).catch(() => {});
}

const parseDate = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (s, n) => { const d = parseDate(s); d.setDate(d.getDate() + n); return isoDate(d); };
const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };

function duration(min) {
  const h = Math.floor(min / 60), m = min % 60;
  if (!h) return `${m} мин`;
  return m ? `${h} ч ${m} мин` : `${h} ч`;
}

function nowMinutes() {
  // Время берём московское, как и всё расписание
  const parts = new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date());
  const get = (t) => Number(parts.find((p) => p.type === t).value);
  return get("hour") * 60 + get("minute");
}

async function confirmDialog(text, ok = "Да") {
  if (Plugins.Dialog) {
    try {
      return (await Plugins.Dialog.confirm({ title: appName(), message: text, okButtonTitle: ok, cancelButtonTitle: "Отмена" })).value;
    } catch (_) { /* упал плагин — обычный диалог */ }
  }
  return window.confirm(text);
}

const appName = () => state.me?.app_name || CONFIG.appName || "Расписание МПГУ";

function showMessage(title, text, ...actions) {
  for (const s of document.querySelectorAll(".screen")) s.hidden = true;
  $("tabs").hidden = true;
  const box = $("screen-message");
  setChildren(box, el("b", {}, title), text, actions.length ? el("div", { class: "message-actions" }, ...actions) : null);
  box.hidden = false;
}

// --- вкладки ---------------------------------------------------------------

const REDUCED_MOTION = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Текущие положение и форма индикатора, даже посреди анимации
function indicatorState(ind) {
  const m = getComputedStyle(ind).transform;
  if (!m || m === "none") return { x: 0, sx: 1, sy: 1 };
  const matrix = new DOMMatrixReadOnly(m);
  // поправка на растяжение: масштаб идёт от центра элемента
  return { x: matrix.m41 - (ind.offsetWidth / 2) * (1 - matrix.m11), sx: matrix.m11, sy: matrix.m22 };
}

// Индикатор вкладки: плавно «перетекает» к активной кнопке, слегка вытягиваясь на ходу
function moveIndicator(animate = false) {
  const active = document.querySelector(".tabbar button.active");
  const ind = $("tab-indicator");
  if (!active || !ind) return;
  const to = active.offsetLeft;
  const { x: from, sx, sy } = indicatorState(ind);
  ind.style.width = active.offsetWidth + "px";
  moveIndicator.anim?.cancel();
  ind.style.transform = `translateX(${to}px)`;
  if (!animate || REDUCED_MOTION || !ind.animate || Math.abs(to - from) < 1) return;
  const stretch = 1 + Math.min(0.22, Math.abs(to - from) / active.offsetWidth * 0.12);
  moveIndicator.anim = ind.animate([
    { transform: `translateX(${from}px) scale(${sx}, ${sy})` },
    { transform: `translateX(${from + (to - from) * 0.5}px) scale(${Math.max(stretch, sx)}, 0.94)`, offset: 0.5 },
    { transform: `translateX(${to}px) scale(1, 1)` },
  ], { duration: 480, easing: "cubic-bezier(.45, .05, .25, 1)" });
}

let currentTab = "schedule";

function openTab(name) {
  setBackButton(null);
  currentTab = name;
  if (name !== "code") stopCodeLive();
  for (const b of document.querySelectorAll(".tabbar button")) {
    b.classList.toggle("active", b.dataset.tab === name);
  }
  moveIndicator(true);
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== "screen-" + name;
  window.scrollTo(0, 0);
  if (name === "schedule") loadWeek();
  if (name === "notifications") renderNotifications();
  if (name === "profile") renderProfile();
  if (name === "admin") renderAdmin();
  if (name === "code") renderCode();
  if (name === "teacher") renderTeacher();
}

// --- расписание ------------------------------------------------------------

let offlineNoted = false;
window.addEventListener("online", () => { offlineNoted = false; if (state.week) loadWeek(); });

async function loadWeek() {
  if (navigator.onLine === false && !offlineNoted) {
    offlineNoted = true;
    toast("Нет сети — показываю сохранённое расписание");
  }
  const params = new URLSearchParams({ date: state.date });
  if (state.group === MINE) params.set("mine", "1");
  else if (state.group) params.set("group", state.group);
  try {
    state.week = await api("/api/schedule?" + params);
  } catch (e) {
    setChildren($("lessons"), el("div", { class: "empty-day" }, el("b", {}, "Не загрузилось"), e.message));
    return false;
  }
  renderWeek();
  if (state.week.days.some((d) => d.date === state.week.today)) loadAttendance();
  return true;
}

// Дни, которые показываем в полоске: Пн–Сб, воскресенье — только если в нём есть пары
function visibleDays() {
  const days = [0, 1, 2, 3, 4, 5];
  if (state.week?.days[6].lessons.length) days.push(6);
  return days;
}

function renderWeek(animation) {
  const w = state.week;
  if (!visibleDays().includes(state.selectedDay)) state.selectedDay = 5;
  setChildren($("days"), ...visibleDays().map((i) => {
    const d = w.days[i];
    const cls = ["day"];
    if (i === state.selectedDay) cls.push("active");
    if (d.date === w.today) cls.push("today");
    if (!d.lessons.length) cls.push("empty");
    // Что-то поменяли на этот день — точка под числом
    if (d.cancelled?.length || d.lessons.some((l) => l.status)) cls.push("changed");
    return el("button", {
      class: cls.join(" "),
      "aria-label": `${WEEKDAYS[i]}, ${parseDate(d.date).getDate()}`,
      "aria-pressed": String(i === state.selectedDay),
      onclick: () => selectDay(i),
    }, el("span", { class: "wd" }, WD_SHORT[i]), el("span", { class: "dn" }, parseDate(d.date).getDate()));
  }));
  renderDay(animation);
}

function selectDay(i, animation) {
  if (i === state.selectedDay) return;
  animation = animation || (i > state.selectedDay ? "next" : "prev");
  state.selectedDay = i;
  haptic();
  renderWeek(animation);
}

// «БИА 2 ПОДГРУППА» → «БИА · 2 подгруппа»
function formatGroup(g) {
  const m = /^(.*?)\s*(\d+)\s*подгруппа$/i.exec(g || "");
  return m ? `${m[1]} · ${m[2]} подгруппа` : g;
}

// --- шапка: приветствие ---

function greetingWord() {
  const h = Math.floor(nowMinutes() / 60);
  if (h >= 5 && h < 12) return "Доброе утро";
  if (h >= 12 && h < 17) return "Добрый день";
  if (h >= 17 && h < 23) return "Добрый вечер";
  return "Доброй ночи";
}

function renderGreeting() {
  const me = state.me;
  const words = displayName(me).split(/\s+/);
  const name = me.teacher && !me.student ? words.slice(1, 3).join(" ") || words[0] : words[1] || words[0];
  $("greeting").textContent = name ? `${greetingWord()}, ${name}` : greetingWord();
  const t = parseDate(me.today);
  $("today-line").textContent = `${WEEKDAYS[(t.getDay() + 6) % 7]}, ${t.getDate()} ${MONTHS[t.getMonth()]}`;
}

const roomText = (room) => (/^\d/.test(room) ? `ауд. ${room}` : room);
// У преподавателя половина — в строке подгрупп
const halfNote = (l) => (l.half && state.week.half === null && !state.week.mine ? `${l.half}-я половина` : null);
// Кто на паре: студенту — преподаватель, преподавателю — подгруппы
const whoText = (l) => (state.week.mine ? groupsText(l.groups, l.half) : l.teacher);

function dayTitle(day) {
  const w = state.week;
  const d = parseDate(day.date);
  const date = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  if (day.date === w.today) return "Весь день";
  if (day.date === addDays(w.today, 1)) return `Завтра, ${date}`;
  return `${WEEKDAYS[day.weekday]}, ${date}`;
}

function dayLabel(title) {
  const w = state.week;
  return el("div", { class: "day-label" },
    el("b", {}, title), el("span", {}, `${w.week_number} неделя · ${PARITY[w.parity]}`));
}

// Эмблема МПГУ водяным знаком на тёмных карточках
const emblem = () => el("span", { class: "logo hero-emblem", "aria-hidden": "true" });

// Чем пара в этот день отличается от обычной: «ауд. 410 вместо 401», «перенос с пн»
function changeText(l) {
  if (l.status === "extra") return l.moved ? l.note || "Перенос" : ["Разовая пара", l.note].filter(Boolean).join(" · ");
  if (l.status !== "changed") return null;
  const w = l.was || {};
  const parts = [];
  if ("room" in w) parts.push(`${l.room ? roomText(l.room) : "без аудитории"} вместо ${w.room || "—"}`);
  if ("start" in w || "end" in w) parts.push(`${l.start}–${l.end} вместо ${w.start || l.start}–${w.end || l.end}`);
  if ("teacher" in w) parts.push(`ведёт ${l.teacher || "—"}`);
  if ("subject" in w) parts.push(`вместо «${w.subject}»`);
  if ("kind" in w) parts.push(l.kind || "другой тип");
  return [`Только в этот день: ${parts.join(", ") || "есть изменения"}`, l.note].filter(Boolean).join(" · ");
}
const changeBadge = (l) => (changeText(l) ? el("div", { class: "change-note" }, icon("calEdit"), changeText(l)) : null);

// Большая карточка: текущая пара или, если сейчас перерыв, следующая
function heroCard(l, mode, now) {
  const start = toMin(l.start), end = toMin(l.end);
  const pair = l.pair_num ? ` · ${l.pair_num} пара` : "";
  const tiles = [];
  if (l.room) {
    tiles.push(el("div", { class: "hero-tile" },
      el("small", {}, l.was && "room" in l.was ? "новая аудитория" : /^\d/.test(l.room) ? "аудитория" : "где"),
      el("b", { class: "room" }, l.room)));
  }
  const who = whoText(l);
  if (who) {
    tiles.push(el("div", { class: "hero-tile grow" },
      el("small", {}, state.week.mine ? "подгруппы" : "преподаватель"), el("b", {}, who)));
  }
  const kind = kindIcon(l.kind);
  const foot = mode === "now"
    ? el("div", { class: "hero-progress" },
      el("div", { class: "track" }, el("div", { class: "fill", style: `width:${Math.round(((now - start) / (end - start)) * 100)}%` })),
      `ещё ${duration(end - now)}`)
    : el("div", { class: "hero-foot" }, `начнётся через ${duration(start - now)}`);
  return el("section", { class: "hero" }, emblem(),
    el("div", { class: "hero-top" },
      el("span", {}, (mode === "now" ? "Сейчас" : "Следующая") + pair), el("span", { class: "num" }, `${l.start}–${l.end}`)),
    el("div", { class: "hero-subject" }, l.subject),
    l.kind ? el("div", { class: "hero-kind" }, kind ? icon(kind) : null, l.kind) : null,
    halfNote(l) ? el("div", { class: "hero-half" }, `Только ${halfNote(l)} группы`) : null,
    changeText(l) ? el("div", { class: "hero-change" }, changeText(l)) : null,
    tiles.length ? el("div", { class: "hero-tiles" }, ...tiles) : null,
    foot,
    heroAction(l),
    attendanceLine(l, state.week.today, true));
}

function nextCard(l, now) {
  const meta = [l.start, l.room && roomText(l.room), l.kind, whoText(l), halfNote(l)].filter(Boolean).join(" · ");
  return el("section", { class: "card next-card" },
    el("div", { class: "next-top" },
      el("span", { class: "eyebrow" }, "Дальше"), el("span", { class: "chip" }, `через ${duration(toMin(l.start) - now)}`)),
    el("div", { class: "next-subject" }, l.subject),
    el("div", { class: "next-meta" }, meta),
    changeBadge(l));
}

// Пары закончились: подскажем, когда завтра первая
function doneCard(day) {
  const last = day.lessons[day.lessons.length - 1];
  const first = state.week.days[state.selectedDay + 1]?.lessons[0];
  return el("section", { class: "card done-card" },
    el("b", {}, "На сегодня всё"),
    el("div", { class: "note" }, `Последняя пара закончилась в ${last.end}.`),
    first ? el("div", { class: "chip tomorrow" },
      `Завтра к ${first.start} — ${first.subject}${first.room ? ", " + roomText(first.room) : ""}`) : null);
}

// Сегодня: «Сейчас» крупно, «Дальше» и весь день списком
function todayView(day) {
  const now = nowMinutes();
  const lessons = day.lessons;
  const cur = lessons.findIndex((l) => toMin(l.start) <= now && now < toMin(l.end));
  const next = lessons.findIndex((l) => toMin(l.start) > now);
  const top = [];
  if (cur >= 0) {
    top.push(heroCard(lessons[cur], "now", now));
    if (next >= 0) top.push(nextCard(lessons[next], now));
  } else if (next >= 0) {
    top.push(heroCard(lessons[next], "next", now));
  } else {
    top.push(checkinBanner(), doneCard(day));
  }
  return [...top, dayLabel("Весь день"), ...dayCards(day, now)];
}

// Пары дня подробными карточками с перерывами и окнами. now — сегодня: прошедшие приглушены,
// текущая выделена (её отметка — на карточке «Сейчас»)
function dayCards(day, now = null) {
  const nodes = [];
  day.lessons.forEach((l, idx) => {
    const done = now !== null && toMin(l.end) <= now;
    const current = now !== null && !done && toMin(l.start) <= now;
    if (idx > 0) {
      const gap = toMin(l.start) - toMin(day.lessons[idx - 1].end);
      if (gap > 0) {
        const isWindow = gap >= WINDOW_MIN;
        nodes.push(el("div", { class: "gap" + (isWindow ? " window" : "") },
          el("span", {}, `${isWindow ? "Окно" : "Перерыв"} ${duration(gap)}`)));
      }
    }
    const kind = kindIcon(l.kind);
    const meta = [];
    if (l.kind) meta.push(el("span", { class: "kind" }, kind ? icon(kind) : null, l.kind));
    if (l.room) meta.push(roomText(l.room));
    if (whoText(l)) meta.push(whoText(l));
    if (halfNote(l)) meta.push(halfNote(l));
    nodes.push(el("div", { class: "lesson-card" + (done ? " done" : "") + (current ? " now" : "") },
      el("div", { class: "lc-top" },
        el("b", {}, `${l.start}–${l.end}`),
        el("span", {}, current ? "идёт сейчас" : done ? "прошла" : l.pair_num ? `${l.pair_num} пара` : null)),
      el("div", { class: "lc-subject" }, l.subject),
      meta.length ? el("div", { class: "lc-meta" }, ...meta.flatMap((m, i) => (i ? [el("span", { class: "sep" }), m] : [m]))) : null,
      changeBadge(l),
      current ? null : attendanceLine(l, day.date)));
  });
  return nodes;
}

// Отменённые в этот день пары — чтобы не ехать зря
function cancelledCard(day) {
  if (!day.cancelled?.length) return null;
  return el("section", { class: "card cancelled-card" },
    el("div", { class: "eyebrow" }, day.cancelled.length > 1 ? "Отменены" : "Отменена"),
    ...day.cancelled.map((l) => el("div", { class: "cancelled-row" },
      el("span", { class: "t" }, l.start),
      el("span", { class: "s" }, el("b", {}, l.subject), l.note ? el("small", {}, l.note) : null))));
}

function editDayButton(day) {
  if (!state.week.can_edit) return null;
  return el("button", { class: "link-btn edit-day", onclick: () => dayChangesSheet(state.week.group, day.date) },
    icon("calEdit"), "Изменить расписание на этот день");
}

// --- разовые изменения на один день (без повторений) -----------------------------------

function longDate(iso) {
  const d = parseDate(iso);
  return `${WEEKDAYS[(d.getDay() + 6) % 7]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

async function ensureConfig() {
  if (!state.config) state.config = await api("/api/admin/config");
  return state.config;
}

// Поля времени с выбором № пары из сетки звонков
function timeFields(bells, l = {}) {
  const pair = el("select", {}, el("option", { value: "" }, "—"),
    ...bells.map((b, i) => el("option", { value: i + 1, selected: l.pair_num === i + 1 }, `${i + 1} (${b.start}–${b.end})`)));
  const start = el("input", { type: "time", value: l.start || "" });
  const end = el("input", { type: "time", value: l.end || "" });
  pair.onchange = () => {
    const b = bells[Number(pair.value) - 1];
    if (b) { start.value = b.start; end.value = b.end; }
  };
  return { pair, start, end, values: () => ({ pair_num: pair.value ? Number(pair.value) : null, start: start.value, end: end.value }) };
}

async function dayChangesSheet(group, iso) {
  const [bells] = await Promise.all([ensureConfig().then((c) => c.bells).catch(() => [])]);
  openSheet(longDate(iso), `${formatGroup(group)} · изменения только на этот день, без повторений. Группа получит уведомление.`,
    (card, close) => {
      const body = el("div", { class: "sheet-form" }, el("p", { class: "note" }, "Загрузка…"));
      const error = el("p", { class: "sheet-error", role: "alert" });
      card.append(body);
      let day = null;

      async function finish(msg) {
        hapticResult("success");
        close();
        toast(msg);
        state.week = null;
        await loadWeek();
      }
      async function send(payload, btn, msg = "Готово — группа получит уведомление") {
        btn.disabled = true;
        error.textContent = "";
        try {
          await api("/api/admin/changes", { method: "POST", body: { group, date: iso, ...payload } });
          await finish(msg);
        } catch (e) { error.textContent = e.message; hapticResult("error"); }
        finally { btn.disabled = false; }
      }
      async function undo(id, label) {
        if (!(await confirmDialog(label, "Да"))) return;
        try {
          await api(`/api/admin/changes/${id}`, { method: "DELETE" });
          await finish("Вернули как было — группа получит уведомление");
        } catch (e) { error.textContent = e.message; }
      }
      const field = (label, input, span) => el("label", { class: span ? "span2" : "" }, label, input);
      const back = () => el("button", { class: "btn tinted", onclick: list }, "Назад");

      // Другая аудитория, время или преподаватель — либо перенос на другой день
      function editForm(l) {
        const t = timeFields(bells, l);
        const room = el("input", { value: l.room, placeholder: "например, 410" });
        const teacher = el("input", { value: l.teacher });
        const note = el("input", { placeholder: "например, лекция в большой аудитории", maxlength: 300 });
        const toDate = el("input", { type: "date", value: addDays(iso, 1) });
        const dateBox = el("div", { class: "span2", hidden: true }, field("На какой день", toDate));
        let mode = "change";
        const seg = segmented([["change", "В этот же день"], ["move", "На другой день"]], mode, (v) => {
          mode = v;
          dateBox.hidden = v !== "move";
          teacherBox.hidden = v === "move";
          save.textContent = v === "move" ? "Перенести" : "Сохранить";
        });
        seg.classList.add("wide");
        const teacherBox = field("Преподаватель", teacher, true);
        const save = el("button", { class: "btn block", onclick: () => {
          const v = { ...t.values(), room: room.value };
          if (mode === "move") send({ action: "move", lesson_id: l.id, to_date: toDate.value, ...v, note: note.value }, save, "Пара перенесена — группа получит уведомление");
          else send({ action: "change", lesson_id: l.id, ...v, teacher: teacher.value, note: note.value }, save);
        } }, "Сохранить");
        setChildren(body, el("h3", {}, `${l.start} · ${l.subject}`), seg,
          el("div", { class: "form-grid" }, dateBox, field("Аудитория", room, true), field("№ пары", t.pair), el("span"),
            field("Начало", t.start), field("Конец", t.end), teacherBox, field("Пояснение", note, true)),
          error, save, back());
        room.focus();
      }

      function cancelForm(l) {
        const note = el("textarea", { class: "full", rows: 2, maxlength: 300, placeholder: "Причина — по желанию: «преподаватель заболел»" });
        const save = el("button", { class: "btn block danger", onclick: () => send({ action: "cancel", lesson_id: l.id, note: note.value }, save, "Пара отменена — группа получит уведомление") },
          "Отменить пару");
        setChildren(body, el("h3", {}, `Отменить ${l.subject} в ${l.start}?`),
          el("p", { class: "note" }, "Только в этот день. В другие недели пара останется."), note, error, save, back());
      }

      function addForm() {
        const t = timeFields(bells);
        const f = {
          subject: el("input", { placeholder: "Название" }),
          kind: el("input", { list: "kinds-once", placeholder: "консультация" }),
          room: el("input"), teacher: el("input"),
          note: el("input", { maxlength: 300, placeholder: "по желанию" }),
          half: el("select", {}, el("option", { value: "" }, "вся группа"),
            el("option", { value: 1 }, "1-я половина"), el("option", { value: 2 }, "2-я половина")),
        };
        const save = el("button", { class: "btn block", onclick: () => send({
          action: "add", subject: f.subject.value, kind: f.kind.value, room: f.room.value, teacher: f.teacher.value,
          half: f.half.value ? Number(f.half.value) : null, note: f.note.value, ...t.values(),
        }, save, "Пара добавлена — группа получит уведомление") }, "Добавить пару");
        setChildren(body, el("h3", {}, "Разовая пара"),
          el("datalist", { id: "kinds-once" }, ...[...KINDS, "консультация", "отработка"].map((k) => el("option", { value: k }))),
          el("div", { class: "form-grid" }, field("Предмет", f.subject, true), field("№ пары", t.pair), field("Тип", f.kind),
            field("Начало", t.start), field("Конец", t.end), field("Аудитория", f.room), field("Половина", f.half),
            field("Преподаватель", f.teacher, true), field("Пояснение", f.note, true)),
          error, save, back());
        f.subject.focus();
      }

      function list() {
        error.textContent = "";
        const rows = day.lessons.map((l) => el("div", { class: "change-row" },
          el("div", { class: "grow" }, el("b", {}, `${l.start} ${l.subject}`),
            el("small", {}, changeText(l) || [l.room && roomText(l.room), l.teacher, l.half && `${l.half}-я половина`].filter(Boolean).join(" · "))),
          el("div", { class: "change-actions" },
            l.change_id ? el("button", { class: "btn tinted small", onclick: () => undo(l.change_id,
              l.status === "extra" ? `Убрать «${l.subject}» в этот день?` : `Вернуть «${l.subject}» как по расписанию?`) },
              l.status === "extra" ? "Убрать" : "Вернуть") : null,
            l.id ? el("button", { class: "btn tinted small", onclick: () => editForm(l) }, "Изменить") : null,
            l.id ? el("button", { class: "btn tinted small danger-text", onclick: () => cancelForm(l) }, "Отменить") : null)));
        const cancelled = day.cancelled.map((l) => el("div", { class: "change-row muted" },
          el("div", { class: "grow" }, el("b", {}, `${l.start} ${l.subject}`), el("small", {}, l.note || "отменена")),
          el("button", { class: "btn tinted small", onclick: () => undo(l.change_id,
            l.moved ? `Отменить перенос «${l.subject}»?` : `Вернуть «${l.subject}» в этот день?`) }, "Вернуть")));
        setChildren(body, ...rows, ...cancelled,
          rows.length || cancelled.length ? null : el("p", { class: "note" }, "В этот день пар нет."),
          error,
          el("button", { class: "btn tinted block", onclick: addForm }, icon("plus"), "Разовая пара"));
      }

      api(`/api/schedule?date=${iso}&group=${encodeURIComponent(group)}&edit=1`)
        .then((w) => { day = w.days.find((x) => x.date === iso); list(); })
        .catch((e) => setChildren(body, el("p", { class: "sheet-error" }, e.message)));
    });
}

// Ближайшие разовые изменения группы — у старосты на главной и в «Парах групп»
function changesPanel(getGroup) {
  const box = el("div", { class: "stack" });
  const date = el("input", { type: "date", value: state.me.today, "aria-label": "День" });
  async function load() {
    const group = getGroup();
    if (!group) { setChildren(box); return; }
    let items;
    try { items = await api("/api/admin/changes?group=" + encodeURIComponent(group)); }
    catch (e) { setChildren(box, el("p", { class: "note" }, e.message)); return; }
    // Перенос — две записи; показываем одну, со стороны старого дня
    items = items.filter((c) => !(c.moved && c.action === "add"));
    setChildren(box,
      ...items.map((c) => el("div", { class: "item" },
        el("div", { class: "grow" }, el("div", {}, c.summary), c.note && !c.moved ? el("div", { class: "sub" }, c.note) : null),
        el("button", { class: "icon-btn", "aria-label": "Открыть день", onclick: () => dayChangesSheet(group, c.date) }, icon("edit")))),
      items.length ? null : el("p", { class: "note" }, "Разовых изменений нет — всё по расписанию."),
      el("div", { class: "inline" }, date,
        el("button", { class: "btn small", onclick: () => date.value && dayChangesSheet(group, date.value) }, "Изменить день")));
  }
  load();
  return { node: box, reload: load };
}

function renderDay(animation) {
  const w = state.week;
  const day = w.days[state.selectedDay];
  $("group-name").textContent = w.mine ? mineTitle() : formatGroup(w.group);
  $("go-today").hidden = day.date === todayTarget().date;
  const box = $("lessons");
  box.classList.remove("slide-next", "slide-prev");
  if (animation) { void box.offsetWidth; box.classList.add("slide-" + animation); }

  if (w.mine && !w.found) {
    setChildren(box, notInScheduleHero());
    return;
  }
  if (!day.lessons.length) {
    setChildren(box, day.date === w.today ? checkinBanner() : null, dayLabel(day.date === w.today ? "Сегодня" : dayTitle(day)),
      el("div", { class: "empty-day" }, el("b", {}, "Пар нет"), day.cancelled?.length ? "Всё отменили" : "Можно отдохнуть 🌿"),
      cancelledCard(day), editDayButton(day));
    return;
  }
  if (day.date === w.today) setChildren(box, ...todayView(day), cancelledCard(day), editDayButton(day));
  else setChildren(box, dayLabel(dayTitle(day)), ...dayCards(day), cancelledCard(day), editDayButton(day));
}

const MINE = "@mine";
const mineTitle = () => (state.me.teacher.schedule_name ? `Мои пары · ${state.me.teacher.schedule_name}` : "Мои пары");

// Выбор в шапке: [значение, подпись]. Один вариант — просто подпись
function setupGroupPicker(options) {
  const sel = $("schedule-group");
  const values = options.map(([v]) => v);
  if (!values.includes(state.group)) state.group = values.length ? values[0] : null;
  const pick = options.length > 1;
  $("group-pick").hidden = !pick;
  $("group-name").hidden = pick;
  if (!pick) return;
  setChildren(sel, ...options.map(([v, label]) => el("option", { value: v }, label)));
  sel.value = state.group;
  sel.onchange = () => { state.group = sel.value; loadWeek(); };
}

async function shiftWeek(n, selectLast = false) {
  state.date = addDays(state.date, 7 * n);
  haptic();
  if (await loadWeek()) {
    const days = visibleDays();
    const target = n > 0 ? days[0] : selectLast ? days[days.length - 1] : state.selectedDay;
    state.selectedDay = target;
    renderWeek(n > 0 ? "next" : "prev");
  }
}

function moveDay(dir) {
  const days = visibleDays();
  const pos = days.indexOf(state.selectedDay) + dir;
  if (pos >= 0 && pos < days.length) selectDay(days[pos], dir > 0 ? "next" : "prev");
  else shiftWeek(dir, dir < 0);
}

// «Сегодня»; в воскресенье — понедельник следующей недели
function todayTarget() {
  const today = state.me.today;
  const wd = (parseDate(today).getDay() + 6) % 7;
  return wd === 6 ? { date: addDays(today, 1), day: 0 } : { date: today, day: wd };
}

function goToday() {
  const t = todayTarget();
  state.date = t.date;
  state.selectedDay = t.day;
  haptic();
  loadWeek();
}

function setupSwipe(node) {
  let x0 = null, y0 = null;
  node.addEventListener("touchstart", (e) => {
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
  }, { passive: true });
  node.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) moveDay(dx < 0 ? 1 : -1);
  }, { passive: true });
}

// --- общие элементы экранов ---------------------------------------------------

function switchControl(checked, onchange) {
  const input = el("input", { type: "checkbox", checked, onchange });
  return { input, node: el("label", { class: "switch" }, input, el("span", { class: "track" })) };
}

function segmented(options, value, onchange) {
  const box = el("div", { class: "segmented" });
  const draw = () => setChildren(box, ...options.map(([v, label]) => el("button", {
    class: v === value ? "on" : "",
    onclick: () => { if (v !== value) { value = v; haptic(); draw(); onchange(v); } },
  }, label)));
  draw();
  return box;
}

// Ряд крупных кнопок-вариантов: минуты до пары, «на сегодня / на завтра»
function choiceRow(options, value, onchange) {
  const box = el("div", { class: "choices" });
  const draw = () => setChildren(box, ...options.map(([v, label]) => el("button", {
    class: "choice" + (v === value ? " on" : ""),
    "aria-pressed": String(v === value),
    onclick: () => { if (v !== value) { value = v; haptic(); draw(); onchange(v); } },
  }, label)));
  draw();
  return box;
}

function pageHead(title, sub, accentSub = false) {
  return el("header", { class: "page-head" },
    el("h1", {}, title),
    sub ? el("div", { class: "page-sub" + (accentSub ? " accent" : "") }, sub) : null);
}

const iconTile = (name) => el("span", { class: "icon-tile" }, icon(name));

// Строка карточки: значок, подпись над текстом или пояснение под ним, значение справа и стрелка
function listRow({ iconName, title, label, hint, value, onclick, chevron = true, danger = false }) {
  return el(onclick ? "button" : "div", { class: "list-row" + (danger ? " danger" : ""), onclick },
    iconName ? iconTile(iconName) : null,
    el("span", { class: "grow" },
      label ? el("small", {}, label) : null, el("b", {}, title), hint ? el("span", { class: "hint" }, hint) : null),
    value !== undefined ? el("span", { class: "value" }, value) : null,
    onclick && chevron ? el("span", { class: "chev-icon" }, icon("chevron")) : null);
}

// --- уведомления --------------------------------------------------------------

// Для примера напоминания — ближайшая пара из расписания
function sampleLesson() {
  const w = state.week;
  if (!w) return null;
  const now = nowMinutes();
  for (const d of w.days) {
    if (d.date < w.today) continue;
    const l = d.lessons.find((x) => d.date !== w.today || toMin(x.start) > now);
    if (l) return l;
  }
  return w.days.flatMap((d) => d.lessons)[0] || null;
}

const minutesWord = (n) => plural(n, "минуту", "минуты", "минут");

// «Уведомления»: лента сообщений и настройки напоминаний о парах
function renderNotifications() {
  const me = state.me;
  const canRemind = !!(me.student || me.teacher);
  if (!canRemind) state.notesView = "inbox";
  const body = el("div", { class: "stackv" });
  const tabs = canRemind ? segmented([["inbox", "Входящие"], ["remind", "Напоминания"]],
    state.notesView, (v) => { state.notesView = v; draw(); }) : null;
  tabs?.classList.add("wide");
  const draw = () => {
    if (state.notesView === "remind") renderReminders(body);
    else renderInbox(body);
  };
  setChildren($("notifications-body"), tabs ? el("div", { class: "notes-tabs" }, tabs) : null, body);
  draw();
}

function renderReminders(box) {
  const me = state.me;
  const s = { ...me.settings };
  const status = el("div", { class: "status" });
  let timer;
  const save = () => {
    clearTimeout(timer);
    status.textContent = "Сохраняю…";
    timer = setTimeout(async () => {
      try {
        await api("/api/me/settings", { method: "PUT", body: s });
        Object.assign(me.settings, s);
        status.textContent = NATIVE ? "Сохранено — напоминания обновлены" : "Сохранено";
        if (s.notify_before !== null || s.digest_time !== null) await askNotificationPermission();
        await syncReminders(true);
        drawPermission();
      } catch (e) { status.textContent = e.message; }
    }, 400);
  };
  let lastBefore = s.notify_before || 15;

  // Так придёт напоминание — на примере ближайшей пары
  const lesson = sampleLesson();
  const previewHead = el("span");
  const preview = el("section", { class: "hero preview" },
    el("div", { class: "hero-top" }, el("span", {}, "Так придёт напоминание")),
    el("div", { class: "bubble" },
      el("div", { class: "bubble-head" }, icon("bell"), previewHead),
      el("div", { class: "bubble-title" }, lesson ? lesson.subject : "Название пары"),
      el("div", { class: "bubble-meta" }, lesson
        ? [lesson.start, lesson.room && roomText(lesson.room), lesson.kind].filter(Boolean).join(" · ")
        : "время · аудитория · тип")));

  const remindSub = el("small");
  const minutes = el("div", {},
    choiceRow([[5, "5"], [10, "10"], [15, "15"], [30, "30"], [60, "60"]], lastBefore, (v) => {
      lastBefore = v; s.notify_before = v; draw(); save();
    }),
    el("div", { class: "choices-note" }, "минут до пары"));
  const remind = switchControl(s.notify_before !== null, () => {
    s.notify_before = remind.input.checked ? lastBefore : null;
    haptic(); draw(); save();
  });

  const digestSub = el("small");
  const time = el("input", { type: "time", class: "time-box", value: s.digest_time || "20:00", "aria-label": "Время сводки",
    onchange: () => { if (time.value) { s.digest_time = time.value; draw(); save(); } } });
  const digestRow = el("div", { class: "digest-row" }, time,
    choiceRow([["today", "на сегодня"], ["tomorrow", "на завтра"]], s.digest_day, (v) => { s.digest_day = v; draw(); save(); }));
  const digest = switchControl(s.digest_time !== null, () => {
    s.digest_time = digest.input.checked ? time.value || "20:00" : null;
    haptic(); draw(); save();
  });

  function draw() {
    const on = s.notify_before !== null;
    previewHead.textContent = `Через ${lastBefore} ${minutesWord(lastBefore)}`;
    preview.classList.toggle("off", !on);
    remindSub.textContent = on ? `за ${s.notify_before} ${minutesWord(s.notify_before)} до начала` : "выключено";
    minutes.hidden = !on;
    digestSub.textContent = s.digest_time === null ? "выключено"
      : `в ${s.digest_time} · расписание ${s.digest_day === "today" ? "на сегодня" : "на завтра"}`;
    digestRow.hidden = s.digest_time === null;
  }

  const card = (iconName, title, sub, sw, body) => el("section", { class: "panel" },
    el("div", { class: "toggle-head" }, iconTile(iconName), el("div", { class: "grow" }, el("b", {}, title), sub), sw.node),
    body);
  // Разрешение на уведомления в системе: без него напоминания не покажутся
  const permission = el("div");
  async function drawPermission() {
    const st = await notificationState();
    const wants = s.notify_before !== null || s.digest_time !== null;
    if (!wants || st === "granted" || st === "unsupported") {
      setChildren(permission, st === "unsupported" && wants ? el("section", { class: "panel" },
        el("p", { class: "note", style: "margin:0" },
          "Напоминания приходят в приложении на телефоне. В браузере — только лента уведомлений.")) : null);
      if (st === "granted" && PLATFORM === "android") drawExact();
      return;
    }
    setChildren(permission, el("section", { class: "panel warn" },
      el("b", {}, "Уведомления выключены в системе"),
      el("p", { class: "note" }, st === "denied"
        ? "Включи их в настройках телефона: Настройки → Приложения → " + appName() + " → Уведомления."
        : "Разреши приложению показывать уведомления — иначе напоминания не придут."),
      st === "denied" ? null : el("button", { class: "btn small", style: "margin-top:10px", onclick: async () => {
        await askNotificationPermission();
        await syncReminders(true);
        drawPermission();
      } }, "Разрешить")));
  }
  // Android 12+: без разрешения на точное время напоминание может опоздать на несколько минут
  async function drawExact() {
    let exact = "granted";
    try { exact = (await Plugins.LocalNotifications.checkExactNotificationSetting()).exact_alarm; } catch (_) { /* старый Android */ }
    if (exact === "granted") return;
    permission.append(el("section", { class: "panel" }, listRow({
      iconName: "clock", title: "Точное время напоминаний", hint: "без него напоминание может прийти на пару минут позже",
      onclick: async () => {
        try { await Plugins.LocalNotifications.changeExactNotificationSetting(); } catch (_) { /* нет такой настройки */ }
      },
    })));
  }
  draw();
  setChildren(box, permission, preview,
    card("alarm", "Перед парой", remindSub, remind, minutes),
    card("list", "Сводка на день", digestSub, digest, digestRow),
    el("p", { class: "foot-note" }, NATIVE
      ? "Телефон напомнит сам, даже без интернета. Время — московское. Изменения сохраняются сами."
      : "Всё время — московское. Изменения сохраняются сами."),
    status);
  drawPermission();
}

// --- лента уведомлений -----------------------------------------------------------

const NOTE_ICONS = { group: "users", role: "star", contact: "mail", reply: "chat", announce: "bell",
  schedule: "calPlus", teacher: "book", info: "bell" };

function timeAgo(iso) {
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const today = state.me.today;
  const local = isoDate(d);
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (local === today) return `сегодня в ${hhmm}`;
  if (local === addDays(today, -1)) return `вчера в ${hhmm}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} в ${hhmm}`;
}

async function renderInbox(box) {
  setChildren(box, loadingNote());
  let page;
  try { page = await api("/api/notifications"); } catch (e) { setChildren(box, errorNote(e)); return; }
  const items = [...page.items];
  const list = el("section", { class: "panel list" });
  const more = el("button", { class: "btn tinted block", onclick: async () => {
    more.disabled = true;
    try {
      const next = await api("/api/notifications?before_id=" + items[items.length - 1].id);
      items.push(...next.items);
      next.items.forEach((n) => list.append(noteRow(n)));
      more.hidden = !next.more;
    } catch (e) { toast(e.message); }
    finally { more.disabled = false; }
  } }, "Показать ещё");
  more.hidden = !page.more;
  setChildren(list, ...items.map(noteRow));
  if (!items.length) {
    setChildren(box, el("section", { class: "empty-day" }, el("b", {}, "Пока пусто"),
      "Здесь появятся сообщения: тебя добавили в группу, изменилось расписание, объявление старосты."));
  } else setChildren(box, list, more);
  if (items[0]) rememberNote(items[0].id);
  // Открыли ленту — всё прочитано (подсветка новых остаётся до следующего открытия)
  if (page.unread) {
    try { setUnread((await api("/api/notifications/read", { method: "POST", body: {} })).unread); } catch (_) { /* не страшно */ }
    Plugins.LocalNotifications?.removeAllDeliveredNotifications?.().catch(() => {});
  }
}

function noteRow(n) {
  const reply = n.can_reply ? el("button", { class: "link-btn", onclick: (e) => { e.stopPropagation(); openReply(n); } }, "Ответить") : null;
  return el("div", { class: "note-row" + (n.read ? "" : " unread") },
    iconTile(NOTE_ICONS[n.kind] || "bell"),
    el("div", { class: "grow" },
      el("b", {}, n.title),
      n.body ? el("div", { class: "note-body" }, n.body) : null,
      el("div", { class: "note-meta" }, el("span", {}, timeAgo(n.created_at)), reply)));
}

// Шторка снизу с полем ввода: ответ на сообщение, объявление, код
function openSheet(title, sub, build, onclose) {
  haptic("light");
  const closeBtn = el("button", { class: "sheet-close", "aria-label": "Закрыть", onclick: () => close() }, icon("close"));
  const card = el("div", { class: "sheet", role: "dialog", "aria-modal": "true" },
    closeBtn, el("h2", {}, title), sub ? el("p", { class: "sheet-sub" }, sub) : null);
  const backdrop = el("div", { class: "sheet-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } }, card);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  function close() {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    setBackButton(null);
    onclose?.();
  }
  build(card, close);
  document.body.append(backdrop);
  document.addEventListener("keydown", onKey);
  setBackButton(close);
  card.querySelector("input, textarea")?.focus({ preventScroll: true });
  return close;
}

// Поле текста и кнопка отправки в шторке
function textSheet({ title, sub, placeholder, button, done, send, rows = 4, maxlength = 2000 }) {
  openSheet(title, sub, (card, close) => {
    const text = el("textarea", { class: "full", rows, maxlength, placeholder });
    const error = el("p", { class: "sheet-error", role: "alert" });
    const btn = el("button", { class: "btn block", onclick: async () => {
      if (!text.value.trim()) { error.textContent = "Напиши текст"; return; }
      btn.disabled = true;
      try {
        const r = await send(text.value.trim());
        hapticResult("success");
        close();
        toast(typeof done === "function" ? done(r) : done);
      } catch (e) { error.textContent = e.message; hapticResult("error"); }
      finally { btn.disabled = false; }
    } }, button);
    card.append(el("div", { class: "sheet-form" }, text, error, btn));
  });
}

function openReply(n) {
  textSheet({
    title: "Ответ", sub: `${n.sender?.name || ""} · «${n.title}»`, placeholder: "Текст ответа", button: "Отправить",
    done: "Ответ отправлен ✅",
    send: (text) => api(`/api/notifications/${n.id}/reply`, { method: "POST", body: { text } }),
  });
}

function setUnread(n) {
  state.unread = n || 0;
  const badge = $("tab-badge");
  if (badge) {
    badge.hidden = !state.unread;
    badge.textContent = state.unread > 99 ? "99+" : String(state.unread);
  }
  Plugins.Badge?.set?.({ count: state.unread }).catch(() => {});
}

async function rememberNote(id) {
  if (id > state.lastNoteId) {
    state.lastNoteId = id;
    await store.set("mpgu_last_note", id);
    bgConfigure();
  }
}

// Пока приложение открыто — раз в минуту проверяем новые уведомления
async function pollInbox() {
  if (!TOKEN || document.hidden) return;
  let r;
  try { r = await api("/api/notifications/new?after_id=" + state.lastNoteId); } catch (_) { return; }
  setUnread(r.unread);
  if (!r.items.length) return;
  const newest = r.items[r.items.length - 1];
  await rememberNote(newest.id);
  if (currentTab === "notifications" && state.notesView === "inbox") renderNotifications();
  else toast(newest.title, 4000);
  // Что-то поменялось в группе или роли — подтянем профиль, чтобы вкладки были актуальны
  if (r.items.some((n) => n.kind === "group" || n.kind === "role" || n.kind === "teacher")) refreshMe();
}

async function refreshMe() {
  try {
    const fresh = await api("/api/me");
    const changed = fresh.role !== state.me.role || fresh.student?.group !== state.me.student?.group
      || !!fresh.teacher !== !!state.me.teacher;
    state.me = fresh;
    if (changed) start();
  } catch (_) { /* в следующий раз */ }
}

// --- системные уведомления на телефоне ---------------------------------------------

// granted / denied / prompt / unsupported (браузер)
async function notificationState() {
  const ln = Plugins.LocalNotifications;
  if (!NATIVE || !ln) return "unsupported";
  try {
    const p = (await ln.checkPermissions()).display;
    return p === "granted" ? "granted" : p === "denied" ? "denied" : "prompt";
  } catch (_) { return "unsupported"; }
}

async function askNotificationPermission() {
  const ln = Plugins.LocalNotifications;
  if (!NATIVE || !ln) return false;
  try {
    if ((await notificationState()) === "prompt") await ln.requestPermissions();
    return (await notificationState()) === "granted";
  } catch (_) { return false; }
}

const REMINDER_KINDS = new Set(["reminder", "digest"]);
let lastSync = 0;

// Напоминания о парах ставим в системный планировщик телефона на 10 дней вперёд.
// Обновляем при открытии приложения (не чаще раза в 20 минут) и после изменения настроек.
async function syncReminders(force = false) {
  const ln = Plugins.LocalNotifications;
  if (!NATIVE || !ln || !TOKEN || !state.me) return;
  if (!force && Date.now() - lastSync < 20 * 60 * 1000) return;
  if ((await notificationState()) !== "granted") return;
  let plan;
  try { plan = await api("/api/me/plan"); } catch (_) { return; }
  lastSync = Date.now();
  try {
    const pending = (await ln.getPending()).notifications || [];
    const old = pending.filter((n) => REMINDER_KINDS.has(n.extra?.kind));
    if (old.length) await ln.cancel({ notifications: old.map((n) => ({ id: n.id })) });
    if (!plan.items.length) return;
    await ln.schedule({ notifications: plan.items.map((i) => ({
      id: i.id, title: i.title, body: i.body, largeBody: i.body,
      schedule: { at: new Date(i.at), allowWhileIdle: true },
      channelId: "lessons", extra: { kind: i.kind },
    })) });
  } catch (e) { console.warn("Не удалось поставить напоминания", e); }
}

async function clearReminders() {
  const ln = Plugins.LocalNotifications;
  if (!NATIVE || !ln) return;
  try {
    const pending = (await ln.getPending()).notifications || [];
    if (pending.length) await ln.cancel({ notifications: pending.map((n) => ({ id: n.id })) });
  } catch (_) { /* нечего отменять */ }
}

// Фоновая проверка (mobile/runners/background.js): раз в ~15 минут спрашивает сервер
// о новых уведомлениях и показывает их системными. Ей нужны адрес сервера и токен.
function bgConfigure() {
  const br = Plugins.BackgroundRunner;
  if (!NATIVE || !br) return;
  br.dispatchEvent({
    label: "ru.mpgu.schedule.check", event: "configure",
    details: { server: SERVER, token: TOKEN || "", lastId: state.lastNoteId || 0 },
  }).catch(() => {});
}

async function setupNativeNotifications() {
  const ln = Plugins.LocalNotifications;
  if (!NATIVE || !ln) return;
  if (PLATFORM === "android") {
    try {
      await ln.createChannel({ id: "lessons", name: "Напоминания о парах", description: "Перед парой и сводка на день",
        importance: 4, visibility: 1, vibration: true });
      await ln.createChannel({ id: "inbox", name: "Сообщения", description: "Группа, расписание, объявления",
        importance: 4, visibility: 1, vibration: true });
    } catch (_) { /* старый Android — каналы не нужны */ }
  }
  // Нажали на уведомление — открываем нужную вкладку
  ln.addListener("localNotificationActionPerformed", (a) => {
    const kind = a.notification?.extra?.kind;
    if (!state.me) return;
    openTab(REMINDER_KINDS.has(kind) ? "schedule" : "notifications");
  }).catch(() => {});
}

// --- профиль ---------------------------------------------------------------

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return one;
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
  return many;
}

// Картинки с сервера: в приложении — с адреса сервера, в браузере — с того же сайта
const mediaUrl = (path) => (path ? SERVER + path : null);

function avatarNode(name, photo) {
  const initials = name.split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase();
  const img = photo ? el("img", { src: mediaUrl(photo), alt: `Фото: ${name}`, loading: "lazy" }) : null;
  const label = el("span", { "aria-hidden": photo ? "true" : null }, initials);
  // Не загрузилось — остаются инициалы
  img?.addEventListener("error", () => { img.remove(); label.removeAttribute("aria-hidden"); });
  return el("div", { class: "avatar", title: name }, label, img);
}

// Квадратное фото 512×512 в JPEG: обрезаем по центру, метаданные (геопозиция) не попадают
async function squareJpeg(file, size = 512) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, fail) => {
      const i = new Image();
      i.onload = () => ok(i);
      i.onerror = () => fail(new Error("Не получилось открыть картинку — выбери другую"));
      i.src = url;
    });
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, size, size);
    ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size);
    return canvas.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}

function pickImage() {
  return new Promise((resolve) => {
    const input = el("input", { type: "file", accept: "image/*", hidden: true });
    input.onchange = () => { resolve(input.files[0] || null); input.remove(); };
    document.body.append(input);
    input.click();
  });
}

function photoChanged(photo) {
  state.me.photo = photo;
  renderProfile();
  setChildren($("me-avatar"), avatarNode(displayName(state.me) || "?", photo));
}

function photoSheet() {
  openSheet("Фото профиля", "Его видят староста и админы — чтобы узнать тебя в списке группы", (card, close) => {
    const error = el("p", { class: "sheet-error", role: "alert" });
    const choose = el("button", { class: "btn block", onclick: async () => {
      const file = await pickImage();
      if (!file) return;
      choose.disabled = true;
      error.textContent = "";
      try {
        const image = await squareJpeg(file);
        const r = await api("/api/me/photo", { method: "POST", body: { image } });
        hapticResult("success");
        close();
        photoChanged(r.photo);
        toast("Фото обновлено");
      } catch (e) { error.textContent = e.message; hapticResult("error"); }
      finally { choose.disabled = false; }
    } }, state.me.photo ? "Выбрать другое фото" : "Выбрать фото");
    const remove = state.me.photo ? el("button", { class: "btn tinted block danger-text", onclick: async () => {
      try {
        await api("/api/me/photo", { method: "DELETE" });
        close();
        photoChanged(null);
        toast("Фото убрано");
      } catch (e) { error.textContent = e.message; }
    } }, "Убрать фото") : null;
    card.append(el("div", { class: "sheet-form" },
      el("div", { class: "photo-preview" }, avatarNode(displayName(state.me), state.me.photo)), error, choose, remove));
  });
}

const displayName = (me) => me.student?.full_name || me.teacher?.full_name || me.full_name || "";

// Тёмная карточка: кто ты, группа и половина (её можно поменять)
function profileHero() {
  const me = state.me;
  const name = displayName(me);
  const sub = [me.login, me.role !== "user" ? ROLE_LABELS[me.role] : null,
    me.teacher ? "преподаватель" : null].filter(Boolean).join(" · ");
  const tiles = [];
  let halfPanel = null;
  if (me.student) {
    tiles.push(el("div", { class: "hero-tile grow" }, el("small", {}, "группа"), el("b", {}, formatGroup(me.student.group))));
    if (me.half_lessons.length) {
      halfPanel = el("div", { class: "hero-panel", hidden: true },
        segmented([[1, "1-я"], [2, "2-я"], [null, "не знаю"]], me.half, async (v) => {
          try {
            await api("/api/me/settings", { method: "PUT", body: { half: v } });
            me.half = v;
            state.week = null;
            toast("Сохранено");
            renderProfile();
          } catch (e) { toast(e.message); }
        }),
        el("div", { class: "hero-note" }, "На этих парах группа делится пополам:",
          ...me.half_lessons.map((l) => el("div", {},
            `${WD_SHORT[l.weekday]}${l.week !== "every" ? ", " + l.week_label : ""}, ${l.start} — ${l.subject}: ${l.half}-я половина`))));
      halfPanel.querySelector(".segmented").classList.add("wide");
      const tile = el("button", { class: "hero-tile half-tile", "aria-expanded": "false", onclick: () => {
        halfPanel.hidden = !halfPanel.hidden;
        tile.setAttribute("aria-expanded", String(!halfPanel.hidden));
        haptic();
      } }, el("small", {}, "половина"), el("b", {}, me.half ? `${me.half}-я` : "не выбрана", icon("chevron")));
      tiles.push(tile);
    }
  } else if (me.teacher) {
    const n = me.teacher.subjects.length;
    tiles.push(el("button", { class: "hero-tile grow half-tile wide", onclick: () => { haptic(); openTab("teacher"); } },
      el("small", {}, "мои предметы"), el("b", {}, n ? countOf(n, "предмет", "предмета", "предметов") : "не выбраны", icon("chevron"))));
  }
  return el("section", { class: "hero profile-hero" }, emblem(),
    el("div", { class: "profile-top" },
      el("button", { class: "avatar-btn", "aria-label": "Фото профиля", onclick: photoSheet },
        avatarNode(name, me.photo), el("span", { class: "avatar-edit" }, icon("camera"))),
      el("button", { class: "grow pname-btn", "aria-label": `ФИО: ${name}. Изменить`, onclick: () => { haptic(); nameSheet(); } },
        el("div", { class: "pname" }, name, el("span", { class: "pname-edit" }, icon("edit"))),
        sub ? el("div", { class: "psub" }, sub) : null)),
    tiles.length ? el("div", { class: "hero-tiles" }, ...tiles) : null,
    halfPanel);
}

function semesterCard() {
  const sem = state.me.semester;
  if (!sem) return null;
  const head = (chip) => el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Семестр"), chip);
  if (!sem.end) {
    return el("section", { class: "panel" }, head(null),
      el("div", { class: "big-line" }, el("b", {}, `${sem.week} неделя`)),
      isAdminRole(state.me.role) ? el("p", { class: "note" }, "Задай дату начала сессии: Я админ → Семестр и ЛК.") : null);
  }
  const left = sem.days_left;
  const end = parseDate(sem.end);
  return el("section", { class: "panel" },
    head(left > 0 ? el("span", { class: "chip" }, `до сессии ${left} ${plural(left, "день", "дня", "дней")}`) : null),
    el("div", { class: "big-line" },
      el("b", {}, `${Math.max(1, Math.min(sem.week, sem.total_weeks))} неделя`), el("span", {}, `из ${sem.total_weeks}`)),
    el("div", { class: "bar" }, el("div", { style: `width:${Math.round(sem.progress * 100)}%` })),
    el("p", { class: "note" }, left > 0 ? `Сессия с ${end.getDate()} ${MONTHS[end.getMonth()]}` : "Сессия уже идёт — удачи! 🍀"));
}

// Своя посещаемость: всего, по предметам и пропуски
function myAttendanceCard() {
  if (!state.me.student) return null;
  const head = el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Моя посещаемость"));
  const card = el("section", { class: "panel" }, head, el("p", { class: "note" }, "Загрузка…"));
  api("/api/attendance/stats").then((st) => {
    if (!st.total) {
      setChildren(card, head, el("p", { class: "note" },
        "Пока не было пар с отметкой. Когда преподаватель или староста откроет отметку, здесь появится статистика."));
      return;
    }
    head.append(el("span", { class: "chip" + (st.rate < 50 ? " low" : "") }, `${st.rate}%`));
    const subjectRow = (x) => el("div", { class: "att-subject" },
      el("span", { class: "grow" }, el("b", {}, x.subject), el("small", {}, `${x.attended} из ${x.total}`)),
      el("span", { class: "rate" + (x.rate < 50 ? " low" : "") }, `${x.rate}%`),
      el("div", { class: "bar" }, el("div", { style: `width:${x.rate}%` })));
    setChildren(card, head,
      el("div", { class: "big-line" }, el("b", {}, `${st.attended} из ${st.total}`),
        el("span", {}, plural(st.total, "пары с отметкой", "пар с отметкой", "пар с отметкой"))),
      el("div", { class: "bar" }, el("div", { style: `width:${st.rate}%` })),
      ...st.subjects.slice(0, 3).map(subjectRow),
      el("button", { class: "link-btn att-more", onclick: () => myAttendanceSheet(st, subjectRow) },
        st.missed.length ? `Все предметы и пропуски (${st.missed.length})` : "Все предметы"));
  }).catch((e) => setChildren(card, head, el("p", { class: "note" }, e.message)));
  return card;
}

function myAttendanceSheet(st, subjectRow) {
  openSheet("Моя посещаемость", `На парах: ${st.attended} из ${st.total} · ${st.rate}%`, (card) => card.append(
    el("div", { class: "att-sheet" },
      el("section", { class: "panel list" },
        el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "По предметам")),
        ...st.subjects.map(subjectRow)),
      st.missed.length ? el("section", { class: "panel list" },
        el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Пропуски"),
          el("span", { class: "chip muted" }, String(st.missed.length))),
        ...st.missed.map((m) => listRow({
          label: `${dateLabel(m.date)}${m.start ? ", " + m.start : ""}`, title: m.subject, hint: m.teacher || undefined,
        }))) : el("p", { class: "note" }, "Пропусков нет 🎉"),
      el("p", { class: "note" }, "Если пропуск отмечен по ошибке, напиши старосте — он может поправить отметку."))));
}

// Почта студента, куратор группы, личный кабинет МПГУ
function universityCard() {
  const me = state.me;
  const rows = [];
  if (me.student?.email) {
    rows.push(listRow({ iconName: "mail", label: "Почта", title: me.student.email, chevron: false,
      onclick: () => copyText(me.student.email, "Почта скопирована") }));
  }
  if (me.curator) {
    const { name, contact } = me.curator;
    const link = contactLink(contact);
    rows.push(listRow({ iconName: "user", label: name && contact ? `Куратор · ${contact}` : "Куратор", title: name || contact,
      chevron: !!link, onclick: contact ? () => (link ? openExternal(link) : copyText(contact)) : undefined }));
  }
  if (me.lk_url) {
    rows.push(listRow({ iconName: "external", title: "Личный кабинет МПГУ", onclick: () => openExternal(me.lk_url) }));
  }
  return rows.length ? el("section", { class: "panel list" }, ...rows) : null;
}

// «Написать админу»: форма раскрывается по нажатию
function contactCard() {
  const me = state.me;
  let topic = "fio";
  const text = el("textarea", { class: "full", rows: 4, maxlength: 1000, placeholder: "Например: в фамилии ошибка, правильно — Иванова" });
  const send = el("button", { class: "btn block", onclick: async () => {
    if (!text.value.trim()) { toast("Напиши, что нужно исправить"); return; }
    send.disabled = true;
    try {
      await api("/api/me/contact", { method: "POST", body: { topic, text: text.value } });
      text.value = "";
      hapticResult("success");
      toast("Отправлено админам ✅ Ответ придёт в «Уведомления»");
    } catch (e) { toast(e.message); }
    finally { send.disabled = false; }
  } }, "Отправить");
  const topics = segmented([["fio", "ФИО"], ["group", "Группа"], ["schedule", "Расписание"], ["other", "Другое"]],
    topic, (v) => (topic = v));
  topics.classList.add("wide");
  const form = el("div", { class: "contact-form", hidden: true },
    topics, text, send,
    me.admin_contact ? el("button", { class: "btn tinted block", onclick: () => {
      const link = contactLink(me.admin_contact);
      if (link) openExternal(link); else copyText(me.admin_contact, "Контакт скопирован");
    } }, `Связаться напрямую: ${me.admin_contact}`) : null,
    el("p", { class: "note" }, "ФИО в списке группы меняет староста или админ. Сообщение уйдёт всем админам "
      + "с твоим ФИО и группой, ответ придёт в «Уведомления»."));
  const row = listRow({ iconName: "chat", title: "Написать админу", hint: "ошибка в ФИО, группе или расписании", onclick: () => {
    form.hidden = !form.hidden;
    row.classList.toggle("open", !form.hidden);
    haptic();
    if (!form.hidden) text.focus();
  } });
  return el("section", { class: "panel list" }, row, form);
}

// Личный код: по нему староста добавляет в группу, а админ назначает роль
function codeCard({ compact = false } = {}) {
  const me = state.me;
  const share = () => shareText(
    `Мой код в приложении «${appName()}»: ${me.code}\n${displayName(me)}`, "Личный код");
  return el("section", { class: "panel code-card" },
    el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Мой личный код")),
    el("button", { class: "personal-code", onclick: () => copyText(me.code, "Код скопирован"), "aria-label": "Личный код " + me.code },
      me.code),
    compact ? null : el("p", { class: "note" }, me.student
      ? "По этому коду админ может назначить тебе роль, а староста — найти тебя в списке."
      : "Покажи код старосте — он добавит тебя в группу. Или введи код группы, если староста его дал."),
    el("div", { class: "pair-btns", style: "margin-top:12px" },
      el("button", { class: "btn small", onclick: share }, "Поделиться"),
      el("button", { class: "btn tinted small", onclick: () => copyText(me.code, "Код скопирован") }, "Копировать")));
}

// «Ввести код»: код группы от старосты, приглашение преподавателя или код главного админа
function enterCodeCard({ open = false, title = "Ввести код", hint = "код группы или приглашение преподавателя" } = {}) {
  const input = el("input", { class: "full code-text", placeholder: "Например, K7M-Q2X", autocomplete: "off",
    autocapitalize: "characters", spellcheck: "false", "aria-label": "Код" });
  const error = el("p", { class: "sheet-error", role: "alert" });
  const send = el("button", { class: "btn block", onclick: submit }, "Применить");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  async function submit() {
    if (!input.value.trim()) { error.textContent = "Введи код"; return; }
    send.disabled = true;
    error.textContent = "";
    try {
      const r = await api("/api/me/code", { method: "POST", body: { code: input.value } });
      hapticResult("success");
      toast(r.message, 4500);
      input.value = "";
      await reloadApp();
    } catch (e) {
      hapticResult("error");
      error.textContent = e.message;
    } finally { send.disabled = false; }
  }
  const form = el("div", { class: "contact-form", hidden: !open },
    input, error, send,
    el("p", { class: "note" }, "Код группы даёт староста. Преподавателю код-приглашение выдаёт главный админ."));
  const row = listRow({ iconName: "hash", title, hint, onclick: () => {
    form.hidden = !form.hidden;
    row.classList.toggle("open", !form.hidden);
    haptic();
    if (!form.hidden) input.focus();
  } });
  if (open) row.classList.add("open");
  return el("section", { class: "panel list" }, row, form);
}

// Своё ФИО — прямо в профиле; у студента меняется и строка в списке группы
function nameSheet() {
  const me = state.me;
  openSheet("ФИО", me.student ? "Так тебя видят староста и преподаватели. Староста получит уведомление о смене."
    : "Так тебя видят в приложении.", (card, close) => {
    const fio = fioFields(displayName(me));
    const error = el("p", { class: "sheet-error", role: "alert" });
    const save = el("button", { class: "btn block", type: "submit" }, "Сохранить");
    card.append(el("form", { class: "sheet-form login-form", onsubmit: async (e) => {
      e.preventDefault();
      error.textContent = fio.problem() || "";
      if (error.textContent) { hapticResult("error"); return; }
      save.disabled = true;
      try {
        const r = await api("/api/me/name", { method: "PUT", body: { full_name: fio.value() } });
        me.full_name = r.full_name;
        if (me.student) me.student.full_name = r.full_name;
        if (me.teacher) me.teacher.full_name = r.full_name;
        hapticResult("success");
        close();
        toast("ФИО изменено");
        renderProfile();
        renderGreeting();
      } catch (err) { error.textContent = err.message; hapticResult("error"); }
      finally { save.disabled = false; }
    } }, ...fio.nodes, error, save));
  });
}

// Аккаунт: пароль, сервер, выход, удаление
function accountCard() {
  const old = el("input", { type: "password", class: "full", placeholder: "Старый пароль", autocomplete: "current-password" });
  const fresh = el("input", { type: "password", class: "full", placeholder: "Новый пароль", autocomplete: "new-password" });
  const rules = passwordRules(fresh);
  const save = el("button", { class: "btn block", onclick: async () => {
    if (rules.problem()) { toast(rules.problem()); hapticResult("error"); return; }
    save.disabled = true;
    try {
      await api("/api/me/password", { method: "PUT", body: { old_password: old.value, new_password: fresh.value } });
      old.value = fresh.value = "";
      form.hidden = true;
      row.classList.remove("open");
      hapticResult("success");
      toast("Пароль изменён. На других устройствах нужно войти заново");
    } catch (e) { toast(e.message); }
    finally { save.disabled = false; }
  } }, "Сменить пароль");
  const form = el("div", { class: "contact-form", hidden: true }, old, fresh, rules.node, save);
  const row = listRow({ iconName: "shield", title: "Пароль", hint: `логин: ${state.me.login}`, onclick: () => {
    form.hidden = !form.hidden;
    row.classList.toggle("open", !form.hidden);
    haptic();
  } });
  const hint = installHint();
  return el("div", { class: "stackv" },
    hint ? el("section", { class: "panel" }, hint) : null,
    el("section", { class: "panel list" },
      el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Аккаунт")),
      row, form,
      NATIVE ? listRow({ iconName: "external", title: "Сервер", hint: serverLabel(), chevron: false }) : null,
      listRow({ iconName: "logout", title: "Выйти", danger: true, chevron: false, onclick: async () => {
        if (!(await confirmDialog("Выйти из аккаунта на этом устройстве?", "Выйти"))) return;
        await logout();
      } }),
      listRow({ iconName: "trash", title: "Удалить аккаунт", danger: true, chevron: false, onclick: deleteAccount })));
}

async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) { /* всё равно выходим */ }
  await setToken(null);
  await clearReminders();
  setUnread(0);
  navigator.serviceWorker?.controller?.postMessage("clear-api-cache");
  showLogin();
}

function deleteAccount() {
  openSheet("Удалить аккаунт?", "Аккаунт и уведомления удалятся. Запись в списке группы и журнал посещаемости останутся.",
    (card, close) => {
      const pass = el("input", { type: "password", class: "full", placeholder: "Пароль для подтверждения",
        autocomplete: "current-password" });
      const error = el("p", { class: "sheet-error", role: "alert" });
      const btn = el("button", { class: "btn block danger-fill", onclick: async () => {
        btn.disabled = true;
        try {
          await api("/api/me/delete", { method: "POST", body: { password: pass.value } });
          close();
          await setToken(null);
          await clearReminders();
          toast("Аккаунт удалён");
          showLogin();
        } catch (e) { error.textContent = e.message; }
        finally { btn.disabled = false; }
      } }, "Удалить навсегда");
      card.append(el("div", { class: "sheet-form" }, pass, error, btn));
    });
}

function renderProfile() {
  setChildren($("profile-body"),
    profileHero(), codeCard({ compact: true }), myAttendanceCard(), semesterCard(), universityCard(), enterCodeCard(),
    contactCard(), accountCard());
}

// --- «Я админ» и «Я староста» ------------------------------------------------------

// Раздел открывается отдельным экраном с кнопкой «Назад».
// bare — разделу не нужна общая белая карточка, у него свои
function block(title, body, onopen, extra = {}) {
  return { title, body, onopen, ...extra };
}

// Откуда открыт раздел: туда же и вернёмся
const SECTION_HOMES = {
  admin: () => ({ box: "admin-body", label: isAdminRole(state.me.role) ? "Я админ" : "Я староста", render: renderAdmin }),
  teacher: () => ({ box: "teacher-body", label: "Я учитель", render: renderTeacher }),
};

// Системная кнопка «Назад» на Android: закрывает шторку или раздел, потом ведёт на «Расписание»
let backHandler = null;
function setBackButton(handler) {
  backHandler = handler;
}

function onHardwareBack() {
  if (backHandler) { backHandler(); return; }
  if (!$("tabs").hidden && currentTab !== "schedule") { openTab("schedule"); return; }
  Plugins.App?.minimizeApp?.().catch(() => Plugins.App?.exitApp?.());
}

function openSection(section, from = "admin") {
  const home = SECTION_HOMES[from]();
  const back = () => { haptic(); home.render(); };
  setChildren($(home.box),
    el("button", { class: "back-btn", onclick: back }, icon("back"), home.label),
    pageHead(section.title),
    ...(section.bare ? [].concat(section.body) : [el("section", { class: "panel" }, section.body)]));
  setBackButton(back);
  window.scrollTo(0, 0);
  section.onopen?.();
}

async function renderAdmin() {
  setBackButton(null);
  const box = $("admin-body");
  const isAdmin = isAdminRole(state.me.role);
  const head = pageHead(isAdmin ? "Я админ" : "Я староста");
  setChildren(box, head, el("p", { class: "note", style: "padding:16px 4px" }, "Загрузка…"));
  let stats = null;
  try {
    [state.groups, state.config, stats] = await Promise.all([
      api("/api/admin/groups"), api("/api/admin/config"), isAdmin ? api("/api/admin/stats") : null]);
  } catch (e) {
    setChildren(box, head, el("p", { class: "note" }, e.message));
    return;
  }
  if (isAdmin) adminDashboard(box, stats);
  else starostaDashboard(box);
}

const statTile = (label, value) => el("div", { class: "hero-tile" }, el("small", {}, label), el("b", { class: "stat" }, String(value)));
const countOf = (n, one, few, many) => `${n} ${plural(n, one, few, many)}`;

function adminDashboard(box, st) {
  const pct = st.students ? Math.round((st.registered * 100) / st.students) : 0;
  const role = ROLE_LABELS[state.me.role];
  const hero = el("section", { class: "hero" },
    el("div", { class: "hero-top" }, el("span", {}, "Регистрации"),
      el("button", { class: "hero-link", onclick: () => { haptic(); openSection(statsBlock()); } }, "по группам", icon("chevron"))),
    el("div", { class: "big-line" }, el("b", {}, `${st.registered} из ${st.students}`), el("span", {}, "студентов в приложении")),
    el("div", { class: "hero-progress" }, el("div", { class: "track" }, el("div", { class: "fill", style: `width:${pct}%` })), `${pct}%`),
    el("div", { class: "hero-tiles three" },
      statTile("без группы", st.without_group), statTile("напоминания", st.reminders), statTile("старосты", st.starostas)));
  const tiles = [
    ["users", "Группы", `${countOf(st.groups.length, "группа", "группы", "групп")} · коды для студентов`, groupsBlock],
    ["userPlus", "Студенты", "добавить по коду, роли, пароли", studentsBlock],
    ["list", "Пары групп", countOf(st.lessons, "пара", "пары", "пар"), lessonsBlock],
    ["upload", "Загрузить xlsx", "расписание или списки", importBlock],
    ["bell", "Объявление", "сообщение группам", announceBlock],
    ["clock", "Семестр и ЛК", "звонки, сессия, ссылка", configBlock],
    ["user", "Кураторы", "контакт для каждой группы", curatorBlock],
    ["checkCircle", "Посещаемость", "журнал групп, Excel", () => attendanceBlock()],
    ["shield", "Команда", `${countOf(st.admins, "админ", "админа", "админов")} · ${countOf(st.teachers, "преподаватель", "преподавателя", "преподавателей")}`, teamBlock],
  ];
  setChildren(box,
    pageHead("Я админ", `${role[0].toUpperCase()}${role.slice(1)} · ${countOf(state.groups.length, "группа", "группы", "групп")}`),
    hero,
    el("div", { class: "action-grid" }, ...tiles.map(([ic, title, sub, make]) => el("button", {
      class: "action-tile", onclick: () => { haptic(); openSection(make()); },
    }, iconTile(ic), el("span", {}, el("b", {}, title), el("small", {}, sub))))));
}

async function starostaDashboard(box) {
  const group = state.me.student.group;
  const sem = state.me.semester;
  const parity = sem.week % 2 ? "odd" : "even";
  const quick = [
    ["userPlus", "Добавить по коду", () => addByCodeSheet(group)],
    ["bell", "Объявление группе", () => announceSheet([group])],
    ["calPlus", "Добавить пару", () => openSection(lessonsBlock({ startAdd: true }))],
    ["user", "Куратор группы", () => openSection(curatorBlock())],
  ];
  const lessonsCard = el("section", { class: "panel list" },
    el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Пары группы"),
      el("span", { class: "hint-text small" }, `${sem.week} неделя · ${PARITY[parity]}`)),
    el("p", { class: "note list-pad" }, "Загрузка…"));
  const studentsCard = el("section", { class: "panel list" },
    el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Студенты группы")),
    el("p", { class: "note list-pad" }, "Загрузка…"));
  const changes = changesPanel(() => group);
  const changesCard = el("section", { class: "panel pad" },
    el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Разовые изменения")),
    el("p", { class: "note" }, "Отмена, другая аудитория или время, перенос — только на один день, без повторений."),
    changes.node);
  setChildren(box,
    pageHead("Я староста", formatGroup(group), true),
    el("section", { class: "hero" },
      el("div", { class: "hero-top" }, el("span", {}, "Быстрые действия")),
      el("div", { class: "quick-grid two" }, ...quick.map(([ic, label, open]) => el("button", {
        class: "quick-tile", onclick: () => { haptic(); open(); },
      }, icon(ic), el("span", {}, label))))),
    groupCodeCard(group),
    attendanceCard(group),
    lessonsCard, changesCard, studentsCard);

  let lessons, students;
  try {
    [lessons, students] = await Promise.all([
      api("/api/admin/lessons?group=" + encodeURIComponent(group)), api("/api/admin/students")]);
  } catch (e) {
    setChildren(lessonsCard, el("p", { class: "note list-pad" }, e.message));
    return;
  }
  // Сколько пар в каждый день этой недели (с учётом чётности и своих недель)
  const perDay = WEEKDAYS.map((_, i) => lessons.filter((l) => l.weekday === i && lessonOnWeek(l, sem.week)).length);
  const days = perDay.map((n, i) => n && listRow({
    title: WEEKDAYS[i], value: countOf(n, "пара", "пары", "пар"),
    onclick: () => openSection(lessonsBlock({ weekday: i })),
  })).filter(Boolean);
  setChildren(lessonsCard, lessonsCard.firstChild,
    ...(days.length ? days : [el("p", { class: "note list-pad" }, "Пар пока нет — нажми «Добавить пару».")]));

  // Сначала старосты, остальные — по алфавиту; показываем несколько, остальные — в разделе
  const sorted = [...students].sort((a, b) => (b.role === "starosta") - (a.role === "starosta") || a.full_name.localeCompare(b.full_name, "ru"));
  const head = studentsCard.firstChild;
  const inApp = students.filter((s) => s.linked).length;
  head.append(el("span", { class: "chip" }, `${inApp} из ${students.length} в приложении`));
  setChildren(studentsCard, head,
    ...sorted.slice(0, 3).map((s) => listRow({
      title: s.full_name, hint: s.role === "starosta" ? "староста" : s.linked ? undefined : "ещё не в приложении",
      onclick: () => openSection(studentsBlock({ openId: s.id })),
    })),
    listRow({ title: students.length ? `Все студенты (${students.length})` : "Добавить студентов",
      onclick: () => (students.length ? openSection(studentsBlock()) : addByCodeSheet(group)) }));
}

// Код группы: студенты вводят его при регистрации или в профиле и сразу попадают в группу
function groupCodeCard(group) {
  const code = el("button", { class: "personal-code", "aria-label": "Код группы" }, "···-···");
  let value = null;
  const load = async (regenerate = false) => {
    try {
      const r = regenerate
        ? await api("/api/admin/group-code", { method: "POST", body: { group } })
        : await api("/api/admin/group-code?group=" + encodeURIComponent(group));
      value = r.code;
      code.textContent = value;
    } catch (e) { code.textContent = "—"; toast(e.message); }
  };
  code.onclick = () => value && copyText(value, "Код группы скопирован");
  const share = () => value && shareText(
    `Группа ${group} в приложении «${appName()}».\nУстанови приложение, зарегистрируйся и введи код группы: ${value}`,
    "Код группы");
  const card = el("section", { class: "panel code-card" },
    el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Код группы"),
      el("button", { class: "link-btn", onclick: async () => {
        if (!(await confirmDialog("Сделать новый код? Старый перестанет работать — те, кто уже в группе, останутся.", "Новый код"))) return;
        await load(true);
        hapticResult("success");
        toast("Новый код готов");
      } }, "Новый код")),
    code,
    el("p", { class: "note" }, "Отправь код в чат группы: студенты введут его при регистрации или в профиле и сразу "
      + "окажутся в группе. О каждом новом придёт уведомление. Без кода — добавь студента по его личному коду."),
    el("div", { class: "pair-btns", style: "margin-top:12px" },
      el("button", { class: "btn small", onclick: share }, "Поделиться"),
      el("button", { class: "btn tinted small", onclick: () => value && copyText(value, "Код группы скопирован") }, "Копировать")));
  load();
  return card;
}

// Добавить студента по личному коду: сначала покажем, кто это, потом добавим
function addByCodeSheet(group, onDone) {
  openSheet("Добавить по коду", "Личный код — в профиле у студента, 6 символов.", (card, close) => {
    const groupSel = !group && state.groups.length
      ? el("select", { class: "full", "aria-label": "Группа" }, ...state.groups.map((g) => el("option", { value: g }, formatGroup(g))))
      : null;
    const input = el("input", { class: "full code-text", placeholder: "ABC-234", autocomplete: "off",
      autocapitalize: "characters", spellcheck: "false", "aria-label": "Личный код студента" });
    const error = el("p", { class: "sheet-error", role: "alert" });
    const found = el("div");
    const btn = el("button", { class: "btn block", onclick: () => (person ? add() : look()) }, "Найти");
    let person = null;
    input.addEventListener("input", () => { person = null; setChildren(found); btn.textContent = "Найти"; error.textContent = ""; });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") btn.click(); });
    async function look() {
      if (!input.value.trim()) { error.textContent = "Введи код"; return; }
      btn.disabled = true;
      try {
        person = await api("/api/admin/lookup?code=" + encodeURIComponent(input.value));
        const target = group || groupSel?.value;
        const where = person.group ? (person.group === target ? "уже в этой группе" : `сейчас в группе ${person.group}`)
          : "пока без группы";
        setChildren(found, el("div", { class: "found-person" },
          avatarNode(person.full_name, person.photo), el("div", { class: "grow" }, el("b", {}, person.full_name),
            el("small", {}, [where, person.teacher && "преподаватель"].filter(Boolean).join(" · ")))));
        btn.textContent = person.group === target ? "Готово" : person.group ? "Перевести в группу" : "Добавить в группу";
      } catch (e) { error.textContent = e.message; person = null; hapticResult("error"); }
      finally { btn.disabled = false; }
    }
    async function add() {
      const target = group || groupSel?.value;
      if (person.group === target) { close(); return; }
      btn.disabled = true;
      try {
        const r = await api("/api/admin/members", { method: "POST", body: { code: input.value, group: target } });
        hapticResult("success");
        close();
        toast(r.status === "moved" ? `${r.full_name} — переведён в ${formatGroup(target)}` : `${r.full_name} — в группе ✅`);
        onDone ? onDone() : renderAdmin();
      } catch (e) { error.textContent = e.message; hapticResult("error"); }
      finally { btn.disabled = false; }
    }
    card.append(el("div", { class: "sheet-form" }, groupSel, input, found, error, btn));
  });
}

function announceSheet(groups) {
  textSheet({
    title: "Объявление", sub: groups.length === 1 ? `Придёт всем в группе ${formatGroup(groups[0])}` : `Придёт ${countOf(groups.length, "группе", "группам", "группам")}`,
    placeholder: "Например: завтра первая пара в 305 аудитории", button: "Отправить",
    done: (r) => `Отправлено: ${countOf(r.delivered, "получатель", "получателя", "получателей")}`,
    send: (text) => api("/api/announce", { method: "POST", body: { groups, text } }),
  });
}

// Админ: объявление нескольким группам
function announceBlock() {
  const chosen = new Set();
  const chips = el("div", { class: "choices wrap" });
  const draw = () => setChildren(chips, ...state.groups.map((g) => el("button", {
    class: "choice" + (chosen.has(g) ? " on" : ""), "aria-pressed": String(chosen.has(g)),
    onclick: () => { if (chosen.has(g)) chosen.delete(g); else chosen.add(g); haptic(); draw(); },
  }, shortGroup(g))));
  draw();
  return block("Объявление", el("div", { class: "stack" },
    el("p", { class: "note", style: "margin:0" }, "Выбери группы — сообщение придёт всем их студентам в «Уведомления»."),
    el("div", { class: "inline" },
      el("button", { class: "btn tinted small", onclick: () => { state.groups.forEach((g) => chosen.add(g)); draw(); } }, "Все"),
      el("button", { class: "btn tinted small", onclick: () => { chosen.clear(); draw(); } }, "Никого")),
    chips,
    el("button", { class: "btn", onclick: () => (chosen.size ? announceSheet([...chosen].sort()) : toast("Выбери хотя бы одну группу")) },
      "Написать объявление")));
}

// Админ: группы, их коды; создать и удалить пустую
function groupsBlock() {
  const list = el("div");
  const name = el("input", { class: "grow", placeholder: "Название, например БИА 1 ПОДГРУППА", "aria-label": "Новая группа" });
  const load = async () => {
    try { state.groups = await api("/api/admin/groups"); } catch (e) { setChildren(list, errorNote(e)); return; }
    const st = await api("/api/admin/stats").catch(() => null);
    const counts = Object.fromEntries((st?.groups || []).map((g) => [g.group, g]));
    setChildren(list, ...state.groups.map((g) => {
      const c = counts[g];
      const codeBtn = el("button", { class: "btn tinted small" }, "Код");
      codeBtn.onclick = async () => {
        try {
          const r = await api("/api/admin/group-code?group=" + encodeURIComponent(g));
          shareText(`Группа ${g} в приложении «${appName()}». Код группы: ${r.code}`, "Код группы");
        } catch (e) { toast(e.message); }
      };
      const remove = c && !c.students ? el("button", { class: "icon-btn danger", "aria-label": "Удалить группу", onclick: async () => {
        if (!(await confirmDialog(`Удалить пустую группу «${g}»?`, "Удалить"))) return;
        try { await api("/api/admin/groups/" + encodeURIComponent(g), { method: "DELETE" }); toast("Удалена"); load(); }
        catch (e) { toast(e.message); }
      } }, icon("trash")) : null;
      return el("div", { class: "item" },
        el("div", { class: "grow" }, el("div", {}, formatGroup(g)),
          el("div", { class: "sub" }, c ? `в приложении ${c.registered} из ${c.students}` : "")),
        codeBtn,
        el("button", { class: "btn tinted small", onclick: () => addByCodeSheet(g, load) }, "+ студент"),
        remove);
    }), state.groups.length ? null : el("p", { class: "note" }, "Групп пока нет — создай первую или загрузи xlsx."));
  };
  const create = el("button", { class: "btn small", onclick: async () => {
    if (!name.value.trim()) { toast("Напиши название группы"); return; }
    try {
      const r = await api("/api/admin/groups", { method: "POST", body: { name: name.value } });
      name.value = "";
      hapticResult("success");
      toast(`Группа создана. Код: ${r.code}`, 4000);
      load();
    } catch (e) { toast(e.message); }
  } }, "Создать");
  return block("Группы", el("div", { class: "stack" },
    el("p", { class: "note", style: "margin:0" }, "У каждой группы свой код: студенты вводят его и сразу попадают в группу. "
      + "Старосту назначь в «Студенты» → студент → Роль."),
    el("div", { class: "inline" }, name, create),
    list), load);
}

function statsBlock() {
  const body = el("div", { class: "stack" }, el("p", { class: "note" }, "Загрузка…"));
  const load = async () => {
    let st;
    try { st = await api("/api/admin/stats"); }
    catch (e) { setChildren(body, el("p", { class: "note" }, e.message)); return; }
    const line = (label, value) => el("div", { class: "item" }, el("span", { class: "grow" }, label), el("b", {}, value));
    const missing = st.groups.filter((g) => g.missing.length);
    setChildren(body,
      line("Аккаунтов в приложении", st.accounts),
      line("Ещё не в группе", st.without_group),
      line("Напоминания перед парой", st.reminders),
      line("Ежедневная сводка", st.digest),
      st.half_unset ? line("Не выбрали половину", st.half_unset) : null,
      line("Админов · старост · преподавателей", `${st.admins} · ${st.starostas} · ${st.teachers}`),
      line("Пар в расписании", st.lessons),
      el("div", { class: "weekday-title" }, "По группам"),
      ...st.groups.map((g) => el("div", { class: "item" },
        el("span", { class: "dot" + (g.students && g.registered === g.students ? " on" : "") }),
        el("span", { class: "grow" }, formatGroup(g.group)),
        el("span", { class: "hint-text" }, `${g.registered}/${g.students}`))),
      missing.length ? el("div", { class: "weekday-title" }, "Есть в списке, но ещё не в приложении") : null,
      ...missing.map((g) => el("div", { class: "item" }, el("div", { class: "grow" },
        el("div", {}, formatGroup(g.group)), el("div", { class: "sub" }, g.missing.join(", "))))));
  };
  return block("Статистика", body, load);
}

function importBlock() {
  const file = el("input", { type: "file", accept: ".xlsx", class: "full" });
  const out = el("div");
  const btn = el("button", { class: "btn", onclick: async () => {
    if (!file.files[0]) return toast("Выбери файл");
    const form = new FormData();
    form.append("file", file.files[0]);
    btn.disabled = true;
    try {
      const r = await api("/api/admin/import", { method: "POST", form });
      setChildren(out, el("pre", { class: "result" }, r.message));
      state.groups = await api("/api/admin/groups");
      haptic("medium");
    } catch (e) {
      setChildren(out, el("pre", { class: "result" }, "Ошибка: " + e.message));
    } finally {
      btn.disabled = false;
    }
  } }, "Загрузить");
  return block("Загрузить xlsx", el("div", { class: "stack" },
    el("p", { class: "note", style: "margin:0" },
      "Расписание или список студентов — тип определится по заголовкам. Расписание группы из файла " +
      "заменяется целиком, студенты группы получат уведомление. Из списка удаляются те, кого нет в файле, — "
      + "кроме тех, кто уже в приложении."),
    file, btn, out));
}

function configBlock() {
  const start = el("input", { type: "date", value: state.config.semester_start });
  const end = el("input", { type: "date", value: state.config.semester_end || "" });
  const lk = el("input", { type: "url", inputmode: "url", class: "full", value: state.config.lk_url || "", placeholder: "https://…" });
  const bells = state.config.bells.map((b) => ({ ...b }));
  const list = el("div");
  const draw = () => setChildren(list, ...bells.map((b, i) => el("div", { class: "item" },
    el("span", { class: "num" }, i + 1),
    el("input", { type: "time", value: b.start, onchange: (e) => (b.start = e.target.value) }),
    el("input", { type: "time", value: b.end, onchange: (e) => (b.end = e.target.value) }),
    el("button", { class: "icon-btn danger", "aria-label": "Удалить", onclick: () => { bells.splice(i, 1); draw(); } }, icon("trash")))));
  draw();
  const save = el("button", { class: "btn", onclick: async () => {
    try {
      const body = { semester_start: start.value, semester_end: end.value || null, bells, lk_url: lk.value.trim() || null };
      await api("/api/admin/config", { method: "PUT", body });
      state.config = body;
      const fresh = await api("/api/me");
      state.me.semester = fresh.semester;
      state.me.lk_url = fresh.lk_url;
      toast("Сохранено");
      state.week = null;
    } catch (e) { toast(e.message); }
  } }, "Сохранить");
  return block("Семестр и ЛК", el("div", { class: "stack" },
    el("div", { class: "inline" }, el("span", { class: "grow" }, "Начало семестра"), start),
    el("p", { class: "note", style: "margin:0" }, "Неделя с этой датой — 1-я, нечётная."),
    el("div", { class: "inline" }, el("span", { class: "grow" }, "Начало сессии"), end),
    el("p", { class: "note", style: "margin:0" }, "Для прогресса семестра в профиле студентов."),
    el("div", { class: "weekday-title" }, "Личный кабинет МПГУ"),
    lk,
    el("p", { class: "note", style: "margin:0" }, "Ссылка для кнопки «Личный кабинет МПГУ» в профиле. Пусто — кнопки нет."),
    el("div", { class: "weekday-title" }, "Звонки"),
    list,
    el("div", { class: "inline" },
      el("button", { class: "btn tinted", onclick: () => { bells.push({ start: "", end: "" }); draw(); } }, "Добавить пару"),
      save)));
}

function lessonsBlock(opts = {}) {
  const select = el("select", { class: "full" }, ...state.groups.map((g) => el("option", { value: g }, g)));
  const list = el("div");
  const formBox = el("div");
  let first = true;  // параметры открытия срабатывают один раз

  async function reload() {
    if (!select.value) { setChildren(list, el("p", { class: "note" }, "Групп пока нет — загрузи xlsx.")); return; }
    let lessons;
    try { lessons = await api("/api/admin/lessons?group=" + encodeURIComponent(select.value)); }
    catch (e) { setChildren(list, el("p", { class: "note" }, e.message)); return; }
    const nodes = [];
    let lastDay = -1;
    for (const l of lessons) {
      if (l.weekday !== lastDay) {
        nodes.push(el("div", { class: "weekday-title", "data-wd": l.weekday }, WEEKDAYS[l.weekday]));
        lastDay = l.weekday;
      }
      nodes.push(el("div", { class: "item" },
        el("div", { class: "grow" },
          el("div", {}, `${l.start}  ${l.subject}`),
          el("div", { class: "sub" }, [l.week_label, l.kind, l.room, l.teacher, l.half && `${l.half}-я половина`]
            .filter(Boolean).join(" · "))),
        el("button", { class: "icon-btn", "aria-label": "Изменить", onclick: () => openForm(l) }, icon("edit")),
        el("button", { class: "icon-btn danger", "aria-label": "Удалить", onclick: async () => {
          if (!(await confirmDialog(`Удалить «${l.subject}»?`))) return;
          try { await api(`/api/admin/lessons/${l.id}`, { method: "DELETE" }); state.week = null; reload(); }
          catch (e) { toast(e.message); }
        } }, icon("trash"))));
    }
    setChildren(list, ...(nodes.length ? nodes : [el("p", { class: "note" }, "Пар нет")]));
    if (first) {
      first = false;
      if (opts.startAdd) openForm(null);
      else if (opts.weekday !== undefined) list.querySelector(`[data-wd="${opts.weekday}"]`)?.scrollIntoView({ block: "start" });
    }
  }

  function openForm(lesson) {
    const l = lesson || { group: select.value, weekday: 0, week: "every", weeks: null, pair_num: null, start: "", end: "",
      subject: "", kind: "", room: "", teacher: "", half: null };
    const nowWeek = state.me.semester?.week || 1;
    const f = {
      weekday: el("select", {}, ...WEEKDAYS.map((d, i) => el("option", { value: i, selected: l.weekday === i }, d))),
      week: el("select", {}, ...[["every", "каждую неделю"], ["odd", "по нечётным"], ["even", "по чётным"],
        ["custom", "свои недели…"]].map(([k, v]) => el("option", { value: k, selected: l.week === k }, v))),
      weeks: el("input", { value: l.weeks || "", placeholder: "1-4, 6, 9 или 2/3", autocomplete: "off" }),
      pair: el("select", {}, el("option", { value: "" }, "—"),
        ...state.config.bells.map((b, i) => el("option", { value: i + 1, selected: l.pair_num === i + 1 },
          `${i + 1} (${b.start}–${b.end})`))),
      start: el("input", { type: "time", value: l.start }),
      end: el("input", { type: "time", value: l.end }),
      subject: el("input", { value: l.subject, placeholder: "Название" }),
      kind: el("input", { value: l.kind, list: "kinds", placeholder: "лекция" }),
      room: el("input", { value: l.room }),
      teacher: el("input", { value: l.teacher }),
      half: el("select", {}, el("option", { value: "" }, "вся группа"),
        el("option", { value: 1, selected: l.half === 1 }, "1-я половина"),
        el("option", { value: 2, selected: l.half === 2 }, "2-я половина")),
    };
    f.pair.onchange = () => {
      const b = state.config.bells[Number(f.pair.value) - 1];
      if (b) { f.start.value = b.start; f.end.value = b.end; }
    };
    // Свои недели: номера через запятую, диапазоны и «раз в N недель» с текущей
    const weeksBox = el("div", { class: "span2 weeks-box stack tight" },
      el("span", { class: "field-label" }, "Какие недели"), f.weeks,
      el("div", { class: "chips" }, el("span", { class: "field-hint" }, `С ${nowWeek}-й недели:`),
        ...[2, 3, 4].map((n) => el("button", { class: "chip-btn", type: "button", onclick: () => {
          f.weeks.value = `${nowWeek}/${n}`;
          haptic();
        } }, `раз в ${n} нед.`))),
      el("span", { class: "field-hint" }, `Сейчас ${nowWeek}-я неделя. Примеры: «1-8» — первые 8 недель, «3, 7, 11», `
        + "«2/3» — со 2-й каждую 3-ю, «1-16/2» — с 1-й по 16-ю через одну"));
    const syncWeeks = () => { weeksBox.hidden = f.week.value !== "custom"; };
    f.week.onchange = () => { syncWeeks(); if (f.week.value === "custom") f.weeks.focus(); };
    syncWeeks();
    const field = (label, input, span) => el("label", { class: span ? "span2" : "" }, label, input);
    const save = el("button", { class: "btn", onclick: async () => {
      const body = {
        group: l.group, weekday: Number(f.weekday.value), week: f.week.value,
        weeks: f.week.value === "custom" ? f.weeks.value : null,
        pair_num: f.pair.value ? Number(f.pair.value) : null,
        start: f.start.value, end: f.end.value, subject: f.subject.value,
        kind: f.kind.value, room: f.room.value, teacher: f.teacher.value,
        half: f.half.value ? Number(f.half.value) : null,
      };
      try {
        if (lesson) await api(`/api/admin/lessons/${lesson.id}`, { method: "PUT", body });
        else await api("/api/admin/lessons", { method: "POST", body });
        setChildren(formBox);
        toast("Сохранено");
        state.week = null;
        reload();
      } catch (e) { toast(e.message); }
    } }, "Сохранить");
    setChildren(formBox, el("div", { class: "form" },
      el("h3", {}, lesson ? "Изменить пару" : "Новая пара"),
      el("datalist", { id: "kinds" }, ...KINDS.map((k) => el("option", { value: k }))),
      el("div", { class: "form-grid" },
        field("Предмет", f.subject, true),
        field("День", f.weekday), field("Повторять", f.week), weeksBox,
        field("№ пары", f.pair), field("Тип", f.kind),
        field("Начало", f.start), field("Конец", f.end),
        field("Аудитория", f.room), field("Половина", f.half),
        field("Преподаватель", f.teacher, true)),
      el("div", { class: "inline", style: "margin-top:14px" }, save,
        el("button", { class: "btn tinted", onclick: () => setChildren(formBox) }, "Отмена"))));
    formBox.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const changes = changesPanel(() => select.value);
  select.onchange = () => { setChildren(formBox); reload(); changes.reload(); };
  select.hidden = state.groups.length === 1;
  return block(isAdminRole(state.me.role) ? "Пары групп" : "Пары группы", el("div", { class: "stack" },
    select,
    el("button", { class: "btn tinted", onclick: () => select.value && openForm(null) }, "Добавить пару"),
    formBox, list,
    el("h3", { class: "section-title" }, "Разовые изменения"),
    el("p", { class: "note" }, "Только на один день, без повторений: отмена, другая аудитория, перенос, разовая пара."),
    changes.node), () => { reload(); changes.reload(); });
}

function curatorBlock() {
  const select = el("select", { class: "full", "aria-label": "Группа" },
    ...state.groups.map((g) => el("option", { value: g }, formatGroup(g))));
  select.hidden = state.groups.length <= 1;
  const name = el("input", { placeholder: "Фамилия Имя Отчество", "aria-label": "Куратор" });
  const contact = el("input", { placeholder: "@ник, телефон или почта", "aria-label": "Связь с куратором" });
  const load = async () => {
    if (!select.value) return;
    try {
      const r = await api("/api/admin/group-info?group=" + encodeURIComponent(select.value));
      name.value = r.curator;
      contact.value = r.contact;
    } catch (e) { toast(e.message); }
  };
  select.onchange = load;
  const save = el("button", { class: "btn", onclick: async () => {
    if (!select.value) { toast("Групп пока нет — загрузи xlsx"); return; }
    try {
      const r = await api("/api/admin/group-info", { method: "PUT",
        body: { group: select.value, curator: name.value, contact: contact.value } });
      if (r.group === state.me.student?.group) {
        state.me.curator = r.curator || r.contact ? { name: r.curator, contact: r.contact } : null;
      }
      haptic("medium");
      toast("Сохранено — студенты увидят в профиле");
    } catch (e) { toast(e.message); }
  } }, "Сохранить");
  const field = (label, input) => el("div", { class: "stack tight" }, el("span", { class: "field-label" }, label), input);
  return block(isAdminRole(state.me.role) ? "Кураторы" : "Куратор группы", el("div", { class: "stack" },
    select,
    field("Куратор", name),
    field("Как связаться", contact),
    el("p", { class: "note", style: "margin:0" },
      "Студенты группы увидят куратора в профиле. По @нику откроется чат в Telegram, ссылка — откроется, телефон и почта копируются."),
    save), load);
}

function studentsBlock(opts = {}) {
  const me = state.me;
  const isAdmin = isAdminRole(me.role);
  const isOwner = me.role === "owner";
  const search = el("input", { class: "full", type: "search", placeholder: isAdmin ? "Поиск по ФИО, группе или коду" : "Поиск по ФИО" });
  const list = el("div");
  const counter = el("p", { class: "note", style: "margin:0" });
  let students = [];
  let openId = opts.openId ?? null;  // раскрытая строка с действиями
  let first = true;

  const load = async () => {
    try { students = await api("/api/admin/students"); draw(); }
    catch (e) { setChildren(list, el("p", { class: "note" }, e.message)); return; }
    if (first) {
      first = false;
      if (opts.startAdd) openAdd();
      else if (openId !== null) list.querySelector(".item-btn.open")?.scrollIntoView({ block: "center" });
    }
  };
  const run = async (request, done) => {
    try { await request(); toast(done); haptic("medium"); await load(); }
    catch (e) { toast(e.message); }
  };

  // Строка в списке без аккаунта: когда студент вступит в группу с таким же ФИО, привяжется к ней
  const addBox = el("div");
  const openAdd = () => {
    if (!state.groups.length) { toast("Групп пока нет — создай группу или загрузи xlsx"); return; }
    const name = el("input", { placeholder: "Фамилия Имя Отчество", autocomplete: "off" });
    const groupSel = isAdmin && state.groups.length > 1
      ? el("select", {}, ...state.groups.map((g) => el("option", { value: g }, formatGroup(g)))) : null;
    const save = el("button", { class: "btn", onclick: async () => {
      if (!name.value.trim()) { toast("Напиши ФИО"); return; }
      save.disabled = true;
      await run(() => api("/api/admin/students", { method: "POST", body: {
        full_name: name.value, group: groupSel ? groupSel.value : state.groups[0] } }),
      "Добавлен в список");
      save.disabled = false;
      name.value = "";
      name.focus();
    } }, "Добавить");
    setChildren(addBox, el("div", { class: "form stack" },
      el("h3", {}, "В список по ФИО"),
      name, groupSel,
      el("p", { class: "note" }, "Когда студент зарегистрируется с таким же ФИО и вступит в группу, он привяжется к этой "
        + "строке — вместе с почтой и посещаемостью. Обычно проще добавить по личному коду."),
      el("div", { class: "inline" }, save, el("button", { class: "btn tinted", onclick: () => setChildren(addBox) }, "Готово"))));
    name.focus();
  };

  // Роль: студент / староста / админ (админа назначает только главный)
  const roleControl = (s) => {
    if (!isAdmin || !s.linked || s.user_id === me.id) return null;
    if (s.role === "owner") return el("p", { class: "note" }, "Главный админ");
    if (s.role === "admin" && !isOwner) return el("p", { class: "note" }, "Админ — роль меняет главный админ");
    const options = [["user", "Студент"], ["starosta", "Староста"]];
    if (isOwner) options.push(["admin", "Админ"]);
    const seg = segmented(options, s.role, (role) => run(
      () => api(`/api/admin/users/${s.user_id}/role`, { method: "PUT", body: { role } }),
      role === "starosta" ? "Назначен старостой — ему пришло уведомление" : role === "admin" ? "Назначен админом" : "Теперь просто студент"));
    seg.classList.add("wide");
    return el("div", { class: "stack tight" }, el("span", { class: "field-label" }, "Роль"), seg);
  };

  // Забыл пароль: временный пароль показываем тому, кто сбросил, — продиктовать студенту
  const resetPassword = async (s) => {
    if (!(await confirmDialog(`Выдать «${s.full_name}» временный пароль? Старый перестанет работать.`, "Выдать"))) return;
    try {
      const r = await api(`/api/admin/users/${s.user_id}/password`, { method: "POST" });
      openSheet("Временный пароль", `${s.full_name} · логин ${r.login}`, (card) => {
        card.append(el("div", { class: "sheet-form" },
          el("button", { class: "personal-code", onclick: () => copyText(r.password, "Пароль скопирован") }, r.password),
          el("p", { class: "note" }, "Передай студенту лично. Пароль показан один раз — потом его можно сменить в профиле.")));
      });
    } catch (e) { toast(e.message); }
  };

  const panel = (s) => {
    const name = el("input", { value: s.full_name, "aria-label": "ФИО" });
    const email = el("input", { type: "email", inputmode: "email", value: s.email || "", placeholder: "name@mpgu.su", "aria-label": "Почта" });
    const groupSel = isAdmin ? el("select", { "aria-label": "Группа" },
      ...state.groups.map((g) => el("option", { value: g, selected: g === s.group }, formatGroup(g)))) : null;
    const save = el("button", { class: "btn small", onclick: () => {
      const body = { full_name: name.value, email: email.value.trim() };
      if (groupSel) body.group = groupSel.value;
      run(() => api(`/api/admin/students/${s.id}`, { method: "PUT", body }), "Сохранено");
    } }, "Сохранить");
    const remove = el("button", { class: "btn tinted small danger-text", onclick: async () => {
      const q = s.linked ? `Убрать «${s.full_name}» из группы? Аккаунт останется, но уже без группы.`
        : `Удалить «${s.full_name}» из списка?`;
      if (!(await confirmDialog(q, "Убрать"))) return;
      openId = null;
      run(() => api(`/api/admin/students/${s.id}`, { method: "DELETE" }), s.linked ? "Убран из группы" : "Удалён из списка");
    } }, s.linked ? "Убрать из группы" : "Удалить");
    const canReset = s.linked && s.user_id !== me.id && (isAdmin ? s.role !== "owner" && (isOwner || s.role !== "admin") : s.role === "user");
    const reset = canReset ? el("button", { class: "btn tinted small", onclick: () => resetPassword(s) }, "Сбросить пароль") : null;
    const dropPhoto = s.photo && s.user_id !== me.id ? el("button", { class: "btn tinted small", onclick: async () => {
      if (!(await confirmDialog(`Убрать фото у «${s.full_name}»? Ему придёт уведомление.`, "Убрать"))) return;
      run(() => api(`/api/admin/users/${s.user_id}/photo`, { method: "DELETE" }), "Фото убрано");
    } }, "Убрать фото") : null;
    const unlink = isAdmin && s.linked && s.user_id !== me.id ? el("button", { class: "btn tinted small", onclick: async () => {
      if (!(await confirmDialog(`Отвязать аккаунт от строки «${s.full_name}»? Строка в списке останется.`, "Отвязать"))) return;
      run(() => api(`/api/admin/students/${s.id}/unlink`, { method: "POST" }), "Аккаунт отвязан");
    } }, "Отвязать аккаунт") : null;
    return el("div", { class: "item-panel form stack" },
      s.photo ? el("div", { class: "photo-preview" }, avatarNode(s.full_name, s.photo)) : null,
      s.linked ? el("p", { class: "note" }, `В приложении · личный код ${s.code}${s.login ? " · логин " + s.login : ""}`)
        : el("p", { class: "note" }, "Ещё не в приложении: строка из списка, к ней никто не привязан."),
      el("div", { class: "stack tight" }, el("span", { class: "field-label" }, "ФИО"), name),
      el("div", { class: "stack tight" }, el("span", { class: "field-label" }, "Почта"), email),
      groupSel ? el("div", { class: "stack tight" }, el("span", { class: "field-label" }, "Группа"), groupSel) : null,
      roleControl(s),
      el("div", { class: "inline" }, save, reset, dropPhoto, remove, unlink));
  };

  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = students.filter((s) => !q || [s.full_name, s.group, s.code].join(" ").toLowerCase().includes(q));
    const inApp = students.filter((s) => s.linked).length;
    counter.textContent = isAdmin
      ? `В приложении ${inApp} из ${students.length}`
      : `В группе ${countOf(students.length, "студент", "студента", "студентов")}, в приложении ${inApp}`;
    setChildren(list, ...shown.flatMap((s) => {
      const open = openId === s.id;
      const tag = s.role && s.role !== "user" ? el("span", { class: "tag" }, ROLE_LABELS[s.role]) : null;
      const sub = [isAdmin && formatGroup(s.group), s.linked ? s.code : "не в приложении", s.half && `${s.half}-я половина`]
        .filter(Boolean).join(" · ");
      const head = el("button", { class: "item item-btn" + (open ? " open" : ""), "aria-expanded": String(open), onclick: () => {
        openId = open ? null : s.id;
        haptic();
        draw();
      } },
        s.photo ? el("span", { class: "mini-avatar" }, avatarNode(s.full_name, s.photo))
          : el("span", { class: "dot" + (s.linked ? " on" : "") }),
        el("div", { class: "grow" }, el("div", {}, s.full_name), el("div", { class: "sub" }, tag, sub)),
        el("span", { class: "chev-icon" }, icon("chevron")));
      return open ? [head, panel(s)] : [head];
    }));
    if (!shown.length) setChildren(list, el("p", { class: "note" }, students.length ? "Никого не нашёл" : "Список пуст"));
  };
  search.oninput = draw;
  const ownGroup = isAdmin ? null : me.student.group;
  return block(isAdmin ? "Студенты" : "Студенты группы", el("div", { class: "stack" },
    el("button", { class: "btn block", onclick: () => addByCodeSheet(ownGroup, load) }, "Добавить по личному коду"),
    el("button", { class: "link-btn", onclick: openAdd }, "Добавить в список по ФИО, без аккаунта"),
    addBox, search, counter, list), load);
}

// «Команда»: админы, старосты и преподаватели. Админов меняет главный, старост — любой админ,
// коды для преподавателей выдаёт главный
function teamBlock() {
  const isOwner = state.me.role === "owner";
  const list = el("div");
  const inviteBox = el("div");
  const person = (title, sub, ...buttons) => el("div", { class: "item" },
    el("div", { class: "grow" }, el("div", {}, title), el("div", { class: "sub" }, sub)), ...buttons);
  const action = (label, question, request) => el("button", { class: "btn tinted small", onclick: async () => {
    if (question && !(await confirmDialog(question))) return;
    try { await request(); haptic("medium"); load(); } catch (e) { toast(e.message); }
  } }, label);

  const load = async () => {
    let data, tdata;
    try { [data, tdata] = await Promise.all([api("/api/admin/admins"), api("/api/admin/teachers")]); }
    catch (e) { setChildren(list, el("p", { class: "note" }, e.message)); return; }
    setChildren(list,
      el("div", { class: "weekday-title" }, "Админы"),
      ...data.admins.map((a) => person(
        a.full_name || a.login,
        [ROLE_LABELS[a.role], a.login, a.code].join(" · "),
        isOwner && a.role !== "owner"
          ? action("Снять", "Снять права админа?", () => api(`/api/admin/admins/${a.id}`, { method: "DELETE" })) : null)),
      el("div", { class: "weekday-title" }, "Старосты"),
      ...(data.starostas.length
        ? data.starostas.map((st) => person(st.full_name, [formatGroup(st.group), st.code].join(" · "),
          action("Снять", `Снять «${st.full_name}» со старост?`,
            () => api(`/api/admin/users/${st.id}/role`, { method: "PUT", body: { role: "user" } }))))
        : [el("p", { class: "note" }, "Пока никого. Назначь по личному коду выше — человек должен быть в группе.")]),
      el("div", { class: "weekday-title" }, "Преподаватели"),
      ...tdata.teachers.map((tc) => person(tc.full_name,
        [tc.schedule_name ? `в расписании ${tc.schedule_name}` : "в расписании не найден",
          tc.subjects.join(", "), tc.login].filter(Boolean).join(" · "),
        isOwner ? action("Убрать", `Убрать «${tc.full_name}» из преподавателей? Журнал пар сохранится.`,
          () => api(`/api/admin/teachers/${tc.id}`, { method: "DELETE" })) : null)),
      ...tdata.invites.map((inv) => person(inv.pretty, `код до ${shortDate(inv.expires)} · ждёт преподавателя`,
        el("button", { class: "btn tinted small", onclick: () => shareInvite(inv) }, "Отправить"),
        action("Отменить", null, () => api(`/api/admin/teachers/invites/${inv.code}`, { method: "DELETE" })))),
      tdata.teachers.length || tdata.invites.length ? null : el("p", { class: "note" }, isOwner
        ? "Пока никого. Выдай преподавателю код — он введёт его в приложении."
        : "Пока никого. Коды для преподавателей выдаёт главный админ."),
    );
  };

  const inviteText = (inv) => `Здравствуйте! Приглашаю вас в приложение «${appName()}» — в нём можно отмечать `
    + `посещаемость студентов кодом вместо листочка.\n\nУстановите приложение, зарегистрируйтесь и введите код: ${inv.pretty}\n`
    + `Код одноразовый, действует до ${shortDate(inv.expires)}.`;
  const shareInvite = (inv) => shareText(inviteText(inv), "Приглашение преподавателю");
  // Новый код: показываем крупно, с готовым текстом приглашения
  const showInvite = (inv) => setChildren(inviteBox, el("div", { class: "form stack" },
    el("h3", { style: "margin:0" }, "Код для преподавателя"),
    el("div", { class: "invite-code" }, inv.pretty),
    el("p", { class: "note" }, `Одноразовый, действует до ${shortDate(inv.expires)}. Преподаватель регистрируется `
      + "в приложении и вводит код при регистрации или в профиле → «Ввести код»."),
    el("div", { class: "pair-btns" },
      el("button", { class: "btn small", onclick: () => shareInvite(inv) }, "Отправить"),
      el("button", { class: "btn tinted small", onclick: () => copyText(inv.pretty, "Код скопирован") }, "Скопировать"))));
  const newInvite = el("button", { class: "btn tinted block", onclick: async () => {
    newInvite.disabled = true;
    try {
      const inv = await api("/api/admin/teachers/invites", { method: "POST" });
      hapticResult("success");
      await load();
      showInvite(inv);
      inviteBox.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) { toast(e.message); }
    finally { newInvite.disabled = false; }
  } }, "Выдать код преподавателю");

  // Роль по личному коду: найти человека, показать, кто это, и выбрать роль
  const codeInput = el("input", { class: "grow code-text", placeholder: "Личный код, ABC-234", autocomplete: "off",
    autocapitalize: "characters", spellcheck: "false", "aria-label": "Личный код" });
  const found = el("div");
  const find = el("button", { class: "btn small", onclick: async () => {
    if (!codeInput.value.trim()) return toast("Введи личный код");
    try {
      const p = await api("/api/admin/lookup?code=" + encodeURIComponent(codeInput.value));
      const options = [["user", "Студент"], ["starosta", "Староста"]];
      if (isOwner) options.push(["admin", "Админ"]);
      const seg = p.role === "owner" || p.id === state.me.id ? null : segmented(options, p.role, async (role) => {
        try {
          await api(`/api/admin/users/${p.id}/role`, { method: "PUT", body: { role } });
          hapticResult("success");
          toast("Роль изменена — человеку пришло уведомление");
          load();
        } catch (e) { toast(e.message); }
      });
      seg?.classList.add("wide");
      setChildren(found, el("div", { class: "form stack" },
        el("div", { class: "found-person" }, avatarNode(p.full_name, p.photo),
          el("div", { class: "grow" }, el("b", {}, p.full_name),
            el("small", {}, [p.group ? formatGroup(p.group) : "без группы", ROLE_LABELS[p.role], p.teacher && "преподаватель"]
              .filter(Boolean).join(" · ")))),
        seg || el("p", { class: "note" }, "Роль этого человека поменять нельзя"),
        !p.group ? el("p", { class: "note" }, "Старостой можно сделать только того, кто в группе: сначала добавь его в группу "
          + "(«Группы» → «+ студент»).") : null));
    } catch (e) { setChildren(found); toast(e.message); }
  } }, "Найти");

  const backupBtn = isOwner ? el("button", { class: "btn tinted block", onclick: async () => {
    backupBtn.disabled = true;
    try { await saveFile(await apiFile("/api/admin/backup")); } catch (e) { toast(e.message); }
    finally { backupBtn.disabled = false; }
  } }, "Скачать бэкап базы") : null;

  return block("Команда", el("div", { class: "stack" },
    el("p", { class: "note", style: "margin:0" }, isOwner
      ? "Роль назначается по личному коду человека — он в профиле. Админов назначает главный админ, старост — любой админ."
      : "Старосту назначь по личному коду. Админов назначает главный админ, коды для преподавателей — тоже он."),
    el("div", { class: "inline" }, codeInput, find),
    found,
    list,
    isOwner ? newInvite : null,
    inviteBox,
    backupBtn,
    isOwner ? el("p", { class: "note" }, "Каждую ночь сервер сам сохраняет копию базы в папку бэкапов.") : null), load);
}

// --- отметка на паре: общее -------------------------------------------------------

// «БИА 2 ПОДГРУППА» → «БИА 2» — коротко, для списков подгрупп
function shortGroup(g) {
  const m = /^(.*?)\s*(\d+)\s*подгруппа$/i.exec(g || "");
  return m ? `${m[1]} ${m[2]}` : g;
}
const groupsText = (groups, half) => groups.map(shortGroup).join(", ") + (half ? ` · ${half}-я половина` : "");

function dateLabel(iso) {
  const today = state.me.today;
  if (iso === today) return "Сегодня";
  if (iso === addDays(today, -1)) return "Вчера";
  const d = parseDate(iso);
  return `${WD_SHORT[(d.getDay() + 6) % 7]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

const shortDate = (iso) => { const d = parseDate(iso); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const loadingNote = () => el("p", { class: "note pad-note" }, "Загрузка…");
const errorNote = (e) => el("p", { class: "note pad-note" }, e.message);

// Кто отметился и кого нет: в живом списке и в журнале. Кружок справа — отметить вручную;
// без onToggle — только посмотреть (староста на паре, где отметку ведёт преподаватель)
function rosterCards(d, onToggle, hint = "Нажмите на кружок, чтобы отметить студента вручную — например, если у него сел телефон.") {
  const multi = d.groups.length > 1;
  const row = (st) => {
    const sub = [multi && shortGroup(st.group),
      st.present ? (st.method === "manual" ? "вручную" : `в ${st.at}`) : !st.in_app && "нет в приложении"].filter(Boolean).join(" · ");
    const mark = onToggle
      ? el("button", {
        class: "mark-btn" + (st.present ? " on" : ""), "aria-pressed": String(st.present),
        "aria-label": (st.present ? "Убрать отметку: " : "Отметить вручную: ") + st.full_name,
        onclick: (e) => { e.currentTarget.disabled = true; onToggle(st, !st.present); },
      }, icon("tick"))
      : el("span", { class: "mark-btn static" + (st.present ? " on" : ""), "aria-label": st.present ? "был" : "не было" },
        icon(st.present ? "tick" : "close"));
    return el("div", { class: "roster-row" },
      el("span", { class: "grow" }, el("b", {}, st.full_name), sub ? el("small", {}, sub) : null), mark);
  };
  const here = d.roster.filter((s) => s.present);
  const away = d.roster.filter((s) => !s.present);
  const head = (title, n, muted) => el("div", { class: "panel-top pad" },
    el("span", { class: "eyebrow" }, title), el("span", { class: "chip" + (muted ? " muted" : "") }, String(n)));
  return [
    el("section", { class: "panel list" }, head("Отметились", here.length),
      ...(here.length ? here.map(row)
        : [el("p", { class: "note list-pad" }, d.open ? "Пока никого — покажите код студентам." : "Никто не отметился.")])),
    away.length ? el("section", { class: "panel list" }, head(d.open ? "Ещё нет" : "Не было", away.length, true),
      ...away.map(row),
      onToggle && hint ? el("p", { class: "note list-pad" }, hint) : null) : null,
  ];
}

// --- посещаемость группы: отметки на парах в расписании и журнал старосты ---------------

// Под начавшейся парой: старосте — кто отметился (нажать — список), студенту — есть ли его отметка
function attendanceLine(l, iso, onHero = false, compact = false) {
  const a = l.attendance;
  if (!a) return null;
  const cls = "att-line" + (onHero ? " on-hero" : "") + (compact ? " compact" : "");
  const chev = el("span", { class: "chev-icon" }, icon("chevron"));
  if (a.teacher) {
    return el("button", { class: cls, onclick: () => openTeacherSession(a, l) },
      icon("users"), el("span", {}, `Отметились ${a.present} из ${a.total}`, a.open ? el("em", {}, " · идёт отметка") : null), chev);
  }
  if (a.staff) {
    const text = a.session
      ? [`Отметились ${a.present} из ${a.total}`, a.open && el("em", {}, " · идёт отметка")]
      : ["Отметить, кто был"];
    return el("button", { class: cls + (a.session ? "" : " todo"), onclick: () => attendanceSheet(state.week.group, iso, l) },
      icon(a.session ? "users" : "checkCircle"), el("span", {}, ...text.filter(Boolean)), chev);
  }
  return el("div", { class: cls + (a.marked ? " yes" : " no") },
    icon(a.marked ? "checkCircle" : "close"), el("span", {}, a.marked ? `Отметка есть · ${a.at}` : "Отметки нет"));
}

// Преподаватель из своего расписания: идущая отметка — на «Код», прошедшая — в журнал
function openTeacherSession(a, l) {
  haptic();
  if (a.open) { openTab("code"); return; }
  state.teacherNext = teacherSessionBlock({ id: a.session, subject: l.subject });
  openTab("teacher");
}

// Кто был на паре: отметить вручную, показать код или посмотреть отметку преподавателя.
// lesson — {subject, start, end}; sessionId — если отметка уже есть
function attendanceSheet(group, iso, lesson, { sessionId = lesson.attendance?.session, onchange } = {}) {
  let changed = false;
  let poll = null, tick = null;
  const stop = () => { clearTimeout(poll); clearInterval(tick); poll = tick = null; };
  openSheet(lesson.subject, `${longDate(iso)} · ${lesson.start}${lesson.end ? "–" + lesson.end : ""}`, (card, close) => {
    const body = el("div", { class: "att-sheet" }, loadingNote());
    const error = el("p", { class: "sheet-error", role: "alert" });
    card.append(body);
    let d = null;
    let deadline = 0;
    const isToday = iso === state.me.today;

    async function call(path, opts, btn) {
      if (btn) btn.disabled = true;
      error.textContent = "";
      try {
        const r = await api(path, opts);
        changed = true;
        return r;
      } catch (e) {
        error.textContent = e.message;
        hapticResult("error");
        return null;
      } finally { if (btn) btn.disabled = false; }
    }
    async function start(withCode, btn) {
      const r = await call("/api/admin/attendance", { method: "POST", body: {
        group, date: iso, start: lesson.start, subject: lesson.subject, code: withCode } }, btn);
      if (r) { hapticResult("success"); show(r); }
    }

    // Отметки ещё нет: код студентам или вручную
    function empty() {
      const codeBtn = isToday ? el("button", { class: "btn block", onclick: (e) => start(true, e.currentTarget) },
        icon("hash"), "Показать код студентам") : null;
      const handBtn = el("button", { class: "btn block" + (isToday ? " tinted" : ""), onclick: (e) => start(false, e.currentTarget) },
        icon("users"), "Отметить вручную");
      setChildren(body,
        el("p", { class: "note" }, isToday
          ? "Отметки на этой паре ещё нет. Покажи студентам код — они отметятся сами в приложении. Или отметь вручную, кто был."
          : "Отметки на этой паре не было. Отметь вручную, кто был, — пара попадёт в журнал посещаемости группы."),
        error, codeBtn, handBtn);
    }

    // Код крупно с обратным отсчётом — пока староста показывает его группе
    const digits = el("div", { class: "code-digits light", role: "status" });
    const left = el("span", { class: "code-left" });
    const fill = el("div", { class: "fill" });
    function drawCode() {
      const ms = Math.max(0, deadline - Date.now());
      const code = ms > 0 && d?.code ? d.code : null;
      setChildren(digits, ...(code || "····").split("").map((c) => el("span", {}, c)));
      digits.classList.toggle("expired", !code);
      const sec = Math.ceil(ms / 1000);
      left.textContent = code ? `${Math.floor(sec / 60)}:${pad(sec % 60)}` : "код истёк";
      fill.style.transform = `scaleX(${code ? Math.min(1, ms / 1000 / d.ttl) : 0})`;
    }

    function show(x) {
      d = x;
      deadline = Date.now() + (d.expires_in || 0) * 1000;
      const source = d.by_teacher
        ? `Отметку ведёт преподаватель${d.teacher ? " — " + d.teacher : ""}. Поменять отметки может только он.`
        : d.open ? "Студенты вводят код в приложении: «Расписание» → «Отметиться на паре»." : null;
      const actions = [];
      if (d.editable && d.open) {
        actions.push(el("div", { class: "att-code" }, digits,
          el("div", { class: "progress" }, el("div", { class: "track" }, fill), left),
          el("div", { class: "att-code-actions" },
            el("button", { class: "btn tinted small", onclick: async (e) => {
              const r = await call(`/api/admin/attendance/${d.id}/code`, { method: "POST" }, e.currentTarget);
              if (r) { haptic("medium"); show(r); }
            } }, icon("refresh"), "Новый код"),
            el("button", { class: "btn tinted small", onclick: async (e) => {
              const r = await call(`/api/admin/attendance/${d.id}/close`, { method: "POST" }, e.currentTarget);
              if (r) { hapticResult("success"); toast(`В журнале: ${r.present} из ${r.total}`); show(r); }
            } }, "Завершить"))));
        drawCode();
      } else if (d.editable && isToday) {
        actions.push(el("button", { class: "btn tinted block", onclick: async (e) => {
          const r = await call(`/api/admin/attendance/${d.id}/code`, { method: "POST" }, e.currentTarget);
          if (r) { hapticResult("success"); show(r); }
        } }, icon("hash"), "Показать код студентам"));
      }
      const toggle = d.editable ? async (st, present) => {
        const r = await call(`/api/admin/attendance/${d.id}/marks/${st.id}`, { method: "PUT", body: { present } });
        if (r) haptic();
        show(r || d);
      } : null;
      const remove = d.editable ? el("button", { class: "link-btn small danger-text att-remove", onclick: async () => {
        if (!(await confirmDialog("Удалить отметку этой пары? Она пропадёт из журнала группы.", "Удалить"))) return;
        if (await call(`/api/admin/attendance/${d.id}`, { method: "DELETE" })) { toast("Отметка удалена"); close(); }
      } }, "Удалить отметку") : null;
      setChildren(body,
        el("div", { class: "big-line" }, el("b", {}, `${d.present} из ${d.total}`), el("span", {}, "отметились")),
        source ? el("p", { class: "note" }, source) : null,
        ...actions, error,
        ...rosterCards(d, toggle, "Нажми на кружок, чтобы отметить студента вручную."),
        remove);
      stop();
      if (d.open) {
        tick = setInterval(drawCode, 250);
        poll = setTimeout(refresh, 3000);
      }
    }
    async function refresh() {
      if (!d || !body.isConnected) return;
      try { show(await api(`/api/admin/attendance/${d.id}`)); }
      catch (_) { poll = setTimeout(refresh, 3000); }
    }

    if (sessionId) {
      api(`/api/admin/attendance/${sessionId}`).then(show).catch((e) => setChildren(body, el("p", { class: "sheet-error" }, e.message)));
    } else empty();
  }, () => {
    stop();
    if (!changed) return;
    if (onchange) onchange();
    else if (state.week && !$("screen-schedule").hidden) loadWeek();
  });
}

const rateText = (rate) => (rate === null || rate === undefined ? "—" : `${rate}%`);

// Журнал группы: явка, студенты по пропускам, все пары с отметкой
function attendanceBlock(group) {
  const box = el("div", { class: "stackv" }, loadingNote());
  let current = group || state.groups?.[0];
  const picker = !group && state.groups?.length > 1
    ? el("select", { class: "full", "aria-label": "Группа", onchange: (e) => { current = e.target.value; load(); } },
      ...state.groups.map((g) => el("option", { value: g }, formatGroup(g))))
    : null;
  async function load() {
    setChildren(box, picker, loadingNote());
    if (!current) { setChildren(box, el("p", { class: "page-note" }, "Групп пока нет.")); return; }
    let j;
    try { j = await api("/api/admin/attendance?group=" + encodeURIComponent(current)); }
    catch (e) { setChildren(box, picker, errorNote(e)); return; }
    const byId = new Map(j.sessions.map((x) => [x.id, x]));
    const openSession = (x) => attendanceSheet(current, x.date, x, { sessionId: x.id, onchange: load });
    const students = [...j.students].sort((a, b) =>
      (a.rate ?? 101) - (b.rate ?? 101) || a.full_name.localeCompare(b.full_name, "ru"));
    const byDate = new Map();
    for (const x of j.sessions) {
      if (!byDate.has(x.date)) byDate.set(x.date, []);
      byDate.get(x.date).push(x);
    }
    setChildren(box, picker,
      el("section", { class: "hero" },
        el("div", { class: "hero-top" }, el("span", {}, "Явка группы"), el("span", {}, formatGroup(current))),
        el("div", { class: "big-line" }, el("b", {}, rateText(j.rate)),
          el("span", {}, j.sessions.length ? `средняя явка · ${countOf(j.sessions.length, "пара", "пары", "пар")} с отметкой` : "пар с отметкой пока нет"))),
      j.sessions.length ? null : el("p", { class: "page-note" },
        "Отметки появляются, когда преподаватель открывает код на паре или ты отмечаешь пару сам: "
        + "«Расписание» → прошедшая пара → «Отметить, кто был»."),
      students.length ? el("section", { class: "panel list" },
        el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Студенты"), el("span", { class: "chip" }, String(students.length))),
        ...students.map((st) => el("button", { class: "roster-row as-btn", onclick: () => studentMissedSheet(st, byId, openSession) },
          el("span", { class: "grow" }, el("b", {}, st.full_name),
            el("small", {}, [st.total ? `на парах: ${st.attended} из ${st.total}` : "пар с отметкой не было",
              st.missed.length && `пропусков: ${st.missed.length}`, !st.in_app && "нет в приложении"].filter(Boolean).join(" · "))),
          st.rate !== null ? el("span", { class: "rate" + (st.rate < 50 ? " low" : "") }, `${st.rate}%`) : null))) : null,
      ...[...byDate].map(([date, items]) => el("section", { class: "panel list" },
        el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, dateLabel(date))),
        ...items.map((x) => listRow({
          label: x.start ? `${x.start}–${x.end}` : null, title: x.subject,
          hint: (x.by_teacher ? "отмечал преподаватель" : "отметка старосты") + (x.open ? " · идёт отметка" : ""),
          value: `${x.present}/${x.total}`, onclick: () => openSession(x),
        })))),
      j.sessions.length ? el("section", { class: "panel list" }, listRow({
        iconName: "file", title: "Журнал в Excel", hint: "файл .xlsx — сохранить или отправить",
        onclick: async () => {
          try { await saveFile(await apiFile("/api/admin/attendance/export?group=" + encodeURIComponent(current))); hapticResult("success"); }
          catch (e) { toast(e.message); }
        },
      })) : null);
  }
  return block("Посещаемость", box, load, { bare: true });
}

// Какие пары студент пропустил — нажать, чтобы открыть пару
function studentMissedSheet(st, byId, openSession) {
  const missed = st.missed.map((id) => byId.get(id)).filter(Boolean);
  openSheet(st.full_name, st.total ? `На парах: ${st.attended} из ${st.total}${st.rate !== null ? ` · ${st.rate}%` : ""}` : "Пар с отметкой не было",
    (card, close) => card.append(el("div", { class: "att-sheet" },
      missed.length
        ? el("section", { class: "panel list" },
          el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Пропуски"), el("span", { class: "chip muted" }, String(missed.length))),
          ...missed.map((x) => listRow({
            label: `${dateLabel(x.date)}${x.start ? ", " + x.start : ""}`, title: x.subject,
            onclick: () => { close(); openSession(x); },
          })))
        : el("p", { class: "note" }, st.total ? "Пропусков нет 🎉" : "Пока не было пар с отметкой."))));
}

// На главной старосты: явка группы и у кого больше всего пропусков
function attendanceCard(group) {
  const card = el("section", { class: "panel list" },
    el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Посещаемость")),
    el("p", { class: "note list-pad" }, "Загрузка…"));
  api("/api/admin/attendance?group=" + encodeURIComponent(group)).then((j) => {
    const head = card.firstChild;
    if (j.rate !== null) head.append(el("span", { class: "chip" }, `явка ${j.rate}%`));
    const worst = j.students.filter((st) => st.missed.length)
      .sort((a, b) => b.missed.length - a.missed.length || a.full_name.localeCompare(b.full_name, "ru")).slice(0, 3);
    setChildren(card, head,
      j.sessions.length ? null : el("p", { class: "note list-pad" },
        "Отметь, кто был: в «Расписании» нажми на прошедшую пару → «Отметить, кто был». Или покажи код на идущей паре."),
      ...worst.map((st) => listRow({
        title: st.full_name, hint: `на парах: ${st.attended} из ${st.total}`,
        value: countOf(st.missed.length, "пропуск", "пропуска", "пропусков"),
        onclick: () => openSection(attendanceBlock(group)),
      })),
      listRow({ iconName: "checkCircle", title: j.sessions.length ? `Журнал (${countOf(j.sessions.length, "пара", "пары", "пар")})` : "Журнал посещаемости",
        onclick: () => openSection(attendanceBlock(group)) }));
  }).catch((e) => setChildren(card, card.firstChild, el("p", { class: "note list-pad" }, e.message)));
  return card;
}

// --- студент: «Отметиться на паре» ------------------------------------------------

// Пары с отметкой сегодня у группы студента: идёт ли отметка и есть ли он в списке
async function loadAttendance() {
  if (!state.me?.student || state.week?.mine) return;
  let sessions;
  try { sessions = (await api("/api/attendance/me")).sessions; } catch (_) { return; }
  if (JSON.stringify(sessions) === JSON.stringify(state.att)) return;
  state.att = sessions;
  const w = state.week;
  if (!$("screen-schedule").hidden && w?.days[state.selectedDay]?.date === w.today) renderDay();
}

const sameText = (a, b) => (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();
const attendanceFor = (l) => state.att.find((x) => (x.start && x.start === l.start) || sameText(x.subject, l.subject));
const liveSession = () => state.att.find((x) => x.code_active && !x.marked);

// Кнопка внизу карточки «Сейчас»: студенту — отметиться, преподавателю — открыть код
function heroAction(l) {
  if (state.week.mine) {
    return el("button", { class: "hero-btn", onclick: () => { haptic(); openTab("code"); } }, icon("hash"), "Код для отметки");
  }
  if (!state.me.student) return null;
  const x = attendanceFor(l);
  if (x?.marked) return el("div", { class: "hero-marked" }, icon("checkCircle"), `Отметка есть · ${x.marked_at}`);
  const live = liveSession();
  return el("button", { class: "hero-btn" + (live ? " live" : ""), onclick: () => openCheckin() },
    icon("checkCircle"), live ? "Идёт отметка — ввести код" : "Отметиться на паре");
}

// Пар по расписанию больше нет, а преподаватель открыл отметку — всё равно показываем кнопку
function checkinBanner() {
  const x = state.me.student && !state.week.mine && liveSession();
  if (!x) return null;
  return el("section", { class: "hero" },
    el("div", { class: "hero-top" }, el("span", {}, "Идёт отметка"), el("span", { class: "num" }, x.started_at)),
    el("div", { class: "hero-subject" }, x.subject),
    el("div", { class: "hero-foot" }, x.teacher),
    el("button", { class: "hero-btn live", onclick: () => openCheckin() }, icon("checkCircle"), "Ввести код"));
}

function openCheckin() {
  haptic("light");
  const input = el("input", {
    class: "code-input", type: "text", inputmode: "numeric", pattern: "[0-9]*", autocomplete: "one-time-code",
    maxlength: 4, "aria-label": "Код из 4 цифр",
  });
  const cells = el("div", { class: "code-cells", "aria-hidden": "true" }, ...[0, 1, 2, 3].map(() => el("span")));
  const field = el("label", { class: "code-field" }, cells, input);
  const error = el("p", { class: "sheet-error", role: "alert" });
  const submit = el("button", { class: "btn block", disabled: true, onclick: () => send() }, "Отметиться");
  const closeBtn = () => el("button", { class: "sheet-close", "aria-label": "Закрыть", onclick: () => close() }, icon("close"));
  const card = el("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": "sheet-title" },
    closeBtn(),
    el("h2", { id: "sheet-title" }, "Отметиться на паре"),
    el("p", { class: "sheet-sub" }, "Введи код, который показал преподаватель или староста. Он работает минуту."),
    field, error, submit);
  const backdrop = el("div", { class: "sheet-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } }, card);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  let busy = false;

  const draw = () => {
    const v = input.value;
    [...cells.children].forEach((c, i) => { c.textContent = v[i] || ""; c.classList.toggle("on", i === Math.min(v.length, 3)); });
    submit.disabled = busy || v.length !== 4;
  };
  input.addEventListener("input", () => {
    input.value = input.value.replace(/\D/g, "").slice(0, 4);
    error.textContent = "";
    draw();
    if (input.value.length === 4) send();
  });

  async function send() {
    if (busy || input.value.length !== 4) return;
    busy = true;
    submit.textContent = "Проверяю…";
    draw();
    try {
      const r = await api("/api/attendance/checkin", { method: "POST", body: { code: input.value } });
      hapticResult("success");
      card.classList.add("done");
      setChildren(card, closeBtn(),
        el("div", { class: "sheet-done" }, icon("tick")),
        el("h2", { id: "sheet-title" }, r.already ? "Отметка уже есть" : "Готово, ты на паре"),
        el("p", { class: "sheet-sub" }, `${r.subject} · ${r.at}`),
        el("p", { class: "sheet-sub" }, r.teacher),
        el("button", { class: "btn block", onclick: () => close() }, "Хорошо"));
      loadAttendance();
    } catch (e) {
      hapticResult("error");
      error.textContent = e.message;
      field.classList.remove("shake");
      void field.offsetWidth;
      field.classList.add("shake");
      input.value = "";
      submit.textContent = "Отметиться";
      input.focus();
    } finally {
      busy = false;
      draw();
    }
  }

  function close() {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    setBackButton(null);
  }

  document.body.append(backdrop);
  document.addEventListener("keydown", onKey);
  setBackButton(close);
  draw();
  // Сразу, в том же нажатии: иначе iOS не покажет клавиатуру
  input.focus({ preventScroll: true });
}

// --- «Код»: преподаватель открывает отметку на паре ---------------------------------

const live = { id: null, data: null, deadline: 0, poll: null, tick: null, fs: null };

function stopCodeLive() {
  clearTimeout(live.poll);
  clearInterval(live.tick);
  live.id = live.poll = live.tick = null;
  live.fs?.close();
}

async function renderCode() {
  stopCodeLive();
  const box = $("code-body");
  const head = pageHead("Код", "Отметка на паре");
  setChildren(box, head, loadingNote());
  let r;
  try { r = await api("/api/teacher/today"); } catch (e) { setChildren(box, head, errorNote(e)); return; }
  if ($("screen-code").hidden) return;
  if (r.active) codeLive(r.active);
  else codeSetup(r);
}

// Преподавателя нет в графе «Преподаватель»: подсказать, как найти себя
function notInScheduleHero() {
  return el("section", { class: "hero" },
    el("div", { class: "hero-top" }, el("span", {}, "Пар пока нет")),
    el("div", { class: "hero-subject small" }, "В расписании не нашлось пар с вашей фамилией"),
    el("div", { class: "hero-foot" }, "Если вы записаны там иначе — найдите себя в списке преподавателей, "
      + "и здесь появятся ваши пары."),
    el("button", { class: "hero-btn", onclick: () => { haptic(); openTeacherPeople(); } }, icon("user"), "Найти себя в расписании"));
}

// Какую пару отмечаем: текущую (или ту, что вот-вот начнётся), иначе ближайшую
function defaultLesson(lessons) {
  const now = nowMinutes();
  return lessons.find((l) => toMin(l.start) - 20 <= now && now < toMin(l.end))
    || lessons.find((l) => toMin(l.start) > now) || null;
}

function codeSetup(r) {
  const box = $("code-body");
  const head = pageHead("Код", "Отметка на паре");
  if (!r.groups.length) {
    setChildren(box, head, notInScheduleHero());
    return;
  }
  const lessons = r.lessons;
  const pick = defaultLesson(lessons);
  const sel = {
    lesson: pick,
    groups: new Set(pick ? pick.groups : r.groups.length === 1 ? r.groups : []),
    half: pick ? pick.half : 0,
  };
  const subject = el("input", { class: "hero-input", placeholder: "Название пары", maxlength: 300, "aria-label": "Название пары" });
  const startBtn = el("button", { class: "hero-btn", onclick: start }, icon("hash"), "Создать код");

  function choose(lesson) {
    sel.lesson = lesson;
    if (lesson) { sel.groups = new Set(lesson.groups); sel.half = lesson.half; }
    haptic();
    draw();
    if (!lesson) subject.focus();
  }

  function draw() {
    const l = sel.lesson;
    const now = nowMinutes();
    const isNow = l && toMin(l.start) <= now && now < toMin(l.end);
    startBtn.disabled = !sel.groups.size;
    const hero = el("section", { class: "hero" },
      el("div", { class: "hero-top" },
        el("span", {}, l ? (isNow ? "Сейчас" : "Пара") + (l.pair_num ? ` · ${l.pair_num} пара` : "") : "Своя пара"),
        l ? el("span", { class: "num" }, `${l.start}–${l.end}`) : null),
      l ? el("div", { class: "hero-subject" }, l.subject) : subject,
      el("div", { class: "hero-foot" }, sel.groups.size ? groupsText([...sel.groups].sort(), sel.half) : "Выберите подгруппы ниже"),
      startBtn);
    const pickRow = (lesson, title, label, hint) => {
      const on = sel.lesson === lesson;
      return el("button", { class: "list-row" + (on ? " picked" : ""), "aria-pressed": String(on), onclick: () => choose(lesson) },
        el("span", { class: "grow" }, label ? el("small", {}, label) : null, el("b", {}, title), hint ? el("span", { class: "hint" }, hint) : null),
        el("span", { class: "pick-mark" }, icon("tick")));
    };
    const list = el("section", { class: "panel list" },
      el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Пары сегодня")),
      ...lessons.map((x) => pickRow(x, x.subject, `${x.start}–${x.end}${x.kind ? " · " + x.kind : ""}`,
        groupsText(x.groups, x.half) + (x.room ? ` · ${roomText(x.room)}` : ""))),
      lessons.length ? null : el("p", { class: "note list-pad" }, "Сегодня ваших пар в расписании нет."),
      pickRow(null, "Другая пара", null, "название и подгруппы — вручную"));
    const chips = el("div", { class: "choices wrap" }, ...r.groups.map((g) => el("button", {
      class: "choice" + (sel.groups.has(g) ? " on" : ""), "aria-pressed": String(sel.groups.has(g)),
      onclick: () => {
        if (sel.groups.has(g)) sel.groups.delete(g); else sel.groups.add(g);
        if (sel.groups.size !== 1) sel.half = 0;
        haptic();
        draw();
      },
    }, shortGroup(g))));
    const half = sel.groups.size === 1
      ? segmented([[0, "вся группа"], [1, "1-я половина"], [2, "2-я половина"]], sel.half, (v) => { sel.half = v; draw(); })
      : null;
    half?.classList.add("wide");
    setChildren(box, head, hero, list,
      el("section", { class: "panel" },
        el("div", { class: "panel-top" }, el("span", { class: "eyebrow" }, "Кто на паре")),
        chips, half ? el("div", { class: "half-pick" }, half) : null),
      el("p", { class: "foot-note" },
        "Код из 4 цифр работает минуту. Студенты вводят его в приложении — «Расписание» → «Отметиться на паре»."));
  }

  async function start() {
    if (!sel.groups.size) { toast("Выберите подгруппы"); return; }
    startBtn.disabled = true;
    const l = sel.lesson;
    try {
      const x = await api("/api/teacher/sessions", { method: "POST", body: {
        groups: [...sel.groups], half: sel.half, subject: l ? l.subject : subject.value,
        start: l ? l.start : null, end: l ? l.end : null } });
      hapticResult("success");
      codeLive(x);
    } catch (e) { toast(e.message); startBtn.disabled = false; }
  }

  draw();
}

// Код крупно, обратный отсчёт и живой список: опрашиваем сервер, пока экран открыт
function codeLive(x) {
  stopCodeLive();
  live.id = x.id;
  const box = $("code-body");
  const digits = el("div", { class: "code-digits", role: "status" });
  const fill = el("div", { class: "fill" });
  const left = el("span", { class: "code-left" });
  const present = el("b", { class: "stat" });
  const newBtn = el("button", { class: "hero-btn", onclick: newCode }, icon("refresh"), "Новый код");
  const hero = el("section", { class: "hero code-hero" },
    el("div", { class: "hero-top" }, el("span", {}, "Код для отметки"),
      el("button", { class: "hero-link", onclick: fullscreen }, icon("expand"), "на весь экран")),
    digits,
    el("div", { class: "hero-progress" }, el("div", { class: "track" }, fill), left),
    el("div", { class: "hero-tiles" },
      el("div", { class: "hero-tile grow" }, el("small", {}, "отметились"), present),
      el("div", { class: "hero-tile grow" }, el("small", {}, x.groups.length > 1 ? "подгруппы" : "подгруппа"),
        el("b", {}, groupsText(x.groups, x.half)))),
    el("div", { class: "hero-actions" }, newBtn, el("button", { class: "hero-btn ghost", onclick: finish }, "Завершить")));
  const lists = el("div", { class: "stackv" });
  setChildren(box, pageHead("Код", x.subject), hero, lists);

  let shownCode, rosterKey = "";
  function apply(d) {
    live.data = d;
    live.deadline = Date.now() + d.expires_in * 1000;
    present.textContent = `${d.present} из ${d.total}`;
    const key = JSON.stringify(d.roster);
    if (key !== rosterKey) { rosterKey = key; setChildren(lists, ...rosterCards(d, toggle)); }
    tick();
  }
  function tick() {
    const d = live.data;
    const ms = Math.max(0, live.deadline - Date.now());
    const code = ms > 0 && d.code ? d.code : null;
    if (code !== shownCode) {
      shownCode = code;
      setChildren(digits, ...(code || "····").split("").map((c) => el("span", {}, c)));
      digits.setAttribute("aria-label", code ? `Код ${code.split("").join(" ")}` : "Код истёк");
    }
    const sec = Math.ceil(ms / 1000);
    hero.classList.toggle("expired", !code);
    fill.style.transform = `scaleX(${code ? Math.min(1, ms / 1000 / d.ttl) : 0})`;
    left.textContent = code ? `${Math.floor(sec / 60)}:${pad(sec % 60)}` : "код истёк";
    live.fs?.update(code, left.textContent);
  }
  async function poll() {
    if (live.id !== x.id) return;
    if (!document.hidden && !$("screen-code").hidden) {
      try {
        const d = await api(`/api/teacher/sessions/${x.id}`);
        if (live.id !== x.id) return;
        if (!d.open) { toast("Отметка завершена"); renderCode(); return; }
        apply(d);
      } catch (_) { /* сеть моргнула — попробуем ещё раз */ }
    }
    live.poll = setTimeout(poll, 2500);
  }
  async function newCode() {
    newBtn.disabled = true;
    try { apply(await api(`/api/teacher/sessions/${x.id}/code`, { method: "POST" })); haptic("medium"); }
    catch (e) { toast(e.message); }
    finally { newBtn.disabled = false; }
  }
  async function finish() {
    if (!(await confirmDialog("Завершить отметку? Код перестанет работать, пара сохранится в журнале."))) return;
    try {
      const d = await api(`/api/teacher/sessions/${x.id}/close`, { method: "POST" });
      hapticResult("success");
      toast(`В журнале: ${d.present} из ${d.total}`);
      renderCode();
    } catch (e) { toast(e.message); }
  }
  async function toggle(st, value) {
    try {
      apply(await api(`/api/teacher/sessions/${x.id}/marks/${st.id}`, { method: "PUT", body: { present: value } }));
      haptic();
    } catch (e) {
      toast(e.message);
      setChildren(lists, ...rosterCards(live.data, toggle));
    }
  }
  // Для проектора или ноутбука на кафедре: только код, крупно
  function fullscreen() {
    haptic();
    const fsDigits = el("div", { class: "fs-digits" });
    const fsLeft = el("div", { class: "fs-left" });
    const ov = el("div", { class: "code-fullscreen", role: "dialog", "aria-label": "Код на весь экран", onclick: () => close() },
      el("div", { class: "fs-top" }, x.subject),
      fsDigits, fsLeft,
      el("div", { class: "fs-hint" }, "Студентам: «Расписание» → «Отметиться на паре» · нажмите, чтобы закрыть"));
    const close = () => { ov.remove(); live.fs = null; setBackButton(null); };
    live.fs = {
      close,
      update: (code, text) => { fsDigits.textContent = code || "····"; fsLeft.textContent = text; ov.classList.toggle("expired", !code); },
    };
    document.body.append(ov);
    setBackButton(close);
    tick();
  }

  apply(x);
  live.tick = setInterval(tick, 200);
  live.poll = setTimeout(poll, 2500);
}

// --- «Я учитель»: подгруппы и журнал посещаемости -------------------------------------

async function renderTeacher() {
  setBackButton(null);
  const box = $("teacher-body");
  const head = pageHead("Я учитель", state.me.teacher.full_name);
  setChildren(box, head, loadingNote());
  let t;
  try { t = await api("/api/teacher"); } catch (e) { setChildren(box, head, errorNote(e)); return; }
  state.teacher = t;
  const students = t.subjects.reduce((n, x) => n + x.students, 0);
  const hero = el("section", { class: "hero" },
    el("div", { class: "hero-top" }, el("span", {}, "Журнал посещаемости")),
    el("div", { class: "big-line" }, el("b", {}, String(t.sessions_total)),
      el("span", {}, plural(t.sessions_total, "пара в журнале", "пары в журнале", "пар в журнале"))),
    el("div", { class: "hero-tiles three" },
      statTile("явка", t.rate === null ? "—" : `${t.rate}%`), statTile("предметы", t.subjects.length), statTile("студенты", students)),
    el("button", { class: "hero-btn", onclick: () => { haptic(); openTab("code"); } }, icon("hash"), "Код для отметки"));
  const subjectsCard = el("section", { class: "panel list" },
    el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Мои пары по расписанию")),
    listRow({
      iconName: "user", label: "В расписании", title: t.schedule_name || "не нашлись",
      hint: t.schedule_name ? countOf(t.lessons, "пара", "пары", "пар") + " · не вы? нажмите" : "найдите себя в списке преподавателей",
      onclick: () => { haptic(); openSection(teacherPeopleBlock(), "teacher"); },
    }),
    ...t.subjects.map((x) => listRow({
      iconName: "book", title: x.subject,
      hint: groupsText(x.groups) + (x.rate !== null ? ` · явка ${x.rate}%` : ""),
      onclick: () => openSection(teacherSubjectBlock(x.subject), "teacher"),
    })));
  const journal = el("section", { class: "panel list" },
    el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, "Последние пары")),
    ...(t.recent.length ? t.recent.map((x) => sessionRow(x))
      : [el("p", { class: "note list-pad" }, "Пока пусто. В начале пары откройте вкладку «Код».")]),
    t.sessions_total > t.recent.length
      ? listRow({ title: `Весь журнал (${t.sessions_total})`, onclick: () => openSection(teacherJournalBlock(), "teacher") }) : null);
  setChildren(box, head, hero, subjectsCard, journal,
    t.sessions_total ? el("section", { class: "panel list" },
      listRow({ iconName: "file", title: "Журнал в Excel", hint: "файл .xlsx — сохранить или отправить", onclick: exportJournal })) : null);
  const next = state.teacherNext;
  state.teacherNext = null;
  if (next === "people") openSection(teacherPeopleBlock(), "teacher");
  else if (next) openSection(next, "teacher");
}

function sessionRow(x, withDate = true) {
  const when = x.start || x.started_at;
  return listRow({
    label: withDate ? `${dateLabel(x.date)}, ${when}` : when, title: x.subject,
    hint: groupsText(x.groups, x.half) + (x.open ? " · идёт отметка" : ""),
    value: `${x.present}/${x.total}`,
    onclick: () => (x.open ? openTab("code") : openSection(teacherSessionBlock(x), "teacher")),
  });
}

// С экрана «Код»: сразу к поиску себя в расписании
function openTeacherPeople() {
  state.teacherNext = "people";
  openTab("teacher");
}

async function exportJournal() {
  try {
    await saveFile(await apiFile("/api/teacher/export"));
    hapticResult("success");
  } catch (e) { toast(e.message); }
}

// Как преподаватель записан в расписании: обычно находится сам по ФИО,
// а если записан иначе («Сидорова-Петрова Е.») — выбирает себя здесь
function teacherPeopleBlock() {
  const search = el("input", { type: "search", class: "full", placeholder: "Поиск по фамилии", "aria-label": "Поиск по фамилии" });
  const list = el("div");
  const box = el("div", { class: "stackv" },
    el("p", { class: "page-note" }, "Ваши пары — те, где в расписании стоит ваша фамилия. Обычно приложение находит вас само по ФИО. "
      + "Если вы записаны иначе — выберите себя."),
    el("section", { class: "panel list" }, el("div", { class: "search-row" }, search), list));
  let data = null;
  const choose = async (name) => {
    try {
      const r = await api("/api/teacher/schedule-name", { method: "PUT", body: { name } });
      Object.assign(state.me.teacher, r);
      hapticResult("success");
      toast(r.lessons ? `Нашлось: ${countOf(r.lessons, "пара", "пары", "пар")}` : "Пар пока нет");
      renderTeacher();
    } catch (e) { toast(e.message); }
  };
  const row = (title, hint, on, onclick, tag) => el("button", { class: "list-row" + (on ? " picked" : ""), "aria-pressed": String(on), onclick },
    el("span", { class: "grow" }, el("b", {}, title), el("span", { class: "hint" }, tag ? el("span", { class: "tag" }, tag) : null, hint)),
    el("span", { class: "pick-mark" }, icon("tick")));
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const people = [...data.people].sort((a, b) => b.match - a.match || a.name.localeCompare(b.name, "ru"))
      .filter((p) => !q || p.names.some((n) => n.toLowerCase().includes(q)));
    setChildren(list,
      q ? null : row("Искать по моему ФИО", state.me.teacher.full_name, data.current === null, () => choose(null)),
      ...people.map((p) => row(p.name,
        `${countOf(p.lessons, "пара", "пары", "пар")} · ${p.subjects.map((x) => x.subject).slice(0, 2).join(", ")}${p.subjects.length > 2 ? "…" : ""}`,
        data.current !== null && p.names.includes(data.current), () => choose(p.name), p.match ? "похоже на вас" : null)),
      people.length ? null : el("p", { class: "note list-pad" }, data.people.length ? "Никого не нашёл" : "Расписания пока нет — его загружает админ"));
  };
  const load = async () => {
    setChildren(list, loadingNote());
    try { data = await api("/api/teacher/people"); } catch (e) { setChildren(list, errorNote(e)); return; }
    draw();
  };
  search.oninput = () => data && draw();
  return block("В расписании", box, load, { bare: true });
}

// Посещаемость по предмету: студенты по подгруппам
function teacherSubjectBlock(subject) {
  const box = el("div", { class: "stackv" }, loadingNote());
  const load = async () => {
    let r;
    try { r = await api("/api/teacher/attendance?subject=" + encodeURIComponent(subject)); }
    catch (e) { setChildren(box, errorNote(e)); return; }
    const byGroup = new Map();
    for (const st of r.students) {
      if (!byGroup.has(st.group)) byGroup.set(st.group, []);
      byGroup.get(st.group).push(st);
    }
    setChildren(box,
      el("section", { class: "hero" },
        el("div", { class: "hero-top" }, el("span", {}, "Посещаемость")),
        el("div", { class: "big-line" }, el("b", {}, r.rate === null ? "—" : `${r.rate}%`),
          el("span", {}, r.sessions ? `средняя явка · ${countOf(r.sessions, "пара", "пары", "пар")}` : "пар с отметкой пока не было"))),
      ...[...byGroup].map(([group, students]) => el("section", { class: "panel list" },
        el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, formatGroup(group)),
          el("span", { class: "chip" }, String(students.length))),
        ...students.map((st) => el("div", { class: "roster-row" },
          el("span", { class: "grow" }, el("b", {}, st.full_name),
            el("small", {}, [st.total ? `${st.attended} из ${st.total}` : "пар ещё не было", !st.in_app && "нет в приложении"].filter(Boolean).join(" · "))),
          st.rate !== null ? el("span", { class: "rate" + (st.rate < 50 ? " low" : "") }, `${st.rate}%`) : null)))),
      byGroup.size ? null : el("p", { class: "page-note" }, "У этого предмета в расписании нет подгрупп со студентами."));
  };
  return block(subject, box, load, { bare: true });
}

function teacherSessionBlock(x) {
  const box = el("div", { class: "stackv" }, loadingNote());
  let d;
  const draw = () => setChildren(box,
    el("section", { class: "hero" },
      el("div", { class: "hero-top" }, el("span", {}, dateLabel(d.date)),
        el("span", { class: "num" }, d.start ? `${d.start}–${d.end}` : `в ${d.started_at}`)),
      el("div", { class: "big-line" }, el("b", {}, `${d.present} из ${d.total}`), el("span", {}, "были на паре")),
      el("div", { class: "hero-foot" }, groupsText(d.groups, d.half))),
    ...rosterCards(d, toggle),
    el("button", { class: "btn tinted block danger-text", onclick: remove }, "Удалить пару из журнала"));
  async function toggle(st, present) {
    try {
      d = await api(`/api/teacher/sessions/${x.id}/marks/${st.id}`, { method: "PUT", body: { present } });
      haptic();
    } catch (e) { toast(e.message); }
    draw();
  }
  async function remove() {
    if (!(await confirmDialog(`Удалить «${d.subject}» из журнала? Отметки этой пары пропадут.`))) return;
    try {
      await api(`/api/teacher/sessions/${x.id}`, { method: "DELETE" });
      toast("Удалено");
      renderTeacher();
    } catch (e) { toast(e.message); }
  }
  const load = async () => {
    try { d = await api(`/api/teacher/sessions/${x.id}`); draw(); }
    catch (e) { setChildren(box, errorNote(e)); }
  };
  return block(x.subject, box, load, { bare: true });
}

function teacherJournalBlock() {
  const box = el("div", { class: "stackv" }, loadingNote());
  const load = async () => {
    let list;
    try { list = await api("/api/teacher/sessions"); } catch (e) { setChildren(box, errorNote(e)); return; }
    const byDate = new Map();
    for (const x of list) {
      if (!byDate.has(x.date)) byDate.set(x.date, []);
      byDate.get(x.date).push(x);
    }
    setChildren(box, ...[...byDate].map(([date, items]) => el("section", { class: "panel list" },
      el("div", { class: "panel-top pad" }, el("span", { class: "eyebrow" }, dateLabel(date))),
      ...items.map((x) => sessionRow(x, false)))));
  };
  return block("Журнал", box, load, { bare: true });
}

// --- вход и регистрация ------------------------------------------------------

let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  renderInstallHint();
});

const isStandalone = () => window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

const SHARE_ICON = '<path d="M12 3v12"/><path d="m7 8 5-5 5 5"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/>';

// В браузере — как поставить на экран «Домой»; в приложении не нужно
function installHint() {
  if (NATIVE || isStandalone()) return null;
  if (installPrompt) {
    return el("button", { class: "btn tinted block", onclick: async () => {
      installPrompt.prompt();
      await installPrompt.userChoice.catch(() => null);
      installPrompt = null;
      renderInstallHint();
    } }, "📲 Установить приложение");
  }
  const share = document.createElement("span");
  share.innerHTML = `<svg class="icon inline-icon" viewBox="0 0 24 24">${SHARE_ICON}</svg>`;
  return el("div", { class: "install" },
    el("b", {}, "📲 Установи на экран «Домой»"),
    isIOS()
      ? el("div", {}, "В Safari нажми «Поделиться» ", share.firstChild, " внизу экрана → «На экран „Домой“».")
      : el("div", {}, "Открой меню браузера ⋮ → «Установить приложение» или «Добавить на главный экран»."));
}

function renderInstallHint() {
  const box = $("install-hint");
  if (box) setChildren(box, installHint() || "");
}

function showOnly(id) {
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== id;
  $("tabs").hidden = true;
  setBackButton(null);
  window.scrollTo(0, 0);
}

// ФИО — каждое слово в своём поле. Первая буква сама становится заглавной
const NAME_WORD = /^[\p{L}]+(?:[-'’.][\p{L}]*)*$/u;
const capFirst = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const letters = (s) => (s.match(/\p{L}/gu) || []).length;
function fioFields(fullName = "") {
  const [last = "", first = "", ...rest] = fullName.split(/\s+/).filter(Boolean);
  const make = (value, autocomplete, placeholder) => el("input", {
    value, autocomplete, placeholder, autocapitalize: "words", spellcheck: "false", maxlength: 60 });
  const inputs = {
    last: make(last, "family-name", "Иванов"),
    first: make(first, "given-name", "Иван"),
    middle: make(rest.join(" "), "additional-name", "Иванович"),
  };
  const words = () => [inputs.last, inputs.first, inputs.middle].map((i) => capFirst(i.value.trim().replace(/\s+/g, " ")));
  return {
    inputs,
    nodes: [
      field("Фамилия", inputs.last),
      field("Имя", inputs.first),
      field("Отчество", inputs.middle, "если есть; как в списке группы"),
    ],
    value: () => words().filter(Boolean).join(" "),
    // Текст ошибки или null
    problem() {
      const [l, f, m] = words();
      if (!l) return "Напиши фамилию";
      if (!f) return "Напиши имя";
      if (![l, f, ...m.split(" ")].filter(Boolean).every((w) => NAME_WORD.test(w))) return "В ФИО — только буквы и дефис";
      if (letters(l) < 2 || letters(f) < 2) return "Фамилию и имя — полностью, не инициалами";
      return null;
    },
  };
}

// Правила пароля списком: выполненные отмечаются галочкой по мере ввода
const PASSWORD_RULES = [
  ["хотя бы 8 символов", (v) => v.length >= 8],
  ["заглавная буква", (v) => /\p{Lu}/u.test(v)],
  ["строчная буква", (v) => /\p{Ll}/u.test(v)],
  ["цифра", (v) => /\d/.test(v)],
];
function passwordRules(input) {
  const items = PASSWORD_RULES.map(([label]) => el("li", {}, icon("tick"), label));
  const node = el("ul", { class: "pass-rules", "aria-label": "Правила пароля" }, ...items);
  const draw = () => PASSWORD_RULES.forEach(([, ok], i) => items[i].classList.toggle("ok", ok(input.value)));
  input.addEventListener("input", draw);
  draw();
  return {
    node,
    problem: () => (/\s/.test(input.value) ? "Пароль без пробелов"
      : PASSWORD_RULES.every(([, ok]) => ok(input.value)) ? null : "Пароль не подходит под правила"),
  };
}

const field = (label, input, hint) => el("label", { class: "field" },
  el("span", { class: "field-label" }, label), input, hint ? el("span", { class: "field-hint" }, hint) : null);

function deviceName() {
  if (NATIVE) return PLATFORM === "ios" ? "iPhone" : "Android";
  return (isIOS() ? "iPhone, " : "") + "браузер";
}

function showLogin(mode = "login", note = "") {
  showOnly("screen-login");
  stopWelcomePoll();
  renderInstallHint();
  $("login-title").textContent = appName();
  const body = $("login-body");
  const error = el("p", { class: "sheet-error", role: "alert" }, note);
  const serverRow = NATIVE ? el("p", { class: "note server-note" }, `Сервер: ${serverLabel()} · `,
    el("button", { class: "link-btn small", onclick: () => showServer() }, "изменить")) : null;

  if (mode === "register") {
    const fio = fioFields();
    const login = el("input", { autocomplete: "username", placeholder: "латиница или почта", autocapitalize: "none",
      spellcheck: "false", inputmode: "email" });
    const pass = el("input", { type: "password", autocomplete: "new-password", placeholder: "Придумай пароль" });
    const rules = passwordRules(pass);
    const code = el("input", { autocomplete: "off", placeholder: "необязательно", autocapitalize: "characters",
      spellcheck: "false", class: "code-text" });
    const btn = el("button", { class: "btn block", type: "submit" }, "Создать аккаунт");
    const form = el("form", { class: "login-form", onsubmit: async (e) => {
      e.preventDefault();
      error.textContent = fio.problem() || rules.problem() || "";
      if (error.textContent) { hapticResult("error"); return; }
      btn.disabled = true;
      try {
        const r = await api("/api/auth/register", { method: "POST", body: {
          full_name: fio.value(), login: login.value, password: pass.value, code: code.value.trim() || null,
          device: deviceName() } });
        await setToken(r.token);
        hapticResult("success");
        await boot();
        if (r.code_error) toast("Аккаунт создан, но код не подошёл: " + r.code_error, 6000);
        else if (r.code_result) toast(r.code_result.message, 4500);
      } catch (err) { error.textContent = err.message; hapticResult("error"); }
      finally { btn.disabled = false; }
    } },
      ...fio.nodes,
      field("Логин", login),
      field("Пароль", pass),
      rules.node,
      field("Код группы или приглашения", code, "его даёт староста; можно ввести и потом, в профиле"),
      error, btn);
    setChildren(body, form,
      el("p", { class: "note" }, "После регистрации у тебя будет личный код. Покажи его старосте — он добавит тебя в группу."),
      el("button", { class: "link-btn", onclick: () => showLogin("login") }, "Уже есть аккаунт? Войти"),
      serverRow);
    fio.inputs.last.focus();
    return;
  }

  const login = el("input", { autocomplete: "username", placeholder: "Логин", autocapitalize: "none", spellcheck: "false",
    inputmode: "email", "aria-label": "Логин" });
  const pass = el("input", { type: "password", autocomplete: "current-password", placeholder: "Пароль", "aria-label": "Пароль" });
  const btn = el("button", { class: "btn block", type: "submit" }, "Войти");
  const form = el("form", { class: "login-form", onsubmit: async (e) => {
    e.preventDefault();
    error.textContent = "";
    btn.disabled = true;
    try {
      const r = await api("/api/auth/login", { method: "POST", body: {
        login: login.value, password: pass.value, device: deviceName() } });
      await setToken(r.token);
      await boot();
    } catch (err) { error.textContent = err.message; hapticResult("error"); }
    finally { btn.disabled = false; }
  } }, login, pass, error, btn);
  setChildren(body, form,
    el("button", { class: "btn tinted block", onclick: () => showLogin("register") }, "Зарегистрироваться"),
    el("p", { class: "note" }, "Забыл пароль? Староста или админ выдаст временный."),
    serverRow);
}

// Адрес сервера — в приложении, если его не вшили при сборке
function showServer() {
  showOnly("screen-login");
  $("login-title").textContent = appName();
  const input = el("input", { value: SERVER.replace(/^https:\/\//, ""), placeholder: "schedule.example.ru",
    autocapitalize: "none", spellcheck: "false", inputmode: "url", autocomplete: "url" });
  const error = el("p", { class: "sheet-error", role: "alert" });
  const btn = el("button", { class: "btn block", type: "submit" }, "Подключиться");
  const form = el("form", { class: "login-form", onsubmit: async (e) => {
    e.preventDefault();
    const url = normalizeServer(input.value);
    if (!url) { error.textContent = "Введи адрес сервера"; return; }
    btn.disabled = true;
    error.textContent = "";
    try {
      const res = await fetch(url + "/api/info");
      const info = await res.json();
      if (info.app !== "schedule") throw new Error();
      SERVER = url;
      await store.set(SERVER_KEY, url);
      if (TOKEN) await setToken(null);
      showLogin("login");
    } catch (_) {
      error.textContent = "Не получилось подключиться. Проверь адрес — его даёт админ.";
    } finally { btn.disabled = false; }
  } }, field("Адрес сервера", input, "его даёт админ, например schedule.example.ru"), error, btn);
  setChildren($("login-body"), form);
}

// --- ещё не в группе ------------------------------------------------------------

let welcomeTimer = null;
function stopWelcomePoll() { clearInterval(welcomeTimer); welcomeTimer = null; }

function renderWelcome() {
  showOnly("screen-welcome");
  const me = state.me;
  const first = displayName(me).split(/\s+/)[1] || displayName(me);
  setChildren($("welcome-body"),
    pageHead(`Привет, ${first}!`, "Осталось попасть в свою группу"),
    codeCard(),
    enterCodeCard({ open: true, title: "Есть код группы?", hint: "или код-приглашение преподавателя" }),
    el("section", { class: "panel list" },
      listRow({ iconName: "refresh", title: "Проверить ещё раз", hint: "староста уже добавил меня", onclick: () => reloadApp(true) }),
      listRow({ iconName: "chat", title: "Написать админу", hint: "если не знаешь, кто староста", onclick: () => textSheet({
        title: "Написать админу", sub: "Сообщение уйдёт админам с твоим ФИО и кодом", placeholder: "Например: я из группы БИА 1, кто староста?",
        button: "Отправить", done: "Отправлено ✅ Ответ придёт сюда же",
        send: (text) => api("/api/me/contact", { method: "POST", body: { topic: "group", text } }),
      }) }),
      listRow({ iconName: "logout", title: "Выйти", danger: true, chevron: false, onclick: logout })));
  // Староста может добавить в любую минуту — проверяем сами
  stopWelcomePoll();
  welcomeTimer = setInterval(() => { if (!document.hidden) reloadApp(); }, 15000);
}

async function reloadApp(manual = false) {
  try {
    const fresh = await api("/api/me");
    const was = state.me;
    state.me = fresh;
    const ready = fresh.student || fresh.teacher || isAdminRole(fresh.role);
    if (!$("screen-welcome").hidden && !ready) {
      if (manual) toast("Пока не в группе. Покажи старосте свой код");
      return;
    }
    if (!was || !$("screen-welcome").hidden || was.role !== fresh.role || was.student?.group !== fresh.student?.group
      || !!was.teacher !== !!fresh.teacher) start();
    else if (currentTab === "profile") renderProfile();
  } catch (e) { if (manual) toast(e.message); }
}

// --- «жидкое стекло» -------------------------------------------------------

// Тема: светлая/тёмная как в системе. На телефоне — и цвет значков в строке состояния
function applyScheme() {
  const dark = !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.scheme = dark ? "dark" : "light";
  Plugins.SystemBars?.setStyle?.({ style: dark ? "DARK" : "LIGHT" }).catch(() => {});
}

// Преломление через SVG-фильтр в backdrop-filter умеет только Chromium (Android, браузеры на ПК).
// На iOS остаётся размытие с бликами по кромке.
const CAN_REFRACT = /Chrome\/\d+/.test(navigator.userAgent) && !/iPhone|iPad|iPod/.test(navigator.userAgent);

// Карта смещений для скруглённого прямоугольника: в центре стекло чистое,
// у кромки изображение под ним «затягивается» внутрь — как в линзе.
function glassMap(w, h, r, bezel) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(w, h);
  const hw = w / 2, hh = h / 2;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px = x + 0.5 - hw, py = y + 0.5 - hh;
      const qx = Math.abs(px) - (hw - r), qy = Math.abs(py) - (hh - r);
      let dist, nx, ny;
      if (qx > 0 && qy > 0) {
        const len = Math.hypot(qx, qy);
        dist = r - len; nx = qx / len; ny = qy / len;
      } else if (qx > qy) { dist = r - qx; nx = 1; ny = 0; }
      else { dist = r - qy; nx = 0; ny = 1; }
      nx *= Math.sign(px) || 1;
      ny *= Math.sign(py) || 1;
      const t = Math.max(0, Math.min(1, 1 - dist / bezel));
      const k = t * t * (3 - 2 * t);
      const i = (y * w + x) * 4;
      img.data[i] = 128 - nx * k * 127;
      img.data[i + 1] = 128 - ny * k * 127;
      img.data[i + 2] = 128;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

function setupLiquidGlass(bar) {
  const feImg = $("lg-map");
  const rebuild = () => {
    moveIndicator();
    if (!CAN_REFRACT) return;
    const w = Math.round(bar.offsetWidth), h = Math.round(bar.offsetHeight);
    if (!w || !h) return;
    const url = glassMap(w, h, h / 2, Math.min(20, h / 2));
    feImg.setAttribute("href", url);
    feImg.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", url);
    feImg.setAttribute("width", w);
    feImg.setAttribute("height", h);
    bar.classList.add("refract");
  };
  if (window.ResizeObserver) new ResizeObserver(rebuild).observe(bar);
  else window.addEventListener("resize", rebuild);
  rebuild();
}

// --- старт -----------------------------------------------------------------

// Экран загрузки с эмблемой и кружком: в приложении и на экране «Домой» — не меньше 6 секунд,
// за это время в фоне грузится расписание. В обычной вкладке браузера — пока грузится
const SPLASH_MIN_MS = 6000;
function hideSplash() {
  const box = $("splash");
  if (!box || box.classList.contains("gone")) return;
  const min = NATIVE || isStandalone() ? SPLASH_MIN_MS : 0;
  setTimeout(() => {
    box.classList.add("gone");
    setTimeout(() => box.remove(), 600);
  }, Math.max(0, min - performance.now()));
}

async function init() {
  const root = document.documentElement;
  root.classList.add("web");
  if (NATIVE) root.classList.add("native", "platform-" + PLATFORM);
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", applyScheme);
  applyScheme();
  // Service worker — только у сайта; в сборке приложения (mobile/www) его нет
  if (!window.NativePlugins && "serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  // Адрес сервера: в приложении — сохранённый или вшитый при сборке; в браузере — тот же сайт,
  // если сборка из mobile/www не указала другой
  SERVER = normalizeServer((NATIVE ? await store.get(SERVER_KEY) : "") || CONFIG.server || "");
  if (NATIVE) {
    Plugins.App?.addListener("backButton", onHardwareBack);
    Plugins.App?.addListener("appStateChange", ({ isActive }) => { if (isActive) onResume(); });
    await setupNativeNotifications();
  }
  TOKEN = await store.get(TOKEN_KEY);
  state.lastNoteId = Number(await store.get("mpgu_last_note")) || 0;
  Plugins.SplashScreen?.hide?.().catch(() => {});

  if (NATIVE && !SERVER) { showServer(); return; }
  if (!TOKEN) { showLogin(); return; }
  await boot();
}

// После входа: профиль с сервера и весь интерфейс
async function boot() {
  try {
    state.me = await api("/api/me");
  } catch (e) {
    if (e.status === 401) return;  // request() уже показал экран входа
    showMessage("Не удалось загрузиться",
      navigator.onLine === false ? "Нет сети. Подключись и попробуй снова." : e.message,
      el("button", { class: "btn", onclick: () => boot() }, "Повторить"),
      el("button", { class: "btn tinted", onclick: () => logout() }, "Выйти"));
    return;
  }
  bgConfigure();
  start();
}

async function onResume() {
  if (!TOKEN || !state.me) return;
  pollInbox();
  syncReminders();
  // Приложение висело в фоне до следующего дня — обновим «сегодня»
  const today = isoDate(new Date());
  if (today !== state.me.today) {
    await refreshMe();
    if (!$("screen-schedule").hidden) goToday();
  } else if (!$("screen-schedule").hidden) loadAttendance();
}

// Обработчики, которые вешаем один раз за запуск
let wired = false;
function wireOnce() {
  if (wired) return;
  wired = true;
  const bar = $("tabbar");
  for (const b of bar.querySelectorAll("button")) b.onclick = () => { haptic(); openTab(b.dataset.tab); };
  setupLiquidGlass(bar);
  $("week-prev").onclick = () => shiftWeek(-1);
  $("week-next").onclick = () => shiftWeek(1);
  $("go-today").onclick = goToday;
  setupSwipe($("screen-schedule"));
  // «Сейчас», прогресс и приветствие обновляются сами
  setInterval(() => {
    if (!state.me || $("tabs").hidden) return;
    renderGreeting();
    if (!$("screen-schedule").hidden && state.week?.days[state.selectedDay]?.date === state.week.today) {
      renderDay();
      loadAttendance();
    }
  }, 30000);
  setInterval(pollInbox, 60000);
  // Вернулись в приложение — вдруг преподаватель уже открыл отметку, а староста что-то написал
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !state.me || $("tabs").hidden) return;
    pollInbox();
    if (!$("screen-schedule").hidden) loadAttendance();
  });
}

// Интерфейс под пользователя: вкладки по ролям. Вызывается и после смены роли или группы
async function start() {
  stopWelcomePoll();
  const me = state.me;
  const isAdmin = isAdminRole(me.role);
  const isStaff = isAdmin || me.role === "starosta";
  const isTeacher = !!me.teacher;
  setUnread(me.unread);
  if (!me.student && !isAdmin && !isTeacher) {
    renderWelcome();
    return;
  }
  $("tab-code").hidden = !isTeacher;
  $("tab-teacher").hidden = !isTeacher;
  $("tab-admin").hidden = !isStaff;

  const today = todayTarget();
  state.date = today.date;
  state.selectedDay = today.day;
  state.week = null;
  state.group = null;

  // Шапка: приветствие по имени и аватарка (нажатие — в профиль)
  renderGreeting();
  const avatar = $("me-avatar");
  setChildren(avatar, avatarNode(displayName(me) || "?", me.photo));
  avatar.onclick = () => { haptic(); openTab("profile"); };

  state.groups = [];
  if (isStaff) {
    $("tab-admin-label").textContent = isAdmin ? "Я админ" : "Я староста";
    try { state.groups = await api("/api/admin/groups"); } catch (_) { state.groups = []; }
  }
  // Преподавателю без группы — сразу его пары. Подгруппу студента менять нельзя;
  // любую группу выбирает только админ, который сам не студент
  const options = [];
  if (isTeacher && me.student) options.push(["", "Моя группа"]);  // студент и преподаватель сразу — группа первой
  if (isTeacher) options.push([MINE, "Мои пары"]);
  if (isAdmin && !me.student) options.push(...state.groups.map((g) => [g, formatGroup(g)]));
  setupGroupPicker(options);

  $("tabs").hidden = false;
  wireOnce();
  // Админу без своей группы и без групп вообще — сразу админка, преподавателю — сразу код
  const noSchedule = isAdmin && !me.student && !isTeacher && !state.groups.length;
  openTab(isTeacher && !me.student ? "code" : noSchedule ? "admin" : "schedule");
  moveIndicator();
  pollInbox();
  // Разрешение на уведомления спрашиваем после входа: и для напоминаний, и для ленты в фоне
  askNotificationPermission().then(() => syncReminders(true));
}

init().catch((e) => console.error(e)).finally(hideSplash);
