# -*- coding: utf-8 -*-
"""增量补漏转码：遍历 storage/videos，把缺失/损坏的输出补转（Python 驱动，中文文件名稳）"""
import os, subprocess, sys

SRC = os.path.join(os.path.dirname(__file__), '..', 'storage', 'videos')
OUT = os.path.join(os.path.dirname(__file__), '..', 'storage', 'videos_720p')

def ffmpeg_bin():
    for cand in (os.environ.get('FFMPEG', 'ffmpeg'), 'ffmpeg'):
        try:
            subprocess.run([cand, '-version'], capture_output=True)
            return cand
        except FileNotFoundError:
            continue
    sys.exit('ffmpeg 未找到')

FF = ffmpeg_bin()
done = skip = fail = 0
for root, dirs, files in os.walk(SRC):
    for fn in files:
        if not fn.lower().endswith('.mp4'):
            continue
        s = os.path.join(root, fn)
        rel = os.path.relpath(s, SRC)
        o = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(o), exist_ok=True)
        if os.path.exists(o) and os.path.getsize(o) > 0:
            skip += 1
            continue
        print('[%s] %s' % ('补转' if os.path.exists(s) else '转', rel), flush=True)
        cmd = [FF, '-y', '-v', 'error', '-i', s,
               '-vf', 'fps=15',
               '-c:v', 'libx264', '-crf', '27', '-preset', 'medium',
               '-maxrate', '500k', '-bufsize', '1000k',
               '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart',
               o]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            done += 1
            mb = os.path.getsize(o) / 1048576
            print('    OK %.0fMB -> %.0fMB' % (os.path.getsize(s) / 1048576, mb), flush=True)
        else:
            fail += 1
            print('    FAIL: %s' % r.stderr[-300:], flush=True)

print('===== 完成: 新转 %d / 跳过 %d / 失败 %d =====' % (done, skip, fail), flush=True)