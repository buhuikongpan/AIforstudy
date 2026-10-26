/* ===== 考研学习平台 - 前端主逻辑 ===== */

const state = {
    subject: 'math',
    mode: 'A',
    delMode: false,
    currentVideoId: null,
    currentVideoTitle: '',
    conversationId: '',
    messages: [],
    subtitles: [],
    typingId: null,
    isStreaming: false,
    images: [],          // 待发送图片数组：{dataUrl, file}，最多 4 张
    autoCapture: false,  // 自动截图当前画面
    uploadController: null,
    currentFolderId: 0,  // 当前选中的文件夹
    convModel: '',       // 当前会话的模型（空 = 全局主模型）
    convEffort: '',      // 当前会话的思考强度（空 = 全局默认 high）
    globalModel: '',     // 全局主模型名（下拉兜底显示）
    focusFolderId: 0,    // 锁定的学习范围文件夹（0 = 未锁定，跟随当前文件）
    focusLocked: 0,      // 1 = 已锁定（复习/引导输出时用）
};

const API_BASE = localStorage.getItem('kaoyan_api_url') || '';
function getApiUrl(p) { return API_BASE + p; }

// ===== API 封装：自动附带访问令牌（⚙️设置 里配置，服务端 .env 需配 PLATFORM_TOKEN）=====
function getApiToken() { try { return localStorage.getItem('kaoyan_token') || ''; } catch (e) { return ''; } }
async function apiFetch(path, opts = {}) {
    const token = getApiToken();
    if (token) {
        const h = new Headers(opts.headers || {});
        h.set('Authorization', 'Bearer ' + token);
        opts.headers = h;
    }
    const resp = await fetch(getApiUrl(path), opts);
    if (resp.status === 401) addSystemMessage('⚠️ 无访问权限：请在 ⚙️ 设置 填写访问令牌（需在服务器 .env 配好 PLATFORM_TOKEN）');
    return resp;
}

// ===== 初始化 =====
// 复位视口滚动：防止历史 scrollIntoView 冒泡把文档滚动位置留在非顶部，导致顶部导航被顶出屏幕、界面看起来"被拉起"
function resetViewportScroll() {
    try {
        window.scrollTo(0, 0);
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
    } catch (e) {}
}
document.addEventListener('DOMContentLoaded', () => { resetViewportScroll(); switchMode('A'); setupSplitters(); initConvResizer(); restoreConvSidebarState(); initChatEffortSelect(); initChatScroll(); loadChatModelList(); loadTree().then(() => restoreActiveConversation()); });

// 终极兜底：任何时刻 document 被滚动（滚动锚定/浏览器内部行为），立即拉回顶部。
// 用 capture 阶段监听，抢在浏览器完成滚动渲染前复位，防止顶部导航被顶出屏幕、视频滚出视口后黑屏。
window.addEventListener('scroll', () => {
    try {
        if (window.scrollY !== 0 || document.documentElement.scrollTop !== 0 || document.body.scrollTop !== 0) {
            window.scrollTo(0, 0);
            document.documentElement.scrollTop = 0;
            document.body.scrollTop = 0;
        }
    } catch (e) {}
}, true);

// ===== 可拖拽分隔条（分隔条在哪，宽度变化就发生在哪） =====
// 用 Pointer Events 统一鼠标 / 触屏 / 触控笔：平板（iPad 等）上手指可直接拖动三栏宽度
// 触屏命中区由 CSS 的 @media (pointer: coarse) 加宽到约 24px，这里不再管
function setupSplitters() {
    const sidebar = document.querySelector('.video-sidebar');
    const chat = document.querySelector('.chat-panel');
    const sideSplit = document.getElementById('splitterSidebar');
    const chatSplit = document.getElementById('splitterChat');
    if (!sidebar || !chat || !sideSplit || !chatSplit) return;

    let isDragging = false;
    let currentSplitter = null;
    let pointerId = null;
    let startX = 0;
    let startWidth = 0;
    let target = null;  // 本次拖动要改宽度的面板：sidebar 或 chat

    function onPointerDown(e, splitter, panel) {
        if (e.pointerType === 'mouse' && e.button !== 0) return;   // 鼠标只认左键
        isDragging = true;
        currentSplitter = splitter;
        target = panel;
        pointerId = e.pointerId;
        startX = e.clientX;
        startWidth = panel.getBoundingClientRect().width;
        splitter.classList.add('dragging');
        document.body.classList.add('col-resizing');
        document.body.style.userSelect = 'none';
        try { splitter.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault();
    }

    function onPointerMove(e) {
        if (!isDragging || !currentSplitter) return;
        if (pointerId !== null && e.pointerId !== pointerId) return;   // 忽略多指的另一根手指
        const dx = e.clientX - startX;
        const cw = document.querySelector('.main-content').clientWidth;
        const VIDEO_MIN = 160;  // 视频区最小宽度：两侧面板可以一直扩大把视频区挤到这么小
        if (target === sidebar) {
            // 往右拖 → 侧边栏变宽（其右缘就是分隔条，原地变化）；上限保证视频区不被挤到 < VIDEO_MIN
            const maxW = cw - chat.getBoundingClientRect().width - VIDEO_MIN;
            const newWidth = Math.max(80, Math.min(maxW, startWidth + dx));
            sidebar.style.width = newWidth + 'px';
        } else {
            // 往左拖 → 聊天区变宽（其左缘就是分隔条，原地变化）；上限保证视频区不被挤到 < VIDEO_MIN
            const maxW = cw - sidebar.getBoundingClientRect().width - VIDEO_MIN;
            const newWidth = Math.max(120, Math.min(maxW, startWidth - dx));
            chat.style.width = newWidth + 'px';
        }
    }

    function onPointerUp(e) {
        if (!isDragging) return;
        if (pointerId !== null && e.pointerId !== undefined && e.pointerId !== pointerId) return;  // 别的指头抬起不影响本次拖动
        if (currentSplitter) {
            currentSplitter.classList.remove('dragging');
            if (pointerId !== null) {
                try { currentSplitter.releasePointerCapture(pointerId); } catch (_) {}
            }
        }
        isDragging = false;
        currentSplitter = null;
        pointerId = null;
        document.body.classList.remove('col-resizing');
        document.body.style.userSelect = '';
    }

    sideSplit.addEventListener('pointerdown', e => onPointerDown(e, sideSplit, sidebar));
    chatSplit.addEventListener('pointerdown', e => onPointerDown(e, chatSplit, chat));
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);
    document.addEventListener('pointercancel', onPointerUp);
}

// ===== 科目切换 =====
function switchSubject(subject) {
    // 动态科目（文件中心）：科目 = 顶层文件夹名，tab 由 tree.js 渲染
    state.subject = subject;
    state.conversationId = '';
    state.convModel = '';
    state.convEffort = '';
    state.messages = [];
    state.subtitles = [];
    state.currentFolderId = 0;
    let label = subject;
    document.querySelectorAll('.subject-tab').forEach(el => {
        const active = el.dataset.subject === subject;
        el.classList.toggle('active', active);
        if (active) label = el.textContent;
    });
    document.getElementById('chatSubjectLabel').textContent = label;
    // 切科目 = 换一批资料：关闭查看器当前内容（PDF/文本/音频窗格 + agent 的 file_id 上下文），
    // 否则中间还显示上一个科目的文件，右侧 agent 也继续带着旧文件的上下文
    if (window.Viewer && window.Viewer.close) { window.Viewer.close(); } else { resetPlayer(); }
    clearChat();
    if (window.FSTree) window.FSTree.setSubject(subject); else loadTree();
    addSystemMessage(`📚 ${subject}`);
    restoreActiveConversation();
}

// ===== Agent 切换 =====
function switchMode(mode) {
    state.mode = mode;
    state.conversationId = '';
    state.convModel = '';
    state.convEffort = '';
    state.messages = [];
    document.querySelectorAll('.mode-tab').forEach(el => el.classList.toggle('active', el.dataset.mode === mode));
    const names = { A: '💬 即时问答', B: '🎯 引导输出', C: '📝 课后问答' };
    const msgs = { A: '👋 看视频随时提问！', B: '🎯 我来出题引导你！', C: '📝 自由提问吧！' };
    document.getElementById('chatModeLabel').textContent = names[mode] || mode;
    // 模式B：显示侧边栏视频勾选框（引导输出用）
    document.querySelectorAll('.b-video-cb').forEach(cb => {
        cb.style.display = (mode === 'B') ? 'inline-block' : 'none';
    });
    // 字幕列表面板已移除，跳过旧展示逻辑
    const _sp = document.getElementById('subtitlePanel');
    if (_sp) _sp.style.display = (mode === 'A' && state.currentVideoId) ? 'flex' : 'none';
    // 切换模式时退出批量删除模式（勾选框由 .del-mode class 控制，不常驻）
    if (state.delMode) exitDelMode();
    clearChat(); addSystemMessage(msgs[mode] || '');
    restoreActiveConversation();
}

// ===== 文件夹树 =====
let treeExpanded = {};  // {folderId: true/false}
let uploadFolderId = 0;  // 上传文件夹选择器的当前值

// JS 加载成功就立即清除 HTML 中的占位文本
try { const el = document.getElementById('pageLoadCheck'); if (el) el.textContent = '✅ JS 已加载'; } catch(e) {}

// 树数据缓存：供搜索过滤时快速重渲染（不用重新请求后端）
let treeCache = null;

async function loadTree() {
    // 文件中心：树渲染已接管给 modules/tree.js（ES module，FSTree）
    if (window.FSTree) return window.FSTree.refresh();
    setTimeout(loadTree, 80);   // 模块尚未就绪，稍后重试
}

// ===== 上传文件夹下拉填充 =====
function populateUploadFolderSelect(folders) {
    const sel = document.getElementById('uploadFolderSelect');
    if (!sel) return;
    const prev = uploadFolderId || 0;
    let html = '<option value="0">（未分类）</option>';
    (function walk(fs, d) {
        for (const f of fs) {
            const indent = '\u00a0'.repeat(d * 2);
            html += `<option value="${f.id}"${f.id == prev ? ' selected' : ''}>${indent}${f.name}</option>`;
            walk(f.children || [], d + 1);
        }
    })(folders, 1);
    sel.innerHTML = html;
    sel.onchange = () => { uploadFolderId = parseInt(sel.value || '0', 10); };
    if (uploadFolderId === 0 && sel.querySelector('option[value="0"]')) {
        // 保持默认选中"未分类"
    } else {
        // 尝试选中上次选的
        try { sel.value = String(uploadFolderId); } catch(e) {}
    }
}

function getTreeFilter() {
    const el = document.getElementById('videoSearch');
    return (el && el.value ? el.value.trim().toLowerCase() : '');
}

function treeVideoMatches(v, q) {
    if (!q) return true;
    const hay = ((v.title || '') + ' ' + (v.filename || '')).toLowerCase();
    return hay.includes(q);
}

function renderTree(data, q) {
    const container = document.getElementById('videoTreeBody');
    if (!container) return;
    if (!data.folders.length && !data.uncategorized.length) {
        container.innerHTML = `<div class="empty-state"><div class="icon">📂</div><div>暂无视频</div></div>`;
        return;
    }
    container.innerHTML = '';
    let shown = 0;
    // 渲染文件夹（搜索时无匹配的整棵隐藏）
    for (const f of data.folders) {
        const node = renderFolderNode(f, 0, q);
        if (node) { container.appendChild(node); shown++; }
    }
    // 未分类视频（搜索时同样过滤）
    const uc = (data.uncategorized || []).filter(v => treeVideoMatches(v, q));
    if (uc.length) {
        const ucDiv = document.createElement('div');
        ucDiv.className = 'tree-folder';
        ucDiv.innerHTML = `<div class="tree-folder-label" onclick="toggleFolder(this)">
            <span class="tree-arrow">▶</span><span class="tree-icon">📁</span><span>未分类</span><span class="tree-count">${uc.length}</span>
        </div><div class="tree-children" style="display:none">`;
        for (const v of uc) ucDiv.querySelector('.tree-children').appendChild(makeVideoNode(v));
        container.appendChild(ucDiv);
        shown++;
    }
    if (!shown) {
        container.innerHTML = `<div class="empty-state"><div class="icon">🔍</div><div>没有匹配「${q}」的视频</div></div>`;
        return;
    }
    // 若存在生成中的课程总结，20 秒后自动刷新一次树状态（后台任务完成时能跟上）
    if (container.querySelector('.status-summary-processing')) {
        clearTimeout(window._summaryPollTimer);
        window._summaryPollTimer = setTimeout(() => loadTree(), 20000);
    }
}

// F4：视频搜索防抖（200ms），避免每敲一键全量重渲染整个树
let videoSearchTimer = null;
function onVideoSearch(v) {
    if (!treeCache) return;
    clearTimeout(videoSearchTimer);
    videoSearchTimer = setTimeout(() => {
        renderTree(treeCache, (v || '').trim().toLowerCase());
        if (state.delMode) exitDelMode();
    }, 200);
}

function renderFolderNode(f, depth, q) {
    const isExpanded = treeExpanded[f.id] === true;
    const expand = isExpanded || !!q;   // 搜索时强制展开
    // 先收集匹配内容：无匹配的文件夹整体隐藏（搜索态）
    const childNodes = [];
    let match = 0;
    for (const c of (f.children || [])) {
        const node = renderFolderNode(c, depth + 1, q);
        if (node) { childNodes.push(node); match++; }
    }
    const videos = (f.videos || []).filter(v => treeVideoMatches(v, q));
    match += videos.length;
    if (q && !match) return null;

    const div = document.createElement('div');
    div.className = 'tree-folder';
    const hasAny = (f.children && f.children.length) || (f.videos && f.videos.length);
    const arrow = hasAny ? (expand ? '▼' : '▶') : '';

    div.innerHTML = `<div class="tree-folder-label" style="padding-left:${depth*16+4}px" onclick="toggleFolder(this)" data-fid="${f.id}">
        <span class="tree-arrow">${arrow}</span>
        <span class="tree-icon">📂</span>
        <span class="tree-fname">${escHtml(f.name)}</span>
        <span class="tree-count">${hasAny ? videos.length : ''}</span>
        <span class="tree-ctx" onclick="event.stopPropagation();renameFolder(${f.id})" title="重命名">✏️</span>
        <span class="tree-ctx" onclick="event.stopPropagation();deleteFolder(${f.id})" title="删除">🗑️</span>
    </div>`;
    const childrenDiv = document.createElement('div');
    childrenDiv.className = 'tree-children';
    childrenDiv.style.display = expand ? 'block' : 'none';
    // 子文件夹
    for (const c of childNodes) childrenDiv.appendChild(c);
    // 视频
    for (const v of videos) childrenDiv.appendChild(makeVideoNode(v, depth + 1));
    div.appendChild(childrenDiv);
    return div;
}

function makeVideoNode(v, depth = 0) {
    const item = document.createElement('div');
    item.className = `tree-video${v.id === state.currentVideoId ? ' active' : ''}`;
    item.dataset.videoId = v.id;
    const labels = { pending: '待处理', processing: '提取中', done: '已识别', failed: '失败' };
    // 课程总结状态：📄 点击查看 / 生成中 / 失败可重试 / 无总结（字幕就绪）可手动生成
    const sLabels = { done: '📄', processing: '⏳', failed: '📄重试', none: '📄生成', pending: '📄生成' };
    const sTitle = {
        done: '查看课程总结', processing: '总结生成中', failed: '重新生成总结',
        none: '生成课程总结（字幕已就绪）', pending: '生成课程总结（字幕已就绪）',
    };
    const sAction = v.summary_status === 'done' ? `viewSummary(${v.id})`
        : v.summary_status === 'failed' ? `generateSummary(${v.id})`
        : (v.summary_status === 'none' || v.summary_status === 'pending')
            && v.subtitle_status === 'done' ? `generateSummary(${v.id})` : '';
    const checked = (state.mode === 'B' && sessionStorage.getItem('b_video_'+v.id) === '1') ? 'checked' : '';
    const delChecked = sessionStorage.getItem('del_sel_'+v.id) === '1' ? 'checked' : '';
    item.innerHTML = `<div class="tree-video-label" style="padding-left:${depth*16+4}px">
        <input type="checkbox" class="del-cb" data-video="${v.id}" ${delChecked} onchange="onDelCheckChange(${v.id}, this)">
        <input type="checkbox" class="b-video-cb" data-video="${v.id}" ${checked} style="display:none" onchange="onBCheckChange(${v.id}, this)">
        <span class="tree-icon" onclick="playVideo(${v.id})">🎬</span>
        <span class="tree-vname" onclick="playVideo(${v.id})">${escHtml(v.title || v.filename)}</span>
        <span class="tree-vmeta">${formatSize(v.file_size)}</span>
        <span class="tree-vstatus status-${v.subtitle_status}">${labels[v.subtitle_status] || ''}</span>
        <span class="tree-sum status-summary-${v.summary_status}" ${sAction ? `onclick="event.stopPropagation();${sAction}"` : ''}
              title="${sTitle[v.summary_status] || ''}">${sLabels[v.summary_status] || ''}</span>
        <span class="tree-ctx" onclick="event.stopPropagation();moveVideoDialog(${v.id})" title="移动到...">📂</span>
        <span class="tree-ctx" onclick="event.stopPropagation();extractSubtitle(${v.id})" title="重新提取字幕">🔄</span>
        <span class="tree-ctx" onclick="event.stopPropagation();deleteVideo(${v.id})" title="删除">🗑️</span>
    </div>`;
    return item;
}

function toggleFolder(el) {
    const children = el.parentElement.querySelector('.tree-children');
    if (!children) return;
    const showing = children.style.display !== 'none';
    children.style.display = showing ? 'none' : 'block';
    const arrow = el.querySelector('.tree-arrow');
    if (arrow) arrow.textContent = showing ? '▶' : '▼';
    const fid = el.dataset.fid;
    if (fid) treeExpanded[fid] = !showing;
}

// ===== 文件夹 CRUD =====
async function showNewFolderDialog() {
    // 拉取文件树生成「父级选择」下拉框，支持多级嵌套（专业→章节→视频）
    let html = '<label>文件夹名称</label><input type="text" id="dlgFolderName" maxlength="255" placeholder="如：第6讲 中值定理">';
    html += '<label>父级（留空为根目录）</label><select id="dlgFolderParent">';
    html += '<option value="0">（根目录）</option>';
    try {
        const resp = await apiFetch(`/api/folders/tree?subject=${state.subject}`);
        if (resp.ok) {
            const data = await resp.json();
            (function walk(fs, d) {
                for (const f of fs) {
                    html += `<option value="${f.id}">${'　'.repeat(d)}${f.name}</option>`;
                    walk(f.children || [], d + 1);
                }
            })(data.folders || [], 1);
        }
    } catch (e) { /* 树拉取失败仅影响父级选择，不阻断创建 */ }
    html += '</select>';
    openDialog({
        title: '📂 新建文件夹',
        bodyHTML: html,
        okText: '创建',
        onOk: () => {
            const name = document.getElementById('dlgFolderName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            const pid = parseInt(document.getElementById('dlgFolderParent').value || '0', 10);
            apiFetch('/api/folders/create', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({subject: state.subject, name: name, parent_id: pid}),
            }).then(r => {
                closeDialog();
                if (r.ok) loadTree();
                else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '创建失败'}`));
            });
        },
    });
}

function renameFolder(id) {
    openDialog({
        title: '✏️ 重命名文件夹',
        bodyHTML: '<label>新名称</label><input type="text" id="dlgFolderName" maxlength="255" placeholder="输入新名称">',
        okText: '保存',
        onOk: () => {
            const name = document.getElementById('dlgFolderName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            apiFetch(`/api/folders/${id}/rename`, {
                method: 'PUT', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({name: name}),
            }).then(r => {
                closeDialog();
                if (r.ok) loadTree();
                else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '重命名失败'}`));
            });
        },
    });
}

function deleteFolder(id) {
    openDialog({
        title: '🗑️ 删除文件夹',
        bodyHTML: '<div class="dialog-warn">删除文件夹后，其中的视频会移回「未分类」，<b>视频文件不会被删除</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/folders/${id}`, {method:'DELETE'})
                .then(r => { closeDialog(); if (r.ok) loadTree(); });
        },
    });
}

async function moveVideoDialog(videoId) {
    // 获取文件夹列表供选择（页内下拉，不再手输 ID）
    try {
        const resp = await apiFetch(`/api/folders/tree?subject=${state.subject}`);
        if (!resp.ok) throw new Error('加载文件夹失败');
        const data = await resp.json();
        let html = '<label>移动到：</label><select id="dlgMoveTarget">';
        html += '<option value="0">（未分类）</option>';
        (function walk(fs, d) {
            for (const f of fs) {
                html += `<option value="${f.id}">${'　'.repeat(d)}${f.name}</option>`;
                walk(f.children || [], d + 1);
            }
        })(data.folders || [], 1);
        html += '</select>';
        openDialog({
            title: '📂 移动视频',
            bodyHTML: html,
            okText: '移动',
            onOk: () => {
                const fid = parseInt(document.getElementById('dlgMoveTarget').value || '0', 10);
                apiFetch(`/api/folders/${videoId}/move?folder_id=${fid}`, {method:'PUT'})
                    .then(r => { closeDialog(); if (r.ok) loadTree(); });
            },
        });
    } catch (e) {
        addSystemMessage(`⚠️ ${e.message}`);
    }
}

// ===== 播放视频 =====
async function playVideo(videoId) {
    hideEndScreen(); // 隐藏结束屏幕
    state.currentVideoId = videoId;
    document.querySelectorAll('.tree-video').forEach(el => el.classList.toggle('active', parseInt(el.dataset.videoId) === videoId));
    closeVideoDrawer(); // 移动端：选完视频自动收起目录抽屉

    const video = document.getElementById('videoPlayer');
    const placeholder = document.getElementById('videoPlaceholder');

    // <video> 标签无法携带 Authorization 请求头，token 走查询参数（服务端两种都认）
    const tk = getApiToken();
    video.src = getApiUrl(`/api/videos/stream/${videoId}`) + (tk ? `?token=${encodeURIComponent(tk)}` : '');

    // 读取上次播放进度（续播：进度是每 5 秒落一次 localStorage）
    let savedPos = 0;
    try { savedPos = parseFloat(localStorage.getItem('kaoyan_progress_' + videoId) || '0') || 0; } catch (e) {}

    // 等待视频元数据加载成功后再切换显示状态（防鉴权失败/文件不存在导致黑屏）
    video.onloadedmetadata = () => {
        placeholder.style.display = 'none';
        video.style.display = 'block';
        if (savedPos > 10 && isFinite(video.duration) && savedPos < video.duration - 10) {
            video.currentTime = savedPos;
            addSystemMessage(`⏩ 已从上次位置 ${fmtTime(savedPos)} 继续播放`);
        } else if (savedPos > 2) {
            video.currentTime = savedPos;
        }
        video.controls = false; // 防御：确保任何情况下系统播放器不出现
        const vp = video.play();
        if (vp && vp.catch) vp.catch(() => { if (window.__vcOnPlayRejected) window.__vcOnPlayRejected(); });
        video.onerror = null; // 成功加载后移除错误处理器
    };

    // 加载失败：占位提示 + 统一错误层（带重试按钮）
    video.onerror = () => {
        video.style.display = 'none';
        placeholder.style.display = 'flex';
        if (window.__vcShowError) window.__vcShowError();
        addSystemMessage(`⚠️ 视频加载失败，请检查网络或访问令牌`);
    };

    await loadSubtitles(videoId);

    // 字幕列表面板已移除（画面字幕覆盖层仍可用，📄 开关控制）
    addSystemMessage(`🎬 播放中`);
}

// 播放完清掉进度记忆，下次重新从开头播；显示结束屏幕
document.getElementById('videoPlayer').addEventListener('ended', () => {
    try {
        if (state.currentVideoId) localStorage.removeItem('kaoyan_progress_' + state.currentVideoId);
    } catch (e) {}
    try { showEndScreen(); } catch (e) {}
});

function resetPlayer() {
    const video = document.getElementById('videoPlayer');
    const placeholder = document.getElementById('videoPlaceholder');
    video.pause(); video.src = ''; video.style.display = 'none';
    placeholder.style.display = 'flex';
    state.currentVideoId = null; state.subtitles = [];
    const _sp = document.getElementById('subtitlePanel');
    if (_sp) _sp.style.display = 'none';
}

// ===== 字幕（可点击跳转） =====
async function loadSubtitles(videoId) {
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/subtitles`);
        if (!resp.ok) return;
        const data = await resp.json();
        state.subtitles = data.subtitles || [];
        renderSubtitles();
    } catch (e) { console.error(e); }
}

function renderSubtitles() {
    const list = document.getElementById('subtitleList');
    const count = document.getElementById('subtitleCount');
    if (!list) return;   // 字幕列表面板已移除（仅保留画面字幕覆盖层）
    // 列表即将重建，先使 F2 的节点缓存与高亮失效
    subtitleItems = null;
    subtitleLastActiveIdx = -1;
    if (!state.subtitles.length) { list.innerHTML = '<div class="empty-state">无字幕</div>'; return; }
    count.textContent = `${state.subtitles.length} 段`;
    list.innerHTML = '';
    state.subtitles.forEach((s, i) => {
        const div = document.createElement('div');
        div.className = 'subtitle-item';
        div.dataset.seq = i;
        div.innerHTML = `<span class="sub-time" onclick="seekTo(${s.start})">${fmtTime(s.start)}</span>
            <span class="sub-text">${escHtml(s.text)}</span>`;
        div.onclick = () => seekTo(s.start);
        list.appendChild(div);
    });
}

// ===== 容器全屏（视频+字幕一起全屏）=====
// ===== 自动截图开关 =====
function toggleAutoCapture() {
    // auto-capture removed: use the camera button in chat input (video frame / PDF page)
    addSystemMessage('INFO: use the camera button in the chat input box instead');
}

function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
           (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}
function toggleFullscreen() {
    const wrapper = document.getElementById('videoWrapper');
    if (!wrapper) return;
    if (isFullscreen()) {
        if (document.exitFullscreen) document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
        return;
    }
    if (isIOS()) {
        addSystemMessage('⚠️ iOS Safari 不支持网页全屏，请把设备横过来看，或用系统播放器的全屏');
        return;
    }
    try {
        if (wrapper.requestFullscreen) wrapper.requestFullscreen();
        else if (wrapper.webkitRequestFullscreen) wrapper.webkitRequestFullscreen();
        else addSystemMessage('⚠️ 当前浏览器不支持全屏');
    } catch (e) {
        addSystemMessage('⚠️ 全屏失败: ' + e.message);
    }
}

async function togglePip() {
    const video = document.getElementById('videoPlayer');
    if (!video) return;
    try {
        // Chrome/Edge/Android WebView：标准 Picture-in-Picture
        if (document.pictureInPictureElement) { await document.exitPictureInPicture(); return; }
        if (video.requestPictureInPicture) { await video.requestPictureInPicture(); return; }
        // Safari：webkitSetPresentationMode
        if (video.webkitSupportsPresentationMode && video.webkitSetPresentationMode) {
            const mode = video.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture';
            video.webkitSetPresentationMode(mode);
            return;
        }
        addSystemMessage('⚠️ 当前浏览器不支持画中画');
    } catch (e) {
        addSystemMessage('⚠️ 画中画失败: ' + e.message);
    }
}

// ===== 字幕覆盖层开关 =====
let subtitleOverlayVisible = true;
function toggleSubtitleOverlay() {
    const overlay = document.getElementById('videoSubtitleOverlay');
    if (!overlay) return;
    subtitleOverlayVisible = !subtitleOverlayVisible;
    overlay.style.display = subtitleOverlayVisible ? 'block' : 'none';
}


// ===== 倍速控制 =====
let currentSpeed = 1;
function setSpeed(speed) {
    const video = document.getElementById('videoPlayer');
    if (video) {
        video.playbackRate = speed;
        currentSpeed = speed;
        // 高亮当前速度按钮
        document.querySelectorAll('.speed-btn').forEach(b => b.classList.toggle('active', parseFloat(b.textContent) === speed));
    }
}

function seekTo(seconds) {
    const video = document.getElementById('videoPlayer');
    if (!video) return;
    if (video.readyState >= 1) {
        video.currentTime = seconds;
        video.play();
    } else {
        // 等视频加载好再跳
        video.addEventListener('loadedmetadata', function onReady() {
            video.currentTime = seconds;
            video.play();
            video.removeEventListener('loadedmetadata', onReady);
        });
        video.load();
    }
}

function fmtTime(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2,'0')}`;
}

// 高亮当前字幕 + 视频悬浮字幕
let subtitleHighlightTimer = null;
// F2：字幕项节点缓存（renderSubtitles 重建列表时清空） + 上次高亮下标
let subtitleItems = null;
let subtitleLastActiveIdx = -1;

// 全屏状态检测
function isFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

document.addEventListener('DOMContentLoaded', () => {
    // 全屏变化时更新字幕显示
    document.addEventListener('fullscreenchange', updateSubtitleOverlayVisibility);
    document.addEventListener('webkitfullscreenchange', updateSubtitleOverlayVisibility);

    document.getElementById('videoPlayer').addEventListener('timeupdate', () => {
        if (state.mode !== 'A' || !state.subtitles.length) return;
        if (subtitleHighlightTimer) return;
        subtitleHighlightTimer = setTimeout(() => {
            subtitleHighlightTimer = null;
            const t = document.getElementById('videoPlayer').currentTime;
            // F2：二分定位当前字幕（字幕按 seq/start 升序），替代逐条线性遍历
            let activeIdx = -1;
            {
                let lo = 0, hi = state.subtitles.length - 1;
                while (lo <= hi) {
                    const mid = (lo + hi) >> 1;
                    if (state.subtitles[mid].start <= t) { activeIdx = mid; lo = mid + 1; }
                    else hi = mid - 1;
                }
                if (activeIdx >= 0 && t > state.subtitles[activeIdx].end) activeIdx = -1;
            }

            // F2：缓存字幕项节点数组 + 只 toggle 上/当前两个节点（替代每次 querySelectorAll 全量清类）
            if (!subtitleItems) subtitleItems = Array.from(document.querySelectorAll('.subtitle-item'));
            if (subtitleLastActiveIdx >= 0 && subtitleLastActiveIdx < subtitleItems.length) {
                if (subtitleItems[subtitleLastActiveIdx].classList.contains('active')) {
                    subtitleItems[subtitleLastActiveIdx].classList.remove('active');
                }
            }
            if (activeIdx >= 0 && activeIdx < subtitleItems.length && subtitleItems[activeIdx]) {
                subtitleItems[activeIdx].classList.add('active');
                subtitleLastActiveIdx = activeIdx;
                // 只滚动字幕列表容器本身：scrollIntoView 会冒泡滚动 document 视口（CSS overflow:hidden 挡不住编程滚动），
                // 把顶部导航顶出屏幕，出现"界面被拉起只剩一半"。改用容器内 scrollTop 计算
                const list = document.getElementById('subtitleList');
                if (list && subtitleItems[activeIdx]) {
                    const el = subtitleItems[activeIdx];
                    const ltop = el.offsetTop - list.clientHeight / 2 + el.clientHeight / 2;
                    list.scrollTop = ltop > 0 ? ltop : 0;
                }
                resetViewportScroll();
            } else {
                subtitleLastActiveIdx = -1;
            }

            // 更新视频悬浮字幕
            const overlay = document.getElementById('videoSubtitleOverlay');
            const subtitlePanel = document.getElementById('subtitlePanel');
            if (overlay && state.subtitles[activeIdx] && subtitleOverlayVisible) {
                overlay.textContent = state.subtitles[activeIdx].text;
                overlay.style.display = 'block';
            } else if (overlay) {
                overlay.style.display = 'none';
            }
        }, 300);
    });
});

function updateSubtitleOverlayVisibility() {
    const overlay = document.getElementById('videoSubtitleOverlay');
    if (!overlay) return;
    if (isFullscreen() && subtitleOverlayVisible) {
        overlay.classList.add('fullscreen');
        overlay.style.display = 'block';
    } else {
        overlay.classList.remove('fullscreen');
    }
}

let subtitlePanelVisible = true;
function toggleSubtitlePanel() {
    const list = document.getElementById('subtitleList');
    if (!list) return;   // 面板已移除
    const btn = document.getElementById('subtitleToggleBtn');
    subtitlePanelVisible = !subtitlePanelVisible;
    list.style.display = subtitlePanelVisible ? 'block' : 'none';
    btn.textContent = subtitlePanelVisible ? '收起' : '展开';
}

// ===== 视频上传 =====
// 上传统一走文件中心 /api/fs/upload（modules/tree.js 处理去重、子目录与产物管线）
async function uploadVideo(input) {
    if (window.FSTree) return window.FSTree.uploadFromInput(input);
}
async function uploadFolder(input) {
    if (window.FSTree) return window.FSTree.uploadFromInput(input);
}

// ===== 字幕提取串行队列：一次只识别一个视频（避免并发触发云 ASR 限流），并显示进度 1/N =====
const subtitleQueue = [];
let subtitleBusy = false;
let subtitleDoneCount = 0;

async function processSubtitleQueue() {
    if (subtitleBusy) return;
    subtitleBusy = true;
    while (subtitleQueue.length > 0) {
        const videoId = subtitleQueue.shift();
        const idx = ++subtitleDoneCount;
        const total = subtitleDoneCount + subtitleQueue.length;
        try {
            addSystemMessage(`🔄 提取字幕中（${idx}/${total}）...`);
            const resp = await apiFetch(`/api/videos/${videoId}/extract-subtitle`, {method:'POST'});
            if (!resp.ok) {
                let detail = '提取失败';
                try { const d = await resp.json(); detail = d.detail || d.message || detail; } catch (e) {}
                throw new Error(detail);
            }
            const data = await resp.json();
            addSystemMessage(`✅ 字幕提取完成（${idx}/${total}）`); loadTree();
            // 只在当前播放的视频才刷新字幕
            if (videoId === state.currentVideoId) {
                await loadSubtitles(videoId);
            }
        } catch (err) {
            addSystemMessage(`⚠️ 字幕提取失败（${idx}/${total}）: ${err.message}`); loadTree();
        }
    }
    subtitleBusy = false;
    subtitleDoneCount = 0;
}

function extractSubtitle(videoId) {
    subtitleQueue.push(videoId);
    processSubtitleQueue();
}

async function deleteVideo(videoId) {
    openDialog({
        title: '🗑️ 删除视频',
        bodyHTML: '<div class="dialog-warn">将删除该视频文件及其字幕/总结记录，<b>不可恢复</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/videos/${videoId}`, {method:'DELETE'}).then(r => {
                closeDialog();
                if (state.currentVideoId === videoId) resetPlayer();
                loadTree();
            });
        },
    });
}

// ===== 批量删除视频（顶部 🗑️ 按钮：首次点击进入批量选择模式，再次点击删除选中）=====

// 进入批量选择模式：显示视频勾选框
function enterDelMode() {
    state.delMode = true;
    const tree = document.getElementById('videoTreeBody');
    if (tree) tree.classList.add('del-mode');
    const btn = document.querySelector('.btn-del-batch');
    if (btn) btn.classList.add('active');
    addSystemMessage('🖱️ 已进入批量选择模式：勾选要删除的视频，再点一次 🗑️ 确认删除；点 × 或切换科目/模式可取消。');
}

// 退出批量选择模式（不自动清空已勾选项，避免误清已做选择）
function exitDelMode() {
    if (!state.delMode) return;
    state.delMode = false;
    const tree = document.getElementById('videoTreeBody');
    if (tree) tree.classList.remove('del-mode');
    const btn = document.querySelector('.btn-del-batch');
    if (btn) btn.classList.remove('active');
}

function getDelSelectedIds() {
    const ids = [];
    document.querySelectorAll('#videoTreeBody .del-cb:checked').forEach(cb => ids.push(Number(cb.dataset.video)));
    return ids;
}

function updateDelBatchCount() {
    const n = getDelSelectedIds().length;
    const badge = document.getElementById('delBatchCount');
    if (!badge) return;
    badge.style.display = n ? 'inline-block' : 'none';
    badge.textContent = n;
}

function onDelCheckChange(videoId, cb) {
    if (cb.checked) sessionStorage.setItem('del_sel_' + videoId, '1');
    else sessionStorage.removeItem('del_sel_' + videoId);
    updateDelBatchCount();
}

async function onDelBatchClick() {
    // 未进入批量模式：首次点击 → 进入批量选择模式
    if (!state.delMode) {
        enterDelMode();
        updateDelBatchCount();
        return;
    }
    // 已进入批量模式：二次点击 → 执行删除
    const ids = getDelSelectedIds();
    if (!ids.length) { addSystemMessage('⚠️ 尚未勾选任何视频，再次点击 🗑️ 退出批量模式'); return; }
    openDialog({
        title: '🗑️ 批量删除视频',
        bodyHTML: `<div class="dialog-warn">将删除选中的 <b>${ids.length}</b> 个视频文件及其字幕/总结记录，<b>不可恢复</b>。确定继续吗？</div>`,
        okText: '删除',
        onOk: () => {
            exitDelMode();
            apiFetch('/api/videos/batch-delete', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids: ids}),
            }).then(async r => {
                closeDialog();
                const d = await r.json().catch(() => ({}));
                if (!r.ok) { addSystemMessage(`⚠️ ${d.detail || '批量删除失败'}`); return; }
                // 清理已删除视频的勾选状态
                ids.forEach(id => sessionStorage.removeItem('del_sel_' + id));
                if (state.currentVideoId && ids.includes(state.currentVideoId)) resetPlayer();
                addSystemMessage(`✅ 已删除 ${d.success || 0} 个视频` + (d.failed ? `，失败 ${d.failed} 个` : ''));
                loadTree();
            });
        },
    });
}

// ===== 重新扫描本地目录（找回手动放入 storage/videos 的视频）=====
async function rescanVideos() {
    addSystemMessage('🔎 正在扫描本地视频目录...');
    try {
        const resp = await apiFetch('/api/videos/rescan', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        addSystemMessage(d.message || (resp.ok ? '✅ 扫描完成' : `⚠️ 扫描失败（${resp.status}）`));
        loadTree();
    } catch (err) { addSystemMessage(`⚠️ 扫描失败：${err.message}`); }
}

// ===== 一键补生成课程总结（存量的字幕已完成但无总结的视频）=====
async function batchSummaries() {
    try {
        const resp = await apiFetch('/api/videos/batch-summary', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) { addSystemMessage(`⚠️ ${d.detail || '批量生成失败'}`); return; }
        addSystemMessage(d.message || `✅ 已开始批量生成（${d.queued} 个视频）`);
        loadTree();
        // 后台正在生成时轻量轮询树状态（有 ⏳ 徽标即每 15 秒刷新，结束自动停）
        const poll = () => {
            if (!document.querySelector('.status-summary-processing')) return;
            loadTree();
            setTimeout(poll, 15000);
        };
        setTimeout(poll, 10000);
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); }
}

// ===== 课程总结（查看 / 手动生成重试）=====
async function viewSummary(videoId) {
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/summary`);
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok || d.status !== 'done' || !d.content) {
            addSystemMessage('ℹ️ 总结尚未生成（字幕完成后自动生成）');
            return;
        }
        document.getElementById('summaryBody').textContent = d.content;
        const dl = document.getElementById('summaryDownload');
        dl.onclick = () => {
            const blob = new Blob([d.content], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            const nameEl = document.querySelector(`.tree-video[data-video-id="${videoId}"] .tree-vname`);
            a.download = `${(nameEl ? nameEl.textContent.trim() : `video_${videoId}`)}-课程总结.md`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        };
        document.getElementById('summaryModal').style.display = 'flex';
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); }
}

function closeSummary() {
    const m = document.getElementById('summaryModal');
    if (m) m.style.display = 'none';
}

async function generateSummary(videoId) {
    addSystemMessage('📄 正在生成课程总结（分段提取，约需 10~60 秒）...');
    try {
        const resp = await apiFetch(`/api/videos/${videoId}/generate-summary`, { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            addSystemMessage(`⚠️ 总结生成失败：${d.detail || resp.status}`);
            loadTree();
            return;
        }
        addSystemMessage('✅ 课程总结生成完成，点 📄 查看');
        loadTree();
    } catch (err) { addSystemMessage(`⚠️ ${err.message}`); loadTree(); }
}

// ===== 聊天 =====
// ask 卡片的待答内容：非空 = 这次发送是"回答 AI 的提问"（作为 tool 结果续上，不再新增用户气泡）
let pendingAsk = null;
// 当前页面上「还没作答」的活动卡片。用户不答、直接打字发新消息时，
// 把这张卡片就地置为「未作答」并禁用，避免它一直可点击、之后提交出一个
// 早已作废的 tool_call_id（后端会拒绝）。
let liveAskCard = null;

// 把活动卡片标记为「未作答，已直接继续」（样式与历史里的未作答卡片一致）
function abandonLiveAskCard() {
    const card = liveAskCard;
    liveAskCard = null;
    if (!card || card.classList.contains('submitted')) return;
    card.classList.add('unanswered', 'readonly');
    card.querySelectorAll('input, button').forEach(el => { el.disabled = true; });
    const foot = card.querySelector('.ask-foot');
    if (foot) foot.innerHTML = '<span class="ask-hint">未作答，已直接继续</span>';
}

async function sendMessage() {
    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    const hasImages = state.images.length > 0;
    const isAskReply = !!pendingAsk;
    if ((!text && !hasImages && !isAskReply) || state.isStreaming) return;
    if (!isAskReply) {
        abandonLiveAskCard();  // 没答卡片直接发新消息：这张卡片就此作废
        // 只发图没打字时，后端会把「分析图片」作为正文（下面 fd 里同一份文本），
        // 气泡也显示同一段，保证「刚发送」和「重载历史」看到的一模一样
        const displayText = text || (hasImages ? '分析图片' : '');
        input.value = '';
        addUserMessage(displayText, state.images.map(i => i.dataUrl), quoteState ? quoteState.text : '');
    }
    document.getElementById('btnSend').disabled = true;
    state.isStreaming = true;
    let typingId = showTyping();  // 思考期间显示"打字中"
    let streamingEl = null;       // 流式正文字泡（SSE 到达后接管）
    let streamingContent = null;
    let lastRender = 0;
    // ---- 思考静默心跳：提升到 try 外，异常路径也能安全清理 ----
    let thinkingTimer = null;     // 500ms 心跳：更新"已用时"计时 + 判定静默
    const stopThinkingTimer = () => {
        if (thinkingTimer) { clearInterval(thinkingTimer); thinkingTimer = null; }
    };
    try {
        // A模式 + 视频播放中 + 自动截图开 → 捕获当前帧
        let captureBlob = null;
        if (state.autoCapture && state.mode === 'A' && state.currentVideoId) {
            const video = document.getElementById('videoPlayer');
            if (video && video.videoWidth > 0) {
                const c = document.createElement('canvas');
                c.width = video.videoWidth;
                c.height = video.videoHeight;
                c.getContext('2d').drawImage(video, 0, 0);
                captureBlob = await new Promise(resolve => c.toBlob(resolve, 'image/jpeg', 0.7));
            }
        }

        // 统一走 SSE 流式端点（文本/图片/自动截图/模式B 勾选都走 FormData）
        const fd = new FormData();
        fd.append('query', text || (isAskReply ? '' : '分析图片'));
        fd.append('subject', state.subject);
        fd.append('mode', state.mode);
        fd.append('subtitle_context', state.mode === 'A' ? await getModeASubtitleContext() : '');
        fd.append('conversation_id', state.conversationId);
        // 文件中心：agent 上下文跟随当前查看的文件（页码/时间点一并传给编排器）
        // 沙箱聚焦（共识 Q4b）：锁定状态下把聚焦文件夹一起传给后端
        if (state.focusFolderId && state.focusLocked) {
            fd.append('focus_folder_id', String(state.focusFolderId));
            fd.append('focus_locked', '1');
        }
        if (window.FSState && window.FSState.currentFileId()) {
            fd.append('file_id', String(window.FSState.currentFileId()));
            const _pos = window.FSState.currentPosition();
            if (_pos) {
                // 带上提问文本 → 后端在 chunks 里检索相关片段注入（自定义编排上下文）
                _pos.hit_query = (text || '').slice(0, 120);
                fd.append('position', JSON.stringify(_pos));
            }
        }
        if (isAskReply) {
            // ask/quiz 回答轮：把用户作答作为 tool 结果回填，后端接着这一轮继续推理。
            // 图片单独作为 multipart 字段发（JSON.stringify 序列化不了 File）
            fd.append('ask_answers', JSON.stringify({
                tool_call_id: pendingAsk.tool_call_id, answers: pendingAsk.answers,
            }));
            (pendingAsk.images || []).forEach(img => fd.append('image', img.file));
            pendingAsk = null;
        }
        if (quoteState && quoteState.text) fd.append('quote', JSON.stringify({ text: quoteState.text }));
        if (state.mode === 'B') fd.append('watched_video_ids', JSON.stringify(getSelectedBVideos()));
        // 手动粘贴/选择的图片全部发出去（同名多字段）；自动截图帧若存在也一并发送
        state.images.forEach(img => fd.append('image', img.file));
        if (captureBlob) fd.append('image', captureBlob, 'frame.jpg');
        removeImage();  // 清空待发图并隐藏预览

        const resp = await apiFetch('/api/chat/send-stream', { method: 'POST', body: fd });
        if (!resp.ok) {
            let detail = `${resp.status}`;
            try { detail = (await resp.json().catch(() => ({}))).detail || detail; } catch (e) {}
            throw new Error(detail);
        }

        // ---- 解析 SSE 流，打字机式渲染 + 思维链"深度思考中"展示 ----
        removeTyping(typingId); typingId = 0;
        streamingEl = document.createElement('div');
        streamingEl.className = 'message assistant';
        streamingEl.innerHTML = `
            <div class="reasoning-block" style="display:none">
                <div class="reasoning-header" onclick="toggleReasoning(this)"><span class="reasoning-arrow">▶</span><span class="reasoning-title">🧠 深度思考中...</span></div>
                <div class="reasoning-idle" style="display:none"><span class="spinner"></span><span class="idle-text">💭 正在深入思考…</span></div>
                <div class="reasoning-body"></div>
            </div>
            <div class="msg-content"></div><div class="msg-time"></div>`;
        streamingContent = streamingEl.querySelector('.msg-content');
        const reasoningBlock = streamingEl.querySelector('.reasoning-block');
        const reasoningBody = streamingEl.querySelector('.reasoning-body');
        const reasoningTitle = streamingEl.querySelector('.reasoning-title');
        const reasoningIdleEl = streamingEl.querySelector('.reasoning-idle');
        document.getElementById('chatMessages').appendChild(streamingEl);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        // F1 流式增量渲染状态：
        //   raw           —— 已收到的全部原生（未转义）正文
        //   baseStableDom —— 已"稳定闭合"的渲染容器（仅 append，不重建，避免 O(n²)）
        //   activeDom     —— 尾部"可能未闭合公式"的活动区（每次用 innerHTML 重建，通常只有几十字）
        //   activeStart   —— activeDom 起始位置对应到 raw 的下标；-1 表示无活动区
        let raw = '';
        let activeStart = -1;
        let baseStableDom = document.createElement('div');
        let activeDom = document.createElement('div');
        const flushStream = () => {
            if (!raw) return;
            const b = mathBoundaryIndex(raw, raw.length);
            const newActive = (b >= 0) ? b : raw.length;
            // 先固化：上次活动区若这次已不是活动区起点之前的部分，代表公式已闭合，
            // 把这段并入稳定区（仅渲染一次并 append）。
            if (activeStart >= 0 && newActive > activeStart) {
                flushStreamStaticPart(activeStart, newActive);
            } else if (activeStart >= 0 && newActive < activeStart) {
                // 内部回退：清掉活动区，重放整个新活动区
                activeDom.innerHTML = '';
            }
            // 活动区渲染
            if (newActive < raw.length) {
                activeDom.innerHTML = renderMath(escHtml(raw.slice(newActive)));
            } else if (activeDom.innerHTML) {
                activeDom.innerHTML = '';
            }
            activeStart = newActive;
        };
        const flushStreamStaticPart = (from, to) => {
            // 把 raw[from:to] 渲染并固化到稳定区
            const htmlSafe = renderMath(escHtml(raw.slice(from, to)));
            // 用临时 wrapper 转成节点再挪入稳定区
            const wrap = document.createElement('div');
            wrap.innerHTML = htmlSafe;
            while (wrap.firstChild) baseStableDom.appendChild(wrap.firstChild);
        };
        // 提前建好稳定/活动容器，避免后续在循环里反复 append 到底层容器
        streamingContent.appendChild(baseStableDom);
        streamingContent.appendChild(activeDom);
        let reasoningText = '';
        let reasoningStarted = false;
        let contentStarted = false;
        let reasoningLastRender = 0;
        // ---- 思考反馈：上游 API 首 token 前常静默很久，用计时/动画让用户知道一直在干活 ----
        const IDLE_THRESHOLD_MS = 2500;   // 思考期静默阈值（有 reasoning 后）
        const WAIT_THRESHOLD_MS = 2500;   // 等待首个 token 阈值（发送后）
        let reasoningStartedAt = 0;       // 思考开始时间（用于标题"已用时"）
        let lastReasoningAt = 0;          // 最近一次收到 reasoning token 的时间
        let reasoningIdle = false;        // 思考期是否处于静默提示态
        let streamStartTime = 0;          // 进入流式（创建 streamingEl）时刻
        let reasoningBlockShown = false;  // 等待期是否已把思考块显示出来
        const idleTextEl = reasoningBlock.querySelector('.idle-text');
        const startThinkingTimer = () => {
            if (thinkingTimer) return;
            thinkingTimer = setInterval(() => {
                const now = Date.now();
                const setLabel = (t) => { if (reasoningTitle.textContent !== t) reasoningTitle.textContent = t; };
                const setIdleText = (t) => { if (idleTextEl.textContent !== t) idleTextEl.textContent = t; };
                // 阶段1：还没收到任何 token —— 等待模型首个输出（最常见的长静默在这里）
                if (!reasoningStarted && !contentStarted) {
                    const waited = now - streamStartTime;
                    if (waited > WAIT_THRESHOLD_MS) {
                        if (!reasoningBlockShown) {
                            reasoningBlockShown = true;
                            reasoningBlock.style.display = '';
                            reasoningIdleEl.style.display = '';
                            setLabel('🧠 深度思考中…');
                        }
                        setIdleText(`⏳ 正在等待模型响应…（已等待 ${Math.round(waited / 1000)}s）`);
                    }
                    return;
                }
                if (contentStarted) return; // 正文开始后由收尾/collapseReasoning 清理
                // 阶段2：思考中 —— 静默判定 + 已用时计时
                const idle = now - lastReasoningAt > IDLE_THRESHOLD_MS;
                if (idle !== reasoningIdle) {
                    reasoningIdle = idle;
                    reasoningIdleEl.style.display = idle ? '' : 'none';
                }
                if (reasoningIdle) setIdleText('💭 正在深入思考…');
                const secs = Math.round((now - reasoningStartedAt) / 1000);
                setLabel(`🧠 深度思考中…（已用时 ${secs}s）`);
            }, 500);
        };
        let sseError = '';
        let done = false;
        let askEvent = null;   // 模型要求调用 ask 工具时的事件（questions/tool_call_id/text）
        // 进入流式即启动心跳：覆盖"等待首个 token"的静默期
        streamStartTime = Date.now();
        startThinkingTimer();

        const renderReasoningThrottled = () => {
            const now = Date.now();
            if (now - reasoningLastRender > 50) {
                // 只更新思考文本，不做任何自动滚动（用户可自由上翻阅读，不被打断）
                reasoningBody.textContent = reasoningText;
                reasoningLastRender = now;
                updateScrollToBottomBtn();
            }
        };
        const renderThrottled = () => {
            // F1：只增量渲染新增段，不再整段 innerHTML 重渲（原为 O(n²)，现 O(n)）
            const now = Date.now();
            if (now - lastRender > 50) {
                flushStream();
                lastRender = now;
                updateScrollToBottomBtn();
            }
        };
        // 正文开始后：思考块收起为一行，可点击展开
        const collapseReasoning = () => {
            if (!reasoningStarted) return;
            stopThinkingTimer();
            reasoningIdleEl.style.display = 'none';
            reasoningBlock.classList.add('collapsed');
            reasoningTitle.textContent = '🧠 已深度思考（点击展开）';
            streamingEl.querySelector('.reasoning-arrow').textContent = '▶';
        };

        while (true) {
            const { value, done: readerDone } = await reader.read();
            if (readerDone) break;
            buffer += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
                const event = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                for (const line of event.split('\n')) {
                    if (!line.startsWith('data:')) continue;
                    const data = line.slice(5).trim();
                    if (!data) continue;
                    let obj;
                    try { obj = JSON.parse(data); } catch (e) { continue; }
                    if (obj.type === 'reasoning' && obj.text) {
                        // 思维链实时展示（独立于正文，不混入回答）
                        if (!reasoningStarted) {
                            reasoningStarted = true;
                            reasoningStartedAt = Date.now();
                            lastReasoningAt = reasoningStartedAt;
                            reasoningBlock.style.display = '';
                            // 撤掉"等待响应"提示：有思考内容了，转文字滚动
                            reasoningIdleEl.style.display = 'none';
                        }
                        reasoningText += obj.text;
                        lastReasoningAt = Date.now();
                        // 恢复输出：立即撤掉静默提示（不等下一次心跳）
                        if (reasoningIdle) {
                            reasoningIdle = false;
                            reasoningIdleEl.style.display = 'none';
                        }
                        renderReasoningThrottled();
                    } else if (obj.type === 'delta' && obj.text) {
                        if (!contentStarted) {
                            contentStarted = true;
                            stopThinkingTimer();  // 正文开始，停心跳（无论是否走过思考）
                            collapseReasoning();
                        }
                        raw += obj.text;
                        renderThrottled();
                    } else if (obj.type === 'done') {
                        state.conversationId = obj.conversation_id;
                        saveActiveConv();
                        clearQuote();  // 发送成功，撤销引用条
                        done = true;
                    } else if (obj.type === 'ask') {
                        askEvent = obj;
                    } else if (obj.type === 'tool') {
                        showToolHint(streamingEl, obj);
                    } else if (obj.type === 'error') {
                        sseError = obj.detail || 'AI 调用失败';
                    }
                }
            }
        }

        // 收尾：停止思考心跳，强制渲染最终稿 + 时间戳（最后把活动区的节点原样固化进稳定区，不重渲）
        stopThinkingTimer();
        flushStream();
        if (streamingContent) {
            // 活动区内容已由最后一次 flushStream 渲染好，直接并入稳定区顶层即可
            if (activeDom.childNodes.length) {
                while (activeDom.firstChild) baseStableDom.appendChild(activeDom.firstChild);
            } else if (activeStart >= 0 && activeStart < raw.length) {
                // 极端兜底：active 区为空但确有未渲染文本（如收尾前没碰过活跃公式），再渲染一次
                flushStreamStaticPart(activeStart, raw.length);
            }
            activeDom.remove();
            streamingEl.querySelector('.msg-time').textContent = time();
            // ask 工具：把 AI 的提问渲染成可交互卡片（提交后作为 tool 结果续上这一轮）
            if (askEvent && !sseError) renderAskCard(streamingEl, askEvent, askEvent.kind);
            scrollChat();
        }
        if (reasoningStarted && !sseError) {
            // 最终落定思考块状态（正文未出现时也收起为"已思考"）
            reasoningTitle.textContent = '🧠 已深度思考（点击展开）';
            streamingEl.querySelector('.reasoning-arrow').textContent = '▶';
            reasoningBlock.classList.add('collapsed');
        }
        if (sseError) {
            streamingEl.remove();
            addSystemMessage(`⚠️ ${sseError}`);
        } else if (!done) {
            streamingEl.remove();
            addSystemMessage('⚠️ 连接中断，请重试');
        }
        if (state.conversationId) loadConversations();
    } catch (err) {
        stopThinkingTimer();
        if (typingId) removeTyping(typingId);
        if (streamingEl) streamingEl.remove();
        addSystemMessage(`⚠️ ${err.message}`);
    }
    finally {
        document.getElementById('btnSend').disabled = false;
        state.isStreaming = false;
        input.focus({ preventScroll: true });
        resetViewportScroll();
    }
}

function onInputKeydown(e) {
    if (e.key==='Enter'&&!e.shiftKey) { e.preventDefault(); sendMessage(); }
}

// Ctrl+V 粘贴图片
// ===== 多图支持：统一加入待发送队列（最多 MAX_IMAGES 张，单张 ≤10MB）=====
const MAX_IMAGES = 4;
function addImages(files) {
    if (!files || !files.length) return;
    let added = 0;
    for (const file of files) {
        if (!file.type || !file.type.startsWith('image/')) continue;
        if (file.size > 10 * 1024 * 1024) { addSystemMessage('⚠️ 单张图片最大 10MB'); continue; }
        if (state.images.length >= MAX_IMAGES) {
            addSystemMessage(`⚠️ 一次最多 ${MAX_IMAGES} 张图片`);
            break;
        }
        const reader = new FileReader();
        reader.onload = ev => {
            state.images.push({ dataUrl: ev.target.result, file });
            renderImagePreview();
            addSystemMessage(`📋 已添加图片（${state.images.length}/${MAX_IMAGES}）`);
        };
        reader.readAsDataURL(file);
        added++;
    }
}

// Ctrl+V 粘贴图片（同时监听 document 级别 + HTML onpaste）
function onPaste(e) {
    const items = e.clipboardData?.items;
    if (!items) { addSystemMessage('⚠️ 无法读取剪贴板'); return; }
    const files = [];
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            const file = item.getAsFile();
            if (file) files.push(file);
        }
    }
    if (files.length) {
        addImages(files);
    } else if (items.length > 0) {
        addSystemMessage('ℹ️ 剪贴板中没有图片');
    }
}

// document 级别兜底（防止 textarea focus 丢失）
document.addEventListener('paste', function(e) {
    const target = e.target;
    // 只处理在聊天输入区的粘贴
    if (target && target.id === 'chatInput') return; // onpaste 已处理
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            // 聚焦到聊天框
            const input = document.getElementById('chatInput');
            if (input) input.focus({preventScroll: true});
            // 重新触发
            onPaste(e);
            break;
        }
    }
});

async function getModeASubtitleContext() {
    // 文件中心：会话绑定文件时，上下文由后端编排器按模板注入（共识 D5），不再传字幕全文
    if (window.FSState && window.FSState.currentFileId()) return '';
    // 模式A上下文 = 整讲字幕全文 + 当前播放位置。
    // 一节视频的字幕仅 1~1.5 万 token（中文 ~1.2 token/字），对 DeepSeek
    // 的大上下文毫无压力；数学讲解前后依赖强（前 30 分钟定理后 30 分钟引用），
    // 全量保留才能让 agent 完整理解整讲脉络，再配合播放位置时间戳，
    // agent 可以精确知道用户学到第几分钟。
    if (!state.subtitles.length) return '';
    const video = document.getElementById('videoPlayer');
    const t = (video && typeof video.currentTime === 'number') ? video.currentTime : 0;
    const d = (video && typeof video.duration === 'number' && isFinite(video.duration)) ? video.duration : 0;
    const pos = `${fmtTime(t)} / ${fmtTime(d)}`;
    // ⚡ 精简模式（设置页勾选）：课程总结 + 当前位置前后 ±10 分钟字幕，回答更快更省
    if (isCompactASubjectCtx() && state.currentVideoId) {
        const WIN = 600;   // ±10 分钟
        let summaryText = '';
        try {
            const resp = await apiFetch(`/api/videos/${state.currentVideoId}/summary`);
            const sd = await resp.json().catch(() => ({}));
            if (resp.ok && sd.status === 'done' && sd.content) summaryText = sd.content.trim();
        } catch (e) {}
        const excerpt = state.subtitles
            .filter(s => s.start >= t - WIN && s.start <= t + WIN)
            .map(s => (s.text || '')).filter(Boolean).join(' ');
        const summaryPart = summaryText ? `\n【课程总结】\n${summaryText}` : '';
        const nearPart = excerpt ? `\n【当前位置附近字幕】\n${excerpt}` : '';
        return `【当前播放位置：${pos}】${summaryPart}${nearPart}`;
    }
    const full = state.subtitles.map(s => (s && s.text) || '').filter(Boolean).join(' ');
    return `【当前播放位置：${pos}】 ${full}`;
}

// ===== 图片上传（支持多选，最多 MAX_IMAGES 张）=====
function onImagePick(input) {
    const files = input.files ? Array.from(input.files) : [];
    input.value = '';
    if (!files.length) return;
    addImages(files);
}

// 渲染待发送图片的缩略图预览（每张可单独删除）
function renderImagePreview() {
    const preview = document.getElementById('imagePreview');
    if (!preview) return;
    preview.innerHTML = '';
    if (!state.images.length) {
        preview.style.display = 'none';
        return;
    }
    state.images.forEach((img, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'image-preview-item';
        wrap.innerHTML = `<img src="${img.dataUrl}" alt="预览${i + 1}">
            <button class="btn-remove-img" onclick="removeImage(${i})" aria-label="移除图片${i + 1}">✕</button>`;
        preview.appendChild(wrap);
    });
    preview.style.display = 'flex';
}

// 移除指定下标（无参会自动删除时兼容旧调用：移除全部）
function removeImage(idx) {
    if (typeof idx === 'number') {
        state.images.splice(idx, 1);
    } else {
        state.images = [];
    }
    renderImagePreview();
}

// ===== 语音输入（浏览器录音 → 后端 Whisper/千问转文字 → 填入输入框）
// 实时草稿：本地引擎下每 2.5s 把「从开头到现在的完整音频」送转一次并刷新输入框
// （边说边出字、边自我修正），点停止定稿；千问云为省配额保持「停止后整段转写」。
let voiceRecorder = null;   // MediaRecorder
let voiceChunks = [];
let voiceStream = null;     // getUserMedia 流（停止时逐个 track.stop）
let voiceTimer = null;      // 录音秒数计时
let voiceSec = 0;
let voiceLiveTimer = null;  // 实时草稿轮询
let voiceLiveBusy = false;  // 上一轮草稿转写未返回时跳过本轮
let voicePrefixTxt = '';    // 录音前输入框已有内容（草稿拼在其后）
let voiceEngine = 'local';  // 当前 ASR 引擎（仅 local 开实时草稿）
let voiceStopping = false;  // 停止/定稿流程标记
const VOICE_MAX_SEC = 120;  // 最长录音 2 分钟（防忘关）
const VOICE_LIVE_MS = 2500; // 实时草稿刷新间隔

function voiceBtnEl() { return document.getElementById('voiceInputBtn'); }

function setVoiceBtnUI(recording, sec) {
    const btn = voiceBtnEl();
    if (!btn) return;
    btn.classList.toggle('recording', recording);
    btn.textContent = recording ? `⏺${sec || ''}` : '🎤';
    btn.title = recording ? '点击停止并定稿' : '🎤 语音输入（录音时边说边出字）';
}

async function toggleVoiceInput() {
    if (voiceRecorder && voiceRecorder.state === 'recording') { stopVoiceInput(); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
        addSystemMessage('⚠️ 当前浏览器不支持录音（需 Chrome/Edge/Firefox 等）');
        return;
    }
    try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        addSystemMessage('⚠️ 无法访问麦克风：请点浏览器地址栏的麦克风图标允许权限后重试');
        return;
    }
    // 语音输入引擎按设置页独立配置解析（voice_provider：auto/local/qwen/zhipu/tencent/siliconflow；
    // auto = 自动降级链 智谱→千问→腾讯→硅基流动→本地，按已配 key 的引擎优先）
    voiceEngine = await detectVoiceEngine();

    voiceChunks = [];
    voiceSec = 0;
    voiceStopping = false;
    const ta = document.getElementById('chatInput');
    voicePrefixTxt = (ta.value || '').trim();
    // 录音期间锁定输入框为只读的实时草稿态
    ta.readOnly = true;
    ta.classList.add('voice-live');
    ta.placeholder = '🎤 识别中…说完点按钮定稿';

    const mr = new MediaRecorder(voiceStream);
    voiceRecorder = mr;
    mr.ondataavailable = (e) => { if (e.data && e.data.size) voiceChunks.push(e.data); };
    mr.onstop = () => { voiceRecorder = null; stopVoiceLive(); finalizeVoice(); };
    mr.onerror = () => {
        voiceRecorder = null; stopVoiceLive(); cleanupVoiceStream();
        restoreVoiceInput();
        addSystemMessage('⚠️ 录音出错，请重试');
    };
    mr.start(250);  // 每 250ms 收集一次数据，防止过长录音丢数据
    setVoiceBtnUI(true, '');
    voiceTimer = setInterval(() => {
        voiceSec++;
        setVoiceBtnUI(true, `${voiceSec}s`);
        if (voiceSec >= VOICE_MAX_SEC) stopVoiceInput();
    }, 1000);
    // 实时草稿只对本地引擎开：云引擎按音频秒计费，每 2.5s 重转整段会放大 ~7 倍费用，
    // 云端退回「停止后整段转写」（30 秒约 6 厘钱，可忽略）
    if (voiceEngine === 'local') {
        voiceLiveTimer = setInterval(() => { voiceLiveTick(); }, VOICE_LIVE_MS);
        setTimeout(() => voiceLiveTick(), 600);  // 尽快出第一版草稿
        addSystemMessage('🎤 录音中…本地 Whisper 实时草稿（免费离线），说完再点一次按钮定稿');
    } else {
        addSystemMessage('🎤 录音中…云端识别（以设置页选中引擎计费），说完再点一次按钮定稿');
    }
}

// 语音输入引擎解析：按设置页 asr.voice_provider（auto/local/qwen/zhipu/tencent/siliconflow）。
// auto = 自动降级链：智谱 → 千问 → 腾讯 → 硅基流动（按已配置 key 的引擎优先），全无则本地
async function detectVoiceEngine() {
    try {
        const resp = await apiFetch('/api/config/model');
        const cfg = await resp.json().catch(() => ({}));
        const asr = (cfg && cfg.asr) || {};
        const vp = String(asr.voice_provider || 'auto').toLowerCase();
        if (vp !== 'auto' && vp !== 'local' && vp !== 'qwen' && vp !== 'zhipu' && vp !== 'tencent' && vp !== 'siliconflow') {
            return 'local';
        }
        if (vp === 'local') return 'local';
        if (['qwen', 'zhipu', 'tencent', 'siliconflow'].includes(vp)) return vp;
        // auto：按降级链顺序取第一个已配置 key 的云引擎
        const eng = (asr.engines) || {};
        for (const name of ['zhipu', 'qwen', 'tencent', 'siliconflow']) {
            const e = eng[name] || {};
            const ok = name === 'tencent'
                ? !!(e.secret_id && e.secret_key)
                : !!e.api_key;
            if (ok) return name;
        }
        return 'local';
    } catch (e) { return 'local'; }
}

// 实时草稿：把「从头到现在的完整音频」整段重转，刷新输入框（结果永远是最新最准的）
async function voiceLiveTick() {
    if (voiceLiveBusy || voiceStopping || !voiceRecorder || voiceRecorder.state !== 'recording') return;
    if (!voiceChunks.length) return;
    voiceLiveBusy = true;
    try {
        const fd = new FormData();
        fd.append('file', new Blob(voiceChunks, { type: 'audio/webm' }), 'voice.webm');
        const resp = await apiFetch('/api/voice/transcribe', { method: 'POST', body: fd });
        const d = await resp.json().catch(() => ({}));
        if (resp.ok && d && d.text) {
            const text = d.text.trim();
            const ta = document.getElementById('chatInput');
            ta.value = voicePrefixTxt ? voicePrefixTxt + ' ' + text : text;
        }
    } catch (e) {
        // 草稿轮询静默失败：下一轮自动覆盖，不打断录音
    }
    voiceLiveBusy = false;
}

function stopVoiceLive() {
    if (voiceLiveTimer) { clearInterval(voiceLiveTimer); voiceLiveTimer = null; }
}

function restoreVoiceInput() {
    const ta = document.getElementById('chatInput');
    if (!ta) return;
    ta.readOnly = false;
    ta.classList.remove('voice-live');
    ta.placeholder = '输入问题，回车发送...';
}

function stopVoiceInput() {
    if (!voiceRecorder || voiceRecorder.state !== 'recording') return;
    voiceStopping = true;
    try { voiceRecorder.stop(); } catch (e) {}
    if (voiceTimer) { clearInterval(voiceTimer); voiceTimer = null; }
    setVoiceBtnUI(false, '');
}

function cleanupVoiceStream() {
    if (voiceStream) {
        voiceStream.getTracks().forEach(t => { try { t.stop(); } catch (e) {} });
        voiceStream = null;
    }
}

// 停止后的定稿：先恢复输入框，再发一次全量转写覆盖草稿（更准的最终版）
async function finalizeVoice() {
    // 等最后一轮草稿转写跑完（最多 3 秒），避免两处同时写输入框
    const t0 = Date.now();
    while (voiceLiveBusy && Date.now() - t0 < 3000) await new Promise(r => setTimeout(r, 100));
    cleanupVoiceStream();
    restoreVoiceInput();
    if (!voiceChunks.length) { addSystemMessage('⚠️ 没有录到声音'); return; }
    addSystemMessage('🎤 正在定稿…');
    const fd = new FormData();
    fd.append('file', new Blob(voiceChunks, { type: 'audio/webm' }), 'voice.webm');
    try {
        const resp = await apiFetch('/api/voice/transcribe', { method: 'POST', body: fd });
        const d = await resp.json().catch(() => ({}));
        if (!resp.ok) { addSystemMessage(`⚠️ 语音识别失败：${d.detail || resp.status}`); return; }
        const text = (d.text || '').trim();
        if (!text) { addSystemMessage('⚠️ 未识别到内容，请靠近麦克风重试'); return; }
        const ta = document.getElementById('chatInput');
        ta.value = voicePrefixTxt ? voicePrefixTxt + ' ' + text : text;
        ta.focus({ preventScroll: true });
        addSystemMessage(`🎤 已填入输入框（${text.length} 字），可修改后回车发送`);
    } catch (err) {
        addSystemMessage(`⚠️ 语音识别失败：${err.message}`);
    }
}

// ===== 消息渲染 =====
// 历史消息里的图片 src：后端给的是 /api/chat/uploads/xxx 这种站内地址（需带令牌），
// 也可能是本地待发图的 data: URL（直接原样用）
function chatImgSrc(src) {
    const s = String(src || '');
    if (!s || s.startsWith('data:') || s.startsWith('blob:')) return s;
    if (/^https?:/i.test(s)) return s;
    const tk = getApiToken();
    return getApiUrl(s) + (tk ? `?token=${encodeURIComponent(tk)}` : '');
}

function addUserMessage(text, imgs, quote) {
    let html = `<div class="message user">`;
    if (quote && quote.trim()) html += `<div class="quote-bubble">📎 引用：${escHtml(quote)}</div>`;
    if (imgs && imgs.length) {
        const imgsHtml = imgs.map(src => `<img src="${src}" class="chat-img" onload="scrollChat()">`).join('');
        html += `<div class="msg-content">${imgsHtml}<br>${escHtml(text)}</div>`;
    } else {
        html += `<div class="msg-content">${escHtml(text)}</div>`;
    }
    html += `<div class="msg-time">${time()}</div></div>`;
    // F3：insertAdjacentHTML 仅追加新节点，避免整段 chatMessages 重解析（innerHTML +=）
    document.getElementById('chatMessages').insertAdjacentHTML('beforeend', html); scrollChat(true);
}
function addAssistantMessage(text) {
    const safe = escHtml(text);
    const withMath = renderMath(safe);
    document.getElementById('chatMessages').insertAdjacentHTML('beforeend',
        `<div class="message assistant"><div class="msg-content">${withMath}</div><div class="msg-time">${time()}</div></div>`);
    scrollChat(true);
}
function addSystemMessage(text) {
    document.getElementById('chatMessages').insertAdjacentHTML('beforeend',
        `<div class="message system"><div class="msg-content">${escHtml(text)}</div></div>`);
    scrollChat(true);
}

// ===== 工具调用提示（read 等）=====
// 模型调工具期间给用户一个可见反馈：正在读文件 / 已读完
const TOOL_HINTS = {
    read: '📄 读取文件',
    read_image: '📷 查看图片',        // 历史兼容（工具已改名 read）
    ask_user_question: '❓ 需要你确认',
    quiz_user: '📝 出题考你',
};
function showToolHint(msgEl, ev) {
    try {
        let box = msgEl.querySelector('.tool-hints');
        if (!box) {
            box = document.createElement('div');
            box.className = 'tool-hints';
            const content = msgEl.querySelector('.msg-content');
            if (content) msgEl.insertBefore(box, content); else msgEl.appendChild(box);
        }
        let row = box.querySelector(`[data-tool="${ev.name}"]`);
        if (!row) {
            row = document.createElement('div');
            row.className = 'tool-hint';
            row.dataset.tool = ev.name;
            box.appendChild(row);
        }
        const base = TOOL_HINTS[ev.name] || `🛠 调用 ${ev.name}`;
        const suffix = ev.status === 'running' ? '中…' : (ev.status === 'done' ? '完成 ✓' : '失败');
        row.textContent = base + suffix;
        row.classList.toggle('failed', ev.status === 'failed');
        row.classList.toggle('running', ev.status === 'running');
        scrollChat();
    } catch (e) { /* 提示失败不影响主流程 */ }
}

// ===== 交互工具（ask_user_question 要用户拍板 / quiz_user 出题考用户）=====
// 两个工具共用同一套卡片与提交机制，只有文案和配色不同（kind: 'ask' | 'quiz'）
// 提问文本遵守公式铁律，这里同样走 renderMath 渲染 $...$ / $$...$$
function askQuestionsHtml(questions) {
    return (questions || []).map(q => `
        <div class="ask-block">
            ${q.header ? `<div class="ask-header">${escHtml(q.header)}</div>` : ''}
            <div class="ask-question">${renderMath(escHtml(q.question || ''))}</div>
            ${(q.options || []).length ? `<div class="ask-options">${q.options.map(o => `
                <div class="ask-option readonly"><span class="ask-option-main">
                    <span class="ask-option-label">${renderMath(escHtml(o.label || ''))}</span>
                    ${o.description ? `<span class="ask-option-desc">${renderMath(escHtml(o.description))}</span>` : ''}
                </span></div>`).join('')}</div>` : ''}
        </div>`).join('');
}

// ===== ask/quiz 卡片的「图片作答」=====
// 整张卡片共用一组图（学生拍一张纸答多道题时最省事，与模型约定一致）。
// 图片随提交一起作为 multipart 字段发出，后端内联进 tool 结果，模型本轮直接看到。
function addAskImages(card, files) {
    if (!files || !files.length) return;
    card._askImages = card._askImages || [];
    for (const file of files) {
        if (!file.type || !file.type.startsWith('image/')) continue;
        if (file.size > 10 * 1024 * 1024) { addSystemMessage('⚠️ 单张图片最大 10MB'); continue; }
        if (card._askImages.length >= MAX_IMAGES) {
            addSystemMessage(`⚠️ 一次最多 ${MAX_IMAGES} 张图片`); break;
        }
        const reader = new FileReader();
        reader.onload = ev => {
            card._askImages.push({ dataUrl: ev.target.result, file });
            renderAskImages(card);
        };
        reader.readAsDataURL(file);
    }
}

function renderAskImages(card) {
    const list = card.querySelector('.ask-img-list');
    if (!list) return;
    const imgs = card._askImages || [];
    list.innerHTML = imgs.map((img, i) => `
        <span class="ask-img-thumb">
            <img src="${img.dataUrl}" alt="作答图片${i + 1}">
            <button type="button" class="ask-img-del" data-i="${i}" aria-label="移除图片${i + 1}">✕</button>
        </span>`).join('');
    list.querySelectorAll('.ask-img-del').forEach(btn =>
        btn.addEventListener('click', () => {
            card._askImages.splice(Number(btn.dataset.i), 1);
            renderAskImages(card);
        }));
}

function renderAskCard(msgEl, ask, kind) {
    const questions = ask.questions || [];
    if (!questions.length) return;
    const isQuiz = kind === 'quiz';   // 出题考用户：按钮/提示/配色走练习版
    const group = 'askg_' + Math.random().toString(36).slice(2, 8);
    const card = document.createElement('div');
    card.className = 'ask-card' + (isQuiz ? ' quiz-card' : '');
    card.innerHTML = (isQuiz ? '<div class="ask-kind">📝 练习</div>' : '') + questions.map((q, qi) => {
        const options = q.options || [];
        const inputType = q.multi_select ? 'checkbox' : 'radio';
        return `
        <div class="ask-block" data-idx="${qi}">
            ${q.header ? `<div class="ask-header">${escHtml(q.header)}</div>` : ''}
            <div class="ask-question">${renderMath(escHtml(q.question || ''))}</div>
            ${options.length ? `<div class="ask-options">${options.map(o => `
                <label class="ask-option">
                    <input type="${inputType}" name="${group}_${qi}" value="${escAttr(o.label || '')}">
                    <span class="ask-option-main">
                        <span class="ask-option-label">${renderMath(escHtml(o.label || ''))}</span>
                        ${o.description ? `<span class="ask-option-desc">${renderMath(escHtml(o.description))}</span>` : ''}
                    </span>
                </label>`).join('')}</div>` : ''}
            <input type="text" class="ask-custom" maxlength="500"
                   placeholder="${options.length ? '也可以自己写答案…' : '输入你的回答…'}">
        </div>`;
    }).join('') + `
        <div class="ask-attach">
            <button type="button" class="ask-img-btn">${isQuiz ? '📷 上传解答照片' : '📷 上传图片回答'}</button>
            <span class="ask-img-hint">可选，最多 ${MAX_IMAGES} 张（整张卡片共用）</span>
            <input type="file" class="ask-img-input" accept="image/*" multiple hidden>
            <div class="ask-img-list"></div>
        </div>
        <div class="ask-foot">
            <button type="button" class="ask-submit">${isQuiz ? '提交答案' : '提交'}</button>
            <span class="ask-hint">${isQuiz ? '提交后 AI 批改并接着讲' : '回答后 AI 接着往下讲'}</span>
        </div>`;
    card.querySelector('.ask-submit').addEventListener('click', () => submitAskCard(card, ask));
    card._askImages = [];
    const imgInput = card.querySelector('.ask-img-input');
    card.querySelector('.ask-img-btn').addEventListener('click', () => imgInput.click());
    imgInput.addEventListener('change', () => { addAskImages(card, imgInput.files); imgInput.value = ''; });
    msgEl.appendChild(card);
    abandonLiveAskCard();  // 理论上同时只会有一张待答卡片；出现新的就把上一张置为未作答
    liveAskCard = card;    // 记录当前活动卡片，用户跳过时由 abandonLiveAskCard 置为未作答
    scrollChat();
}

function submitAskCard(card, ask) {
    if (state.isStreaming) return;   // 正在生成时不允许提交
    const answers = [];
    (ask.questions || []).forEach((q, qi) => {
        const block = card.querySelector(`.ask-block[data-idx="${qi}"]`);
        if (!block) return;
        const selected = [...block.querySelectorAll('input[type=radio]:checked, input[type=checkbox]:checked')]
            .map(i => i.value);
        const custom = (block.querySelector('.ask-custom').value || '').trim();
        if (!selected.length && !custom) return;
        const item = { id: q.id, selected: selected };
        if (custom) item.custom = custom;
        answers.push(item);
    });
    if (!answers.length && !(card._askImages || []).length) {
        addSystemMessage('⚠️ 请先选择、填写，或上传解答图片'); return;
    }
    const askImgs = (card._askImages || []).slice();
    // 卡片转为已提交态：禁用输入 + 回显用户的选择（历史里同样能看到）
    card.classList.add('submitted');
    card.querySelectorAll('input, button').forEach(el => { el.disabled = true; });
    const imgHint = card.querySelector('.ask-img-hint');
    if (imgHint) imgHint.textContent = '';
    const echo = document.createElement('div');
    echo.className = 'ask-echo';
    const echoText = answers.map(a => {
        const parts = [...(a.selected || [])];
        if (a.custom) parts.push(a.custom);
        return parts.join('、');
    }).join('；');
    echo.textContent = '✅ ' + [echoText, askImgs.length ? `📷 ${askImgs.length} 张作答图片` : '']
        .filter(Boolean).join('；');
    card.appendChild(echo);
    if (liveAskCard === card) liveAskCard = null;  // 已作答，不再是待答卡片
    pendingAsk = { tool_call_id: ask.tool_call_id, answers: answers, images: askImgs };
    sendMessage();
}
// 思维链块：点击标题行展开/折叠（流式与新历史消息共用）
function toggleReasoning(headerEl) {
    const block = headerEl.closest('.reasoning-block');
    if (!block) return;
    const collapsed = block.classList.toggle('collapsed');
    headerEl.querySelector('.reasoning-arrow').textContent = collapsed ? '▶' : '▼';
    // 展开时不再自动滚到思考内容末尾，避免打断用户从头阅读
}
let typingCount=0;
function showTyping() { const id=++typingCount;
    document.getElementById('chatMessages').insertAdjacentHTML('beforeend', `<div class="message assistant" id="typing-${id}"><div class="msg-content"><div class="typing-indicator"><span></span><span></span><span></span></div></div></div>`);
    scrollChat(true); return id; }
function removeTyping(id) { const e=document.getElementById(`typing-${id}`); if(e) e.remove(); }
function clearChat() { document.getElementById('chatMessages').innerHTML=''; liveAskCard = null; }
// ===== 聊天滚动策略 =====
// 流式输出（思考链 / 正文）期间不做任何自动滚动，用户可自由上翻阅读，不被打断；
// 只有「发送消息 / 切换会话 / 加载历史」等主动操作才强制滚到底。
// 非强制调用（图片异步加载、流式收尾等）仅在用户本就在底部附近时才跟随。
const CHAT_STICK_THRESHOLD_PX = 80;   // 距底部多少 px 内视为"贴底"
function isChatNearBottom(box) {
    if (!box) return true;
    return box.scrollHeight - box.scrollTop - box.clientHeight <= CHAT_STICK_THRESHOLD_PX;
}
function updateScrollToBottomBtn() {
    const btn = document.getElementById('scrollToBottomBtn');
    if (!btn) return;
    const box = document.getElementById('chatMessages');
    btn.classList.toggle('show', !!box && !isChatNearBottom(box));
}
function initChatScroll() {
    const box = document.getElementById('chatMessages');
    if (!box || box.dataset.scrollBound) return;
    box.dataset.scrollBound = '1';
    box.addEventListener('scroll', updateScrollToBottomBtn, { passive: true });
    updateScrollToBottomBtn();
}
function scrollChat(force = false) {
    const box = document.getElementById('chatMessages');
    if (!box || !box.scrollHeight) return;
    if (!force && !isChatNearBottom(box)) return;   // 用户已上翻：绝不拽回
    const toBottom = () => { box.scrollTop = box.scrollHeight; updateScrollToBottomBtn(); };
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            requestAnimationFrame(toBottom);
        });
    });
    // 兜底：100ms 后再滚一次（防 KaTeX / 图片异步加载导致布局延迟）
    setTimeout(toBottom, 100);
}
// 「↓ 回到底部」：强制滚到底并恢复跟随，同时隐藏按钮
function jumpChatToBottom() {
    const btn = document.getElementById('scrollToBottomBtn');
    if (btn) btn.classList.remove('show');
    scrollChat(true);
}

// ===== 会话管理（新建/删除/重命名/选择/按科目分组） =====
function convLsKey() { return `kaoyan_conv_${state.subject}_${state.mode}`; }
function saveActiveConv() { try { localStorage.setItem(convLsKey(), state.conversationId || ''); } catch(e){} }
function loadActiveConv() { try { return localStorage.getItem(convLsKey()) || ''; } catch(e){ return ''; } }

async function loadConversations() {
    try {
        const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}`);
        if (!resp.ok) return;
        const groups = await resp.json();
        const box = document.getElementById('conversationList');
        const modeNames = {A:'💬 即时问答', B:'🎯 引导输出', C:'📝 课后问答'};
        let html = '';
        for (const m of ['A','B','C']) {
            const list = groups[m] || [];
            if (!list.length) continue;
            html += `<div class="conv-group"><div class="conv-group-title">${modeNames[m]}</div>`;
            for (const c of list) {
                const active = c.conversation_id === state.conversationId ? ' active' : '';
                html += `<div class="conv-item${active}" onclick="selectConversation('${c.conversation_id}')" title="${escHtml(c.name)}">`
                      + `<span class="conv-name">${escHtml(c.name)}</span>`
                      + `${c.model ? `<span class="conv-model-tag" title="会话模型：${escHtml(c.model)}">${escHtml(c.model)}</span>` : ''}`
                      + `<span class="conv-count">${c.message_count}</span>`
                      + `<span class="conv-ops" onclick="event.stopPropagation()">`
                      + `<button class="conv-op" onclick="renameConversation('${c.conversation_id}')" title="重命名">✏️</button>`
                      + `<button class="conv-op" onclick="deleteConversation('${c.conversation_id}')" title="删除">🗑️</button>`
                      + `</span></div>`;
            }
            html += '</div>';
        }
        if (!html) html = '<div class="conv-empty">（当前科目还没有对话，点上方「＋新对话」开始）</div>';
        box.innerHTML = html;
    } catch(e) {}
}

const convSbKey = 'kaoyan_conv_sidebar';       // '1' 显示 / '0' 隐藏
const convSbWKey = 'kaoyan_conv_sidebar_w';
function setConvSidebarVisible(visible) {
    document.getElementById('conversationSidebar').style.display = visible ? '' : 'none';
    try { localStorage.setItem(convSbKey, visible ? '1' : '0'); } catch(e){}
}
function toggleConversationList() {
    const sb = document.getElementById('conversationSidebar');
    const visible = sb.style.display !== 'none';
    setConvSidebarVisible(!visible);
    if (!visible) loadConversations();
}
function collapseConversationSidebar() { setConvSidebarVisible(false); }
function initConvResizer() {
    const r = document.getElementById('convSidebarResizer');
    const sb = document.getElementById('conversationSidebar');
    if (!r || !sb) return;
    // 与三栏分隔条一致走 Pointer Events：平板上手指也能拖“我的对话”宽度
    let dragging = false, pointerId = null, startX = 0, startW = 0;
    r.addEventListener('pointerdown', e => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        dragging = true; pointerId = e.pointerId; startX = e.clientX; startW = sb.offsetWidth;
        document.body.classList.add('col-resizing'); document.body.style.userSelect = 'none';
        r.classList.add('dragging');
        try { r.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault();
    });
    document.addEventListener('pointermove', e => {
        if (!dragging) return;
        if (pointerId !== null && e.pointerId !== pointerId) return;
        let w = startW + (e.clientX - startX);
        w = Math.max(140, Math.min(380, w));
        sb.style.width = w + 'px';
        try { localStorage.setItem(convSbWKey, w); } catch(x){}
    });
    function endDrag(e) {
        if (!dragging) return;
        if (pointerId !== null && e.pointerId !== undefined && e.pointerId !== pointerId) return;
        dragging = false; pointerId = null;
        document.body.classList.remove('col-resizing'); document.body.style.userSelect = '';
        r.classList.remove('dragging');
    }
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);
}
function restoreConvSidebarState() {
    try {
        if (localStorage.getItem(convSbKey) === '0') setConvSidebarVisible(false);
        const w = localStorage.getItem(convSbWKey);
        if (w) document.getElementById('conversationSidebar').style.width = w + 'px';
    } catch(e){}
}

async function newConversation() {
    try {
        const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}&mode=${state.mode}${(window.FSState && window.FSState.currentFileId()) ? '&file_id=' + window.FSState.currentFileId() : ''}`, {method:'POST'});
        if (!resp.ok) throw new Error('创建失败');
        const d = await resp.json();
        state.conversationId = d.conversation_id;
        state.convModel = '';
        state.convEffort = '';
        syncChatModelSelect();
        syncChatEffortSelect();
        saveActiveConv();
        clearChat();
        document.querySelectorAll('.b-video-cb').forEach(cb => cb.checked=false);
        addSystemMessage('🆕 已开新对话');
        loadConversations();
    } catch(e){ addSystemMessage('⚠️ 新对话失败: '+e.message); }
}

async function selectConversation(convId) {
    state.conversationId = convId;
    saveActiveConv();
    clearChat();
    try {
        const resp = await apiFetch(`/api/chat/${convId}/history`);
        if (!resp.ok) throw new Error('加载失败');
        const d = await resp.json();
        state.convModel = d.model || '';   // 会话级模型（历史会话恢复时下拉对齐）
        state.convEffort = d.effort || ''; // 会话级思考强度（历史会话恢复时下拉对齐）
        syncChatModelSelect();
        syncChatEffortSelect();
        // 一次性拼出全部 HTML 再写入 DOM，避免逐步 append 触发滚动锚定
        const box = document.getElementById('chatMessages');
        let html = '';
        for (const msg of d.messages) {
            const qBlock = (msg.quote && msg.quote.trim())
                ? `<div class="quote-bubble">📎 引用：${escHtml(msg.quote)}</div>` : '';
            if (msg.role === 'user') {
                const imgsHtml = (msg.images && msg.images.length)
                    ? msg.images.map(src => `<img src="${chatImgSrc(src)}" class="chat-img">`).join('') : '';
                html += `<div class="message user">${qBlock}<div class="msg-content">${imgsHtml}${escHtml(msg.content)}</div><div class="msg-time">${time()}</div></div>`;
            } else if (msg.role === 'tool') {
                // ask/quiz 工具的回答：历史里回显用户当时的作答（文字、图片或两者都有）
                const txt = (msg.answers || []).map(a => {
                    const parts = [...(a.selected || [])];
                    if (a.custom) parts.push(a.custom);
                    return parts.join('、');
                }).filter(Boolean).join('；');
                const ansImgs = (msg.images || []).filter(Boolean)
                    .map(src => `<img src="${chatImgSrc(src)}" class="chat-img">`).join('');
                if (txt || ansImgs) {
                    const head = txt ? `✅ ${escHtml(txt)}` : '✅ 图片作答';
                    html += `<div class="message user ask-answer"><div class="msg-content">${ansImgs}${head}</div><div class="msg-time">${time()}</div></div>`;
                }
            } else if (msg.role === 'assistant') {
                const hasText = (msg.content || '').trim();
                const hasReason = (msg.reasoning || '').trim();
                const hasAsk = !!(msg.ask_questions && msg.ask_questions.length);
                // 纯工具调用轮（模型只调了 read 还没说话）：不渲染空气泡
                if (!hasText && !hasReason && !hasAsk) continue;
                // 历史消息带思维链时显示折叠的思考块（点击可展开）
                const rBlock = hasReason
                    ? `<div class="reasoning-block collapsed"><div class="reasoning-header" onclick="toggleReasoning(this)"><span class="reasoning-arrow">▶</span><span class="reasoning-title">🧠 已深度思考（点击展开）</span></div><div class="reasoning-body">${escHtml(msg.reasoning)}</div></div>`
                    : '';
                // 当时调用过 ask/quiz 工具：把问题以只读卡片形式一并还原；
                // 「已作答」与「当时未作答、直接继续」用不同样式/文案区分（老数据无 ask_answered 字段，按已作答处理）
                const isQuizCard = msg.ask_kind === 'quiz';
                const quizTag = isQuizCard ? '<div class="ask-kind">📝 练习</div>' : '';
                const askAnswered = msg.ask_answered !== false;
                const askImgNote = msg.ask_images
                    ? `<div class="ask-foot"><span class="ask-hint">📷 含 ${msg.ask_images} 张图片作答</span></div>`
                    : '';
                const askBlock = hasAsk
                    ? (askAnswered
                        ? `<div class="ask-card submitted readonly${isQuizCard ? ' quiz-card' : ''}">`
                          + quizTag + askQuestionsHtml(msg.ask_questions) + askImgNote + '</div>'
                        : `<div class="ask-card unanswered readonly${isQuizCard ? ' quiz-card' : ''}">`
                          + quizTag + askQuestionsHtml(msg.ask_questions)
                          + '<div class="ask-foot"><span class="ask-hint">未作答，已直接继续</span></div></div>')
                    : '';
                html += `<div class="message assistant">${rBlock}<div class="msg-content">${renderMath(escHtml(msg.content))}</div>${askBlock}<div class="msg-time">${time()}</div></div>`;
            }
        }
        if (!d.messages.length) html += '<div class="message system"><div class="msg-content">（空对话）</div></div>';
        box.innerHTML = html;
        // 布局完成后再滚到底部（scrollChat 内部有三层 rAF + 100ms 兜底）
        scrollChat(true);
        loadConversations();
    } catch(e){ addSystemMessage('️ '+e.message); }
}

async function renameConversation(convId) {
    openDialog({
        title: '✏️ 重命名对话',
        bodyHTML: '<label>输入新名称</label><input type="text" id="dlgConvName" maxlength="255" placeholder="给这个对话起个名字">',
        okText: '保存',
        onOk: () => {
            const name = document.getElementById('dlgConvName').value.trim();
            if (!name) { addSystemMessage('⚠️ 名称不能为空'); return; }
            apiFetch(`/api/chat/conversations/${convId}?name=${encodeURIComponent(name)}`, {method:'PATCH'})
                .then(r => {
                    closeDialog();
                    if (r.ok) loadConversations();
                    else r.json().then(d => addSystemMessage(`⚠️ ${d.detail || '重命名失败'}`));
                });
        },
    });
}

async function deleteConversation(convId) {
    openDialog({
        title: '🗑️ 删除对话',
        bodyHTML: '<div class="dialog-warn">将删除这个对话及其全部消息，<b>不可恢复</b>。确定继续吗？</div>',
        okText: '删除',
        onOk: () => {
            apiFetch(`/api/chat/conversations/${convId}`, {method:'DELETE'}).then(r => {
                closeDialog();
                if (r.ok) {
                    if (state.conversationId === convId) newConversation();
                    else loadConversations();
                }
            });
        },
    });
}

async function restoreActiveConversation() {
    const convId = loadActiveConv();
    if (convId) await selectConversation(convId);
}

// ===== 引用（选中 AI 回答某段 → 悬浮按钮 → 引用条 → 发送特别说明） =====
let quoteState = null;   // {text: '...'}
let quoteFloater = null; // 悬浮"引用"按钮元素

function quoteFloaterEl() {
    if (quoteFloater) return quoteFloater;
    quoteFloater = document.createElement('div');
    quoteFloater.id = 'quoteFloater';
    quoteFloater.className = 'quote-floater';
    quoteFloater.style.display = 'none';
    quoteFloater.innerHTML = '<button type="button" onclick="applyQuote()">💬 引用</button>';
    document.body.appendChild(quoteFloater);
    return quoteFloater;
}

// 鼠标抬起：若在 AI 回答块内选中了文字，显示悬浮"引用"按钮
document.addEventListener('mouseup', (e) => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) { hideQuoteFloater(); return; }
    const text = (sel.toString() || '').trim();
    if (!text) { hideQuoteFloater(); return; }
    // 选区锚点需落在 AI 回答内容里（reasoning 思考块不参与）
    let node = sel.anchorNode;
    const inAssistant = node && node.nodeType === 3 ? node.parentElement : node;
    const msgEl = inAssistant ? inAssistant.closest('.message.assistant') : null;
    if (!msgEl || !msgEl.querySelector('.msg-content') || !msgEl.querySelector('.msg-content').contains(inAssistant)) {
        hideQuoteFloater(); return;
    }
    try {
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        if (!rect || !rect.width && !rect.height) { hideQuoteFloater(); return; }
        const f = quoteFloaterEl();
        f.style.display = '';
        let left = rect.right + 8;
        let top = rect.bottom + 8;
        if (left + 90 > window.innerWidth) left = rect.left - 100;
        if (top + 34 > window.innerHeight) top = rect.top - 40;
        f.style.left = left + 'px';
        f.style.top = top + 'px';
        f.dataset.text = text;
    } catch (err) { hideQuoteFloater(); }
});
document.addEventListener('mousedown', (e) => {
    if (quoteFloater && !quoteFloater.contains(e.target)) hideQuoteFloater();
});

function hideQuoteFloater() {
    if (quoteFloater) quoteFloater.style.display = 'none';
}

function applyQuote() {
    const text = quoteFloater ? (quoteFloater.dataset.text || '') : '';
    if (!text) return;
    quoteState = { text: text.slice(0, 500) };
    document.getElementById('quoteBar').style.display = '';
    document.getElementById('quoteBarText').textContent =
        `引用了回答：「${quoteState.text.length > 80 ? quoteState.text.slice(0, 80) + '…' : quoteState.text}」`;
    hideQuoteFloater();
    // 清理选区，聚焦输入框等待补充说明
    try { window.getSelection().removeAllRanges(); } catch (e) {}
    document.getElementById('chatInput').focus();
}

function clearQuote() {
    quoteState = null;
    const bar = document.getElementById('quoteBar');
    if (bar) bar.style.display = 'none';
}

// ===== 会话级模型一键切换（当前 API Key 下可用模型列表，仅本会话生效） =====
let chatModels = [];        // 可用模型列表缓存
function chatModelSel() { return document.getElementById('chatModelSelect'); }

// F5：模型列表短 TTL 缓存（避免同一会话内反复请求外网 /models）
const chatModelsTTL = 60 * 1000;
let chatModelsCache = { t: 0, models: [], current: '' };
async function loadChatModelList(force) {
    try {
        // 设置保存 / 明确要求时 force 强刷；否则走 60s 缓存
        const now = Date.now();
        if (!force && now - chatModelsCache.t < chatModelsTTL) {
            chatModels = chatModelsCache.models;
            state.globalModel = chatModelsCache.current;
            fillChatModelSelect();
            syncChatModelSelect();
            return;
        }
        const resp = await apiFetch('/api/config/chat-models', { method: 'POST' });
        const d = await resp.json().catch(() => ({}));
        chatModels = d.models || [];
        state.globalModel = d.current || '';
        chatModelsCache = { t: now, models: chatModels, current: state.globalModel };
        fillChatModelSelect();
        syncChatModelSelect();
    } catch (e) {}
}

function fillChatModelSelect() {
    const sel = chatModelSel();
    if (!sel) return;
    sel.innerHTML = '';
    if (chatModels.length) {
        chatModels.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; sel.appendChild(o); });
    } else {
        const o = document.createElement('option');
        o.value = state.globalModel;
        o.textContent = state.globalModel || '（无法获取模型列表）';
        sel.appendChild(o);
        o.disabled = !state.globalModel;
    }
}

// 把下拉值对齐到当前会话的模型（convModel 优先，其次全局）
function syncChatModelSelect() {
    const sel = chatModelSel();
    if (!sel) return;
    const active = state.convModel || state.globalModel || '';
    const options = [...sel.options];
    const hit = options.find(o => o.value === active);
    if (hit) sel.value = hit.value;
    else if (active) {
        const o = document.createElement('option');
        o.value = active; o.textContent = active;
        sel.appendChild(o); sel.value = active;
    } else if (options.length) {
        sel.selectedIndex = 0;
    }
}

async function onChatModelChange(sel) {
    const model = (sel.value || '').trim();
    if (!model || model === state.convModel) return;
    // 还没有会话时先自动建一个（当前科目/模式），保证模型绑定到会话
    if (!state.conversationId) {
        try {
            const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}&mode=${state.mode}`, { method: 'POST' });
            if (!resp.ok) throw new Error('创建会话失败');
            const d = await resp.json();
            state.conversationId = d.conversation_id;
            saveActiveConv();
            loadConversations();
        } catch (e) {
            addSystemMessage(`⚠️ ${e.message}`);
            syncChatModelSelect();
            return;
        }
    }
    try {
        const resp = await apiFetch(`/api/chat/conversations/${state.conversationId}/model?model=${encodeURIComponent(model)}`, { method: 'POST' });
        if (!resp.ok) throw new Error('保存失败');
        state.convModel = model;
        addSystemMessage(`✅ 本会话已切换模型：${model}`);
        loadConversations();
    } catch (e) {
        addSystemMessage(`⚠️ 模型切换失败：${e.message}`);
        syncChatModelSelect();
    }
}

// ===== 思考强度（会话级，与模型选择逻辑一致）=====
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'];
const EFFORT_LABELS = { low: '低·min', medium: '中·m', high: '高·h', xhigh: '非常高·xh', max: '极限·max' };
const DEFAULT_EFFORT = 'high';   // 会话未设时用全局默认（与后端 DEFAULT_EFFORT 对应）
function chatEffortSel() { return document.getElementById('chatEffortSelect'); }

function initChatEffortSelect() {
    const sel = chatEffortSel();
    if (!sel) return;
    sel.innerHTML = '';
    EFFORT_LEVELS.forEach(lv => {
        const o = document.createElement('option');
        o.value = lv;
        o.textContent = (EFFORT_LABELS[lv] || lv);
        sel.appendChild(o);
    });
    syncChatEffortSelect();
}

// 把下拉值对齐到当前会话的思考强度（convEffort 优先，其次默认 high）
function syncChatEffortSelect() {
    const sel = chatEffortSel();
    if (!sel) return;
    const active = state.convEffort || DEFAULT_EFFORT;
    const hit = [...sel.options].find(o => o.value === active);
    if (hit) sel.value = hit.value;
    else {
        // 兜底：未知值追加一项，防止下拉显示空白
        const o = document.createElement('option');
        o.value = active; o.textContent = active;
        sel.appendChild(o); sel.value = active;
    }
}

async function onChatEffortChange(sel) {
    const effort = (sel.value || '').trim();
    if (!effort || effort === state.convEffort) return;
    // 还没有会话时先自动建一个（当前科目/模式），保证强度绑定到会话
    if (!state.conversationId) {
        try {
            const resp = await apiFetch(`/api/chat/conversations?subject=${state.subject}&mode=${state.mode}`, { method: 'POST' });
            if (!resp.ok) throw new Error('创建会话失败');
            const d = await resp.json();
            state.conversationId = d.conversation_id;
            saveActiveConv();
            loadConversations();
        } catch (e) {
            addSystemMessage(`⚠️ ${e.message}`);
            syncChatEffortSelect();
            return;
        }
    }
    try {
        const resp = await apiFetch(`/api/chat/conversations/${state.conversationId}/effort?effort=${encodeURIComponent(effort)}`, { method: 'POST' });
        if (!resp.ok) throw new Error('保存失败');
        state.convEffort = effort;
        addSystemMessage(`✅ 本会话已切换思考强度：${EFFORT_LABELS[effort] || effort}`);
        loadConversations();
    } catch (e) {
        addSystemMessage(`⚠️ 思考强度切换失败：${e.message}`);
        syncChatEffortSelect();
    }
}

// ===== 通用操作弹层（替换原生 prompt/confirm，输入框回车确认、Esc 取消）=====
let dialogOkHandler = null;

function openDialog(opts) {
    const modal = document.getElementById('dialogModal');
    if (!modal) return;
    document.getElementById('dialogTitle').textContent = opts.title || '操作';
    document.getElementById('dialogBody').innerHTML = opts.bodyHTML || '';
    document.getElementById('dialogOkBtn').textContent = opts.okText || '确定';
    dialogOkHandler = opts.onOk || null;
    modal.style.display = 'flex';
    const first = modal.querySelector('input, select, textarea');
    if (first) { setTimeout(() => { try { first.focus(); } catch (e) {} }, 30); }
}

function closeDialog(e) {
    if (e && e.target !== e.currentTarget) return;  // 点背景才触发；按钮走 onclick
    const modal = document.getElementById('dialogModal');
    if (modal) modal.style.display = 'none';
    dialogOkHandler = null;
    // 关闭对话框时若处于批量删除模式则退出（取消/ESC/点背景均生效），无副作用执行
    exitDelMode();
}

function dialogOk() {
    if (dialogOkHandler) dialogOkHandler();
}

document.getElementById('dialogOkBtn').addEventListener('click', dialogOk);
document.getElementById('dialogModal').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeDialog(); }
    else if (e.key === 'Enter' && e.target && e.target.tagName !== 'TEXTAREA' && e.target.id !== 'dialogOkBtn') {
        e.preventDefault();
        dialogOk();
    }
});

// ===== 设置页一键填充常用服务 =====
const API_PRESETS = {
    main:   { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-flash' },
    asr:    { base_url: 'https://dashscope.aliyuncs.com', model: 'qwen3-asr-flash' },
};

function applyPreset(section) {
    const p = API_PRESETS[section];
    const ids = {
        main:   { base: 'setMainBaseUrl', model: 'setMainModel' },
        asr:    { base: 'setAsrBaseUrl', model: 'setAsrModel' },
    };
    const m = ids[section];
    if (!p || !m) return;
    const elBase = document.getElementById(m.base);
    if (elBase) elBase.value = p.base_url;
    const elModel = document.getElementById(m.model);
    if (elModel) elModel.value = p.model;
    if (elBase) {
        elBase.style.borderColor = 'var(--color-primary)';
        setTimeout(() => { elBase.style.borderColor = ''; }, 1000);
    }
}

// ⚡ 模式A 精简上下文开关（设置页勾选，本地记忆）
function isCompactASubjectCtx() {
    try { return localStorage.getItem('kaoyan_compact_ctx') === '1'; } catch (e) { return false; }
}

// ===== 设置（模型 API 配置，保存即生效）=====
function showSettings() {
    document.getElementById('settingsModal').style.display = 'flex';
    const tokenEl = document.getElementById('setToken');
    if (tokenEl) tokenEl.value = getApiToken();
    // 清空 Key 输入框（留空 = 保留已有 Key），加载当前配置填充
    ['setMainKey', 'setAsrKey', 'setAsrKeyZhipu', 'setAsrTencentSecretKey', 'setAsrKeySiliconflow'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    loadModelConfig();
    loadTiers();
}
function closeSettings(e) {
    if (e && e.target !== e.currentTarget) return;
    document.getElementById('settingsModal').style.display = 'none';
}
async function loadModelConfig() {
    try {
        const resp = await apiFetch('/api/config/model');
        if (!resp.ok) return;
        const c = await resp.json();
        const fill = (id, v) => {
            const el = document.getElementById(id);
            if (el && v != null) el.value = v;
        };
        const ph = (id, hasKey) => {
            const el = document.getElementById(id);
            if (el && !hasKey) el.placeholder = '尚未配置 Key';
        };
        fill('setMainName', c.main && c.main.name);
        fill('setMainBaseUrl', c.main && c.main.base_url);
        fill('setMainModel', c.main && c.main.model);
        const compactA = document.getElementById('setCompactA');
        if (compactA) compactA.checked = isCompactASubjectCtx();
        ph('setMainKey', c.main && c.main.has_key);
        if (c.main && !c.main.has_key) document.getElementById('setMainKey').placeholder = '尚未配置 Key';

        fill('setAsrName', c.asr && c.asr.name);
        fill('setAsrBaseUrl', c.asr && c.asr.base_url);
        fill('setAsrModel', c.asr && c.asr.model);
        const ap = document.getElementById('setAsrProvider');
        if (ap) ap.value = (c.asr && c.asr.provider) || 'auto';
        const asz = document.getElementById('setAsrSize');
        if (asz) asz.value = (c.asr && c.asr.size) || 'small';
        const asv = document.getElementById('setAsrVoiceProvider');
        if (asv) asv.value = (c.asr && c.asr.voice_provider) || 'auto';
        if (c.asr && !c.asr.has_key) document.getElementById('setAsrKey').placeholder = '尚未配置 Key';
        // 各云引擎 Key 状态提示（留空未配置 = 不参与自动降级链）
        const eng = (c.asr && c.asr.engines) || {};
        const engHint = {
            setAsrKeyZhipu: eng.zhipu,
            setAsrTencentAppId: eng.tencent,
            setAsrTencentSecretId: eng.tencent,
            setAsrTencentSecretKey: eng.tencent,
            setAsrKeySiliconflow: eng.siliconflow,
        };
        Object.keys(engHint).forEach(id => {
            const el = document.getElementById(id);
            if (el) el.placeholder = engHint[id] ? '已配置（留空 = 保持）' : '尚未配置（不参与自动）';
        });
    } catch (e) {}

}
// 从 OpenAI 兼容端点拉取可用模型列表，填充对应 datalist（不落库，用表单当前值）
async function fetchModels(section) {
    const ids = section === 'main'
        ? { base: 'setMainBaseUrl', key: 'setMainKey', model: 'setMainModel', list: 'mainModelList' }
        : { base: 'setAsrBaseUrl', key: 'setAsrKey', model: 'setAsrModel', list: 'asrModelList' };
    const val = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const baseUrl = val(ids.base);
    if (!baseUrl) { alert('请先填写 API 地址'); return; }
    const btn = document.querySelector(`#settingsModal .form-group:has(#${ids.model}) .btn-sm`);
    if (btn) btn.textContent = '⏳ 获取中...';
    try {
        const resp = await apiFetch('/api/config/list-models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ base_url: baseUrl, api_key: val(ids.key) }),
        });
        const d = await resp.json().catch(() => ({}));
        const dl = document.getElementById(ids.list);
        if (dl) dl.innerHTML = '';
        const models = d.models || [];
        if (models.length) {
            models.forEach(m => {
                const o = document.createElement('option');
                o.value = m;
                if (dl) dl.appendChild(o);
            });
            alert(`✅ 获取到 ${models.length} 个模型，点击模型输入框可从列表选择（也可直接手输）`);
        } else {
            alert(`⚠️ 未能获取模型列表${d.error ? `：${d.error}` : ''}\n端点可能不支持 /models 接口，可手动输入模型名`);
        }
    } catch (err) {
        alert(`❌ 获取失败：${err.message}`);
    } finally {
        if (btn) btn.textContent = '🔍 获取模型';
    }
}


// ===== 提供商分组头：名字 + 配置摘要（加名字要能管理：名字输入实时更新头部） =====
function refreshProvHead(section) {
    const idMap = { main: ['setMainName', 'pgNameMain', 'pgSumMain'],
                    asr: ['setAsrName', 'pgNameAsr', 'pgSumAsr'] };
    const [nameId, nameElId, sumElId] = idMap[section] || [];
    const nb = document.getElementById(nameElId);
    const ns = document.getElementById(sumElId);
    if (!nb || !ns) return;
    const input = document.getElementById(nameId);
    const name = (input && input.value.trim()) || '未命名';
    nb.textContent = name;
    // 摘要：地址 + 模型 + Key 状态（读已填充的输入框）
    const baseId = section === 'main' ? 'setMainBaseUrl' : 'setAsrBaseUrl';
    const modelId = section === 'main' ? 'setMainModel' : 'setAsrModel';
    const keyId = section === 'main' ? 'setMainKey' : 'setAsrKey';
    const g = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const parts = [g(baseId), g(modelId)];
    const keyEl = document.getElementById(keyId);
    // 有输入值，或留空但 placeholder 不是"尚未配置"（= 已存 Key）→ 视为有 Key
    const hasKey = keyEl ? !!(keyEl.value || !((keyEl.placeholder || '').includes('尚未配置'))) : false;
    ns.textContent = [parts.filter(Boolean).join(' ・ '), keyEl ? (hasKey ? 'Key ✔' : '无 Key') : ''].filter(Boolean).join(' ・ ') || '（未配置）';
}
// name 输入即改即存预览（保存走 saveSettings）
['main', 'asr'].forEach(s => {
    const id = { main: 'setMainName', asr: 'setAsrName' }[s];
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => refreshProvHead(s));
});


// HTML 属性安全转义（渲染进 attribute value 用）
function escAttr(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/'/g, '&#39;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ===== 分层提供商条目库（每层可多个提供商；模型列表获取即录、勾选启停） =====
const TIER_ORDER = ['text', 'asr'];
let tiersState = { text: [], asr: [] };

async function loadTiers() {
    try {
        const resp = await apiFetch('/api/config/tiers');
        const d = await resp.json().catch(() => ({}));
        tiersState = (d.tiers && d.tiers.text != null) ? d.tiers : { text: [], asr: [] };
    } catch (e) { tiersState = { text: [], asr: [] }; }
    TIER_ORDER.forEach(renderTier);
}

function tierBoxId(tier) { return 'tierItems' + (tier === 'text' ? 'Text' : 'Asr'); }

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
    const idMap = { text: ['pgNameMain', 'pgSumMain'], asr: ['pgNameAsr', 'pgSumAsr'] };
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

async function saveSettings() {
    // 访问令牌（本地）
    const token = document.getElementById('setToken').value.trim();
    try {
        if (token) localStorage.setItem('kaoyan_token', token);
        else localStorage.removeItem('kaoyan_token');
    } catch (e) {}

    try {
        if (document.getElementById('setCompactA').checked) localStorage.setItem('kaoyan_compact_ctx', '1');
        else localStorage.removeItem('kaoyan_compact_ctx');
    } catch (e) {}

    // 模型配置（服务器，留空 Key = 保留已有）；地址/key/模型取当前生效提供商条目
    const actOf = t => (tiersState[t] || []).find(x => x.active) || (tiersState[t] || [])[0] || {};
    const mA = actOf('text'), aA = actOf('asr');
    const mdl0 = p => ((p.models || [])[0] || '').trim();
    const payload = {
        main: {
            base_url: (mA.base_url || '').trim(),
            api_key: (mA.api_key || '').trim(),
            model: mdl0(mA),
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
    await saveTiersAll();
    try {
        const resp = await apiFetch('/api/config/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const d = await resp.json().catch(() => ({}));
            throw new Error(d.detail || resp.status);
        }
        alert('✅ 模型配置已保存，立即生效');
        // 全局模型可能变了：强制刷新对话框模型下拉列表（绕过 60s 缓存）
        loadChatModelList(true);
    } catch (err) {
        alert(`❌ 保存失败：${err.message}`);
    }
    closeSettings();
}

// ===== KaTeX 公式渲染 =====
function renderMath(text) {
    if (typeof katex === 'undefined') {
        // 本地 KaTeX 未加载完成：不静默裸露，给出提示（text 已 escHtml 转义）
        return text + '<div class="katex-pending">⚠️ 公式渲染组件加载中，请刷新页面后查看</div>';
    }
    // escHtml 会把公式内部的 < > & 等转成 &lt; &gt; &amp;，而 KaTeX 不认 HTML 实体：
    // 普通公式里出现裸 & 会直接解析失败（& 仅在对齐/矩阵环境内合法），导致整段公式乱码。
    // 因此在交给 KaTeX 前，先把公式内容还原回原始 LaTeX 字符（DOM 解码单次完成，&amp; 不会二次解码）。
    const unescapeMath = s => {
        const d = document.createElement('div');
        d.innerHTML = s;
        return d.textContent;
    };
    const render = (formula, display) => {
        try {
            return katex.renderToString(unescapeMath(formula).trim(), { displayMode: display, throwOnError: false });
        } catch (e) {
            // 渲染失败：原样返回公式（保留可读性），不吞不伪装
            return display ? `$$${escHtml(formula)}$$` : `$${escHtml(formula)}$`;
        }
    };

    // 0) 归一化模型偶尔违规输出的形态：\[...\] → $$...$$、\(...\) → $...$
    //    提示词里是软约束，这里在渲染层做硬兜底，任何形态都能渲染
    text = text.replace(/\\\[([\s\S]*?)\\\]/g, (_, f) => `$$${f}$$`);
    text = text.replace(/\\\(([\s\S]*?)\\\)/g, (_, f) => `$${f}$`);

    // 1) $$...$$ 块级公式（允许跨行）
    text = text.replace(/\$\$([\s\S]*?)\$\$/g, (_, formula) => render(formula, true));

    // 2) $...$ 行内公式（不跨行、非贪婪，且要求 $ 前不是另一公式的 $）
    text = text.replace(/(^|[^\$])\$([^\$\n]+?)\$(?!\$)/g, (m, prefix, formula) => prefix + render(formula, false));
    return text;
}

/* ===== 流式增量渲染辅助（F1 性能优化）=====
 * 打字机流式输出时不再对整段回答反复全局重渲（innerHTML = renderMath(full)，O(n²)），
 * 而是只对"新增文本"增量渲染并 append。难点是正则公式 $...$ / $$...$$ 可能被流式边界
 * 截断——`$` 在上一批末尾、闭包在下一批开头。因此渲染前用状态机定位"边界是否处于
 * 未闭合公式内"，若是则把渲染起点回退到该公式的起始 $，仅重做这一小段。
 */
function mathBoundaryIndex(text, upto) {
    // 从头部扫描 text，返回 text[0:upto] 结尾是否处于公式内；若是则返回该公式起始下标，否则 -1。
    // 公式语法：$$...$$（块级，可跨行）| $...$(非$)(?!$)（行内，不跨行）。
    if (upto <= 0) return -1;
    let inBlock = false, inInline = false, lastStart = -1;
    const s = text;
    let i = 0;
    while (i < upto && i < s.length) {
        const c = s[i];
        if (c === '$') {
            const isDouble = s[i + 1] === '$';
            if (isDouble) {
                if (!inBlock && !inInline) { inBlock = true; lastStart = i; i += 2; continue; }
                if (inBlock) { inBlock = false; lastStart = -1; i += 2; continue; }
                i += 2; continue; // inInline 时遇到 $$，视为普通字符，忽略边界情况
            } else {
                // 单个 $：须判断不是紧邻 $ 的成对片段（$$ 已在上面的分支吃掉）
                const prevIsDollar = i > 0 && s[i - 1] === '$';
                if (!prevIsDollar) {
                    if (!inInline && !inBlock) { inInline = true; lastStart = i; }
                    else if (inInline) { inInline = false; lastStart = -1; }
                }
            }
        }
        // 行内公式不跨行：遇到换行且仍在行内，视为出错强行闭合（与 renderMath 的 $[^\$\n] 语义一致）
        if (inInline && c === '\n') { inInline = false; lastStart = -1; }
        i++;
    }
    return (inBlock || inInline) ? lastStart : -1;
}

// ===== 播放进度记忆 =====
let progressSaveTimer = null;
document.addEventListener('DOMContentLoaded', () => {
    const video = document.getElementById('videoPlayer');
    video.addEventListener('timeupdate', () => {
        if (!state.currentVideoId) return;
        if (progressSaveTimer) return;
        progressSaveTimer = setTimeout(() => {
            progressSaveTimer = null;
            try {
                localStorage.setItem('kaoyan_progress_' + state.currentVideoId, String(video.currentTime));
                localStorage.setItem('kaoyan_last_video', String(state.currentVideoId));
                // 记录今日观看
                const today = new Date().toISOString().slice(0, 10);
                let watched = JSON.parse(localStorage.getItem('kaoyan_watched') || '{}');
                if (!watched[today]) watched[today] = [];
                if (!watched[today].includes(state.currentVideoId)) {
                    watched[today].push(state.currentVideoId);
                    localStorage.setItem('kaoyan_watched', JSON.stringify(watched));
                }
            } catch(e) {}
        }, 5000);  // 每5秒存一次
    });
    
    // 恢复上次播放
    try {
        const lastId = localStorage.getItem('kaoyan_last_video');
        if (lastId) {
            setTimeout(() => {
                const items = document.querySelectorAll('.tree-video');
                for (const item of items) {
                    if (item.dataset.videoId === lastId) {
                        item.click();
                        break;
                    }
                }
            }, 500);
        }
    } catch(e) {}
});

// ===== 工具 =====
// ===== 模式B 视频选择器 =====
const B_MAX_VIDEOS = 3;  // 模式B 一次最多选几个视频
function onBCheckChange(id, cb) {
    if (cb.checked) {
        const selected = document.querySelectorAll('.b-video-cb:checked');
        if (selected.length > B_MAX_VIDEOS) {
            cb.checked = false;
            addSystemMessage(`⚠️ 模式B最多选 ${B_MAX_VIDEOS} 个视频；要继续引导下一组，请点【🆕 新对话】再选下一组`);
            return;
        }
    }
    sessionStorage.setItem('b_video_'+id, cb.checked ? '1' : '0');
}
function getSelectedBVideos() {
    const cbs = document.querySelectorAll('.b-video-cb:checked');
    return Array.from(cbs).map(c => parseInt(c.dataset.video));
}

function escHtml(t) { const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }

// ===== 移动端抽屉（≤900px）=====
function openVideoDrawer() {
    document.querySelector('.video-sidebar').classList.add('drawer-open');
    document.getElementById('drawerMask').classList.add('show');
}
function openChatDrawer() {
    document.querySelector('.chat-panel').classList.add('drawer-open');
    document.getElementById('drawerMask').classList.add('show');
}
function closeVideoDrawer() {
    document.querySelector('.video-sidebar').classList.remove('drawer-open');
    syncMask();
}
function closeChatDrawer() {
    document.querySelector('.chat-panel').classList.remove('drawer-open');
    syncMask();
}
function closeDrawers() {
    document.querySelectorAll('.drawer-open').forEach(el => el.classList.remove('drawer-open'));
    syncMask();
}
function syncMask() {
    const open = document.querySelectorAll('.drawer-open').length > 0;
    document.getElementById('drawerMask').classList.toggle('show', open);
}
// 桌面 >900px 时 drawer-open 无副作用；窄屏切换科目时顺手收起（可选体验）
if (window.matchMedia('(max-width: 900px)').matches && window.location.hash) {
    // 保留占位：不主动触发，避免干扰初始加载
}

// ===== 自绘播放器控制条：播放/进度/时间 + 点击显隐（不依赖系统播放器，全浏览器统一） =====
(function () {
    const wrapper = document.getElementById('videoWrapper');
    const video = document.getElementById('videoPlayer');
    const bar = document.getElementById('vcBar');
    const playBtn = document.getElementById('vcPlayBtn');
    const prog = document.getElementById('vcProgress');
    const timeEl = document.getElementById('vcTime');
    if (!wrapper || !video || !bar) return;

    function fmt(sec) {
        if (!isFinite(sec) || sec < 0) sec = 0;
        const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
        return m + ':' + String(s).padStart(2, '0');
    }
    function setPlayIcon() { if (playBtn) playBtn.textContent = video.paused ? '▶' : '⏸'; }
    function syncUI() {
        const d = video.duration || 0;
        if (d > 0 && prog) prog.value = Math.round((video.currentTime / d) * 1000);
        if (timeEl) timeEl.textContent = fmt(video.currentTime) + ' / ' + fmt(d);
        setPlayIcon();
    }
    function showControls() {
        document.querySelectorAll('.video-controls-overlay').forEach(el => {
            el.classList.remove('hide');
            el.classList.add('ctrl-visible');
        });
        const barEl = document.getElementById('vcBar');
        if (barEl) barEl.classList.remove('hide');
        scheduleHide();
    }
    function hideControls() {
        document.querySelectorAll('.video-controls-overlay').forEach(el => {
            el.classList.add('hide');
            el.classList.remove('ctrl-visible');
        });
        const barEl = document.getElementById('vcBar');
        if (barEl) barEl.classList.add('hide');
    }
    let hideTimer = null;
    function scheduleHide() {
        clearTimeout(hideTimer);
        // 播放中 3 秒无操作自动隐藏；暂停时常驻（用户要在暂停状态操作按钮）
        if (!video.paused) hideTimer = setTimeout(hideControls, 3000);
    }

    // 拖动进度条时重置自动隐藏计时（拖动期间不隐藏）
    const progressEl = document.getElementById('vcProgress');
    if (progressEl) {
        ['input', 'pointerdown', 'touchstart'].forEach(ev =>
            progressEl.addEventListener(ev, () => { showControls(); }));
    }

    // 单击画面：播放/暂停 + 唤起控制条（点按钮/控制条区域除外，未选视频忽略）——双击全屏已取消
    wrapper.addEventListener('click', e => {
        if (e.target.closest('.speed-btn, .speed-sep, .vc-bar, .vc-tap-to-play')) return;
        if (!(video.getAttribute('src') || video.currentSrc)) return;
        if (video.paused) { video.play().catch(() => {}); } else { video.pause(); }
        showControls();
    });

    // 视频事件同步 UI
    video.addEventListener('play', () => { setPlayIcon(); showControls(); });
    video.addEventListener('pause', () => { setPlayIcon(); showControls(); });
    video.addEventListener('timeupdate', syncUI);
    video.addEventListener('durationchange', syncUI);
    video.addEventListener('ended', () => { if (playBtn) playBtn.textContent = '↻'; });
    video.addEventListener('loadedmetadata', () => { bar.classList.remove('hide'); syncUI(); });
    wrapper.addEventListener('mousemove', showControls);
    wrapper.addEventListener('mouseleave', scheduleHide);

    // 全局 API（供 inline onclick 调用）
    window.togglePlay = function () { if (video.paused) video.play(); else video.pause(); };
    window.showControls = showControls;
    window.hideControls = hideControls;
    window.seekFromSlider = function (v) {
        const d = video.duration;
        if (d > 0) video.currentTime = (v / 1000) * d;
    };

    // 键盘：空格播放/暂停，← → 快退/快进 5 秒，F 全屏
    document.addEventListener('keydown', e => {
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
        if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
        else if (e.key === 'ArrowLeft' && !video.paused) { video.currentTime = Math.max(0, video.currentTime - 5); }
        else if (e.key === 'ArrowRight' && !video.paused) { video.currentTime = Math.min(video.duration || 0, video.currentTime + 5); }
        else if (e.key === 'f' || e.key === 'F') { toggleFullscreen(); }
    });
})();

// ===== 画面手势：长按=临时 2×倍速（300ms 未移动触发）；按住滑动=快进/回退 =====
(function () {
    const wrapper = document.getElementById('videoWrapper');
    const video = document.getElementById('videoPlayer');
    const tip = document.getElementById('vcSeekTip');
    if (!wrapper || !video || !tip) return;

    let startX = null, startT = 0, dragging = false, lastSeek = 0;
    let holdTimer = null, boostActive = false, boostBase = 1;
    const PX_PER_SEC = 3.2;   // 每 3.2px ≈ 1 秒
    const TAP_SLOP = 12;      // <12px 视为点击
    const HOLD_MS = 300;      // 长按阈值：300ms 未移动 → 进入临时 2× 倍速

    function fmt(s) {
        s = Math.max(0, Math.floor(s));
        return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    }
    function cancelHold() {
        if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
    }
    function endBoost() {
        if (boostActive) {
            boostActive = false;
            video.playbackRate = boostBase || 1;
        }
    }

    wrapper.addEventListener('touchstart', e => {
        if (e.target.closest('.speed-btn, .speed-sep, .vc-bar')) return;
        const t = e.touches[0];
        startX = t.clientX;
        startT = video.currentTime;
        dragging = false;
        endBoost();
        cancelHold();
        // 长按计时：未移动 300ms → 临时 2× 当前倍速（最高 5x）
        holdTimer = setTimeout(() => {
            holdTimer = null;
            if (dragging) return;
            boostBase = video.playbackRate || 1;
            video.playbackRate = Math.min(boostBase * 2, 5);
            boostActive = true;
            if (tip) { tip.textContent = '⏩×' + video.playbackRate + ' (临时)'; tip.style.display = 'block'; }
        }, HOLD_MS);
    }, { passive: true });

    wrapper.addEventListener('touchmove', e => {
        if (startX === null) return;
        const t = e.touches[0];
        const dx = t.clientX - startX;
        if (!dragging) {
            if (Math.abs(dx) < TAP_SLOP) return;
            dragging = true;
            cancelHold();
            endBoost();          // 长按后改为滑动 → 取消 boost 转 seek
            e.preventDefault();
        }
        const now = Date.now();
        if (now - lastSeek < 80) return;
        lastSeek = now;
        const dur = video.duration;
        if (!(dur > 0)) return;
        const target = Math.max(0, Math.min(dur, startT + dx / PX_PER_SEC));
        try { video.currentTime = target; } catch (_) {}
        const delta = target - startT;
        tip.textContent = (delta >= 0 ? '⏩ +' : '⏪ ') + fmt(Math.abs(delta)) + ' ｜ ' + fmt(target) + ' / ' + fmt(dur);
        tip.style.display = 'block';
    }, { passive: false });

    function endGesture() {
        if (startX !== null) tip.style.display = 'none';
        if (boostActive) {
            endBoost();
            cancelHold();
            wrapper._suppressClick = true;   // 长按后吞掉 click（不触播放/暂停）
        } else if (dragging) {
            wrapper._suppressClick = true;   // 拖完吞掉 click
        }
        cancelHold();
        startX = null; dragging = false;
    }
    wrapper.addEventListener('touchend', endGesture);
    wrapper.addEventListener('touchcancel', endGesture);

    // 手势结束后，捕获阶段拦截同一次触摸产生的 click
    wrapper.addEventListener('click', e => {
        if (wrapper._suppressClick) {
            e.stopPropagation();
            wrapper._suppressClick = false;
        }
    }, true);
})();

// ===== 全屏状态变化 =====
// 全屏时控制条默认隐藏（见 CSS #videoWrapper:fullscreen .video-controls-overlay），
// 进全屏后先亮一次，避免用户以为按钮没了。
function onFullscreenChange() {
    const el = document.fullscreenElement || document.webkitFullscreenElement;
    if (el && window.showControls) window.showControls();
}
document.addEventListener('fullscreenchange', onFullscreenChange);
document.addEventListener('webkitfullscreenchange', onFullscreenChange);
function formatSize(b) { if(!b) return ''; const u=['B','KB','MB','GB']; let s=b,i=0; while(s>=1024&&i<u.length-1){s/=1024;i++} return `${s.toFixed(1)}${u[i]}`; }
function time() { const n=new Date(); return `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`; }

// ===== 播放器补充层：缓冲指示 / 音量 / 错误重试 / 自动播放失败兜底 =====
(function () {
    const video = document.getElementById('videoPlayer');
    const spinner = document.getElementById('vcSpinner');
    const tap = document.getElementById('vcTapToPlay');
    const err = document.getElementById('vcError');
    const vol = document.getElementById('vcVolume');
    const muteBtn = document.getElementById('vcMuteBtn');
    if (!video) return;

    // 1. 缓冲指示（网络卡顿/dragging seek 时转圈）
    if (spinner) {
        video.addEventListener('waiting', () => { spinner.style.display = 'flex'; });
        video.addEventListener('playing', () => { spinner.style.display = 'none'; });
        video.addEventListener('seeked', () => { spinner.style.display = 'none'; });
        video.addEventListener('canplay', () => { spinner.style.display = 'none'; });
    }

    // 2. 音量（静音 + 滑块）
    function syncVol() {
        if (vol) vol.value = Math.round((video.muted ? 0 : (video.volume || 1)) * 100);
        if (muteBtn) muteBtn.textContent = (video.muted || video.volume === 0) ? '🔇' : '🔊';
    }
    window.toggleMute = function () { video.muted = !video.muted; syncVol(); };
    window.setVolume = function (v) { video.volume = Math.max(0, Math.min(1, v / 100)); video.muted = false; syncVol(); };
    video.addEventListener('volumechange', syncVol);

    // 3. 自动播放被浏览器拦截时的"点击播放"兜底层
    function showTap() { if (tap) tap.style.display = 'flex'; }
    window.__vcOnPlayRejected = showTap;
    if (tap) {
        tap.addEventListener('click', e => {
            e.stopPropagation();
            video.play().then(() => { tap.style.display = 'none'; }).catch(() => {});
        });
    }

    // 4. 错误提示 + 重试（playVideo 的 onerror 里会调用 __vcShowError）
    window.__vcShowError = function () {
        if (err) err.style.display = 'flex';
        if (spinner) spinner.style.display = 'none';
    };
    window.retryVideo = function () {
        if (err) err.style.display = 'none';
        video.load();
        const p = video.play();
        if (p && p.catch) p.catch(() => showTap());
    };
    video.addEventListener('error', () => {
        if (!(video.currentSrc || video.getAttribute('src'))) return;
        if (spinner) spinner.style.display = 'none';
    });
})();

// ==================================================================
// 视频播放结束屏幕（End Screen）
// ==================================================================

// 从 treeCache 中递归提取所有视频的扁平列表（保持目录树的显示顺序）
// 注意：接口返回的结构是 folder.children = 子文件夹、folder.videos = 本文件夹的视频，
// folder 本身没有 type 字段 —— 旧实现按 children 递归并把 folder 当视频收集，
// 导致 findCurrentVideoIndex() 永远找不到当前视频，「上一课/下一课」按钮被隐藏。
function getAllVideosFlat() {
    if (!treeCache) return [];
    const result = [];
    const pushVideos = (videos) => {
        for (const v of (videos || [])) {
            result.push({ id: v.id, title: v.title || v.filename });
        }
    };
    const walkFolder = (f) => {
        for (const c of (f.children || [])) walkFolder(c);  // 先子文件夹（与树渲染顺序一致）
        pushVideos(f.videos);                               // 再本文件夹的视频
    };
    for (const f of (treeCache.folders || [])) walkFolder(f);
    pushVideos(treeCache.uncategorized);                    // 未分类排在最后
    return result;
}

// 查找当前视频在列表中的索引（id 可能是数字或字符串，统一按数字比较）
function findCurrentVideoIndex() {
    if (state.currentVideoId == null) return -1;
    const cur = Number(state.currentVideoId);
    const list = getAllVideosFlat();
    return list.findIndex(v => Number(v.id) === cur);
}

// 获取上一个视频的 ID
function getPrevVideoId() {
    const list = getAllVideosFlat();
    const idx = findCurrentVideoIndex();
    if (idx > 0) return list[idx - 1].id;
    return null;
}

// 获取下一个视频的 ID
function getNextVideoId() {
    const list = getAllVideosFlat();
    const idx = findCurrentVideoIndex();
    if (idx >= 0 && idx < list.length - 1) return list[idx + 1].id;
    return null;
}

// 刷新结束屏幕上的"上一课 / 下一课"按钮可见性
function refreshEndButtons() {
    const prevBtn = document.getElementById('endPrevBtn');
    const nextBtn = document.getElementById('endNextBtn');
    if (prevBtn) prevBtn.style.display = getPrevVideoId() ? '' : 'none';
    if (nextBtn) nextBtn.style.display = getNextVideoId() ? '' : 'none';
}

// 显示结束屏幕
function showEndScreen() {
    try {
        const el = document.getElementById('videoEndScreen');
        if (!el) return;
        // 暂停视频（确保画面停住）
        const video = document.getElementById('videoPlayer');
        if (video && !video.paused) video.pause();
        refreshEndButtons();
        el.style.display = 'flex';
        // 目录树还没加载（例如刷新后直接续播）时，拉一次树再校正按钮
        if (!treeCache) loadTree().then(() => refreshEndButtons()).catch(() => {});
    } catch (e) { console.warn('showEndScreen error:', e); }
}

// 隐藏结束屏幕
function hideEndScreen() {
    try {
        const el = document.getElementById('videoEndScreen');
        if (!el) return;
        el.style.display = 'none';
    } catch (e) { console.warn('hideEndScreen error:', e); }
}

// 重播当前视频
function onEndReplay() {
    try {
        const video = document.getElementById('videoPlayer');
        if (!video || !state.currentVideoId) return;
        hideEndScreen();
        video.currentTime = 0;
        video.play().catch(() => {});
        addSystemMessage(`↻ 重新开始播放`);
    } catch (e) { console.warn('onEndReplay error:', e); }
}

// 上一个视频
function onEndPrev() {
    try {
        const prevId = getPrevVideoId();
        if (!prevId) return;
        hideEndScreen();
        playVideo(prevId);
    } catch (e) { console.warn('onEndPrev error:', e); }
}

// 下一个视频
function onEndNext() {
    try {
        const nextId = getNextVideoId();
        if (!nextId) return;
        hideEndScreen();
        playVideo(nextId);
    } catch (e) { console.warn('onEndNext error:', e); }
}

// ESC 键关闭结束屏幕
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const el = document.getElementById('videoEndScreen');
        if (el && el.style.display !== 'none') {
            hideEndScreen();
        }
    }
});

// 点击结束屏幕空白区域也可关闭
try {
    const endScreenEl = document.getElementById('videoEndScreen');
    if (endScreenEl) {
        endScreenEl.addEventListener('click', (e) => {
            if (e.target === endScreenEl) {
                hideEndScreen();
            }
        });
    }
} catch (e) {}


// ===== 设置弹窗 Tab 切换（分页减少滚动长度） =====
function switchSettingsTab(paneId, btn) {
    document.querySelectorAll('#settingsModal .settings-tab').forEach(t =>
        t.classList.toggle('active', t === btn));
    document.querySelectorAll('#settingsModal .settings-pane').forEach(p =>
        p.style.display = (p.id === paneId) ? '' : 'none');
}
