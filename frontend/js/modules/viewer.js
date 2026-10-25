// viewer.js — 全类型查看器容器（中间区域，共识 D6：pdf.js 统一路径）
// 窗格：videoPane（现有播放器）/ pdfPane / imgPane / textPane / audioPane / infoPane
import { openPdf, closePdf, pdfCurrentPage, pdfInfo, gotoPage, zoomPdf, stepPage, pdfGoto, pdfFit } from './viewer-pdf.js';
import { openImage } from './viewer-image.js';
import { openText } from './viewer-text.js';
import { stopMedia } from './viewer-media.js';

let current = null;          // 当前文件 status 载荷
let pollTimer = null;

const PANES = ['videoPane', 'pdfPane', 'imgPane', 'textPane', 'audioPane', 'infoPane'];

function _pane(id) { return document.getElementById(id); }
function _infoBody() { return document.getElementById('infoBody'); }

function showOnly(paneId) {
    for (const p of PANES) {
        const el = _pane(p);
        if (el) el.style.display = p === paneId ? 'flex' : 'none';
    }
    clearTimeout(pollTimer);
}

const Viewer = {
    // 当前文件信息（FSState 同步）
    file() { return current; },

    showPane(paneId) { showOnly(paneId); },

    async open(st) {
        // st: /api/files/{id}/status 载荷 {id,name,kind,status,status_msg,artifacts,tasks}
        current = st;
        if (window.FSState) window.FSState.setCurrentFile(
            { id: st.id, name: st.name, kind: st.kind, status: st.status, status_msg: st.status_msg });

        switch (st.kind) {
            case 'pdf':
                showOnly('pdfPane');
                openPdf(`/api/files/${st.id}/view`, st.id);
                return;
            case 'office': {
                if (st.artifacts && st.artifacts['derived.pdf']) {
                    showOnly('pdfPane');
                    openPdf(`/api/files/${st.id}/artifacts/derived.pdf`, st.id);
                } else if (st.status === 'error') {
                    this._showError(st);
                } else {
                    this._showProcessing(st);
                }
                return;
            }
            case 'image':
                showOnly('imgPane');
                openImage(`/api/files/${st.id}/view`);
                return;
            case 'text':
            case 'markdown':
                showOnly('textPane');
                openText(`/api/files/${st.id}/raw`, st.kind, st.name);
                return;
            case 'audio': {
                showOnly('audioPane');
                const tk = window.getApiToken ? window.getApiToken() : '';
                const audio = document.getElementById('viewerAudio');
                if (audio) {
                    audio.src = `/api/files/${st.id}/view` + (tk ? `?token=${encodeURIComponent(tk)}` : '');
                    document.getElementById('audioName').textContent = st.name;
                }
                return;
            }
            default:
                // other：给下载入口 + 说明
                this._showInfo(st);
        }
    },

    // 处理中：展示任务进度并轮询（转换/提取完成自动切换到 PDF 视图）
    _showProcessing(st) {
        showOnly('infoPane');
        const render = (s) => {
            const tasks = (s.tasks || []).map(t => `<div class="task-row">
                <span>${t.type === 'convert_pdf' ? '🔄 转换 PDF' : t.type === 'extract_text' ? '📝 提取文本' : '🧠 生成总结'}</span>
                <span class="task-status task-${t.status}">${{ pending: '排队', running: '进行中', done: '完成', error: '失败' }[t.status] || t.status}</span>
            </div>`).join('');
            _infoBody().innerHTML = `
                <div class="info-icon">⏳</div>
                <div class="info-title">${escHtml(st.name)}</div>
                <div class="info-desc">AI 产物生成中（转换 → 提取 → 总结），完成后自动打开。</div>
                <div class="task-list">${tasks}</div>`;
        };
        render(st);
        const poll = async () => {
            try {
                const resp = await apiFetch(`/api/files/${st.id}/status`);
                if (!resp.ok) return;
                const s = await resp.json();
                current = s;
                if (s.artifacts && s.artifacts['derived.pdf']) {
                    showOnly('pdfPane');
                    openPdf(`/api/files/${s.id}/artifacts/derived.pdf`, s.id);
                    if (window.FSTree) window.FSTree.refresh();
                    return;
                }
                if (s.status === 'error') { this._showError(s); return; }
                render(s);
                pollTimer = setTimeout(poll, 2500);
            } catch (e) { pollTimer = setTimeout(poll, 5000); }
        };
        pollTimer = setTimeout(poll, 2500);
    },

    _showError(st) {
        showOnly('infoPane');
        const tasks = (st.tasks || []).filter(t => t.status === 'error')
            .map(t => `<div class="task-error">${escHtml(t.type)}：${escHtml(t.error || '')}</div>`).join('');
        _infoBody().innerHTML = `
            <div class="info-icon">⚠️</div>
            <div class="info-title">${escHtml(st.name)}</div>
            <div class="info-desc">${escHtml(st.status_msg || '处理失败')}</div>
            ${tasks}
            <div class="info-actions">
                <button class="btn-primary" onclick="Viewer.reprocess(${st.id})">🔁 重新处理</button>
                <button class="btn-secondary" onclick="FSTree.downloadFile(${st.id})">⬇️ 下载原文件</button>
            </div>`;
    },

    _showInfo(st) {
        showOnly('infoPane');
        _infoBody().innerHTML = `
            <div class="info-icon">📦</div>
            <div class="info-title">${escHtml(st.name)}</div>
            <div class="info-desc">该类型暂不支持在线预览。</div>
            <div class="info-actions">
                <button class="btn-primary" onclick="FSTree.downloadFile(${st.id})">⬇️ 下载原文件</button>
            </div>`;
    },

    async reprocess(id) {
        const resp = await apiFetch(`/api/files/${id}/reprocess`, { method: 'POST' });
        if (resp.ok) {
            addSystemMessage('🔁 已重新排队处理');
            const st = await (await apiFetch(`/api/files/${id}/status`)).json();
            this.open(st);
        } else {
            addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`);
        }
    },

    // 当前查看位置（agent 编排器定位用）：PDF 页码 / 视频时间点
    currentPosition() {
        if (_pane('pdfPane') && _pane('pdfPane').style.display !== 'none' && pdfInfo().numPages) {
            return { page: pdfCurrentPage() };
        }
        const v = document.getElementById('videoPlayer');
        if (v && v.src && !v.paused && typeof v.currentTime === 'number') {
            return { time: Math.round(v.currentTime) };
        }
        return null;
    },

    close() {
        closePdf();
        stopMedia();
        current = null;
        if (window.FSState) window.FSState.setCurrentFile(null);
        showOnly('videoPane');
        // 视频区回到占位态
        if (window.resetPlayer) window.resetPlayer();
    },

    // 工具栏
    // 用 stepPage：基于滚动位置实时算当前页，连点/边界都正确
    pdfGoto(page) { pdfGoto(page); },
    pdfPrev() { stepPage(-1); },
    pdfNext() { stepPage(1); },
    pdfZoomIn() { zoomPdf(0.2); },
    pdfFit() { pdfFit(); },
    pdfZoomOut() { zoomPdf(-0.2); },

    // ===== 章节大纲面板（显示/隐藏/编辑）=====
    async toggleOutline() {
        const panel = document.getElementById('pdfOutline');
        if (!panel) return;
        if (panel.style.display === 'flex') { panel.style.display = 'none'; return; }
        panel.style.display = 'flex';
        panel.innerHTML = '<div class="pdf-outline-loading">📂 目录加载中…</div>';
        const fid = window.FSState ? window.FSState.currentFileId() : 0;
        if (!fid) { panel.innerHTML = '<div class="pdf-outline-loading">未打开文件</div>'; return; }
        try {
            const resp = await apiFetch(`/api/files/${fid}/outline`);
            if (!resp.ok) {
                panel.innerHTML = '<div class="pdf-outline-loading">📄 此文档暂无目录（未识别到章节结构）</div>';
                return;
            }
            const ol = await resp.json();
            const unit = ol.unit === 'sec';
            const fmt = (v) => unit
                ? String(Math.floor(v / 60)).padStart(2, '0') + ':' + String(Math.round(v % 60)).padStart(2, '0')
                : 'p' + v;
            panel.innerHTML = ol.chapters.map(ch => `
                <div class="pdf-outline-item lv${ch.level}" data-start="${ch.start}" data-cid="${ch.id}">
                    <span class="po-title" title="${escHtml(ch.title)}">${escHtml(ch.title)}</span>
                    <span class="po-loc">${fmt(ch.start)}</span>
                    <span class="po-edits">
                        <span class="po-edit" data-act="rename" data-cid="${ch.id}" title="重命名">✏️</span>
                        <span class="po-edit" data-act="split" data-cid="${ch.id}" title="拆分">🔀</span>
                        <span class="po-edit" data-act="merge" data-cid="${ch.id}" title="并入上一章">⬆️</span>
                    </span>
                </div>`).join('');
        } catch (e) {
            panel.innerHTML = '<div class="pdf-outline-loading">⚠️ 目录加载失败</div>';
        }
    },

    // 大纲编辑（端点：outline/rename|split|merge）
    outlineEdit(act, cid) {
        const fid = window.FSState ? window.FSState.currentFileId() : 0;
        if (!fid) return;
        const body = { cid };
        if (act === 'rename') {
            const panel = document.getElementById('pdfOutline');
            const item = panel.querySelector(`.pdf-outline-item[data-cid="${cid}"] .po-title`);
            openDialog({
                title: '重命名章节',
                bodyHTML: `<label>章节名称</label><input type="text" id="dlgInput" class="dlg-input" value="${escHtml(item ? item.textContent : '')}">`,
                onOk: async () => {
                    body.title = ((document.getElementById('dlgInput') || {}).value || '').trim();
                    if (!body.title) return false;
                    const resp = await apiFetch(`/api/files/${fid}/outline/rename`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) { addSystemMessage('⚠️ 重命名失败'); return false; }
                    addSystemMessage('✏️ 已重命名章节');
                    this.toggleOutline().then(() => this.toggleOutline());
                },
            });
            return;
        }
        if (act === 'split') {
            const cur = this.currentPosition();
            openDialog({
                title: '拆分章节',
                bodyHTML: `<label>拆分位置（页码）</label><input type="number" id="dlgInput" class="dlg-input" value="${(cur && cur.page) || 0}">`
                    + '<div class="dlg-hint">该页之前归「上」，该页起归「下」</div>',
                onOk: async () => {
                    body.page = parseInt((document.getElementById('dlgInput') || {}).value || '0', 10);
                    const resp = await apiFetch(`/api/files/${fid}/outline/split`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) { addSystemMessage('⚠️ ' + ((await resp.json().catch(() => ({}))).detail || '拆分失败')); return false; }
                    addSystemMessage('🔀 已拆分章节');
                    this.toggleOutline().then(() => this.toggleOutline());
                },
            });
            return;
        }
        if (act === 'merge') {
            openDialog({
                title: '合并章节',
                bodyHTML: '<div style="font-size:14px">该章节（含子节）将并入上一章。确定？</div>',
                onOk: async () => {
                    const resp = await apiFetch(`/api/files/${fid}/outline/merge`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) { addSystemMessage('⚠️ 合并失败'); return false; }
                    addSystemMessage('⬆️ 已合并');
                    this.toggleOutline().then(() => this.toggleOutline());
                },
            });
        }
    },
};

// 大纲面板事件代理：点标题跳页；点 ✏️/🔀/⬆️ 编辑
document.addEventListener('click', (e) => {
    const panel = document.getElementById('pdfOutline');
    if (!panel || !panel.contains(e.target)) return;
    const item = e.target.closest('.pdf-outline-item');
    if (!item) return;
    const edit = e.target.closest('.po-edit');
    if (edit) {
        e.stopPropagation();
        window.Viewer.outlineEdit(edit.dataset.act, edit.dataset.cid);
        return;
    }
    if (e.target.closest('.po-title') || e.target.closest('.po-loc')) {
        window.Viewer.pdfGoto(+item.dataset.start);
    }
});

window.Viewer = Viewer;
// ===== 划词问 AI（M5）：文本窗格 / PDF 文本层选中文本后浮动"问 AI"按钮 =====
(function initSelectionFloater() {
    let floater = null;
    function hide() { if (floater) { floater.remove(); floater = null; } }
    function activePaneAllows() {
        const pdf = document.getElementById('pdfPane');
        const txt = document.getElementById('textPane');
        return (pdf && pdf.style.display !== 'none') || (txt && txt.style.display !== 'none');
    }
    document.addEventListener('mouseup', (e) => {
        setTimeout(() => {
            hide();
            if (!activePaneAllows()) return;
            const sel = window.getSelection();
            if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
            const text = (sel.toString() || '').trim();
            if (text.length < 2) return;
            const btn = document.createElement('button');
            btn.className = 'selection-ask-btn';
            btn.textContent = '🤖 问 AI';
            btn.style.left = Math.min(e.clientX + 8, window.innerWidth - 110) + 'px';
            btn.style.top = Math.min(e.clientY + 10, window.innerHeight - 50) + 'px';
            btn.addEventListener('mousedown', ev => ev.stopPropagation());
            btn.addEventListener('click', () => {
                const clipped = text.length > 1200 ? text.slice(0, 1200) + '…' : text;
                const pos = Viewer.currentPosition();
                const posTxt = pos && pos.page ? `（第 ${pos.page} 页）` : '';
                const input = document.getElementById('chatInput');
                if (input) {
                    input.value = `【选自当前资料的文本${posTxt}】
「${clipped}」

请针对这段内容：`;
                    input.focus();
                }
                hide();
                window.getSelection().removeAllRanges();
            });
            document.body.appendChild(btn);
            floater = btn;
        }, 10);
    });
    document.addEventListener('mousedown', e => {
        if (floater && !floater.contains(e.target)) hide();
    });
})();

window.Viewer = Viewer;
