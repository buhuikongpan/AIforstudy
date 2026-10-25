// viewer-image.js — 图片适配器

export function openImage(url) {
    const img = document.getElementById('viewerImg');
    if (!img) return;
    const tk = window.getApiToken ? window.getApiToken() : '';
    img.src = url + (url.includes('?') ? '&' : '?') + (tk ? `token=${encodeURIComponent(tk)}` : '');
}
