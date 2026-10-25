// viewer-media.js — 音视频适配器：播放器增强（黑边自适应 / 字幕显示 / 快捷键 / 进度记忆）
// 视频链路（字幕/倍速/总结）全部保留，只是窗格管理交给 viewer.js

export function openMedia(videoId) {
    if (window.playVideo) window.playVideo(videoId);
}

export function stopMedia() {
    const v = document.getElementById('videoPlayer');
    if (v) { try { v.pause(); } catch (e) { } }
    const a = document.getElementById('viewerAudio');
    if (a) { try { a.pause(); } catch (e) { } }
}

// ================= ① 消除上下黑边：容器高度贴合视频宽高比 =================
export function fitVideoAspect() {
    const wrap = document.getElementById('videoWrapper');
    const v = document.getElementById('videoPlayer');
    if (!wrap || !v || !v.videoWidth || !v.videoHeight) return;
    const ar = v.videoWidth / v.videoHeight;
    wrap.style.setProperty('--video-ar', String(ar));
    wrap.classList.add('adaptive-aspect');
}

// ================= ② 字幕显示优化：字号 / 背景 / 显示开关 =================
const SUB_CFG_KEY = 'kaoyan_subtitle_cfg';
const SUB_DEFAULTS = { fontSize: 19, opacity: 0.62, visible: true };
const SUB_STEPS = [14, 16, 18, 19, 22, 26, 30];

export function loadSubtitleCfg() {
    try {
        const raw = localStorage.getItem(SUB_CFG_KEY);
        if (raw) return { ...SUB_DEFAULTS, ...JSON.parse(raw) };
    } catch (e) { }
    return { ...SUB_DEFAULTS };
}

export function saveSubtitleCfg(cfg) {
    try { localStorage.setItem(SUB_CFG_KEY, JSON.stringify(cfg)); } catch (e) { }
}

export function applySubtitleStyle() {
    const cfg = loadSubtitleCfg();
    const overlay = document.getElementById('videoSubtitleOverlay');
    if (!overlay) return;
    overlay.style.fontSize = cfg.fontSize + 'px';
    overlay.style.background = `rgba(0, 0, 0, ${cfg.opacity})`;
    overlay.style.display = cfg.visible ? '' : 'none';
    const btn = document.getElementById('subtitleOverlayBtn');
    if (btn) btn.style.opacity = cfg.visible ? '1' : '.45';
}

export function subtitleCfgStep(field, dir) {
    const cfg = loadSubtitleCfg();
    if (field === 'fontSize') {
        const i = SUB_STEPS.indexOf(cfg.fontSize);
        const next = SUB_STEPS[Math.max(0, Math.min(SUB_STEPS.length - 1,
            (i === -1 ? 3 : i) + dir))];
        cfg.fontSize = next;
    } else if (field === 'opacity') {
        cfg.opacity = Math.max(0, Math.min(0.95, Math.round((cfg.opacity + dir * 0.15) * 100) / 100));
    }
    saveSubtitleCfg(cfg);
    applySubtitleStyle();
    if (window.addSystemMessage) {
        window.addSystemMessage(field === 'fontSize'
            ? `🔤 字幕字号：${cfg.fontSize}px`
            : `🎚️ 字幕背景：${Math.round(cfg.opacity * 100)}%`);
    }
}

export function toggleSubtitleVisible() {
    const cfg = loadSubtitleCfg();
    cfg.visible = !cfg.visible;
    saveSubtitleCfg(cfg);
    applySubtitleStyle();
    if (window.addSystemMessage) {
        window.addSystemMessage(cfg.visible ? '📄 字幕已显示' : '📄 字幕已隐藏');
    }
}

// ================= 📷 一键截「当前查看内容」到聊天输入框（共识 Q4） =================
// 视频 → 当前帧（canvas 抓帧）；PDF → 当前页渲染图（/page-image 端点，带缓存复用）；
// 截到的图走既有 state.images 通道（🖼️ 上传同款），随下一条消息一起发给 AI。
export async function captureCurrentView() {
    const tell = (m) => { if (window.addSystemMessage) window.addSystemMessage(m); };

    // ---- 视频当前帧 ----
    const videoPane = document.getElementById('videoPane');
    const v = document.getElementById('videoPlayer');
    if (videoPane && videoPane.style.display !== 'none' && v && v.currentSrc && v.videoWidth > 0) {
        const c = document.createElement('canvas');
        c.width = v.videoWidth;
        c.height = v.videoHeight;
        c.getContext('2d').drawImage(v, 0, 0);
        const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.85));
        const t = Math.round(v.currentTime || 0);
        const name = `视频帧_${String(Math.floor(t / 60)).padStart(2, '0')}${String(t % 60).padStart(2, '0')}.jpg`;
        if (window.addImages) window.addImages([new File([blob], name, { type: 'image/jpeg' })]);
        tell('📷 已截取视频当前帧，随下一条消息发送');
        return true;
    }

    // ---- PDF 当前页（office/pdf 都走 derived.pdf/原文件渲染） ----
    const pdfPane = document.getElementById('pdfPane');
    const fid = window.FSState ? window.FSState.currentFileId() : 0;
    if (pdfPane && pdfPane.style.display !== 'none' && fid) {
        const pos = window.Viewer ? window.Viewer.currentPosition() : null;
        const page = (pos && pos.page) || 1;
        const tk = window.getApiToken ? window.getApiToken() : '';
        const url = `/api/files/${fid}/page-image?page=${page}` + (tk ? `&token=${encodeURIComponent(tk)}` : '');
        const resp = await fetch(url);
        if (!resp.ok) { tell('⚠️ 页面截图失败（HTTP ' + resp.status + '）'); return false; }
        const blob = await resp.blob();
        const name = `第${page}页截图.jpg`;
        if (window.addImages) window.addImages([new File([blob], name, { type: 'image/jpeg' })]);
        tell(`📷 已截取 PDF 第 ${page} 页，随下一条消息发送`);
        return true;
    }

    tell('ℹ️ 当前没有可截图的内容（打开视频或 PDF 后可用）');
    return false;
}

// ================= ③ 快捷键 + ④ 进度记忆增强 =================
const PROGRESS_PREFIX = 'kaoyan_progress_';

function setVol(delta) {
    const el = document.getElementById('videoPlayer');
    if (!el) return;
    el.volume = Math.max(0, Math.min(1, el.volume + delta));
    const slider = document.getElementById('vcVolume');
    if (slider) slider.value = String(Math.round(el.volume * 100));
}

function seekBy(sec) {
    const v = document.getElementById('videoPlayer');
    if (!v || !isFinite(v.duration)) return;
    v.currentTime = Math.max(0, Math.min(v.duration - 0.5, v.currentTime + sec));
    if (window.addSystemMessage) window.addSystemMessage(`⏩ ${sec > 0 ? '+' : ''}${sec}s`);
}

function toggleFullscreen() {
    if (window.toggleFullscreen) window.toggleFullscreen();
}

export function initPlayerExtras() {
    const v = document.getElementById('videoPlayer');
    if (!v || v.dataset.extrasReady === '1') return;
    v.dataset.extrasReady = '1';

    // 黑边自适应
    v.addEventListener('loadedmetadata', fitVideoAspect);
    fitVideoAspect();

    // 字幕样式应用
    applySubtitleStyle();

    // 进度记忆：更勤地落盘（每 3 秒 + 页面隐藏/卸载时）
    let tick = 0;
    v.addEventListener('timeupdate', () => {
        const id = window.FSState ? window.FSState.currentFileId() : 0;
        if (!id) return;
        if (++tick % 3 !== 0) return;
        try { localStorage.setItem(PROGRESS_PREFIX + id, String(v.currentTime)); } catch (e) { }
    });
    const flush = () => {
        const id = window.FSState ? window.FSState.currentFileId() : 0;
        if (id && isFinite(v.currentTime)) {
            try { localStorage.setItem(PROGRESS_PREFIX + id, String(v.currentTime)); } catch (e) { }
        }
    };
    // 暂停 / 拖动结束 时立刻落盘：暂停状态 timeupdate 不再触发，只靠它会把进度停在旧值
    v.addEventListener('pause', flush);
    v.addEventListener('seeked', flush);
    v.addEventListener('ended', () => {
        const id = window.FSState ? window.FSState.currentFileId() : 0;
        if (id) { try { localStorage.removeItem(PROGRESS_PREFIX + id); } catch (e) { } }
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });
    window.addEventListener('beforeunload', flush);

    // 快捷键：只在没有输入焦点、且视频窗格可见时生效
    document.addEventListener('keydown', (e) => {
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        const pane = document.getElementById('videoPane');
        if (!pane || pane.style.display === 'none') return;
        if (!v.currentSrc) return;
        switch (e.key) {
            case ' ':
            case 'k':
            case 'K':
                e.preventDefault();
                if (v.paused) v.play().catch(() => { }); else v.pause();
                break;
            case 'ArrowLeft':
                e.preventDefault(); seekBy(-5); break;
            case 'ArrowRight':
                e.preventDefault(); seekBy(5); break;
            case 'j':
            case 'J':
                e.preventDefault(); seekBy(-10); break;
            case 'l':
            case 'L':
                e.preventDefault(); seekBy(10); break;
            case 'ArrowUp':
                e.preventDefault(); setVol(0.1); break;
            case 'ArrowDown':
                e.preventDefault(); setVol(-0.1); break;
            case 'm':
            case 'M':
                e.preventDefault();
                v.muted = !v.muted;
                if (window.addSystemMessage) window.addSystemMessage(v.muted ? '🔇 静音' : '🔊 已取消静音');
                break;
            case 'f':
            case 'F':
                e.preventDefault(); toggleFullscreen(); break;
            case '[':
                e.preventDefault(); subtitleCfgStep('fontSize', -1); break;
            case ']':
                e.preventDefault(); subtitleCfgStep('fontSize', 1); break;
            case 's':
            case 'S':
                e.preventDefault(); toggleSubtitleVisible(); break;
        }
        // 数字键 0-9：跳到 0%~90%
        if (/^[0-9]$/.test(e.key) && isFinite(v.duration)) {
            e.preventDefault();
            v.currentTime = v.duration * (parseInt(e.key, 10) / 10);
        }
    });
}
