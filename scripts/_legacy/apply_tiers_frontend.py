# -*- coding: utf-8 -*-
"""重构：分层提供商条目（每层可多个，模型列表勾选）HTML + JS 一次性改造"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============ 1) index.html 三段 form-card 替换 ============
p = 'frontend/index.html'
src = open(p, encoding='utf-8').read()

old1 = '''                    <div class="form-card" style="margin-top:6px">
                        <label>提供商名称（可改名，用于管理）</label>
                        <input type="text" id="setMainName" placeholder="未命名">
                        <label>API 地址（可输 base 或完整 /chat/completions 全称）</label>
                        <input type="text" id="setMainBaseUrl" placeholder="https://api.deepseek.com/v1 或完整 https://api.deepseek.com/v1/chat/completions">
                        <label>API Key（留空 = 保持已有 Key）</label>
                        <input type="password" id="setMainKey" placeholder="sk-...">
                        <div class="model-row"><label>模型名称</label><button class="btn-sm" onclick="fetchModels('main')" title="从 API 拉取可用模型">🔍 获取模型</button></div>
                        <input type="text" id="setMainModel" list="mainModelList" placeholder="可手输，或点「获取模型」自动拉取">
                        <datalist id="mainModelList"></datalist>'''
new1 = '''                    <div class="form-card" style="margin-top:6px">
                        <div style="font-size:12px;color:var(--color-text-muted);margin-bottom:4px">▼ 提供商列表（可多个；勾选「当前」生效；模型获取即录、勾选启停）</div>
                        <div id="tierItemsText"></div>
                        <button class="btn-sm btn-primary-sm" onclick="addTierProvider('text')" type="button">＋ 增加提供商</button>'''
assert old1 in src, '文本层form-card未找到'
src = src.replace(old1, new1)

old2 = '''                    <div class="form-card" style="margin-top:6px">
                        <label>提供商名称（可改名，用于管理）</label>
                        <input type="text" id="setVisionName" placeholder="未命名">
                        <label class="checkbox-row"><input type="checkbox" id="setVisionEnabled" checked> 启用视觉辅助</label>
                        <label>API 地址（可输 base 或完整 /chat/completions 全称）</label>
                        <input type="text" id="setVisionBaseUrl" placeholder="https://open.bigmodel.cn/api/paas/v4 或完整端点">
                        <label>API Key（留空 = 保持已有 Key）</label>
                        <input type="password" id="setVisionKey" placeholder="...">
                        <div class="model-row"><label>模型名称</label><button class="btn-sm" onclick="fetchModels('vision')" title="从 API 拉取可用模型">🔍 获取模型</button></div>
                        <input type="text" id="setVisionModel" list="visionModelList" placeholder="可手输，或点「获取模型」自动拉取">
                        <datalist id="visionModelList"></datalist>'''
new2 = '''                    <div class="form-card" style="margin-top:6px">
                        <label class="checkbox-row"><input type="checkbox" id="setVisionEnabled" checked> 启用视觉辅助</label>
                        <div style="font-size:12px;color:var(--color-text-muted);margin:4px 0">▼ 提供商列表（可多个；勾选「当前」生效；模型获取即录、勾选启停）</div>
                        <div id="tierItemsVision"></div>
                        <button class="btn-sm btn-primary-sm" onclick="addTierProvider('vision')" type="button">＋ 增加提供商</button>'''
assert old2 in src, '视觉层form-card未找到'
src = src.replace(old2, new2)

old3 = '''                    <div class="form-card" style="margin-top:6px">
                        <label>提供商名称（可改名，用于管理）</label>
                        <input type="text" id="setAsrName" placeholder="未命名">
                        <label>识别引擎（视频字幕用）</label>'''
new3 = '''                    <div class="form-card" style="margin-top:6px">
                        <div style="font-size:12px;color:var(--color-text-muted);margin-bottom:4px">▼ 提供商列表（可多个；勾选「当前」生效；模型获取即录、勾选启停）</div>
                        <div id="tierItemsAsr"></div>
                        <button class="btn-sm btn-primary-sm" onclick="addTierProvider('asr')" type="button">＋ 增加提供商</button>
                        <label>识别引擎（视频字幕用）</label>'''
assert old3 in src, 'ASR层form-card开头未找到'
src = src.replace(old3, new3)

old4 = '''                        <div class="model-row"><label>千问模型名称</label><button class="btn-sm" onclick="fetchModels('asr')" title="从 API 拉取可用模型">🔍 获取模型</button></div>
                        <input type="text" id="setAsrModel" list="asrModelList" placeholder="可手输，或点「获取模型」自动拉取">
                        <datalist id="asrModelList"></datalist>'''
assert old4 in src, 'ASR千问模型行未找到'
src = src.replace(old4, '')

open(p, 'w', encoding='utf-8').write(src)
print('index.html 三段替换完成')

# ============ 2) app.js ============
p2 = 'frontend/js/app.js'
js = open(p2, encoding='utf-8').read()

# 2.1) showSettings 加载 tiers
old = '''    loadModelConfig();
    loadTiers();
}'''
if old not in js:
    old = '''    loadModelConfig();
}'''
assert old in js, 'showSettings 结尾'
js = js.replace(old, '''    loadModelConfig();
    loadTiers();
}''', 1)

# 2.2) tiers 核心逻辑（事件委托，避免 inline 引号地狱）
anchor = 'async function saveSettings() {'
assert anchor in js
tiers_js = r'''// ===== 分层提供商条目库（每层可多个提供商；模型列表获取即录、勾选启停） =====
const TIER_ORDER = ['text', 'vision', 'asr'];
let tiersState = { text: [], vision: [], asr: [] };

async function loadTiers() {
    try {
        const resp = await apiFetch('/api/config/tiers');
        const d = await resp.json().catch(() => ({}));
        tiersState = (d.tiers && d.tiers.text != null) ? d.tiers : { text: [], vision: [], asr: [] };
    } catch (e) { tiersState = { text: [], vision: [], asr: [] }; }
    TIER_ORDER.forEach(renderTier);
}

function tierBoxId(tier) { return 'tierItems' + (tier === 'text' ? 'Text' : tier === 'vision' ? 'Vision' : 'Asr'); }

function renderTier(tier) {
    const box = document.getElementById(tierBoxId(tier));
    if (!box) return;
    const list = tiersState[tier] || [];
    box.innerHTML = '';
    if (!list.length) {
        box.innerHTML = '<div style="font-size:13px;color:var(--color-text-muted);padding:4px 2px">暂无提供商，点「＋ 增加提供商」添加。</div>';
    }
    list.forEach((p, idx) => {
        const card = document.createElement('div');
        card.className = 'tier-card';
        card.dataset.tier = tier;
        card.dataset.idx = idx;
        card.style.cssText = 'border:1px solid var(--color-border-light);border-radius:8px;padding:8px 10px;margin-bottom:8px;background:#fff';
        const enabled = p.enabled || {};
        const models = (p.models || []).map(m => {
            const on = enabled[m] !== false;
            return '<label class="checkbox-row" style="font-size:12.5px;margin:2px 0"><input type="checkbox" class="tier-model" data-model="' + escAttr(m) + '" ' + (on ? 'checked' : '') + '> ' + escHtml(m) + '</label>';
        }).join('');
        card.innerHTML =
            '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
              '<label class="checkbox-row" style="margin:0;font-size:12px" title="设为当前生效"><input type="radio" class="tier-act" name="tierActive' + tier + '" ' + (p.active ? 'checked' : '') + '> 当前</label>' +
              '<input class="tier-name" style="flex:1;min-width:110px" value="' + escAttr(p.name || '') + '" placeholder="名字（未命名）">' +
              '<button class="btn-sm tier-del" style="color:var(--color-danger)">✕</button>' +
            '</div>' +
            '<div style="display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap">' +
              '<input class="tier-base" style="flex:2;min-width:170px" value="' + escAttr(p.base_url || '') + '" placeholder="API 地址">' +
              '<input class="tier-key" type="password" style="flex:1;min-width:100px" placeholder="API Key（留空=保留）">' +
            '</div>' +
            '<div style="margin-top:6px">' +
              '<div style="display:flex;gap:6px;align-items:center;margin-bottom:2px;flex-wrap:wrap">' +
                '<span style="font-size:12px;color:var(--color-text-muted)">模型列表（勾选=启用，会话中选具体模型）</span>' +
                '<button class="btn-sm tier-fetch">🔍 获取模型</button>' +
                '<span style="font-size:12px;color:' + (p.has_key ? 'var(--color-success)' : 'var(--color-warning)') + '">' + (p.has_key ? 'Key ✔' : '无 Key') + '</span>' +
              '</div>' +
              (models || '<div style="font-size:12px;color:#aaa">（空）</div>') +
              '<div style="display:flex;gap:6px;margin-top:4px">' +
                '<input class="tier-addmodel" style="flex:1;min-width:120px" placeholder="手动加模型名">' +
                '<button class="btn-sm tier-addbtn">＋</button>' +
              '</div>' +
            '</div>';
        box.appendChild(card);
    });
    refreshTierHead(tier);
}

function refreshTierHead(tier) {
    const idMap = { text: ['pgNameMain', 'pgSumMain'], vision: ['pgNameVision', 'pgSumVision'], asr: ['pgNameAsr', 'pgSumAsr'] };
    const nb = document.getElementById(idMap[tier][0]);
    const ns = document.getElementById(idMap[tier][1]);
    if (!nb || !ns) return;
    const list = tiersState[tier] || [];
    const active = list.find(x => x.active) || list[0];
    if (!active) return;
    nb.textContent = active.name || '未命名';
    const models = (active.models || []).filter(m => (active.enabled || {})[m] !== false);
    ns.textContent = [active.base_url, active.has_key ? 'Key ✔' : '无 Key', models.length ? '模型 ' + models.length + ' 个' : ''].filter(Boolean).join(' ・ ');
}

// 事件委托（每个容器一个监听）
TIER_ORDER.forEach(tier => {
    const box = document.getElementById(tierBoxId(tier));
    if (!box) return;
    box.addEventListener('click', e => {
        const card = e.target.closest('.tier-card');
        if (!card) return;
        const t = card.dataset.tier;
        const i = +card.dataset.idx;
        if (e.target.closest('.tier-del')) { removeTierProvider(t, i); }
        else if (e.target.closest('.tier-fetch')) { fetchTierModels(t, i, e.target); }
        else if (e.target.closest('.tier-addbtn')) {
            const input = card.querySelector('.tier-addmodel');
            const m = (input && input.value || '').trim();
            if (!m) return;
            const p = (tiersState[t] || [])[i];
            if (!p) return;
            p.models = p.models || [];
            if (!p.models.includes(m)) { p.models.push(m); p.enabled = p.enabled || {}; p.enabled[m] = true; }
            if (input) input.value = '';
            renderTier(t);
        }
    });
    box.addEventListener('input', e => {
        const card = e.target.closest('.tier-card');
        if (!card) return;
        const t = card.dataset.tier;
        const i = +card.dataset.idx;
        const p = (tiersState[t] || [])[i];
        if (!p) return;
        if (e.target.classList.contains('tier-name')) { p.name = e.target.value; refreshTierHead(t); }
        else if (e.target.classList.contains('tier-base')) { p.base_url = e.target.value; refreshTierHead(t); }
        else if (e.target.classList.contains('tier-key')) { p.api_key = e.target.value; }
    });
    box.addEventListener('change', e => {
        const card = e.target.closest('.tier-card');
        if (!card) return;
        const t = card.dataset.tier;
        const i = +card.dataset.idx;
        const p = (tiersState[t] || [])[i];
        if (!p) return;
        if (e.target.classList.contains('tier-model')) {
            p.enabled = p.enabled || {};
            p.enabled[e.target.dataset.model] = e.target.checked;
        } else if (e.target.classList.contains('tier-act')) {
            activateTier(t, i);
        }
    });
});

function addTierProvider(tier) {
    if (!tiersState[tier]) tiersState[tier] = [];
    tiersState[tier].push({ id: '', name: '', base_url: '', api_key: '', models: [], enabled: {}, active: false });
    renderTier(tier);
}
function removeTierProvider(tier, idx) {
    const arr = tiersState[tier] || [];
    if (arr.length <= 1) { alert('每层至少保留一个提供商'); return; }
    arr.splice(idx, 1);
    renderTier(tier);
}
async function fetchTierModels(tier, idx, btn) {
    const p = (tiersState[tier] || [])[idx];
    if (!p) return;
    const base = (p.base_url || '').trim();
    if (!base) { alert('先填 API 地址'); return; }
    if (btn) btn.textContent = '⏳...';
    try {
        const resp = await apiFetch('/api/config/list-models', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url: base, api_key: (p.api_key || '').trim() }),
        });
        const d = await resp.json().catch(() => ({}));
        const models = d.models || [];
        p.models = p.models || [];
        p.enabled = p.enabled || {};
        let added = 0;
        models.forEach(m => { if (!p.models.includes(m)) { p.models.push(m); p.enabled[m] = true; added++; } });
        if (btn) btn.textContent = '🔍 获取模型';
        alert(added ? '✅ 已录入 ' + models.length + ' 个模型（新增 ' + added + '）' : (d.error ? '⚠️ ' + d.error : '无新模型'));
        renderTier(tier);
    } catch (e) {
        if (btn) btn.textContent = '🔍 获取模型';
        alert('获取失败: ' + e.message);
    }
}
async function activateTier(tier, idx) {
    const p = (tiersState[tier] || [])[idx];
    if (!p) return;
    if (p.id) {
        try {
            const resp = await apiFetch('/api/config/tiers/activate', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tier: tier, provider_id: p.id }),
            });
            const d = await resp.json().catch(() => ({}));
            if (d.message) addSystemMessage('✅ ' + d.message);
        } catch (e) { addSystemMessage('⚠️ 激活失败: ' + e.message); }
    }
    (tiersState[tier] || []).forEach((x, i) => { x.active = (i === idx); });
    renderTier(tier);
}
async function saveTiersAll() {
    for (const tier of TIER_ORDER) {
        try {
            const providers = (tiersState[tier] || []).map(p => ({
                id: p.id || '', name: (p.name || '').trim(), base_url: (p.base_url || '').trim(),
                api_key: (p.api_key || '').trim(), models: p.models || [], enabled: p.enabled || {}, active: !!p.active,
            }));
            await apiFetch('/api/config/tiers', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tier: tier, providers: providers }),
            });
        } catch (e) {}
    }
}

'''
js = js.replace(anchor, tiers_js + anchor, 1)

# 2.3) saveSettings payload 从 tiersState 读取 + saveTiersAll
old_payload = '''    // 模型配置（服务器，留空 Key = 保留已有）
    const payload = {
        main: {
            name: (document.getElementById('setMainName') || {}).value ? document.getElementById('setMainName').value.trim() : '',
            base_url: document.getElementById('setMainBaseUrl').value.trim(),
            api_key: document.getElementById('setMainKey').value.trim(),
            model: document.getElementById('setMainModel').value.trim(),
        },
        vision: {
            name: (document.getElementById('setVisionName') || {}).value ? document.getElementById('setVisionName').value.trim() : '',
            enabled: document.getElementById('setVisionEnabled').checked,
            base_url: document.getElementById('setVisionBaseUrl').value.trim(),
            api_key: document.getElementById('setVisionKey').value.trim(),
            model: document.getElementById('setVisionModel').value.trim(),
        },
        asr: {
            name: (document.getElementById('setAsrName') || {}).value ? document.getElementById('setAsrName').value.trim() : '',
            base_url: document.getElementById('setAsrBaseUrl').value.trim(),
            api_key: document.getElementById('setAsrKey').value.trim(),
            model: document.getElementById('setAsrModel').value.trim(),
            provider: document.getElementById('setAsrProvider').value,
            size: document.getElementById('setAsrSize').value,
            voice_provider: document.getElementById('setAsrVoiceProvider').value,
            engines: {
                qwen: { api_key: document.getElementById('setAsrKey').value.trim() },
                zhipu: { api_key: document.getElementById('setAsrKeyZhipu').value.trim() },
                tencent: {
                    app_id: document.getElementById('setAsrTencentAppId').value.trim(),
                    secret_id: document.getElementById('setAsrTencentSecretId').value.trim(),
                    secret_key: document.getElementById('setAsrTencentSecretKey').value.trim(),
                },
                siliconflow: { api_key: document.getElementById('setAsrKeySiliconflow').value.trim() },
            },
        },
    };'''
new_payload = '''    // 模型配置（服务器，留空 Key = 保留已有）；地址/key/模型取当前生效提供商条目
    const actOf = t => (tiersState[t] || []).find(x => x.active) || (tiersState[t] || [])[0] || {};
    const mA = actOf('text'), vA = actOf('vision'), aA = actOf('asr');
    const mdl0 = p => ((p.models || [])[0] || '').trim();
    const payload = {
        main: {
            base_url: (mA.base_url || '').trim(),
            api_key: (mA.api_key || '').trim(),
            model: mdl0(mA),
        },
        vision: {
            enabled: document.getElementById('setVisionEnabled') ? document.getElementById('setVisionEnabled').checked : true,
            base_url: (vA.base_url || '').trim(),
            api_key: (vA.api_key || '').trim(),
            model: mdl0(vA),
        },
        asr: {
            base_url: (aA.base_url || '').trim(),
            api_key: (aA.api_key || '').trim(),
            model: mdl0(aA),
            provider: document.getElementById('setAsrProvider') ? document.getElementById('setAsrProvider').value : 'auto',
            size: document.getElementById('setAsrSize') ? document.getElementById('setAsrSize').value : 'small',
            voice_provider: document.getElementById('setAsrVoiceProvider') ? document.getElementById('setAsrVoiceProvider').value : 'auto',
            engines: {
                qwen: { api_key: document.getElementById('setAsrKey') ? document.getElementById('setAsrKey').value.trim() : '' },
                zhipu: { api_key: document.getElementById('setAsrKeyZhipu') ? document.getElementById('setAsrKeyZhipu').value.trim() : '' },
                tencent: {
                    app_id: document.getElementById('setAsrTencentAppId') ? document.getElementById('setAsrTencentAppId').value.trim() : '',
                    secret_id: document.getElementById('setAsrTencentSecretId') ? document.getElementById('setAsrTencentSecretId').value.trim() : '',
                    secret_key: document.getElementById('setAsrTencentSecretKey') ? document.getElementById('setAsrTencentSecretKey').value.trim() : '',
                },
                siliconflow: { api_key: document.getElementById('setAsrKeySiliconflow') ? document.getElementById('setAsrKeySiliconflow').value.trim() : '' },
            },
        },
    };
    // 分层提供商条目一起保存
    await saveTiersAll();'''
assert old_payload in js, '旧 payload 未找到'
js = js.replace(old_payload, new_payload)

open(p2, 'w', encoding='utf-8').write(js)
print('app.js 改造完成')