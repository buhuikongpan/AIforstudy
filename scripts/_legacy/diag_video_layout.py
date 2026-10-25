# -*- coding: utf-8 -*-
"""无头实测：横屏视口 + 旋转态 + 播放中，测量 video/容器/视口矩形并截图"""
import asyncio, json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://127.0.0.1:8000/"

async def measure(page, tag):
    data = await page.evaluate("""(tag) => {
        const v = document.getElementById('videoPlayer');
        const w = document.getElementById('videoWrapper');
        const vc = document.querySelector('.video-center');
        function r(el){ if(!el) return null; const b=el.getBoundingClientRect();
            return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}; }
        const d = { tag: tag,
            viewport: {w: window.innerWidth, h: window.innerHeight},
            video: r(v), wrapper: r(w), videoCenter: r(vc),
            videoDuration: v ? v.duration : 0,
            videoPaused: v ? v.paused : null,
            rotateClass: document.documentElement.classList.contains('rotate-landscape'),
            videoCss: v ? {w: v.style.width, h: v.style.height, objectFit: getComputedStyle(v).objectFit,
                          maxW: getComputedStyle(v).maxWidth, maxH: getComputedStyle(v).maxHeight} : null };
        if (d.video && d.wrapper) {
            d.overflow = { right: d.video.x + d.video.w > d.wrapper.x + d.wrapper.w,
                           bottom: d.video.y + d.video.h > d.wrapper.y + d.wrapper.h,
                           left: d.video.x < d.wrapper.x, top: d.video.y < d.wrapper.y };
        }
        return d;
    }""", tag)
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return data

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=EDGE, headless=True, args=['--autoplay-policy=no-user-gesture-required'])
        ctx = await browser.new_context(viewport={'width': 844, 'height': 390}, device_scale_factor=1)
        page = await ctx.new_page()
        page.on('console', lambda m: print('[console]', m.text[:120]) if m.type == 'error' else None)
        page.on('pageerror', lambda e: print('[pageerror]', str(e)[:300]))
        await page.goto(URL, wait_until='networkidle')
        await page.wait_for_timeout(800)

        # 1) 基线：横屏视口、未旋转
        await measure(page, 'LANDSCAPE 初始 未旋转')

        # 2) 强制旋转态（绕过全屏 API 不确定性，直接模拟用户横屏旋转后的 class）
        await page.evaluate("() => document.documentElement.classList.add('rotate-landscape')")
        await measure(page, 'LANDSCAPE 旋转态(未播放)')

        # 3) 选第一个视频并播放
        await page.evaluate("() => { const el = document.querySelector('.tree-video'); if(el) el.click(); }")
        await page.wait_for_timeout(1500)
        await measure(page, 'LANDSCAPE 旋转态 播放中')

        # 4) 顺带测竖屏旋转态（iOS 降级场景）
        await page.set_viewport_size({'width': 375, 'height': 667})
        await page.wait_for_timeout(400)
        await measure(page, 'PORTRAIT 旋转态 播放中')

        await page.screenshot(path='storage/sample/diag.png', full_page=False) if False else None
        await page.screenshot(path='storage/sample/diag_portrait.png')
        await page.set_viewport_size({'width': 844, 'height': 390})
        await page.wait_for_timeout(300)
        await page.screenshot(path='storage/sample/diag_landscape.png')
        print('截图已保存: storage/sample/diag_*.png')
        await browser.close()

asyncio.run(main())