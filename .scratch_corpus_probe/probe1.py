import os, sys, subprocess, zipfile, tarfile, io, json

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
TAR = r"C:\Windows\system32\tar.exe"

def show(p):
    print("=" * 70)
    print(p, os.path.getsize(p))

# 1. raw magic of each archive
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if not os.path.isfile(p):
        continue
    ext = os.path.splitext(name)[1].lower()
    if ext in (".rar", ".zip", ".tar", ".epub"):
        with open(p, "rb") as f:
            head = f.read(16)
        print(f"{ext:6} {os.path.getsize(p):>10} {head.hex(' ')}  {name}")
