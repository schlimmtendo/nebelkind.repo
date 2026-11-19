# -*- coding: utf-8 -*-
"""Legacy update notifier disabled.

NebelFlix Skin now owns update checks and installation, strictly AFTER its
widget preload and a fresh Kodi start. The Hybrid service imports this function
for compatibility with older service.py installations. It must return quickly:
never block Hybrid's service thread, never perform background update checks.
"""

def run_startup_check():
    return None

