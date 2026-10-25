// state.js — 文件中心全局状态（当前查看的文件 = agent 上下文锚点，共识 D5）
const FSState = {
    currentFile: null,          // {id, name, kind, status, status_msg}

    setCurrentFile(info) {
        this.currentFile = info || null;
        document.dispatchEvent(new CustomEvent('fs-file-changed', { detail: info }));
    },

    currentFile() { return this.currentFile; },

    currentFileId() { return this.currentFile ? this.currentFile.id : 0; },

    // 当前查看位置（chat 发送时随 file_id 一起传给后端编排器）
    currentPosition() {
        if (window.Viewer) return window.Viewer.currentPosition();
        return null;
    },
};
window.FSState = FSState;
