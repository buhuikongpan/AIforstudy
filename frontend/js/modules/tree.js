// tree.js — 托管资源管理器（左侧文件树，共识 D2：上传复制制 + 资源管理器体验）
// 复用 app.js 的全局工具：apiFetch / escHtml / formatSize / addSystemMessage / openDialog
// 复用现有 CSS 类：tree-folder / tree-video / tree-ctx 等

const KIND_ICON = {
    video: '🎬', pdf: '📕', office: '📊', image: '🖼️',
    markdown: '📝', text: '📄', audio: '🎵', other: '📦',
};
const KIND_LABEL = {
    video: '视频', pdf: 'PDF', office: 'Office', image: '图片',
    markdown: 'Markdown', text: '文本', audio: '音频', other: '文件',
};

const FSTree = {
    cache: null,
    expanded: {},            // {folderId: bool}
    selectedFolderId: 0,     // 上传目标 = 当前选中目录（0 = 根）
    currentFileId: 0,
    delMode: false,
    delSelected: new Set(),
    keyword: '',
    subjectFilter: '',       // 当前科目（= 顶层文件夹名），空 = 显示全部
    _pollTimer: null,

    init() {
        const search = document.getElementById('videoSearch');
        if (search) {
            search.placeholder = '🔍 搜索文件…（全部资料）';
            let timer = null;
            search.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => { this.keyword = (search.value || '').trim().toLowerCase(); this.render(); }, 200);
            });
        }
        // 树滚动时收起操作菜单（fixed 定位菜单不跟随滚动）
        // 拖拽上传（资源管理器体验：把文件拖进树区即上传到选中目录）
        const body = document.getElementById('videoTreeBody');
        if (body) {
            body.addEventListener('scroll', () => this._closeMenu(), true);
            body.addEventListener('dragover', e => { e.preventDefault(); body.classList.add('drag-over'); });
            body.addEventListener('dragleave', () => body.classList.remove('drag-over'));
            body.addEventListener('drop', e => {
                e.preventDefault(); body.classList.remove('drag-over');
                const files = Array.from(e.dataTransfer.files || []);
                if (files.length) this.uploadFiles(files, this.selectedFolderId);
            });
        }
        this.refresh();
    },

    async refresh() {
        const container = document.getElementById('videoTreeBody');
        if (!container) return;
        try {
            const resp = await apiFetch('/api/fs/tree');
            if (!resp.ok) throw new Error('加载失败');
            this.cache = await resp.json();
            this.render();
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><div>${escHtml(err.message)}</div></div>`;
        }
    },

    render() {
        const container = document.getElementById('videoTreeBody');
        if (!container) return;
        if (!this.cache) return;
        container.innerHTML = '';
        const kw = this.keyword;
        let shown = 0;

        // 科目隔离：切换科目后只显示该顶层文件夹的子树（根级散文件始终可见）
        let topFolders = this.cache.folders || [];
        if (this.subjectFilter) {
            topFolders = topFolders.filter(f => f.name === this.subjectFilter);
        }

        const walk = (folders, depth, mount) => {
            for (const f of folders) {
                const kwSelf = f.name.toLowerCase().includes(kw);
                const sub = document.createElement('div');
                sub.className = 'tree-folder';
                const kids = document.createElement('div');
                kids.className = 'tree-children';
                const expand = this.expanded[f.id] === true || !!kw;
                const childFolders = (f.children || []).filter(c => kwSelf || this._subtreeMatch(c));
                const files = (f.files || []).filter(v => !kw || v.name.toLowerCase().includes(kw));
                const hasAny = childFolders.length || files.length || kwSelf;
                if (!hasAny) continue;
                sub.innerHTML = `<div class="tree-folder-label${this.selectedFolderId === f.id ? ' active' : ''}"
                    style="padding-left:${depth * 16 + 4}px" data-fid="${f.id}">
                    <span class="tree-arrow">${(f.children || []).length || files.length ? (expand ? '▼' : '▶') : ''}</span>
                    <span class="tree-icon">📁</span>
                    <span class="tree-fname">${escHtml(f.name)}</span>
                    <span class="tree-count">${files.length || ''}</span>
                    ${this.delMode ? `<input type="checkbox" class="del-cb" data-folder="${f.id}" onchange="FSTree.toggleDel('folder',${f.id},this)">` : `
                    <span class="tree-ctx tree-more" title="更多操作（新建子文件夹/重命名/移动/删除）">⋯</span>`}
                </div>`;
                const label = sub.querySelector('.tree-folder-label');
                label.addEventListener('click', e => {
                    if (e.target.closest('.tree-more')) {
                        e.stopPropagation();
                        this._folderMenu(e, f);
                        return;
                    }
                    if (e.target.closest('.del-cb')) return;
                    if ((f.children || []).length || files.length) {
                        this.expanded[f.id] = !expand;
                        this.render();
                    }
                    this.selectFolder(f.id);
                });
                if (expand) {
                    walk(childFolders, depth + 1, kids);
                    for (const v of files) kids.appendChild(this._fileNode(v, depth + 1));
                }
                sub.appendChild(kids);
                mount.appendChild(sub);
                shown++;
            }
        };
        walk(topFolders, 0, container);

        // 根级文件
        for (const v of (this.cache.rootFiles || []).filter(v => !kw || v.name.toLowerCase().includes(kw))) {
            container.appendChild(this._fileNode(v, 0));
            shown++;
        }

        if (!shown) {
            container.innerHTML = `<div class="empty-state"><div class="icon">${kw ? '🔍' : '📂'}</div>
                <div>${kw ? '没有匹配「' + escHtml(kw) + '」的文件' : '托管库为空：点击右上角 ＋ 上传资料'}</div></div>`;
        }
        // 有处理中文件时轮询刷新（转换/提取完成自动更新徽标）
        clearTimeout(this._pollTimer);
        if (container.querySelector('.status-processing')) {
            this._pollTimer = setTimeout(() => this.refresh(), 4000);
        }
        this.renderSubjectTabs();
    },

    // ===== 动态科目（顶部 tab = 顶层文件夹） =====
    renderSubjectTabs() {
        const box = document.getElementById('subjectTabs');
        if (!box || !this.cache) return;
        const tops = this.cache.folders || [];
        const names = tops.map(f => f.name);
        // 当前科目不存在（被删/首次加载旧值 math）→ 自动切到第一个
        if (names.length && !names.includes(state.subject)) {
            switchSubject(names[0]);
            return;
        }
        box.innerHTML = tops.map(f =>
            `<button class="subject-tab${f.name === state.subject ? ' active' : ''}" data-subject="${escHtml(f.name)}" onclick="switchSubject('${escHtml(f.name).replace(/'/g, '&#39;')}')">📁 ${escHtml(f.name)}</button>`
        ).join('');
    },

    setSubject(name) {
        this.subjectFilter = name || '';
        this.currentFileId = 0;               // 换科目后旧科目的文件不再是“当前文件”
        const top = (this.cache ? this.cache.folders || [] : []).find(f => f.name === name);
        if (top) this.selectFolder(top.id);   // 上传目标跟随科目
        else this.render();
    },

    createSubject() {
        openDialog({
            title: '新建科目',
            bodyHTML: '<label>科目名称</label><input type="text" id="dlgInput" class="dlg-input" placeholder="如：数学、英语、法考…">'
                + '<div class="dlg-hint">每个科目是托管库里的一级目录：资料与聊天会话相互隔离，AI 模型配置全局共享。</div>',
            onOk: async () => {
                const name = ((document.getElementById('dlgInput') || {}).value || '').trim();
                if (!name) return false;
                const resp = await apiFetch('/api/fs/mkdir', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, parent_id: 0 }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '创建失败'}`); return false; }
                const d = await resp.json();
                await this.refresh();
                switchSubject(d.name || name);
            },
        });
        setTimeout(() => { const el = document.getElementById('dlgInput'); if (el) el.focus(); }, 50);
    },

    _subtreeMatch(f) {
        if (f.name.toLowerCase().includes(this.keyword)) return true;
        for (const c of (f.children || [])) if (this._subtreeMatch(c)) return true;
        for (const v of (f.files || [])) if (v.name.toLowerCase().includes(this.keyword)) return true;
        return false;
    },

    _fileNode(v, depth) {
        const item = document.createElement('div');
        const active = v.id === this.currentFileId ? ' active' : '';
        item.className = `tree-video${active}`;
        item.dataset.fileId = v.id;
        const badge = v.status === 'processing' ? '<span class="tree-vstatus status-processing">⏳</span>'
            : v.status === 'error' ? `<span class="tree-vstatus status-failed" title="${escHtml(v.status_msg || '处理失败')}">⚠️</span>`
            : '';

        item.innerHTML = `<div class="tree-video-label" style="padding-left:${depth * 16 + 4}px">
            ${this.delMode ? `<input type="checkbox" class="del-cb" data-file="${v.id}" onchange="FSTree.toggleDel('file',${v.id},this)">` : ''}
            <span class="tree-icon" onclick="FSTree.openFile(${v.id})">${KIND_ICON[v.kind] || '📦'}</span>
            <span class="tree-vname" onclick="FSTree.openFile(${v.id})" title="${escHtml(v.name)}">${escHtml(v.name)}</span>
            <span class="tree-vmeta">${formatSize(v.size)}</span>
            ${badge}
            ${this.delMode ? '' : `<span class="tree-ctx tree-more" title="更多操作">⋯</span>`}
        </div>`;
        const more = item.querySelector('.tree-more');
        if (more) more.addEventListener('click', e => { e.stopPropagation(); this._fileMenu(e, v); });
        return item;
    },

    // ---------- 选中 / 打开 ----------
    selectFolder(id) {
        this.selectedFolderId = id;
        const hint = document.getElementById('fsTargetHint');
        if (hint) hint.textContent = id ? `📁 ${this._folderName(id)}` : '📁 根目录（托管库）';
        this.render();
    },

    async openFile(id) {
        try {
            const resp = await apiFetch(`/api/files/${id}/status`);
            if (!resp.ok) throw new Error('文件不存在');
            const st = await resp.json();
            this.currentFileId = id;
            this.render();
            closeVideoDrawer(); // 移动端抽屉
            if (st.kind === 'video') {
                // 关键：视频也必须登记当前文件，否则右侧 agent 拿不到 file_id（不知道你在学哪一课）
                if (window.FSState) {
                    window.FSState.setCurrentFile({
                        id: st.id, name: st.name, kind: st.kind,
                        status: st.status, status_msg: st.status_msg,
                    });
                }
                if (window.Viewer) window.Viewer.showPane('videoPane');
                window.playVideo(id);
            } else if (window.Viewer) {
                window.Viewer.open(st);
            }
        } catch (e) { addSystemMessage(`⚠️ ${e.message}`); }
    },

    // ---------- 上传 ----------
    uploadFromInput(input) {
        const files = Array.from(input.files || []);
        input.value = '';
        if (!files.length) return;
        this.uploadFiles(files, this.selectedFolderId);
    },

    async uploadFiles(files, folderId) {
        if (!files.length) return;
        if (files.length > 50) { addSystemMessage('⚠️ 单次最多上传 50 个文件'); return; }
        addSystemMessage(`📤 上传 ${files.length} 个文件到「${folderId ? this._folderName(folderId) : '根目录'}」...`);
        let ok = 0, dup = 0, failed = 0;
        for (const file of files) {
            try {
                const fd = new FormData();
                fd.append('file', file);
                fd.append('folder_id', String(folderId || 0));
                if (file.webkitRelativePath) fd.append('rel_path', file.webkitRelativePath);
                const resp = await apiFetch('/api/fs/upload', { method: 'POST', body: fd });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) { failed++; addSystemMessage(`⚠️ ${file.name}：${data.detail || '上传失败'}`); continue; }
                if (data.duplicate) dup++; else ok++;
            } catch (e) { failed++; }
        }
        addSystemMessage(`✅ 上传完成：成功 ${ok}${dup ? `，跳过重复 ${dup}` : ''}${failed ? `，失败 ${failed}` : ''}`);
        this.refresh();
    },

    _folderName(id) {
        if (!this.cache) return String(id);
        const walk = fs => {
            for (const f of fs) { if (f.id === id) return f.name; const r = walk(f.children || []); if (r) return r; }
            return null;
        };
        return walk(this.cache.folders || []) || String(id);
    },

    // ---------- 目录操作 ----------
    async mkdir(parentId) {
        openDialog({
            title: '新建文件夹',
            bodyHTML: '<label>文件夹名称</label><input type="text" id="dlgInput" class="dlg-input" placeholder="如：第16讲、高数基础…">',
            onOk: async () => {
                const name = (document.getElementById('dlgInput') || {}).value || '';
                const resp = await apiFetch('/api/fs/mkdir', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, parent_id: parentId }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '创建失败'}`); return false; }
                this.expanded[parentId] = true;
                this.refresh();
            },
        });
        setTimeout(() => { const el = document.getElementById('dlgInput'); if (el) el.focus(); }, 50);
    },

    async renameFolder(id, oldName) {
        openDialog({
            title: '重命名文件夹',
            bodyHTML: `<label>新的名称</label><input type="text" id="dlgInput" class="dlg-input" value="${escHtml(oldName)}">`,
            onOk: async () => {
                const name = (document.getElementById('dlgInput') || {}).value || '';
                const resp = await apiFetch(`/api/fs/folder/${id}/rename`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`); return false; }
                this.refresh();
            },
        });
    },

    async renameFile(id, oldName) {
        openDialog({
            title: '重命名文件',
            bodyHTML: `<label>新的名称</label><input type="text" id="dlgInput" class="dlg-input" value="${escHtml(oldName)}">`,
            onOk: async () => {
                const name = (document.getElementById('dlgInput') || {}).value || '';
                const resp = await apiFetch(`/api/fs/file/${id}/rename`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`); return false; }
                this.refresh();
            },
        });
    },

    // 移动：弹出目录选择（平铺列表，含根）
    _folderPickerHtml(targetId) {
        const opts = [];
        const walk = (fs, d) => {
            for (const f of fs) {
                opts.push(`<option value="${f.id}"${f.id === targetId ? ' selected' : ''}>${'\u00a0'.repeat(d * 2)}${escHtml(f.name)}</option>`);
                walk(f.children || [], d + 1);
            }
        };
        walk(this.cache ? this.cache.folders || [] : [], 0);
        return `<label>目标文件夹</label><select id="dlgMoveTarget">
            <option value="0">📁 根目录（托管库）</option>${opts.join('')}</select>`;
    },

    moveFile(id) {
        openDialog({
            title: '移动文件到...',
            bodyHTML: this._folderPickerHtml(0),
            onOk: async () => {
                const target = parseInt((document.getElementById('dlgMoveTarget') || {}).value || '0', 10);
                const resp = await apiFetch(`/api/fs/file/${id}/move`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_folder_id: target }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`); return false; }
                this.refresh();
            },
        });
    },

    moveFolder(id) {
        openDialog({
            title: '移动文件夹到...',
            bodyHTML: this._folderPickerHtml(0),
            onOk: async () => {
                const target = parseInt((document.getElementById('dlgMoveTarget') || {}).value || '0', 10);
                const resp = await apiFetch(`/api/fs/folder/${id}/move`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_folder_id: target }),
                });
                if (!resp.ok) { addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`); return false; }
                this.refresh();
            },
        });
    },

    // ---------- 删除（回收站） ----------
    async deleteFile(id) {
        openDialog({
            title: '删除文件',
            bodyHTML: '<div style="font-size:14px">文件将移入回收站，可在回收站中恢复。确定删除？</div>',
            onOk: async () => {
                const resp = await apiFetch(`/api/fs/file/${id}`, { method: 'DELETE' });
                if (!resp.ok) { addSystemMessage('⚠️ 删除失败'); return false; }
                if (this.currentFileId === id) { this.currentFileId = 0; FSState.setCurrentFile(null); if (window.Viewer) window.Viewer.close(); }
                this.refresh();
            },
        });
    },

    async deleteFolder(id) {
        openDialog({
            title: '删除文件夹',
            bodyHTML: '<div style="font-size:14px">整个文件夹（含其中全部文件）将移入回收站。确定删除？</div>',
            onOk: async () => {
                const resp = await apiFetch(`/api/fs/folder/${id}`, { method: 'DELETE' });
                if (!resp.ok) { addSystemMessage('⚠️ 删除失败'); return false; }
                this.refresh();
            },
        });
    },

    toggleDel(type, id, cb) {
        const key = `${type}:${id}`;
        if (cb.checked) this.delSelected.add(key); else this.delSelected.delete(key);
        this._updateDelCount();
    },

    _updateDelCount() {
        const el = document.getElementById('delBatchCount');
        if (el) {
            el.style.display = this.delSelected.size ? 'inline' : 'none';
            el.textContent = this.delSelected.size || '';
        }
    },

    // 批量删除：首次点击进入选择模式，再次点击确认删除
    async onDelBatchClick() {
        if (!this.delMode) {
            this.delMode = true;
            this.delSelected.clear();
            this.render();
            return;
        }
        if (!this.delSelected.size) { this.delMode = false; this.render(); return; }
        openDialog({
            title: '批量删除',
            bodyHTML: `<div style="font-size:14px">将删除选中的 ${this.delSelected.size} 项（移入回收站）。确定？</div>`,
            onOk: async () => {
                for (const key of this.delSelected) {
                    const [type, idStr] = key.split(':');
                    const url = type === 'file' ? `/api/fs/file/${idStr}` : `/api/fs/folder/${idStr}`;
                    try { await apiFetch(url, { method: 'DELETE' }); } catch (e) {}
                }
                this.delMode = false;
                this.delSelected.clear();
                this._updateDelCount();
                this.refresh();
            },
        });
    },

    // ---------- 回收站 ----------
    async showTrash() {
        const resp = await apiFetch('/api/fs/trash');
        if (!resp.ok) { addSystemMessage('⚠️ 回收站加载失败'); return; }
        const data = await resp.json();
        const rows = [];
        for (const f of (data.files || [])) {
            rows.push(`<div class="trash-row">
                <span class="tree-icon">${KIND_ICON[f.kind] || '📦'}</span>
                <span class="trash-name">${escHtml(f.name)}</span>
                <span class="tree-ctx" onclick="FSTree.restoreFile(${f.id})" title="恢复">↩️</span>
                <span class="tree-ctx" onclick="FSTree.purgeFile(${f.id})" title="彻底删除">❌</span>
            </div>`);
        }
        for (const f of (data.folders || [])) {
            rows.push(`<div class="trash-row">
                <span class="tree-icon">📁</span>
                <span class="trash-name">${escHtml(f.name)}/</span>
                <span class="tree-ctx" onclick="FSTree.restoreFolder(${f.id})" title="恢复">↩️</span>
                <span class="tree-ctx" onclick="FSTree.purgeFolder(${f.id})" title="彻底删除">❌</span>
            </div>`);
        }
        openDialog({
            title: '🗑️ 回收站',
            bodyHTML: rows.length ? rows.join('') : '<div style="color:#999;font-size:13px">回收站是空的</div>',
            onOk: () => { this.refresh(); return true; },
            okText: '关闭',
        });
    },

    async restoreFile(id) { await apiFetch(`/api/fs/file/${id}/restore`, { method: 'POST' }); this.showTrash(); this.refresh(); },
    async restoreFolder(id) { await apiFetch(`/api/fs/folder/${id}/restore`, { method: 'POST' }); this.showTrash(); this.refresh(); },
    async purgeFile(id) {
        openDialog({ title: '彻底删除', bodyHTML: '<div style="font-size:14px">文件与其 AI 产物将永久删除，不可恢复。确定？</div>',
            onOk: async () => { await apiFetch(`/api/fs/file/${id}/purge`, { method: 'DELETE' }); this.showTrash(); this.refresh(); return true; } });
    },
    async purgeFolder(id) {
        openDialog({ title: '彻底删除', bodyHTML: '<div style="font-size:14px">文件夹及其全部内容将永久删除，不可恢复。确定？</div>',
            onOk: async () => { await apiFetch(`/api/fs/folder/${id}/purge`, { method: 'DELETE' }); this.showTrash(); this.refresh(); return true; } });
    },

    downloadFile(id) {
        const tk = getApiToken();
        const url = getApiUrl(`/api/files/${id}/raw?download=1`) + (tk ? `&token=${encodeURIComponent(tk)}` : '');
        window.open(url, '_blank');
    },

    // ===== 「⋯」垂直操作菜单（fixed 定位挂 body，防被树容器 overflow 裁剪） =====
    _openMenu(e, items) {
        this._closeMenu();
        const menu = document.createElement('div');
        menu.className = 'ctx-menu';
        menu.setAttribute('role', 'menu');
        // 三列布局：① 复选框列（有产物的行才有，其余留等宽空位对齐）
        //           ② 功能图标列（🔄/📑/✏️…，常驻显示，不再被复选框顶替）
        //           ③ 文字
        menu.innerHTML = items.map((it, i) => {
            const cbCol = it.cbcol
                ? `<input type="checkbox" class="ctx-menu-cb" ${it.checked ? 'checked' : ''} data-cb="${i}" title="${it.checked ? '已生成' : '未生成，点击生成'}">`
                : `<span class="ctx-menu-cb-ph"></span>`;
            const icon = it.icon ? `<span class="ctx-menu-icon">${it.icon}</span>` : '';
            return `<div class="ctx-menu-item${it.danger ? ' danger' : ''}${it.disabled ? ' disabled' : ''}" data-i="${i}" role="menuitem">${cbCol}${icon}<span class="ctx-menu-label">${escHtml(it.label)}</span></div>`;
        }).join('');
        document.body.appendChild(menu);
        // 锚点 = 被点的「⋯」按钮本身（文件夹的监听在整行上，必须回溯到按钮，
        // 否则菜单会从行左边缘弹出——点右边开左边的反人类位置）
        const anchor = (e.currentTarget.closest && e.currentTarget.closest('.tree-more')) || e.currentTarget;
        const r = anchor.getBoundingClientRect();
        const mw = menu.offsetWidth, mh = menu.offsetHeight;
        // 标准 kebab：菜单右缘紧贴按钮右缘（向左展开），竖直方向挂在按钮下沿
        let x = Math.round(r.right - mw);
        if (x < 8) x = 8;
        if (x + mw > window.innerWidth - 8) x = window.innerWidth - mw - 8;
        let y = Math.round(r.bottom + 2);
        if (y + mh > window.innerHeight - 8) y = Math.max(8, Math.round(r.top - mh - 2));
        menu.style.left = x + 'px';
        menu.style.top = y + 'px';
        // 【关键】用「同一个捕获阶段处理器」既执行动作又阻断冒泡。
        // 不能用两个分开的监听：单独一个捕获阶段的 stopPropagation 会在事件
        // 进入菜单时就掐断，导致菜单自己的冒泡 click 处理器永远收不到事件（菜单项全变哑巴）。
        menu.addEventListener('click', ev => {
            ev.stopPropagation();
            ev.preventDefault();
            const el = ev.target.closest('.ctx-menu-item');
            if (!el) return;
            const it = items[parseInt(el.dataset.i, 10)];
            if (!it || it.disabled) return;
            const cbEl = ev.target.closest('.ctx-menu-cb');
            if (cbEl) {
                // 复选框：未勾选 → 生成产物并原地勾上；已勾选 → 保持（纯状态）
                if (!it.checked && it.genAction) {
                    try { it.genAction(); } catch (err) { console.error(err); }
                    cbEl.checked = true;
                    setTimeout(() => this.refresh(), 800);
                }
                return;   // 点复选框不关菜单
            }
            this._closeMenu();
            if (it.action) {
                try { it.action(); } catch (err) { console.error('[menu action]', err); }
            }
        }, true);
        // 其余鼠标事件在捕获阶段阻断即可（防止穿透到树行触发播放/选中）
        ['mousedown', 'mouseup', 'contextmenu'].forEach(t => {
            menu.addEventListener(t, ev => ev.stopPropagation(), true);
        });
        this._menu = menu;
        setTimeout(() => {
            // 外部点击关闭也用捕获，但菜单已 stopPropagation 不会误触发
            this._menuCloser = (ev) => { if (this._menu && !this._menu.contains(ev.target)) this._closeMenu(); };
            document.addEventListener('mousedown', this._menuCloser, true);
            this._menuKey = (ev) => { if (ev.key === 'Escape') this._closeMenu(); };
            document.addEventListener('keydown', this._menuKey);
        }, 0);
    },

    _closeMenu() {
        if (this._menu) { this._menu.remove(); this._menu = null; }
        if (this._menuCloser) { document.removeEventListener('mousedown', this._menuCloser, true); this._menuCloser = null; }
        if (this._menuKey) { document.removeEventListener('keydown', this._menuKey); this._menuKey = null; }
    },

    _folderMenu(e, f) {
        this._openMenu(e, [
            { icon: '📌', label: '设为上传目标', action: () => this.selectFolder(f.id) },
            { icon: '➕', label: '新建子文件夹', action: () => this.mkdir(f.id) },
            { icon: '✏️', label: '重命名', action: () => this.renameFolder(f.id, f.name) },
            { icon: '📂', label: '移动到…', action: () => this.moveFolder(f.id) },
            { icon: '🗑️', label: '删除（进回收站）', danger: true, action: () => this.deleteFolder(f.id) },
        ]);
    },

    _fileMenu(e, v) {
        const items = [];
        // 第一列 = 复选框列（勾选 = 该产物已存在），第二列 = 功能图标
        const sum = v.summary || '';

        if (v.kind === 'video') {
            items.push({ cbcol: true, checked: true, icon: '🔄', label: '提取/重新提取字幕',
                action: () => {
                    // 只提取字幕，不打开/播放视频（点菜单不该切走当前正在看的内容）
                    extractSubtitle(v.id);
                    addSystemMessage(`🔄 已加入字幕提取队列：${v.name}`);
                } });
            if (sum === 'done') {
                items.push({ cbcol: true, checked: true, icon: '📑', label: '查看课程总结', action: () => viewSummary(v.id) });
            } else if (sum === 'processing') {
                items.push({ cbcol: true, checked: false, icon: '📑', label: '总结生成中…', disabled: true });
            } else {
                items.push({ cbcol: true, checked: false, genAction: () => generateSummary(v.id), icon: '📑', label: '生成课程总结', action: () => generateSummary(v.id) });
            }
        } else if (v.kind === 'office' || v.kind === 'pdf') {
            items.push({ cbcol: true, checked: true, icon: '🔄', label: '重跑 AI 处理（转换/提取）', action: () => this._reprocess(v.id) });
            if (sum === 'done') {
                items.push({ cbcol: true, checked: true, icon: '📑', label: '查看 AI 总结', action: () => this._viewDocSummary(v.id) });
            } else {
                items.push({ cbcol: true, checked: false, genAction: () => this._reprocess(v.id), icon: '📑', label: '生成 AI 总结', action: () => this._reprocess(v.id) });
            }
        }
        items.push(
            { icon: '✏️', label: '重命名', action: () => this.renameFile(v.id, v.name) },
            { icon: '📂', label: '移动到…', action: () => this.moveFile(v.id) },
            { icon: '⬇️', label: '下载原文件', action: () => this.downloadFile(v.id) },
            { icon: '🗑️', label: '删除（进回收站）', danger: true, action: () => this.deleteFile(v.id) },
        );
        this._openMenu(e, items);
    },

    // 文档类 AI 总结查看（复用课程总结弹窗；/api/files/{id}/summary）
    async _viewDocSummary(id) {
        try {
            const resp = await apiFetch(`/api/files/${id}/summary`);
            const d = await resp.json().catch(() => ({}));
            if (!resp.ok || !d.content) { addSystemMessage('ℹ️ 总结尚未生成'); return; }
            document.getElementById('summaryBody').textContent = d.content;
            const dl = document.getElementById('summaryDownload');
            dl.onclick = () => {
                const blob = new Blob([d.content], { type: 'text/markdown;charset=utf-8' });
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `${d.name || 'file_' + id}-AI总结.md`;
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 1000);
            };
            document.getElementById('summaryModal').style.display = 'flex';
        } catch (e) { addSystemMessage(`⚠️ ${e.message}`); }
    },

    _genSummary(id, kind) {
        if (kind === 'video') {
            generateSummary(id);
            this.refresh();
        } else {
            this._reprocess(id);
        }
    },

    async _reprocess(id) {
        const resp = await apiFetch(`/api/files/${id}/reprocess`, { method: 'POST' });
        if (resp.ok) {
            addSystemMessage('🔁 已重新排队处理（转换→提取→总结）');
            this.refresh();
        } else {
            addSystemMessage(`⚠️ ${(await resp.json().catch(() => ({}))).detail || '失败'}`);
        }
    },

    // ===== 学习范围（沙箱）聚焦：锁定/解除（共识 Q4b） =====
    async setFocus(folderId, locked = true) {
        const convId = (typeof state !== 'undefined' && state.conversationId) || '';
        if (!convId) {
            addSystemMessage('ℹ️ 先发一条消息或新建对话，再锁定学习范围');
            return;
        }
        const resp = await apiFetch(
            `/api/chat/conversations/${convId}/focus?folder_id=${folderId}&locked=${locked ? 1 : 0}`,
            { method: 'POST' });
        if (!resp.ok) { addSystemMessage('⚠️ 设置学习范围失败'); return; }
        const d = await resp.json();
        if (typeof state !== 'undefined') {
            state.focusFolderId = d.focus_folder_id || 0;
            state.focusLocked = d.focus_locked || 0;
        }
        this.renderFocusChip(d.focus_folder_id, d.focus_folder_name, d.focus_locked);
        addSystemMessage(d.focus_locked
            ? `🎯 学习范围已锁定：${d.focus_folder_name}（AI 将聚焦该文件夹的全部资料）`
            : '🎯 已解除锁定，学习范围恢复为跟随当前文件');
    },

    async clearFocus() {
        const convId = (typeof state !== 'undefined' && state.conversationId) || '';
        if (!convId) return;
        const resp = await apiFetch(
            `/api/chat/conversations/${convId}/focus?folder_id=0&locked=0`, { method: 'POST' });
        if (resp.ok) {
            if (typeof state !== 'undefined') { state.focusFolderId = 0; state.focusLocked = 0; }
            this.renderFocusChip(0, '', 0);
            addSystemMessage('🎯 已解除锁定，学习范围恢复为跟随当前文件');
        }
    },

    renderFocusChip(folderId, name, locked) {
        const chip = document.getElementById('focusChip');
        const txt = document.getElementById('focusChipText');
        const btn = document.getElementById('focusBtn');
        if (!chip || !txt) return;
        const on = locked && folderId;
        chip.style.display = on ? 'inline-flex' : 'none';
        if (on) txt.textContent = '🎯 ' + (name || ('文件夹 ' + folderId));
        if (btn) btn.classList.toggle('active', !!on);
    },

    // 弹出文件夹选择（用现有对话框下拉，平铺）
    pickFocusFolder() {
        if (!this.cache) { addSystemMessage('⚠️ 文件树尚未加载'); return; }
        const opts = [];
        const walk = (fs, d) => {
            for (const f of fs) {
                const n = (f.files || []).length;
                opts.push(`<option value="${f.id}">${' '.repeat(d * 2)}${escHtml(f.name)}${n ? '（' + n + '）' : ''}</option>`);
                walk(f.children || [], d + 1);
            }
        };
        walk(this.cache.folders || [], 0);
        openDialog({
            title: '🎯 锁定学习范围',
            bodyHTML: '<label>选择要聚焦的文件夹</label>'
                + `<select id="dlgFocus">${opts.join('')}</select>`
                + '<div class="dlg-hint">锁定后 AI 只在这个文件夹范围内检索/阅读资料（复习整讲时用）；'
                + '不锁定时跟随你当前播放或查看的文件。</div>',
            onOk: () => {
                const sel = document.getElementById('dlgFocus');
                const fid = parseInt((sel || {}).value || '0', 10);
                if (fid) this.setFocus(fid, true);
                return true;
            },
        });
    },
};

window.FSTree = FSTree;
