# -*- coding: utf-8 -*-
"""把 build_480p 放到独立 PowerShell 进程跑，记录 PID 供查询/停止"""
import subprocess, os, sys, time
script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "build_480p.py")
log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "build_480p.log")
ps = (
    "Start-Process -FilePath 'python' -ArgumentList '%s' "
    "-WorkingDirectory '%s' "
    "-RedirectStandardOutput '%s' -RedirectStandardError '%s' "
    "-WindowStyle Hidden -PassThru | Select-Object -ExpandProperty Id"
    % (script, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), log, log + ".err")
)
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print("PID:", r.stdout.strip())
print("stderr:", r.stderr.strip())