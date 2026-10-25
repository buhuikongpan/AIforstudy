// 用 Edge headless + CDP 截图 & 诊断 UI
// 用法: node scripts/screenshot_ui.mjs <token> <输出png> [动作表达式]
//   动作表达式：在页面里 await 执行的 async 表达式，返回值会被打印（RESULT）
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

const TOKEN = process.argv[2] || '';
const OUT = process.argv[3] || path.join(ROOT, 'data', 'ui_shot.png');
const ACTION = process.argv[4] || '';

const PORT = 9340 + Math.floor(Math.random() * 40);
const EDGE_CANDIDATES = [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const EDGE = EDGE_CANDIDATES.find(p => fs.existsSync(p));
if (!EDGE) { console.log('FAIL: 找不到 Edge'); process.exit(1); }

const profileDir = path.join(ROOT, 'data', `_edge_profile_${PORT}`);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const proc = spawn(EDGE, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--mute-audio',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profileDir}`,
    '--window-size=1600,1000',
    'about:blank',
], { stdio: 'ignore', detached: true });

let ws;
try {
    await sleep(2200);
    let targets;
    for (let i = 0; i < 25; i++) {
        try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (targets.length) break; } catch (e) { }
        await sleep(400);
    }
    const page = targets && targets.find(t => t.type === 'page');
    if (!page) { console.log('FAIL: 没有可用 page target'); process.exit(1); }

    ws = new WebSocket(page.webSocketDebuggerUrl);
    let msgId = 0;
    const pending = new Map();
    ws.addEventListener('message', (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    });
    await new Promise(r => ws.addEventListener('open', r));
    const send = (method, params = {}) => new Promise(res => {
        const id = ++msgId; pending.set(id, res);
        ws.send(JSON.stringify({ id, method, params }));
    });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.navigate', { url: 'http://127.0.0.1:8000/' });
    await sleep(2500);
    if (TOKEN) {
        await send('Runtime.evaluate', {
            expression: `localStorage.setItem('kaoyan_token', ${JSON.stringify(TOKEN)}); location.reload();`,
        });
        await sleep(4500);
    }
    if (ACTION) {
        const r = await send('Runtime.evaluate', { expression: ACTION, awaitPromise: true, returnByValue: true });
        if (r.result?.exceptionDetails) {
            console.log('ACTION ERROR:', JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails).slice(0, 300));
        }
        if (r.result?.result?.value !== undefined) {
            console.log('RESULT:', String(r.result.result.value));
        }
    }
    const st = await send('Runtime.evaluate', {
        expression: `JSON.stringify({
            diag: (document.getElementById('fsDiag')||{}).textContent || '',
            FSTree: typeof FSTree,
            treeLen: (document.getElementById('videoTreeBody')||{innerHTML:''}).innerHTML.length
        })`, returnByValue: true,
    });
    console.log('STATE:', st.result?.result?.value || '(无)');

    const shot = await send('Page.captureScreenshot', { format: 'png' });
    if (shot.result?.data) {
        fs.mkdirSync(path.dirname(OUT), { recursive: true });
        fs.writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
        console.log('SHOT:', OUT);
    } else {
        console.log('FAIL: 截图失败');
    }
} finally {
    try { ws && ws.close(); } catch (e) { }
    try { process.kill(-proc.pid); } catch (e) { try { proc.kill(); } catch (e2) { } }
    try { fs.rmSync(profileDir, { recursive: true, force: true }); } catch (e) { }
}
process.exit(0);
