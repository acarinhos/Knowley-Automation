const $ = (id) => document.getElementById(id);
const state = { config: null, activeJob: null, timer: null };

function option(value, label) {
  const el = document.createElement('option');
  el.value = value; el.textContent = label; return el;
}

function formatNumber(value) {
  return value === null || value === undefined ? '—' : new Intl.NumberFormat('tr-TR').format(value);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function renderProviders(providers) {
  $('provider-cards').innerHTML = Object.entries(providers).map(([name, item]) => {
    const live = item.live;
    const ready = item.configured && (!live || live.reachable);
    const stateLabel = !item.configured ? 'PASİF' : !live ? 'KONTROL' : ready ? 'HAZIR' : 'ERİŞİLEMİYOR';
    const remaining = item.remaining_questions;
    const used = item.session_usage?.requests || 0;
    const models = live?.available_models?.length ? live.available_models : item.models;
    const suffix = item.remaining_is_exact ? 'kesin kalan' : 'tahmini kalan';
    return `<div class="provider-card">
      <div class="provider-top"><div class="provider-name"><span class="provider-orb">${escapeHtml(name[0].toUpperCase())}</span>${escapeHtml(name)}</div><span class="provider-state ${ready ? '' : 'off'}">${stateLabel}</span></div>
      <div class="provider-metrics"><div><strong>${formatNumber(remaining)}</strong><span>${suffix}</span></div><div><strong>${used}</strong><span>bu oturumdaki çağrı</span></div></div>
      <div class="provider-model">${escapeHtml(models?.[0] || 'Model bulunamadı')}</div>
    </div>`;
  }).join('');
}

async function loadConfig() {
  const response = await fetch('/api/config');
  state.config = await response.json();
  const cfg = state.config;
  const summary = cfg.algorithm;
  $('algorithm-version').textContent = `Sheets v${summary.source.version}`;
  $('question-version').textContent = `Soru v${cfg.question_schema_version}`;
  $('source-version').textContent = summary.source.version;
  $('category-count').textContent = summary.category_count;
  $('country-count').textContent = cfg.countries.length;
  $('subcategory-count').textContent = summary.generic_subcategory_count;
  $('sheet-link').href = summary.source.url;
  $('pool-total').textContent = summary.pool_totals['154'];
  cfg.countries.forEach(item => $('country').append(option(item.name, item.name)));
  $('languages').textContent = `${cfg.languages.length} aktif dil`;
  renderProviders(cfg.providers);
  if (cfg.latest_job && ['starting','running','stopping'].includes(cfg.latest_job.status)) watchJob(cfg.latest_job.id);
  await Promise.allSettled([loadFirebase(), probeProviders()]);
}

async function loadFirebase() {
  try {
    const response = await fetch('/api/firebase');
    const data = await response.json();
    $('firebase-count').textContent = formatNumber(data.question_count);
    $('firebase-health').textContent = data.reachable ? 'CANLI' : 'ERİŞİLEMİYOR';
    $('firebase-top-status').textContent = data.reachable ? 'Firestore bağlı' : 'Firestore erişilemiyor';
    $('firebase-dot').classList.toggle('green', Boolean(data.reachable));
  } catch {
    $('firebase-health').textContent = 'HATA';
    $('firebase-top-status').textContent = 'Firestore hatası';
    $('firebase-dot').classList.remove('green');
  }
}

async function probeProviders() {
  $('probe-button').disabled = true;
  $('probe-button').textContent = '…';
  try {
    const response = await fetch('/api/providers?live=1');
    renderProviders((await response.json()).providers);
  } finally { $('probe-button').disabled = false; $('probe-button').textContent = '↻'; }
}

function payloadFromForm() {
  const mode = document.querySelector('input[name="generation-mode"]:checked').value;
  const countryProfileEnabled = $('country-profile-enabled').checked;
  return {
    question_count: Number($('question-count').value),
    generation_mode: mode,
    difficulty: $('difficulty').value,
    country_profile_enabled: countryProfileEnabled,
    country: countryProfileEnabled ? $('country').value : '',
    combo_percent: Number($('combo-percent').value),
    languages: state.config.languages.map(language => language.code),
  };
}

function updateMode() {
  const mode = document.querySelector('input[name="generation-mode"]:checked').value;
  const count = mode === '154' ? 154 : (mode === '64' ? 64 : 21);
  $('question-count').value = count;
  $('pool-total').textContent = count;
  $('mode-caption').textContent = mode === 'basic' ? 'temel hedef havuzu' : 'kredi havuzu';
}

function updateCountryProfile() {
  const enabled = $('country-profile-enabled').checked;
  $('country-field').classList.toggle('hidden', !enabled);
  $('country').disabled = !enabled;
}

async function startJob(event) {
  event.preventDefault();
  $('form-error').textContent = '';
  $('start-button').disabled = true;
  try {
    const response = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payloadFromForm()) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Üretim başlatılamadı.');
    watchJob(data.id);
  } catch (error) {
    $('form-error').textContent = error.message;
    $('start-button').disabled = false;
  }
}

function eventMessage(event) {
  if (event.event === 'planning') return `${event.category} › ${event.subcategory}${event.combo ? ` + ${event.secondary_category}` : ''}`;
  if (event.event === 'saved') return event.question || 'Soru Firestore’a yazıldı.';
  if (event.event === 'retry') return `${event.message || 'API yeniden deneniyor'} · ${event.wait_seconds}s`;
  if (event.event === 'duplicate') return `${event.category} için tekrar soru atlandı.`;
  if (event.event === 'completed') return `${event.produced} soru başarıyla tamamlandı.`;
  if (event.event === 'stopped') return 'Üretim güvenli biçimde durduruldu.';
  if (event.event === 'failed') return event.message || 'Üretim başarısız oldu.';
  return `Sheets v${event.algorithm_version || ''} ile üretim başladı.`;
}

function renderJob(job) {
  const total = job.options.question_count || 0;
  const producedEvents = job.events.filter(e => ['saved', 'generated'].includes(e.event));
  const completed = job.summary?.produced || Math.max(0, ...producedEvents.map(e => e.produced || 0));
  const pct = total ? Math.min(100, completed / total * 100) : 0;
  $('progress-count').textContent = `${completed} / ${total}`;
  $('progress-label').textContent = job.status.toLocaleUpperCase('tr-TR');
  $('progress-bar').style.width = `${pct}%`;
  $('activity-title').textContent = ['completed','stopped','failed'].includes(job.status) ? 'Son üretim özeti' : 'Üretim devam ediyor';
  $('event-list').innerHTML = job.events.length ? [...job.events].reverse().map(event => {
    const icons = { started:'▶', planning:'⌁', saved:'✓', retry:'↻', duplicate:'≋', completed:'◆', stopped:'■', failed:'!' };
    const time = new Date((event.timestamp || 0) * 1000).toLocaleTimeString('tr-TR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
    const message = escapeHtml(eventMessage(event));
    return `<div class="event-row"><span class="event-icon">${icons[event.event] || '·'}</span><span class="event-kind">${escapeHtml(event.event)}</span><span class="event-message" title="${message}">${message}</span><span class="event-meta">${escapeHtml(event.provider || time)}</span></div>`;
  }).join('') : '<div class="empty-state"><span>⌁</span><p>İş hazırlanıyor…</p></div>';
  const active = ['starting','running','stopping'].includes(job.status);
  $('start-button').disabled = active;
  $('stop-button').classList.toggle('hidden', !active);
  if (!active) {
    clearInterval(state.timer); state.timer = null; loadFirebase();
    if (job.summary?.provider_status) renderProviders(job.summary.provider_status);
  }
}

function watchJob(id) {
  state.activeJob = id;
  clearInterval(state.timer);
  const poll = async () => {
    try {
      const response = await fetch(`/api/jobs/${id}`);
      if (response.ok) renderJob(await response.json());
    } catch (error) { $('form-error').textContent = error.message; }
  };
  poll(); state.timer = setInterval(poll, 1400);
}

async function stopJob() {
  if (!state.activeJob) return;
  await fetch(`/api/jobs/${state.activeJob}/stop`, { method: 'POST' });
}

document.querySelectorAll('input[name="generation-mode"]').forEach(input => input.addEventListener('change', updateMode));
$('country-profile-enabled').addEventListener('change', updateCountryProfile);
$('combo-percent').addEventListener('input', () => $('combo-value').textContent = `%${$('combo-percent').value}`);
$('generation-form').addEventListener('submit', startJob);
$('stop-button').addEventListener('click', stopJob);
$('probe-button').addEventListener('click', probeProviders);
loadConfig().catch(error => $('form-error').textContent = `Panel yüklenemedi: ${error.message}`);
