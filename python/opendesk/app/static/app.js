/* opendesk app — vanilla JS, no framework.
 *
 * Rendering model
 * ---------------
 * The app polls /api/state every 1.5s.  Re-rendering the whole DOM on every
 * poll causes visible flicker and clobbers focused inputs, so we use a
 * skeleton-once-then-patch approach:
 *
 *   1. A *view transition* (welcome ↔ pairing ↔ main) wipes #root and
 *      stamps a fresh template.  This happens rarely.
 *   2. *Inside* a view, each poll just updates the elements whose data
 *      changed.  We compare new HTML against the existing innerHTML and
 *      only swap when different — that's cheap and visually stable.
 *
 * Event handlers use delegation: a single click listener on #root reads
 * data-action / data-peer-name from the closest element and dispatches.
 */

const root = document.getElementById('root');
const identityEl = document.getElementById('identity');

// View mode: null = auto-decide; 'hosting' | 'controlling' = sticky.
let lastState = null;
let viewMode = loadMode();
let controllingPeer = null;
let renderedView = null;          // 'welcome' | 'pairing' | 'main' | null
let screenshotTimer = null;
let pollTimer = null;
let auditEntries = [];
let auditPending = false;
let discoveryTimer = null;
let discoveryPending = false;
let pendingDiscoverPair = null;   // { host, name } while pair modal is open

function loadMode() {
    try {
        const m = localStorage.getItem('opendesk.mode');
        return (m === 'hosting' || m === 'controlling') ? m : null;
    } catch { return null; }
}

function setMode(mode) {
    viewMode = mode;
    try {
        if (mode) localStorage.setItem('opendesk.mode', mode);
        else localStorage.removeItem('opendesk.mode');
    } catch {}
    applyModeClass();
    if (lastState) render(lastState);
}

function applyModeClass() {
    document.body.classList.remove('mode-hosting', 'mode-controlling');
    if (viewMode) document.body.classList.add(`mode-${viewMode}`);
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

async function apiGet(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return await r.json();
}

async function apiPost(path, body) {
    const r = await fetch(path, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Opendesk-Token': window.__OPENDESK_TOKEN__ || '',
        },
        body: body ? JSON.stringify(body) : '{}',
    });
    if (!r.ok) {
        let detail;
        try { detail = (await r.json()).detail; } catch { detail = await r.text(); }
        throw new Error(detail || `${r.status}`);
    }
    return await r.json();
}

async function apiDelete(path) {
    const r = await fetch(path, { method: 'DELETE' });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return await r.json();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function tpl(id) {
    return document.getElementById(id).content.cloneNode(true);
}

function setHtml(el, html) {
    /* In-place innerHTML swap, but only when content actually changed. */
    if (el && el.innerHTML !== html) el.innerHTML = html;
}

function setText(el, text) {
    if (el && el.textContent !== text) el.textContent = text;
}

function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

function fmtAge(secs) {
    if (!secs && secs !== 0) return '';
    const s = Math.round(secs);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    return `${Math.floor(s / 86400)}d`;
}

function fmtPairedAt(ts) {
    if (!ts) return '';
    return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'medium' });
}

function toast(message, kind = '') {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ---------------------------------------------------------------------------
// Top-level render dispatch
// ---------------------------------------------------------------------------

function render(state) {
    if (state.identity) {
        const fp = `<span>fp <strong>${escapeHtml(state.identity.fingerprint)}</strong></span>`;
        setHtml(identityEl, fp);
    }
    renderModeSwitcher();
    updateEnvBanner(state.host_environment);

    if (state.pairing_active) {
        ensureView('pairing');
        updatePairing(state);
        return;
    }

    // viewMode === null means "no explicit choice".  When idle (no inbound,
    // not driving anyone), show the welcome screen — that's also where
    // clicking the logo lands you, regardless of whether peers are paired.
    const idle = !state.active_session && !controllingPeer;
    if (viewMode === null && idle) {
        ensureView('welcome');
        return;
    }

    // Activity ongoing without an explicit choice → auto-default but DON'T
    // persist (so a subsequent logo click goes cleanly back to welcome).
    if (viewMode === null) {
        viewMode = state.active_session ? 'hosting' : 'controlling';
        applyModeClass();
    }

    ensureView('main');
    updateMain(state);
}

function startDiscoveryLoop() {
    if (discoveryTimer) return;
    runDiscovery(true);
    discoveryTimer = setInterval(() => runDiscovery(false), 10_000);
}

function stopDiscoveryLoop() {
    if (discoveryTimer) {
        clearInterval(discoveryTimer);
        discoveryTimer = null;
    }
}

function ensureView(view) {
    if (renderedView === view) return;
    stopDiscoveryLoop();
    root.innerHTML = '';
    if (view === 'welcome') {
        root.appendChild(tpl('tpl-welcome'));
    } else if (view === 'pairing') {
        root.appendChild(tpl('tpl-pairing'));
    } else if (view === 'main') {
        root.appendChild(tpl('tpl-main'));
    }
    renderedView = view;
    // Static descendants that won't move within this view.  We set up event
    // delegation once per view — incremental updates inside the view never
    // re-bind handlers.
    if (view !== 'welcome' && !root.dataset.delegated) {
        // (No-op; we delegate at #root level below, outside ensureView.)
    }
}

// One click handler on body handles every button via data-action (covers both
// #root and the control modal which lives outside #root).
document.body.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const peer = btn.dataset.peer || null;
    const key = btn.dataset.key || null;
    try {
        switch (action) {
            case 'begin-pair':
                setMode('hosting');
                await beginPair();
                break;
            case 'go-controlling':
                setMode('controlling');
                break;
            case 'cancel-pair':
                await cancelPair();
                break;
            case 'save-description':
                await saveSelfDescription();
                break;
            case 'clear-description':
                await clearSelfDescription();
                break;
            case 'disconnect':
                await doDisconnect();
                break;
            case 'unpair-active':
                if (peer) await doUnpair(peer);
                break;
            case 'unpair':
                if (peer) await doUnpair(peer);
                break;
            case 'unpair-all':
                await unpairAll();
                break;
            case 'control':
                if (peer) await startControlling(peer);
                break;
            case 'stop-controlling':
                await stopControlling();
                break;
            case 'set-default':
                if (peer) await setDefault(peer);
                break;
            case 'clear-default':
                await setDefault(null);
                break;
            case 'discover':
                await discover();
                break;
            case 'discover-pair': {
                pendingDiscoverPair = { host: btn.dataset.host, name: btn.dataset.peerName || '' };
                setText(document.getElementById('pair-modal-name'), pendingDiscoverPair.name || pendingDiscoverPair.host);
                setText(document.getElementById('pair-modal-host'), pendingDiscoverPair.host);
                document.getElementById('pair-modal-code').value = '';
                document.getElementById('pair-modal-name-input').value = pendingDiscoverPair.name;
                document.getElementById('pair-modal-description').value = '';
                document.getElementById('pair-modal').hidden = false;
                document.getElementById('pair-modal-code').focus();
                break;
            }
            case 'confirm-discover-pair': {
                if (!pendingDiscoverPair) break;
                const code = document.getElementById('pair-modal-code').value.trim();
                if (!code) { toast('Enter the 6-digit code', 'error'); break; }
                const nameOverride = document.getElementById('pair-modal-name-input').value.trim();
                const desc = document.getElementById('pair-modal-description').value.trim();
                const r = await apiPost('/api/pair-with', {
                    host: pendingDiscoverPair.host,
                    code,
                    name: nameOverride || pendingDiscoverPair.name,
                    description: desc,
                });
                document.getElementById('pair-modal').hidden = true;
                pendingDiscoverPair = null;
                toast(`Paired with ${r.peer_name}`, 'ok');
                await poll();
                break;
            }
            case 'cancel-discover-pair':
                document.getElementById('pair-modal').hidden = true;
                pendingDiscoverPair = null;
                break;
            case 'send-key':
                if (key) await sendKey(key);
                break;
            case 'wsl-setup':
                await applyWslSetup();
                break;
            case 'wsl-undo':
                await applyWslUndo();
                break;
            case 'wsl-enable-mirrored':
                await applyWslEnableMirrored();
                break;
            case 'copy-shutdown':
                await copyShutdownCommand();
                break;
        }
    } catch (e) {
        toast(e.message, 'error');
    }
});

function renderModeSwitcher() {
    const switcher = document.getElementById('mode-switcher');
    if (!switcher) return;
    if (viewMode === null) {
        switcher.style.display = 'none';
        return;
    }
    switcher.style.display = '';
    switcher.querySelectorAll('button').forEach(b => {
        const should = b.dataset.mode === viewMode;
        if (b.classList.contains('active') !== should) b.classList.toggle('active', should);
        if (!b._wired) {
            b._wired = true;
            b.addEventListener('click', () => setMode(b.dataset.mode));
        }
    });
}

// ---------------------------------------------------------------------------
// WSL / host-environment banner
// ---------------------------------------------------------------------------
//
// Surface the WSL2-NAT situation prominently so users don't waste time
// wondering why other devices on the LAN can't reach them.  When WSL is
// detected we show a banner with the *Windows* LAN IPs (which is what
// controllers actually need) plus a one-click button to add the port-proxy
// + firewall rule via UAC.  Outside WSL the banner stays hidden.

function updateEnvBanner(env) {
    const el = document.getElementById('env-banner');
    if (!el) return;
    // Banner only matters inside WSL.  When mirrored mode is *active*,
    // mDNS and inbound LAN traffic work natively — nothing to warn about.
    if (!env || !env.wsl || env.mirrored_active) {
        if (!el.hidden) el.hidden = true;
        return;
    }
    const ips = env.reachable_ipv4s || [];
    const port = env.server_port || 8423;
    const ipHtml = ips.length
        ? ips.map(ip => `<code>${escapeHtml(ip)}:${port}</code>`).join(' or ')
        : '<span class="muted">(none detected)</span>';

    let title, body, primary, secondary;
    if (env.mirrored_configured) {
        // Config is set but the runtime hasn't picked it up.  The user just
        // needs to bounce WSL.  This is the *most common* state after the
        // user (or our button) has written .wslconfig.
        title = '⏎ Restart WSL to finish — mirrored mode is configured but not active';
        body = `
            <p>Your <code>${escapeHtml(env.wslconfig_path || '.wslconfig')}</code>
            already has <code>networkingMode=mirrored</code>, but WSL hasn't
            been restarted since.  Once you do, this WSL instance will share
            the Windows network adapters — mDNS discovery and LAN inbound
            will Just Work.</p>
            <p class="muted small">
                In PowerShell, run <code>wsl --shutdown</code>, then reopen
                this terminal and refresh.
            </p>
        `;
        primary = `<button class="ghost" data-action="copy-shutdown">Copy 'wsl --shutdown'</button>`;
        secondary = '';
    } else {
        // Neither configured nor active — full setup needed.
        title = '⚠ Running inside WSL — LAN discovery won\'t work';
        body = `
            <p>WSL2 puts this Linux instance behind a NAT, so mDNS doesn't
            cross either way.  The robust fix is <strong>mirrored networking
            mode</strong> — one line in
            <code>${escapeHtml(env.wslconfig_path || '~/.wslconfig')}</code>
            plus a WSL restart.</p>
            <p class="muted small">
                As a fallback, you can also forward inbound TCP on the
                Windows side.  Controllers would then connect to
                ${ipHtml}.
            </p>
        `;
        primary = `<button class="primary" data-action="wsl-enable-mirrored">Enable mirrored networking</button>`;
        secondary = `
            <button class="ghost" data-action="wsl-setup">Use port forwarding instead</button>
            <button class="ghost" data-action="wsl-undo" title="Remove a previous port-forwarding rule">Undo port forwarding</button>
        `;
    }

    const html = `
        <div class="env-banner-inner">
            <div class="env-banner-title">${title}</div>
            <div class="env-banner-body">${body}</div>
            <div class="env-banner-actions row gap">
                ${primary}
                ${secondary}
            </div>
        </div>
    `;
    setHtml(el, html);
    if (el.hidden) el.hidden = false;
}

async function applyWslEnableMirrored() {
    try {
        const r = await apiPost('/api/wsl/enable-mirrored');
        if (r.wrote) {
            toast(`Wrote ${r.path} — now run "wsl --shutdown" in PowerShell.`, 'ok');
        } else {
            toast(`${r.path} already has mirrored mode — just run "wsl --shutdown".`, 'ok');
        }
        await poll();
    } catch (e) {
        toast(`Could not write .wslconfig: ${e.message}`, 'error');
    }
}

async function copyShutdownCommand() {
    try {
        await navigator.clipboard.writeText('wsl --shutdown');
        toast('"wsl --shutdown" copied — paste it into PowerShell.', 'ok');
    } catch (e) {
        toast('Run: wsl --shutdown (in PowerShell)', '');
    }
}

async function applyWslSetup() {
    toast('Look for the UAC prompt on the Windows desktop…', 'ok');
    try {
        const r = await apiPost('/api/wsl/setup');
        if (r.ok) {
            toast('Port forwarding set up.  LAN devices can now reach this host.', 'ok');
        } else {
            toast(`Setup did not complete (rc=${r.returncode}).  UAC may have been declined.`, 'error');
        }
        await poll();
    } catch (e) {
        toast(`Setup failed: ${e.message}`, 'error');
    }
}

async function applyWslUndo() {
    if (!confirm('Remove the Windows port-forwarding rule for opendesk?')) return;
    try {
        const r = await apiPost('/api/wsl/undo');
        if (r.ok) {
            toast('Port forwarding removed.', 'ok');
        } else {
            toast(`Undo did not complete (rc=${r.returncode}).`, 'error');
        }
        await poll();
    } catch (e) {
        toast(`Undo failed: ${e.message}`, 'error');
    }
}

// ---------------------------------------------------------------------------
// Pairing view
// ---------------------------------------------------------------------------

function updatePairing(state) {
    const code = state.pairing_code || '------';
    setText(document.getElementById('bigcode'), code);

    updatePairingEndpoints(state.host_environment, code);

    if (state.pairing_result) {
        if (state.pairing_result.ok) {
            toast(`Paired with ${state.pairing_result.peer_name || 'new peer'}`, 'ok');
        } else {
            toast(`Pairing failed: ${state.pairing_result.reason}`, 'error');
        }
    }
}

function updatePairingEndpoints(env, code) {
    const el = document.getElementById('pairing-endpoints');
    if (!el || !env) return;
    const ips = env.reachable_ipv4s || [];
    const port = env.server_port || 8423;
    if (!ips.length) {
        // No reachable IPs — fall back to the generic instruction.  Inside
        // WSL this happens when ipconfig.exe couldn't be reached.
        const note = env.wsl
            ? 'Could not auto-detect Windows LAN IPs.  Run <code>ipconfig.exe</code> in PowerShell and use that machine\'s IPv4 address.'
            : 'No non-loopback interfaces detected.';
        setHtml(el, `
            <p class="muted">On the controller, run:</p>
            <pre class="code-hint"><code>opendesk pair-with &lt;this-host&gt; <span class="code-strong">${escapeHtml(code)}</span></code></pre>
            <p class="muted small">${note}</p>
        `);
        return;
    }
    const rows = ips.map(ip => `
        <pre class="code-hint"><code>opendesk pair-with ${escapeHtml(ip)}:${port} <span class="code-strong">${escapeHtml(code)}</span></code></pre>
    `).join('');
    let wslNote = '';
    if (env.wsl) {
        if (env.mirrored_active) {
            wslNote = `<p class="muted small">WSL detected (mirrored networking active) — these are your <strong>Windows</strong> LAN IPs.</p>`;
        } else if (env.mirrored_configured) {
            wslNote = `<p class="muted small">WSL detected — these are your <strong>Windows</strong> LAN IPs.
                Restart WSL (see banner above) to activate mirrored networking before pairing.</p>`;
        } else {
            wslNote = `<p class="muted small">WSL detected — these are your <strong>Windows</strong> LAN IPs.
                If pairing fails with "connection refused", use the
                <strong>Set up Windows port forwarding</strong> button in the
                banner above.</p>`;
        }
    }
    setHtml(el, `
        <p class="muted">On the controller, run one of:</p>
        ${rows}
        ${wslNote}
    `);
}

async function cancelPair() {
    await apiPost('/api/pair/cancel');
    await poll();
}

// ---------------------------------------------------------------------------
// Main view — incremental updates
// ---------------------------------------------------------------------------

function updateMain(state) {
    updateSelfDescription(state);
    updateActiveSession(state.active_session);
    updateTrustedPeers(state.trusted_peers);
    updatePairedHosts(state.trusted_peers);
    updateControlPanel();
    if (viewMode === 'hosting') {
        scheduleAuditRefresh();
    }
    if (viewMode === 'controlling') {
        startDiscoveryLoop();
    }
}

function updateSelfDescription(state) {
    const ta = document.getElementById('self-description');
    if (!ta) return;
    // Don't overwrite while the user is typing.
    if (document.activeElement === ta) return;
    const desired = (state.identity && state.identity.description) || '';
    if (ta.value !== desired) ta.value = desired;
}

function updateActiveSession(s) {
    const el = document.getElementById('active-session');
    if (!el) return;
    if (!s) {
        setHtml(el, '<p class="muted">No one is connected.</p>');
        return;
    }
    const html = `
        <div class="session-card">
            <div class="session-headline">${escapeHtml(s.peer_name || '(unnamed)')}</div>
            <div class="session-meta">
                from ${escapeHtml(s.remote_addr)} ·
                connected ${fmtAge(s.age_seconds)} ago ·
                fp ${escapeHtml(s.peer_fingerprint)}
            </div>
            <div class="row gap">
                <button class="ghost" data-action="disconnect">Disconnect</button>
                <button class="danger ghost" data-action="unpair-active" data-peer="${escapeHtml(s.peer_name)}">Unpair</button>
            </div>
        </div>
    `;
    setHtml(el, html);
}

function updateTrustedPeers(peers) {
    const el = document.getElementById('trusted-peers');
    if (!el) return;
    const unpairAll = document.getElementById('btn-unpair-all');
    if (unpairAll) {
        const want = peers.length ? '' : 'none';
        if (unpairAll.style.display !== want) unpairAll.style.display = want;
    }
    if (!peers.length) {
        setHtml(el, '<p class="muted">None paired.</p>');
        return;
    }
    const html = peers.map(p => `
        <div class="peer-row">
            <div class="peer-main">
                <div class="peer-name">${escapeHtml(p.name || '(unnamed)')}${p.is_default ? ' <span class="badge default">default</span>' : ''}</div>
                <div class="peer-meta muted small">${escapeHtml(p.fingerprint)} · paired ${escapeHtml(fmtPairedAt(p.paired_at))}</div>
                ${p.description ? `<div class="peer-desc muted small">${escapeHtml(p.description.split('\\n')[0])}</div>` : ''}
            </div>
            <div class="peer-actions row gap">
                <button class="danger ghost" data-action="unpair" data-peer="${escapeHtml(p.name)}">Unpair</button>
            </div>
        </div>
    `).join('');
    setHtml(el, html);
}

function updatePairedHosts(peers) {
    const el = document.getElementById('paired-hosts');
    if (!el) return;
    if (!peers.length) {
        setHtml(el, '<p class="muted">Nothing paired yet — use the form below.</p>');
        return;
    }
    const html = peers.map(p => {
        const isActive = controllingPeer === p.name;
        const badges = [];
        if (p.is_default) badges.push('<span class="badge default">default</span>');
        if (p.outbound_active) badges.push('<span class="badge active">connected</span>');
        return `
            <div class="peer-row${isActive ? ' active' : ''}">
                <div class="peer-main">
                    <div class="peer-name">${escapeHtml(p.name || '(unnamed)')} ${badges.join(' ')}</div>
                    <div class="peer-meta muted small">${escapeHtml(p.fingerprint)}</div>
                    ${p.description ? `<div class="peer-desc muted small">${escapeHtml(p.description.split('\\n')[0])}</div>` : ''}
                </div>
                <div class="peer-actions row gap">
                    ${isActive
                        ? `<button class="ghost" data-action="stop-controlling">Stop</button>`
                        : `<button class="primary" data-action="control" data-peer="${escapeHtml(p.name)}">Control</button>`}
                    ${p.is_default
                        ? `<button class="ghost" data-action="clear-default">Clear default</button>`
                        : `<button class="ghost" data-action="set-default" data-peer="${escapeHtml(p.name)}">Make default</button>`}
                    <button class="danger ghost" data-action="unpair" data-peer="${escapeHtml(p.name)}">Unpair</button>
                </div>
            </div>
        `;
    }).join('');
    setHtml(el, html);
}

function updateControlPanel() {
    const modal = document.getElementById('control-modal');
    if (!modal) return;
    if (controllingPeer) {
        setText(document.getElementById('control-peer'), controllingPeer);
        modal.hidden = false;
        attachScreenInputs();
        startScreenshotLoop();
    } else {
        modal.hidden = true;
        stopScreenshotLoop();
    }
}

// ---------------------------------------------------------------------------
// Audit — fetched separately, but only when in hosting mode
// ---------------------------------------------------------------------------

function scheduleAuditRefresh() {
    if (auditPending) return;
    auditPending = true;
    apiGet('/api/audit?limit=50')
        .then(data => {
            auditPending = false;
            auditEntries = data.entries || [];
            renderAudit();
        })
        .catch(e => {
            auditPending = false;
            const el = document.getElementById('audit-table');
            if (el) setHtml(el, `<p class="muted small">audit unavailable: ${escapeHtml(e.message)}</p>`);
        });
}

function renderAudit() {
    const el = document.getElementById('audit-table');
    if (!el) return;
    if (!auditEntries.length) {
        setHtml(el, '<p class="muted small">No activity yet.</p>');
        return;
    }
    const rows = auditEntries.slice().reverse().map(e => {
        const t = new Date((e.ts || 0) * 1000).toLocaleTimeString();
        const peer = (e.peer && (e.peer.name || e.peer.fp)) || '?';
        const kind = e.type;
        let outcome = e.outcome || '';
        if (e.outcome === 'error' && e.error_code) outcome = `error/${e.error_code}`;
        const summary = e.summary || (
            e.type === 'session.opened' ? `from ${e.remote_addr}` :
            e.type === 'session.closed' ? `${fmtAge(e.duration)} session` :
            e.type === 'session.rejected' ? e.reason :
            ''
        );
        const cls = e.outcome === 'error' ? 'audit-row-error' : 'audit-row-ok';
        return `<tr class="${cls}">
            <td>${t}</td>
            <td>${escapeHtml(peer)}</td>
            <td>${escapeHtml(kind)}</td>
            <td>${escapeHtml(outcome)}</td>
            <td>${escapeHtml(summary || '')}</td>
        </tr>`;
    }).join('');
    setHtml(el, `
        <table class="audit-table">
            <thead><tr>
                <th>time</th><th>peer</th><th>event</th><th>outcome</th><th>detail</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`);
}

// ---------------------------------------------------------------------------
// Action handlers
// ---------------------------------------------------------------------------

async function beginPair() {
    const r = await apiPost('/api/pair/begin');
    toast(`Pairing code: ${r.code}`, 'ok');
    await poll();
}

async function saveSelfDescription() {
    const ta = document.getElementById('self-description');
    if (!ta) return;
    await apiPost('/api/describe', { text: ta.value });
    toast('Description saved', 'ok');
}

async function clearSelfDescription() {
    await apiPost('/api/describe', { clear: true });
    const ta = document.getElementById('self-description');
    if (ta) ta.value = '';
    toast('Description cleared', 'ok');
}

async function doDisconnect() {
    await apiPost('/api/disconnect');
    toast('Disconnected', 'ok');
    await poll();
}

async function doUnpair(name) {
    if (!confirm(`Unpair ${name}?  Revokes trust and disconnects if active.`)) return;
    await apiPost('/api/unpair', { name });
    toast(`Unpaired ${name}`, 'ok');
    if (controllingPeer === name) await stopControlling();
    await poll();
}

async function unpairAll() {
    if (!confirm('Unpair every peer?  This revokes all trust.')) return;
    const r = await apiPost('/api/unpair-all');
    toast(`Unpaired ${r.unpaired} peer(s)`, 'ok');
    await stopControlling();
    await poll();
}


async function runDiscovery(showSpinner) {
    if (discoveryPending) return;
    const el = document.getElementById('discovered');
    if (!el) return;
    discoveryPending = true;
    if (showSpinner) setHtml(el, '<p class="muted">scanning…</p>');
    try {
        const r = await apiGet('/api/discover?timeout=2');
        if (!r.peers.length) {
            const env = lastState && lastState.host_environment;
            const wslHint = env && env.wsl
                ? `<p class="muted small">WSL2 blocks mDNS — peers on the LAN
                   won't appear here.  Use <strong>Pair with a new host</strong>
                   above with the host's IP + 6-digit code.</p>`
                : '';
            setHtml(el, `<p class="muted">No peers found on the LAN.</p>${wslHint}`);
        } else {
            const pairedFps = new Set(
            (lastState && lastState.trusted_peers || []).map(p => p.fingerprint)
        );
        const html = r.peers.map(p => {
            const isPaired = pairedFps.has(p.fingerprint);
            const action = isPaired
                ? `<span class="badge already-paired">Paired</span>`
                : `<button class="ghost" data-action="discover-pair"
                       data-host="${escapeHtml(p.host)}:${p.port}"
                       data-peer-name="${escapeHtml(p.name)}">Pair</button>`;
            return `
                <div class="peer-row">
                    <div class="peer-main">
                        <div class="peer-name">${escapeHtml(p.name)}</div>
                        <div class="peer-meta muted small">${escapeHtml(p.host)}:${p.port} · ${escapeHtml(p.fingerprint)}</div>
                        ${p.description ? `<div class="peer-desc muted small">${escapeHtml(p.description)}</div>` : ''}
                    </div>
                    <div class="peer-actions row gap">${action}</div>
                </div>`;
        }).join('');
        setHtml(el, html);
        }
    } catch (e) {
        setHtml(el, `<p class="muted">discover failed: ${escapeHtml(e.message)}</p>`);
    } finally {
        discoveryPending = false;
    }
}

async function discover() {
    await runDiscovery(true);
}

async function setDefault(name) {
    await apiPost('/api/peers/default', name ? { name } : { clear: true });
    toast(name ? `Default → ${name}` : 'Default cleared', 'ok');
    await poll();
}

// ---------------------------------------------------------------------------
// Controlling — screenshot + actions
// ---------------------------------------------------------------------------

async function startControlling(name) {
    await apiPost('/api/connect', { peer: name });
    controllingPeer = name;
    toast(`Now controlling ${name}`, 'ok');
    await poll();
}

async function stopControlling() {
    if (!controllingPeer) return;
    const name = controllingPeer;
    controllingPeer = null;
    stopScreenshotLoop();
    try { await apiDelete(`/api/peer/${encodeURIComponent(name)}`); } catch {}
    await poll();
}

function startScreenshotLoop() {
    if (screenshotTimer) return;
    pullScreenshot();
    screenshotTimer = setInterval(pullScreenshot, 1000);
}

function stopScreenshotLoop() {
    if (screenshotTimer) {
        clearInterval(screenshotTimer);
        screenshotTimer = null;
    }
}

async function pullScreenshot() {
    if (!controllingPeer) return;
    const img = document.getElementById('screen');
    const status = document.getElementById('screen-status');
    if (!img) return;
    try {
        const r = await fetch(`/api/peer/${encodeURIComponent(controllingPeer)}/screenshot`);
        if (!r.ok) {
            if (r.status === 410) {
                toast('Host evicted us', 'error');
                await stopControlling();
                return;
            }
            throw new Error(`${r.status}`);
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        img.dataset.logicalWidth = r.headers.get('X-Logical-Width') || '';
        img.dataset.logicalHeight = r.headers.get('X-Logical-Height') || '';
        img.onload = () => URL.revokeObjectURL(url);
        img.src = url;
        if (status) setText(status, `${img.dataset.logicalWidth}×${img.dataset.logicalHeight}`);
    } catch (e) {
        if (status) setText(status, `error: ${e.message}`);
    }
}

function attachScreenInputs() {
    const img = document.getElementById('screen');
    if (!img || img._wired) return;
    img._wired = true;
    img.addEventListener('click', async (ev) => {
        if (!controllingPeer) return;
        const rect = img.getBoundingClientRect();
        const lw = parseFloat(img.dataset.logicalWidth) || rect.width;
        const lh = parseFloat(img.dataset.logicalHeight) || rect.height;
        const x = ((ev.clientX - rect.left) / rect.width) * lw;
        const y = ((ev.clientY - rect.top) / rect.height) * lh;
        try {
            await apiPost(`/api/peer/${encodeURIComponent(controllingPeer)}/action`,
                          { kind: 'click', x, y });
        } catch (e) { toast(e.message, 'error'); }
    });

    const typeInput = document.getElementById('type-input');
    if (typeInput && !typeInput._wired) {
        typeInput._wired = true;
        typeInput.addEventListener('keydown', async (ev) => {
            if (ev.key !== 'Enter') return;
            const text = typeInput.value;
            if (!text) return;
            try {
                await apiPost(`/api/peer/${encodeURIComponent(controllingPeer)}/action`,
                              { kind: 'type', text });
                typeInput.value = '';
            } catch (e) { toast(e.message, 'error'); }
        });
    }
}

async function sendKey(keysym) {
    if (!controllingPeer) return;
    await apiPost(`/api/peer/${encodeURIComponent(controllingPeer)}/action`,
                  { kind: 'key', keysym });
}

// Helper used by the data-key buttons in the control panel.
document.body.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-key]');
    if (!btn) return;
    sendKey(btn.dataset.key).catch(e => toast(e.message, 'error'));
});

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

async function poll() {
    try {
        const s = await apiGet('/api/state');
        lastState = s;
        render(s);
    } catch (e) {
        setHtml(root, `<p class="muted">backend unreachable: ${escapeHtml(e.message)}</p>`);
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(poll, 1500);
    poll();
}

// Click the logo to go back to the welcome screen.  Clears the sticky
// view mode; render() will land on welcome when idle, or auto-resolve
// (without persisting) when something is active.
const brandEl = document.getElementById('brand');
if (brandEl) {
    brandEl.addEventListener('click', () => setMode(null));
}

// Pair modal — Enter submits, Escape cancels.
document.getElementById('pair-modal-code').addEventListener('keydown', e => {
    if (e.key === 'Enter')  document.querySelector('[data-action="confirm-discover-pair"]').click();
    if (e.key === 'Escape') document.querySelector('[data-action="cancel-discover-pair"]').click();
});

applyModeClass();
startPolling();
