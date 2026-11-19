"""Bounded, opt-in loading diagnostics. Never record URLs, titles or tokens."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
import xbmc
import xbmcgui
import xbmcvfs

LOCK = threading.Lock()
ROOT = Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))


def enabled():
    value = xbmcgui.Window(10000).getProperty('NF.DebugTiming')
    if value:
        return value == 'true'
    try:
        return json.loads((ROOT / 'diagnostics.json').read_text(encoding='utf-8')).get('enabled') is True
    except (OSError, ValueError):
        return False


def record(component, operation, started, **metrics):
    duration = round((time.monotonic() - started) * 1000, 1)
    debug = enabled()
    if not debug and duration < 1500:
        return
    event = dict(at=time.strftime('%Y-%m-%d %H:%M:%S'), component=component,
                 operation=operation, ms=duration)
    event.update({key: value for key, value in metrics.items()
                  if isinstance(value, (int, float, bool)) or
                  key in ('hub', 'provider', 'status', 'profile')})
    line = json.dumps(event, ensure_ascii=True)
    xbmc.log('[NF-Timing] ' + line, xbmc.LOGINFO)
    if debug:
        try:
            with LOCK:
                ROOT.mkdir(parents=True, exist_ok=True)
                target = ROOT / 'loading-times.jsonl'
                if target.exists() and target.stat().st_size > 512 * 1024:
                    target.replace(ROOT / 'loading-times.previous.jsonl')
                with target.open('a', encoding='utf-8') as stream:
                    stream.write(line + '\n')
        except OSError:
            pass


@contextmanager
def span(component, operation, **metrics):
    start = time.monotonic()
    try:
        yield
    finally:
        record(component, operation, start, **metrics)


def report():
    totals = {}
    try:
        for line in (ROOT / 'loading-times.jsonl').read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
                key = row['component'] + ' / ' + row['operation']
                stats = totals.setdefault(key, [0, 0, 0])
                stats[0] += 1; stats[1] += row['ms']; stats[2] = max(stats[2], row['ms'])
            except (KeyError, ValueError, TypeError):
                continue
    except OSError:
        return 'Noch keine Messungen. Diagnose einschalten und die betroffenen Aktionen ausführen.'
    return '\n'.join('%s\n  %d Aufrufe · Ø %.0f ms · Maximum %.0f ms' %
                     (name, values[0], values[1] / values[0], values[2])
                     for name, values in sorted(totals.items(), key=lambda row: row[1][2], reverse=True))
