// main.js — 文件中心前端模块入口（ES modules，零构建）
// 加载顺序即依赖顺序；全部挂到 window，供 index.html 内联 onclick 调用。
import './state.js';
import './viewer-pdf.js';       // pdf.js 适配器（viewer.js 内部 import）
import './viewer-image.js';
import './viewer-text.js';
import * as PlayerExtras from './viewer-media.js';
import './viewer.js';
import './tree.js';
import './orchestrator-ui.js';

// 播放器增强（黑边自适应 / 字幕 / 快捷键 / 进度记忆）
window.PlayerExtras = PlayerExtras;
if (PlayerExtras.initPlayerExtras) PlayerExtras.initPlayerExtras();

// 初始化资源管理器
if (window.FSTree) window.FSTree.init();

// 设置弹窗打开时顺带渲染「上下文编排 & 文件转换」区块（透明化层）
const _showSettings = window.showSettings;
if (_showSettings) {
    window.showSettings = function (...args) {
        if (window.OrchestratorUI) window.OrchestratorUI.renderSettings();
        return _showSettings.apply(this, args);
    };
}

console.log('[fs] 文件中心模块已加载');
// 诊断条只保留「报错」用途：模块加载失败时 index.html 的 window.onerror 会显示红字。
// 正常加载不再弹绿条（已确认工作稳定，避免每次刷新都出现）。
