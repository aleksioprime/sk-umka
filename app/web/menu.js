const el = id => document.getElementById(id);
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

function options(select, items) {
  select.replaceChildren(...items.map(item => new Option(item.name, item.id)));
}

function showModel(data) {
  options(el("model"), data.models);
  el("model").value = data.active || "";
  el("model-status").textContent = data.active ? `Активна: ${el("model").selectedOptions[0]?.text}` : "Модель загружается";
}

function showMode(active) {
  el("start").hidden = active;
  el("capture-controls").hidden = !active;
  if (active) el("view").src = "/api/camera.mjpg";
  else el("view").removeAttribute("src");
}

async function destinations() {
  const data = await request("/api/capture");
  const select = el("destination");
  const previous = select.value;
  options(select, data.destinations);
  if (previous) select.value = previous;
  if (!select.value) el("photo-status").textContent = "Выберите доступный носитель";
  el("capture").disabled = !select.value;
  return data;
}

function action(id, status, callback) {
  const button = el(id);
  button.onclick = async () => {
    button.disabled = true;
    try { await callback(); }
    catch (error) { el(status).textContent = error.message; }
    finally { button.disabled = false; }
  };
}

action("apply", "model-status", async () => {
  el("model-status").textContent = "Загрузка модели…";
  try { showModel(await request("/api/models", {id: el("model").value})); }
  catch (error) {
    showModel(await request("/api/models"));
    el("model-status").textContent += `. Ошибка: ${error.message}`;
  }
});
action("start", "photo-status", async () => {
  const data = await request("/api/capture/mode", {active: true});
  showMode(data.active);
  await destinations();
});
action("finish", "photo-status", async () => {
  await request("/api/capture/mode", {active: false});
  location.assign("/" + location.search);
});
action("refresh", "photo-status", destinations);
el("destination").onchange = () => { el("capture").disabled = !el("destination").value; };
action("capture", "photo-status", async () => {
  const result = await request("/api/capture", {
    category: el("category").value, destination: el("destination").value,
  });
  el("photo-status").textContent = `Сохранено: ${el("destination").selectedOptions[0].text} / ${result.filename}`;
});

request("/api/models").then(showModel).catch(error => { el("model-status").textContent = error.message; });
destinations().then(data => showMode(data.active)).catch(error => { el("photo-status").textContent = error.message; });
