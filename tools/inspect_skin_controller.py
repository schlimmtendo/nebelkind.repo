#!/usr/bin/env python3
"""Temporary CI diagnostic of the shipped Skin background widget controller."""
from pathlib import Path
from zipfile import ZipFile
import re
root=Path(__file__).resolve().parents[1]
skin=sorted((root/"packages/skin.nebelflix").glob("skin.nebelflix-*.zip"))[-1]
with ZipFile(skin) as z:
    raw=z.read("skin.nebelflix/resources/controller.py")
code=raw.decode("utf-8",errors="replace").replace("\r\n","\n")
lines=code.splitlines()
print("CONTROLLER LEN",len(lines),"ZIP",skin.name)
print("TOP LEVEL DEFINITIONS:")
for i,line in enumerate(lines):
    if re.match(r"^(def |class )",line):print(i+1,line.strip()[:180])
for needle in ["Background cache warm","prepare_started","pending_reset","artwork","ThreadPoolExecutor","futures","submit(","_warm","widget-cache","prepare_widgets","MONITOR.waitForAbort","NF.PrepareWidgets","AutoInstaller","mark_preload_complete","executor.shutdown","isPlaying"]:
    matches=[i for i,line in enumerate(lines) if needle.lower() in line.lower()]
    print("NEEDLE",repr(needle),"MATCHES",len(matches))
    for i in matches[:10]:
        print("---",needle,"line",i+1)
        for j in range(max(0,i-4),min(len(lines),i+7)):
            print("%04d %s"%(j+1,lines[j][:250]))
