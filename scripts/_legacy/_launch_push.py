# -*- coding: utf-8 -*-
"""后台启动 push_to_cloud，日志写 data/push_cloud.log"""
import subprocess, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if False else os.getcwd()
log = open(os.path.join(os.getcwd(), "data", "push_cloud.log"), "w", encoding="utf-8", errors="replace", buffering=1)
p = subprocess.Popen([sys.executable, "-u", os.path.join(os.getcwd(), "scripts", "push_to_cloud.py")],
                     stdout=log, stderr=subprocess.STDOUT, cwd=os.getcwd(), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
print(p.pid)
