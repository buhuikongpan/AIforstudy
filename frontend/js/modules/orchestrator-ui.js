// orchestrator-ui.js — 编排器透明化层（共识 D5）：
// ① 聊天面板「上下文预览」：当前会话实际注入哪些产物、各占多少字
// ② 设置页「上下文编排 & 文件转换」：按文件类型配置模板 + x2t 路径，保存即生效
// 复用 app.js 的 apiFetch / escHtml / openDialog

const ORCH_FIELDS = [
    ['include_summary', '注入 AI 总结（summary.md）', 'bool'],
    ['summary_chars', '总结注入上限（字符）', 'num'],
    ['include_extracted', '注入全文摘录（头尾）', 'bool'],
    ['extracted_head', '摘录开头字符数', 'num'],
    ['extracted_tail', '摘录结尾字符数', 'num'],
    ['include_position', '注入当前查看位置', 'bool'],
];
const KIND_LABELS = {
    office: '📊 Office 文档', pdf: '📕 PDF', video: '🎬 视频',
    image: '🖼️ 图片', audio: '🎵 音频', text: '📄 文本',
    markdown: '📝 Markdown', other: '📦 其他',
};

const OrchestratorUI = {
    // ---------- ① 上下文实时预览 ----------
    async preview() {
        const cur = window.FSState ? window.FSState.currentFile() : null;
        if (!cur) {
            openDialog({ title: '🧩 上下文预览', bodyHTML: '<div style="color:#999;font-size:13px">当前没有打开的文件。从左侧打开任意资料后，这里展示 agent 实际会收到哪些上下文。</div>', onOk: () => true, okText: '关闭' });
            return;
        }
        const pos = window.Viewer ? window.Viewer.currentPosition() : null;
        const qs = new URLSearchParams({ file_id: cur.id });
        if (pos && pos.page) qs.set('page', pos.page);
        if (pos && pos.time) qs.set('time', pos.time);
        const resp = await apiFetch(`/api/chat/context-preview?${qs}`);
        if (!resp.ok) { addSystemMessage('⚠️ 预览加载失败'); return; }
        const d = await resp.json();
        const rows = (d.components || []).map(c => `<tr>
            <td>${escHtml(c.label)}</td><td class="mono">${escHtml(c.source)}</td>
            <td class="mono">${c.chars.toLocaleString()} 字</td></tr>`).join('');
        openDialog({
            title: `🧩 上下文预览 — ${escHtml(d.file_name)}`,
            bodyHTML: `
            <div class="ctx-prev-meta">类型：${escHtml(d.kind)}　状态：${escHtml(d.status)}　
                总注入：<b>${(d.total_chars || 0).toLocaleString()}</b> 字　
                编排器：${d.enabled ? '✅ 启用' : '⛔ 关闭'}</div>
            ${rows ? `<table class="ctx-prev-table"><tr><th>组件</th><th>来源</th><th>大小</th></tr>${rows}</table>`
                   : '<div style="color:#999;font-size:13px">（无注入组件——产物尚未生成，或该类型模板全部关闭）</div>'}
            ${d.text_preview ? `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:13px">查看注入文本前 2000 字</summary>
                <pre class="ctx-prev-text">${escHtml(d.text_preview)}</pre></details>` : ''}`,
            onOk: () => true, okText: '关闭',
        });
    },

    // ---------- ② 设置页配置 ----------
    _settingsCache: null,

    async renderSettings() {
        const box = document.getElementById('orchSettings');
        if (!box) return;
        box.innerHTML = '<div style="color:#999;font-size:13px">加载中…</div>';
        try {
            const resp = await apiFetch('/api/fs/settings');
            if (!resp.ok) throw new Error('加载失败');
            const d = await resp.json();
            this._settingsCache = d;
            const tpl = d.orchestrator.templates || {};
            let html = `
            <label class="checkbox-row"><input type="checkbox" id="orchEnabled" ${d.orchestrator.enabled ? 'checked' : ''}>
                ✅ 启用上下文编排器（关闭后 agent 不再自动获得文件内容，仍可用沙箱工具自己翻）</label>`;
            for (const [kind, label] of Object.entries(KIND_LABELS)) {
                const t = tpl[kind] || {};
                html += `<details class="provider-group" style="border:1px solid var(--color-border);border-radius:8px;padding:8px 10px;margin:6px 0">
                    <summary style="cursor:pointer;font-size:13px;font-weight:600">${label}</summary>
                    <div class="orch-tpl" data-kind="${kind}" style="margin-top:6px">`;
                for (const [key, lab, type] of ORCH_FIELDS) {
                    if (!(key in t)) continue;
                    if (type === 'bool') {
                        html += `<label class="checkbox-row"><input type="checkbox" data-f="${key}" ${t[key] ? 'checked' : ''}> ${lab}</label>`;
                    } else {
                        html += `<label style="display:flex;align-items:center;gap:8px;font-size:13px;margin:2px 0">${lab}
                            <input type="number" data-f="${key}" value="${t[key] || 0}" style="width:100px"></label>`;
                    }
                }
                html += '</div></details>';
            }
            const found = d.x2t && d.x2t.found;
            html += `
            <div style="margin-top:10px;border-top:1px solid var(--color-border);padding-top:8px">
                <div style="font-size:13px;font-weight:600;margin-bottom:4px">🔄 Office → PDF 转换引擎（x2t，OnlyOffice 转换引擎）</div>
                <div style="font-size:12px;color:var(--color-text-muted);margin-bottom:4px">
                    外部依赖，不随仓库分发。配置 x2t 可执行文件路径（依赖库与字体索引需在同目录）。
                    当前：${d.x2t.configured ? (found ? '✅ 已找到' : '⚠️ 已配置但文件不存在') : '未配置'}
                    ｜ LibreOffice 兜底：${d.libreoffice ? '✅ 检测到' : '未检测到'}</div>
                <input type="text" id="x2tPath" value="${escHtml((d.x2t && d.x2t.path) || '')}"
                    placeholder="例如 C:\\Program Files\\ONLYOFFICE\\DocumentServer\\FileConverter\\bin\\x2t.exe" style="width:100%">
            </div>
            <div style="margin-top:10px;border-top:1px solid var(--color-border);padding-top:8px">
                <div style="font-size:13px;font-weight:600">🗺️ 科目知识地图</div>
                <div style="font-size:12px;color:var(--color-text-muted);margin:4px 0">
                    合并本科目所有文档总览/课程总结，供 AI 把握整体结构与先后依赖。
                    当前科目：<b>${escHtml(((typeof state !== 'undefined' && state.subject) || '') || '（未选择）')}</b>
                    <span id="smStatus" style="margin-left:8px"></span></div>
                <button class="btn-sm btn-primary-sm" onclick="OrchestratorUI.rebuildSubjectMap()">🔄 重建/更新当前科目地图</button>
            </div>
            <div style="text-align:right;margin-top:8px">
                <button class="btn-sm btn-primary-sm" onclick="OrchestratorUI.saveSettings()">💾 保存编排配置</button>
            </div>`;
            box.innerHTML = html;
        } catch (e) {
            box.innerHTML = `<div style="color:#c00;font-size:13px">⚠️ ${escHtml(e.message)}</div>`;
        }
    },

    async rebuildSubjectMap() {
        const subject = ((typeof state !== 'undefined' && state.subject) || '') || '';
        if (!subject) { addSystemMessage('⚠️ 无当前科目（先在顶部选择或新建）'); return; }
        const st = document.getElementById('smStatus');
        if (st) st.textContent = '⏳ 生成中…（约 10-30 秒）';
        try {
            const resp = await apiFetch('/api/subject-map/rebuild', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject }),
            });
            const d = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                if (st) st.textContent = '⚠️ ' + (d.detail || '失败');
                return;
            }
            if (st) st.textContent = '✅ 已更新（合并 ' + d.docs + ' 份文档总览）';
            addSystemMessage('🗺️ 「' + subject + '」知识地图已更新（合并 ' + d.docs + ' 份文档总览），下次提问自动注入');
        } catch (e) {
            if (st) st.textContent = '⚠️ ' + e.message;
        }
    },

    async saveSettings() {
        const templates = {};
        document.querySelectorAll('#orchSettings .orch-tpl').forEach(box => {
            const kind = box.dataset.kind;
            const t = {};
            box.querySelectorAll('input[data-f]').forEach(inp => {
                t[inp.dataset.f] = inp.type === 'checkbox' ? inp.checked : (parseInt(inp.value, 10) || 0);
            });
            templates[kind] = t;
        });
        const payload = {
            enabled: !!(document.getElementById('orchEnabled') || {}).checked,
            templates,
            x2t: { path: (document.getElementById('x2tPath') || {}).value || '' },
        };
        const resp = await apiFetch('/api/fs/settings', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (resp.ok) {
            addSystemMessage('✅ 编排配置已保存，立即生效');
            this.renderSettings();
        } else {
            addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '保存失败'}`);
        }
    },
};
window.OrchestratorUI = OrchestratorUI;
