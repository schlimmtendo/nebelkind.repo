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

print("WIDGETS PACKAGE CANDIDATES:")
with ZipFile(skin) as z:
    candidate_files=[n for n in z.namelist() if n.endswith(".py") and any(x in n.lower() for x in ("widget", "cache"))]
    print(candidate_files[:40])
    for name in candidate_files:
        try:
            body=z.read(name).decode("utf-8").replace("\\r\\n","\\n")
        except Exception:
            continue
        if "class Widgets" not in body and "def prepare_warm" not in body:
            continue
        widget_lines=body.splitlines()
        print("WIDGET MODULE",name,len(widget_lines))
        for i,line in enumerate(widget_lines):
            if re.match(r"^\\s*(def|class) (tick|close|shutdown|load|start|prepare_warm|warm_status|flush_cache|stop|Widgets|Widget|warm|cancel|reset)\\b",line):
                print("METHOD",i+1,line[:160])
        for needle in ["warm_active", "warm_paths", "self.active", "self.jobs", "executor", "pool", "self.queue", "self.start", "def tick", "def stop", "def close"]:
            ids=[i for i,line in enumerate(widget_lines) if needle in line]
            print("WIDGET NEEDLE",needle,"COUNT",len(ids))
            for i in ids[:8]:
                print("AROUND",i+1, " | ".join(str(j+1)+":"+widget_lines[j][:140] for j in range(max(0,i-3),min(len(widget_lines),i+5))))
print("CONTROLLER CORE LOOP 860-1077:")
for i in range(859,min(1078,len(lines))):
    print("%04d %s"%(i+1,lines[i][:240]))
