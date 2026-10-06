// Фоновая проверка уведомлений. Система запускает её примерно раз в 15 минут, пока приложение
// закрыто (на iOS — когда сочтёт нужным). Отдельный JS-движок: нет DOM, есть fetch, CapacitorKV
// и CapacitorNotifications. Состояние между запусками — только в CapacitorKV.

const kv = (key) => {
  try { return (CapacitorKV.get(key) || {}).value || ""; } catch (_) { return ""; }
};

// Приложение передаёт адрес сервера, токен и номер последнего показанного уведомления
addEventListener("configure", (resolve, reject, args) => {
  try {
    const { server = "", token = "", lastId = 0 } = args || {};
    if (token) {
      CapacitorKV.set("server", server);
      CapacitorKV.set("token", token);
    } else {
      CapacitorKV.remove("token");
    }
    if (Number(lastId) > Number(kv("lastId") || 0)) CapacitorKV.set("lastId", String(lastId));
    resolve();
  } catch (e) {
    reject(e);
  }
});

addEventListener("check", async (resolve, reject) => {
  try {
    const server = kv("server");
    const token = kv("token");
    if (!server || !token) { resolve(); return; }
    const lastId = Number(kv("lastId") || 0);
    const res = await fetch(`${server}/api/notifications/new?after_id=${lastId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      if (res.status === 401) CapacitorKV.remove("token");  // вышли из аккаунта или сессия истекла
      resolve();
      return;
    }
    const data = await res.json();
    const items = data.items || [];
    if (items.length) {
      // Отрицательные номера — чтобы не пересечься с напоминаниями о парах
      CapacitorNotifications.schedule(items.slice(-5).map((n) => ({
        id: -1 - (n.id % 2000000000),
        title: n.title,
        body: n.body,
        largeBody: n.body,
        channelId: "inbox",
        smallIcon: "ic_stat_notify",
        autoCancel: true,
        scheduleAt: new Date(Date.now() + 1000),
        extra: { kind: "inbox", id: n.id },
      })));
      CapacitorKV.set("lastId", String(items[items.length - 1].id));
    }
    resolve();
  } catch (e) {
    reject(e);
  }
});
