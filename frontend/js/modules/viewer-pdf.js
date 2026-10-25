// viewer-pdf.js — PDF 渲染适配器（pdf.js，本地 vendor，零构建）
// 丝滑要点：Web Worker 解析不卡主线程、按需渲染（IntersectionObserver 懒渲染）、
// 先占位后绘制、位置记忆（currentPage 供 agent 编排器定位）
import * as pdfjsLib from '../../vendor/pdfjs/pdf.min.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc =
    new URL('../../vendor/pdfjs/pdf.worker.min.mjs', import.meta.url).href;

let pdfDoc = null;
let observer = null;
let zoom = 1.0;           // 相对"适应宽度"的倍率（1.0 = 恰好撑满；>1 才会横向滚动）
let numPages = 0;
let currentPage = 1;
let container = null;
let renderSeq = 0;        // 关闭/切换后丢弃在途渲染
let currentFileId = 0;    // 位置记忆：按文件存"上次读到的页"

function _posKey() { return 'fs_pdf_pos_' + currentFileId; }

// 容器可用宽度 → 适配缩放比。留 40px 给滚动条与页面边距，避免横向溢出被裁
function _calcFitScale(scroll, baseWidth) {
    const cw = (scroll && scroll.clientWidth) ? scroll.clientWidth : 800;
    const usable = Math.max(240, cw - 40);
    return usable / Math.max(baseWidth, 1);
}

function _pane() { return document.getElementById('pdfPane'); }
function _scroll() { return document.getElementById('pdfScroll'); }
function _pageLabel() { return document.getElementById('pdfPageLabel'); }

export function isPdfOpen() { return !!pdfDoc; }

// ===== 页码跟踪：从滚动位置确定性计算，不依赖 IntersectionObserver =====
// 为什么不用 observer 定页码：它的 rootMargin 会让视口外几百 px 的页也算"相交"，
// 多页同时相交时谁后回调谁赢 → 页码随机漂移，上一页/下一页就会基于错误页码算目标，
// 表现为「点了没反应 / 跳错页」。
let _navGuardUntil = 0;   // 程序化跳转后的静默期（此期间滚动事件不改页码）
let _onScroll = null;
let _onResize = null;
let _resizeTimer = 0;     // 窗口尺寸变化 → 重算 fitScale 并重排（修复：打开后改窗口/初次测量偏小导致页面偏小居中）
let _fitScale = 1.0;      // 当前适配缩放（基准：容器宽 / 页面原始宽）
let _baseWidth = 0;       // 页面原始宽度（pt）

function _pageEls() {
    const sc = _scroll();
    return sc ? Array.from(sc.querySelectorAll('.pdf-page-wrap')) : [];
}

// 当前"阅读线"落在哪一页（1-based）；找不到返回 0
function _pageAtReadingLine() {
    const sc = _scroll();
    if (!sc) return 0;
    const els = _pageEls();
    if (!els.length) return 0;
    const sr = sc.getBoundingClientRect();
    const line = sr.top + Math.min(140, sr.height * 0.33);
    let found = 0;
    for (const el of els) {
        const r = el.getBoundingClientRect();
        const page = parseInt(el.dataset.page, 10);
        if (line >= r.top && line < r.bottom) return page;
        if (r.top > line) break;
        if (r.bottom <= line) found = page;
    }
    return found || 1;
}

// 把 currentPage 校正到真实位置（供按钮/外部查询使用）
function _syncCurrentFromScroll() {
    if (performance.now() < _navGuardUntil) return currentPage;
    const page = _pageAtReadingLine();
    if (page && page !== currentPage) {
        currentPage = page;
        _updateLabel();
        _persistPage();
    }
    return currentPage;
}

function _persistPage() {
    try { if (currentFileId) localStorage.setItem(_posKey(), String(currentPage)); } catch (e) { }
}

export async function openPdf(url, fileId = 0) {
    const pane = _pane();
    if (!pane) return;
    closePdf();
    currentFileId = fileId || 0;
    pane.style.display = 'flex';
    _scroll().innerHTML = '<div class="pdf-loading">📄 文档加载中…</div>';

    const tk = window.getApiToken ? window.getApiToken() : '';
    const full = url + (url.includes('?') ? '&' : '?') + (tk ? `token=${encodeURIComponent(tk)}` : '');
    const seq = ++renderSeq;
    try {
        pdfDoc = await pdfjsLib.getDocument(full).promise;
    } catch (e) {
        if (seq !== renderSeq) return;
        _scroll().innerHTML = `<div class="pdf-loading">⚠️ PDF 加载失败：${String(e && e.message || e)}</div>`;
        return;
    }
    if (seq !== renderSeq) { return; }
    numPages = pdfDoc.numPages;
    currentPage = 1;
    _updateLabel();

    // 占位 + 懒渲染
    const scroll = _scroll();
    scroll.innerHTML = '';
    // 先取第一页尺寸定占位宽高比（大文档无需逐页探测）
    const p1 = await pdfDoc.getPage(1);
    const base = p1.getViewport({ scale: 1 });
    // 等一帧让 pdfPane 完成布局，否则 clientWidth 可能是 0/旧值 → 页面比容器宽被截断
    await new Promise(r => requestAnimationFrame(() => r()));
    _baseWidth = base.width;
    _fitScale = _calcFitScale(scroll, base.width);
    const fitScale = _fitScale;
    const ph = Math.round(base.height * fitScale * zoom);

    // 窗口 resize → 重算适配并重排（防抖）
    if (_onResize) window.removeEventListener('resize', _onResize);
    _onResize = () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => relayout(), 200);
    };
    window.addEventListener('resize', _onResize);

    for (let i = 1; i <= numPages; i++) {
        const wrap = document.createElement('div');
        wrap.className = 'pdf-page-wrap';
        wrap.dataset.page = i;
        wrap.style.height = ph + 'px';
        wrap.innerHTML = `<div class="pdf-page-num">${i}</div>
            <canvas class="pdf-canvas" data-page="${i}"></canvas>
            <div class="pdf-page-loading">…</div>`;
        scroll.appendChild(wrap);
    }
    // 位置记忆：回到上次读到的页（ M5 交付）
    let savedPage = 0;
    try { savedPage = parseInt(localStorage.getItem(_posKey()) || '0', 10) || 0; } catch (e) {}
    if (savedPage > 1 && savedPage <= numPages) {
        setTimeout(() => gotoPage(savedPage), 60);
    }

    // 逐页真实高度修正 + 可见渲染
    observer = new IntersectionObserver(entries => {
        for (const en of entries) {
            const page = parseInt(en.target.dataset.page, 10);
            if (en.isIntersecting) {
                en.target.classList.add('in-view');
                _renderPage(page);   // 只做懒渲染（用全局 _fitScale）；页码由滚动位置计算
            }
        }
    }, { root: scroll, rootMargin: '600px 0px' });
    scroll.querySelectorAll('.pdf-page-wrap').forEach(w => observer.observe(w));

    // 滚动 → 校正页码（rAF 节流，避免高频 getBoundingClientRect）
    let ticking = false;
    _onScroll = () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(() => { ticking = false; _syncCurrentFromScroll(); });
    };
    scroll.addEventListener('scroll', _onScroll, { passive: true });
    currentPage = 1;
    _updateLabel();
}

async function _renderPage(pageNo) {
    const fitScale = _fitScale;
    const seq = renderSeq;
    if (!pdfDoc || pageNo < 1 || pageNo > numPages) return;
    const wrap = _scroll().querySelector(`.pdf-page-wrap[data-page="${pageNo}"]`);
    if (!wrap || wrap.dataset.rendered === '1') return;
    try {
        const page = await pdfDoc.getPage(pageNo);
        if (seq !== renderSeq) return;
        const viewport = page.getViewport({ scale: fitScale * zoom });
        const canvas = wrap.querySelector('canvas');
        const ctx = canvas.getContext('2d');
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(viewport.width * dpr);
        canvas.height = Math.floor(viewport.height * dpr);
        canvas.style.width = viewport.width + 'px';
        canvas.style.height = viewport.height + 'px';
        wrap.style.height = (viewport.height + 16) + 'px';
        await page.render({ canvasContext: ctx, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
        if (seq !== renderSeq) return;
        wrap.dataset.rendered = '1';
        // 文本层（可选可复制，划词问 AI 用）：--scale-factor 必须与渲染 scale 一致
        try {
            wrap.style.setProperty('--scale-factor', String(fitScale * zoom));
            const tl = document.createElement('div');
            tl.className = 'textLayer';
            wrap.appendChild(tl);
            const tc = await page.getTextContent();
            if (seq === renderSeq) {
                await new pdfjsLib.TextLayer({
                    textContentSource: tc, container: tl, viewport,
                }).render();
            }
        } catch (e) { /* 文本层失败不影响画面 */ }
        const loading = wrap.querySelector('.pdf-page-loading');
        if (loading) loading.remove();
    } catch (e) { /* 渲染失败静默（占位保留） */ }
}

export function zoomPdf(delta) {
    zoom = Math.min(3, Math.max(0.5, zoom + delta));
    relayout();
}

/** 重排：按当前容器宽度重算 fitScale，重置所有页占位与渲染状态，保留滚动比例 */
export function relayout() {
    if (!pdfDoc) return;
    const scroll = _scroll();
    if (!scroll) return;
    const keepRatio = scroll.scrollTop / Math.max(scroll.scrollHeight, 1);
    scroll.querySelectorAll('.pdf-page-wrap').forEach(w => {
        w.dataset.rendered = '';
        w.style.height = '';
        const loading = w.querySelector('.pdf-page-loading');
        if (!loading) {
            const tip = document.createElement('div');
            tip.className = 'pdf-page-loading';
            tip.textContent = '…';
            w.appendChild(tip);
        }
    });
    pdfDoc.getPage(1).then(p1 => {
        const base = p1.getViewport({ scale: 1 });
        _baseWidth = base.width;
        _fitScale = _calcFitScale(scroll, base.width);
        const fitScale = _fitScale;
        // 重建 observer（懒渲染用新 scale）
        if (observer) observer.disconnect();
        observer = new IntersectionObserver(entries => {
            for (const en of entries) {
                if (en.isIntersecting) {
                    _renderPage(parseInt(en.target.dataset.page, 10));
                }
            }
        }, { root: scroll, rootMargin: '600px 0px' });
        scroll.querySelectorAll('.pdf-page-wrap').forEach(w => observer.observe(w));
        scroll.scrollTop = keepRatio * scroll.scrollHeight;
    });
}

/** 恢复「适应宽度」（zoom=1.0） */
export function pdfFit() {
    zoom = 1.0;
    relayout();
}

export function gotoPage(page) {
    const sc = _scroll();
    if (!sc || !numPages) return;
    const target = Math.max(1, Math.min(numPages, Math.round(Number(page) || 1)));
    const wrap = sc.querySelector(`.pdf-page-wrap[data-page="${target}"]`);
    if (!wrap) return;

    // 立即把页码切到目标（不等滚动结束），连点也能正确累加
    const from = _syncCurrentFromScroll();
    currentPage = target;
    _updateLabel();

    const sr = sc.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    const top = sc.scrollTop + (wr.top - sr.top) - 8;
    const far = Math.abs(target - from) > 4;   // 远跳用瞬时，近翻用平滑
    try {
        sc.scrollTo({ top, behavior: far ? 'auto' : 'smooth' });
    } catch (e) {
        sc.scrollTop = top;
    }
    // 平滑滚动期间（约 400ms）不让滚动事件覆盖我们刚设的页码
    _navGuardUntil = performance.now() + (far ? 80 : 500);
    _persistPage();
}

export function pdfGoto(page) { gotoPage(page); }

export function pdfInfo() { return { numPages, currentPage: _syncCurrentFromScroll() }; }
export function pdfCurrentPage() { return _syncCurrentFromScroll(); }

export function stepPage(delta) {
    const sc = _scroll();
    if (!sc || !numPages) return false;
    const cur = _syncCurrentFromScroll();
    const target = cur + delta;
    if (target < 1 || target > numPages) {
        if (window.addSystemMessage) {
            window.addSystemMessage(target < 1 ? '📄 已经是第一页' : '📄 已经是最后一页');
        }
        return false;
    }
    gotoPage(target);
    return true;
}

export function closePdf() {
    renderSeq++;
    if (_onResize) { window.removeEventListener('resize', _onResize); _onResize = null; }
    if (_onScroll) {
        const sc = _scroll();
        if (sc) sc.removeEventListener('scroll', _onScroll);
        _onScroll = null;
    }
    if (observer) { observer.disconnect(); observer = null; }
    if (pdfDoc) { try { pdfDoc.destroy(); } catch (e) {} pdfDoc = null; }
    const pane = _pane();
    if (pane) { pane.style.display = 'none'; }
    if (_scroll()) _scroll().innerHTML = '';
    zoom = 1.0;
}

function _updateLabel() {
    if (_pageLabel()) _pageLabel().textContent = `${currentPage} / ${numPages}`;
}
