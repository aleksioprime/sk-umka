"use strict";

const MQTT_RESULTS = {
  accepted: "Принято",
  unregistered: "Устройство не зарегистрировано",
  invalid: "Некорректное сообщение",
  unsupported: "Неподдерживаемый топик",
  duplicate: "Повтор события",
};

let mqttVersion = 0;
let mqttQuery;

function setAdmin(allowed) {
  for (const controls of document.querySelectorAll("[data-admin]")) controls.hidden = !allowed;
  if (allowed) return;
  $("register-dialog").close();
  $("mqtt-dialog").close();
  mqttVersion++;
  $("mqtt-list").replaceChildren();
  $("admin-result").hidden = true;
}

function showAdminResult(message) {
  $("admin-result").textContent = message;
  $("admin-result").hidden = false;
}

function formError(form, message = "") {
  const feedback = form.querySelector(".error");
  feedback.textContent = message;
  feedback.hidden = !message;
}

function resetDeviceManagement() {
  for (const id of ["device-edit-form", "device-delete-form"]) {
    $(id).hidden = true;
    formError($(id));
  }
}

function submitForm(id, action) {
  const form = $(id);
  const button = form.querySelector("[type=submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    formError(form);
    try {
      await action(Object.fromEntries(new FormData(form)));
    } catch (error) {
      formError(form, error.message);
    } finally {
      button.disabled = false;
    }
  });
}

function mqttEntry(message) {
  const item = document.createElement("li");
  item.className = "card mqtt-entry";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = message.topic;
  const meta = document.createElement("small");
  const result = MQTT_RESULTS[message.result] || message.result;
  meta.textContent = [time(message.received_at), result, `QoS ${message.qos}`,
    message.retained && "retained", `${number(message.size)} Б`].filter(Boolean).join(" · ");
  const payload = document.createElement("pre");
  details.append(summary, meta, payload);
  item.append(details);
  let loading = false;
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded || loading) return;
    loading = true;
    payload.textContent = "Загрузка сообщения…";
    try {
      const data = await api(`/api/mqtt/messages/${message.id}`);
      payload.textContent = data.encoding === "base64" ? `Base64\n${data.payload}` : data.payload || "(пустое сообщение)";
      loaded = true;
    } catch (error) {
      payload.textContent = error.message;
    } finally {
      loading = false;
    }
  });
  return item;
}

async function loadMqtt(before = null) {
  const version = ++mqttVersion;
  const list = $("mqtt-list");
  const more = $("mqtt-more");
  const message = $("mqtt-message");
  const query = new URLSearchParams(mqttQuery);
  if (before) query.set("before", before);
  else list.replaceChildren();
  more.hidden = true;
  message.textContent = "Загрузка…";
  try {
    const data = await api(`/api/mqtt/messages?${query}`);
    if (version !== mqttVersion) return;
    list.append(...data.items.map(mqttEntry));
    message.textContent = list.children.length ? `Показано сообщений: ${list.children.length}` : "Сообщений по этим фильтрам нет";
    more.hidden = !data.next;
    more.textContent = "Показать ещё";
    more.onclick = () => loadMqtt(data.next);
  } catch (error) {
    if (version !== mqttVersion) return;
    message.textContent = `Журнал недоступен. ${error.message}`;
    more.hidden = false;
    more.textContent = "Повторить";
    more.onclick = () => loadMqtt(before);
  }
}

function applyMqttFilters() {
  mqttQuery = new URLSearchParams();
  for (const [key, value] of new FormData($("mqtt-filters"))) {
    if (value.trim()) mqttQuery.set(key, value.trim());
  }
  loadMqtt();
}

function setupAdmin() {
  for (const button of document.querySelectorAll(".admin-dialog [data-close]")) {
    button.addEventListener("click", () => button.closest("dialog").close());
  }
  $("register-device").addEventListener("click", () => {
    $("device-register-form").reset();
    formError($("device-register-form"));
    $("register-dialog").showModal();
  });
  submitForm("device-register-form", async (fields) => {
    const device = await api("/api/devices", "POST", { id: fields.id.trim(), name: fields.name.trim() });
    $("register-dialog").close();
    showAdminResult(`Устройство ${device.id} зарегистрировано`);
    await refresh();
    openPanel(device.id);
  });
  $("edit-device").addEventListener("click", () => {
    const device = devices.find((item) => item.id === selected?.id);
    if (!device) return;
    resetDeviceManagement();
    $("device-edit-form").hidden = false;
    $("edit-device-name").value = device.name || device.id;
    $("edit-device-name").focus();
  });
  $("delete-device").addEventListener("click", () => {
    if (!selected) return;
    resetDeviceManagement();
    $("delete-device-confirm").textContent = `Удалить ${selected.id} из мониторинга? История сохранится. Чтобы вернуть устройство, зарегистрируйте тот же идентификатор.`;
    $("device-delete-form").hidden = false;
    $("device-delete-form").querySelector("[data-cancel]").focus();
  });
  for (const [form, button] of [["device-edit-form", "edit-device"], ["device-delete-form", "delete-device"]]) {
    $(form).querySelector("[data-cancel]").addEventListener("click", () => {
      resetDeviceManagement();
      $(button).focus();
    });
  }
  submitForm("device-edit-form", async ({name}) => {
    const id = selected?.id;
    if (!id) return;
    await api(`/api/devices/${encodeURIComponent(id)}`, "PATCH", { name: name.trim() });
    if (selected?.id === id) {
      resetDeviceManagement();
      $("edit-device").focus();
    }
    await refresh();
  });
  submitForm("device-delete-form", async () => {
    const id = selected?.id;
    if (!id) return;
    await api(`/api/devices/${encodeURIComponent(id)}`, "DELETE");
    if (selected?.id === id) {
      closePanel(false);
      $("register-device").focus();
    }
    showAdminResult(`Устройство ${id} удалено из мониторинга`);
    await refresh();
  });
  $("open-mqtt").addEventListener("click", () => {
    $("mqtt-dialog").showModal();
    applyMqttFilters();
  });
  $("mqtt-dialog").addEventListener("close", () => { mqttVersion++; });
  $("mqtt-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    applyMqttFilters();
  });
}
