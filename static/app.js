const state = { services: [], query: '' };
const grid = document.querySelector('#serviceGrid');
const empty = document.querySelector('#emptyState');
const count = document.querySelector('#serviceCount');
const onlineCount = document.querySelector('#onlineCount');
const scanTime = document.querySelector('#scanTime');
const search = document.querySelector('#searchInput');
const dialog = document.querySelector('#addDialog');
const form = document.querySelector('#addForm');
const toasts = document.querySelector('#toasts');
document.querySelector('#hubHost').textContent = window.location.hostname;

const icons = {
  power: '<path d="M12 2v10m6.4-6.4a9 9 0 1 1-12.8 0"/>',
  kvm: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01"/>',
  custom: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
};
const colors = { power: '#65d6ad', kvm: '#9b8cff', server: '#65a8ff', custom: '#ffb85c', web: '#7fc9e8' };

function esc(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function toast(message, error = false) {
  const item = document.createElement('div');
  item.className = `toast${error ? ' error' : ''}`;
  item.textContent = message;
  toasts.append(item);
  setTimeout(() => item.remove(), 3500);
}

function displayPort(service) {
  try { return new URL(service.url).port || (service.url.startsWith('https:') ? '443' : '80'); } catch { return service.port || '—'; }
}

function render() {
  const query = state.query.trim().toLowerCase();
  const shown = state.services.filter(service => `${service.name} ${service.description} ${service.url} ${service.port || ''}`.toLowerCase().includes(query));
  count.textContent = `${shown.length} found`;
  onlineCount.textContent = state.services.filter(service => service.online).length;
  empty.hidden = shown.length > 0;
  grid.hidden = shown.length === 0;
  grid.innerHTML = shown.map(service => {
    const kind = icons[service.kind] ? service.kind : 'web';
    const deletable = service.source === 'manual';
    return `<div class="service-wrap">
      ${deletable ? `<button class="delete-button" data-delete="${esc(service.id)}" aria-label="Remove ${esc(service.name)}"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg></button>` : ''}
      <a class="service-card" href="${esc(service.url)}" target="_blank" rel="noopener" style="--accent:${colors[kind]}">
        <div class="card-top">
          <span class="service-icon"><svg viewBox="0 0 24 24">${icons[kind]}</svg></span>
          <span class="status ${service.online ? 'online' : ''}">${service.online ? 'Online' : 'Offline'}</span>
        </div>
        <h3>${esc(service.name)}</h3>
        <p>${esc(service.description || `Service on port ${displayPort(service)}`)}</p>
        <div class="card-footer"><span>${esc(new URL(service.url).protocol.replace(':','').toUpperCase())} · PORT ${displayPort(service)}</span><span class="open-arrow">↗</span></div>
      </a>
    </div>`;
  }).join('');
}

async function load(force = false) {
  const button = document.querySelector('#refreshButton');
  button.disabled = true;
  try {
    const payload = await api(force ? '/api/refresh' : '/api/services');
    state.services = payload.services;
    scanTime.textContent = `Scanned ${new Date(payload.scanned_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    render();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

search.addEventListener('input', () => { state.query = search.value; render(); });
document.querySelector('#refreshButton').addEventListener('click', () => load(true));
document.querySelector('#addButton').addEventListener('click', () => dialog.showModal());
document.querySelector('#closeDialog').addEventListener('click', () => dialog.close());

form.addEventListener('submit', async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  try {
    await api('/api/services', { method: 'POST', body: JSON.stringify(data) });
    form.reset();
    dialog.close();
    toast(`${data.name} added`);
    await load(true);
  } catch (error) { toast(error.message, true); }
});

grid.addEventListener('click', async event => {
  const button = event.target.closest('[data-delete]');
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const service = state.services.find(item => item.id === button.dataset.delete);
  if (!service || !window.confirm(`Remove ${service.name} from Service Hub?`)) return;
  try {
    await api(`/api/services/${encodeURIComponent(service.id)}`, { method: 'DELETE' });
    toast(`${service.name} removed`);
    await load(true);
  } catch (error) { toast(error.message, true); }
});

load();
setInterval(() => load(), 30000);
