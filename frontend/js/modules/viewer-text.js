// viewer-text.js — 文本 / Markdown 适配器（零依赖极简 md 渲染：标题/粗体/行内代码/代码块/列表）

export async function openText(url, kind, name) {
    const el = document.getElementById('viewerText');
    if (!el) return;
    el.textContent = '加载中…';
    const tk = window.getApiToken ? window.getApiToken() : '';
    try {
        const resp = await fetch(url + (url.includes('?') ? '&' : '?') + (tk ? `token=${encodeURIComponent(tk)}` : ''));
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        if (kind === 'markdown') {
            el.innerHTML = _miniMarkdown(text);
            el.classList.add('md-render');
        } else {
            el.textContent = text;
            el.classList.remove('md-render');
        }
    } catch (e) {
        el.textContent = `⚠️ 加载失败：${e.message}`;
    }
    const nameEl = document.getElementById('viewerTextName');
    if (nameEl) nameEl.textContent = name || '';
}

function _esc(t) {
    return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _inline(t) {
    return t
        .replace(/\$\$([\s\S]+?)\$\$/g, '$$$$')  // 块公式占位保留原文
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
}

function _miniMarkdown(src) {
    const lines = _esc(src).split('\n');
    let html = '', inCode = false, inList = false;
    for (const line of lines) {
        if (line.startsWith('```')) {
            if (inCode) { html += '</pre>'; inCode = false; }
            else { if (inList) { html += '</ul>'; inList = false; } html += '<pre class="md-code">'; inCode = true; }
            continue;
        }
        if (inCode) { html += line + '\n'; continue; }
        const h = line.match(/^(#{1,4})\s+(.*)/);
        if (h) {
            if (inList) { html += '</ul>'; inList = false; }
            const lv = Math.min(h[1].length + 2, 6);
            html += `<h${lv}>${_inline(h[2])}</h${lv}>`;
            continue;
        }
        if (/^\s*[-*]\s+/.test(line)) {
            if (!inList) { html += '<ul>'; inList = true; }
            html += `<li>${_inline(line.replace(/^\s*[-*]\s+/, ''))}</li>`;
            continue;
        }
        if (inList) { html += '</ul>'; inList = false; }
        if (!line.trim()) { html += '<div class="md-gap"></div>'; continue; }
        html += `<p>${_inline(line)}</p>`;
    }
    if (inList) html += '</ul>';
    if (inCode) html += '</pre>';
    return html;
}
