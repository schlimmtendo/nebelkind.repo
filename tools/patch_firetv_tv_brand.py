#!/usr/bin/env python3
"""Bound Fire TV background load and replace NebelFlix TV Estuary branding.

Runs after build_overrides and guide navigation patch. Fail on source drift.
Does not change auto-updater, widget catalog, EPG or playback URLs.
"""
import io, json, os, tempfile
from pathlib import Path
from zipfile import ZipFile
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
SKIN='skin.nebelflix'
TV='plugin.video.nebelflix.tv'

def once(source, old, new, label):
    count=source.count(old)
    if count!=1:
        raise ValueError('%s: expected one anchor; found %d' % (label,count))
    return source.replace(old,new,1)

def artwork(source):
    if 'self.max_pending = 18 if self.low_power else 42' in source:
        return source
    source=once(source, '        self.pool = ThreadPoolExecutor(max_workers=2)',
        '''        # Keep Kodi's UI responsive on Android/Fire TV.
        try:
            import xbmc
            self.low_power = bool(xbmc.getCondVisibility('System.Platform.Android'))
        except (ImportError, AttributeError, RuntimeError):
            self.low_power = False
        self.pool = ThreadPoolExecutor(max_workers=1 if self.low_power else 2)
        self.max_pending = 18 if self.low_power else 42
        self.max_per_batch = 5 if self.low_power else 10''', 'artwork pool')
    source=once(source,
        '''    def prepare(self, items):
        for item in items:
            for field in ('Fanart', 'Poster', 'Clearlogo'):
                url = item.get(field) or ''
                if not url.startswith(('http://', 'https://')):
                    continue
                if self.local(url) != url or url in self.attempted:
                    continue
                self.attempted.add(url)
                self.jobs[url] = self.pool.submit(self.download, url)
''',
        '''    def prepare(self, items):
        # Bounded prefetch: Kodi continues rendering URLs via its own cache.
        submitted = 0
        for item in items:
            for field in ('Poster', 'Fanart', 'Clearlogo'):
                if len(self.jobs) >= self.max_pending or submitted >= self.max_per_batch:
                    return
                url = item.get(field) or ''
                if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
                    continue
                if self.local(url) != url or url in self.attempted:
                    continue
                self.attempted.add(url)
                self.jobs[url] = self.pool.submit(self.download, url)
                submitted += 1
''', 'artwork job cap')
    compile(source,'artwork.py','exec')
    return source

def library(source):
    if 'self.persisted_snapshots = {}' in source and 'self.max_logo_pending = 8 if self.low_power else 24' in source:
        return source
    source=once(source, '        self.logo_pool = ThreadPoolExecutor(max_workers=3)',
        '''        self.low_power = bool(xbmc.getCondVisibility('System.Platform.Android'))
        self.logo_pool = ThreadPoolExecutor(max_workers=2 if self.low_power else 3)
        self.max_logo_pending = 8 if self.low_power else 24''', 'logo pool')
    source=once(source, "        self.snapshots = {}\n        self.artwork = Artwork(ROOT / 'widget-artwork')",
        "        self.snapshots = {}\n        self.persisted_snapshots = {}\n        self.artwork = Artwork(ROOT / 'widget-artwork')", 'snapshot tracking')
    source=once(source,
        '''        for path in planned:
            self.prepare_artwork(self.cache.get(path, {}).get('items', []))
        self.warm_scheduled = True''',
        '''        # Cached data is immediately available; downloading artwork for all
        # background tabs here would block the home screen on Fire TV.
        # Visible rows request their own artwork in publish().
        self.warm_scheduled = True''', 'cached background image flood')
    source=once(source,
        '''                elif key not in self.logo_jobs:
                    self.logo_jobs[key] = self.logo_pool.submit(self.load_logo, key)''',
        '''                elif key not in self.logo_jobs and len(self.logo_jobs) < self.max_logo_pending:
                    self.logo_jobs[key] = self.logo_pool.submit(self.load_logo, key)''',
        'logo cap')
    source=once(source,
        '''        self.snapshots[cid] = items
        write(row_snapshot_name(self.page, cid), items)
        state = self.rows.get(cid, {})''',
        '''        self.snapshots[cid] = items
        snapshot_name = row_snapshot_name(self.page, cid)
        previous = self.persisted_snapshots.get(snapshot_name)
        if previous is None:
            previous = read(snapshot_name, None)
        if previous != items or not (ROOT / snapshot_name).is_file():
            write(snapshot_name, items)
        self.persisted_snapshots[snapshot_name] = items
        state = self.rows.get(cid, {})''', 'duplicate disk writes')
    compile(source,'library.py','exec')
    return source

def tv_guide(s):
    if "channel-placeholder.png" in s: return s
    return once(s,
        "li.setArt({'thumb':img or str(ASSETS/'icon.png'),'icon':img or str(ASSETS/'icon.png')})",
        "li.setArt({'thumb':img or str(ASSETS/'resources/media/channel-placeholder.png'),'icon':img or str(ASSETS/'resources/media/channel-placeholder.png')})",
        'guide fallback')

def tv_playback(s):
    if "channel-placeholder.png" in s:return s
    return once(s, "logo=art.logo_for(ch,info) or str(ASSETS/'icon.png')",
        "logo=art.logo_for(ch,info) or str(ASSETS/'resources/media/channel-placeholder.png')",
        'playing channel fallback')

def tv_ui(s):
    if "CHANNEL_ICON=str(ASSETS" in s and "icon or CHANNEL_ICON" in s:return s
    s=once(s, "ICON=str(ASSETS / 'icon.png')",
        "ICON=str(ASSETS / 'icon.png')\nCHANNEL_ICON=str(ASSETS / 'resources/media/channel-placeholder.png')",
        'TV neutral icon')
    s=once(s, 'entries.append(item(label,path,True,nowstr+nextstr,icon,False,context))',
        'entries.append(item(label,path,True,nowstr+nextstr,icon or CHANNEL_ICON,False,context))',
        'station list fallback')
    s=once(s, "'Wiedergabe über '+channel['source'],channel.get('logo'),True)",
        "'Wiedergabe über '+channel['source'],channel.get('logo') or CHANNEL_ICON,True)",
        'schedule play icon')
    s=once(s, "url('play',id=channel['id']),True,desc,channel.get('logo'),False))",
        "url('play',id=channel['id']),True,desc,channel.get('logo') or CHANNEL_ICON,False))",
        'schedule listings fallback')
    compile(s,'ui.py','exec')
    return s

def font(size):
    try:return ImageFont.truetype('DejaVuSans-Bold.ttf',size)
    except OSError:return ImageFont.load_default()

def encode(image):
    b=io.BytesIO()
    image.save(b,format='PNG',optimize=True)
    return b.getvalue()

def branded_icon(data):
    # Preserve NebelFlix's own NF graphic while distinguishing the TV add-on.
    im=Image.open(io.BytesIO(data)).convert('RGBA').resize((256,256),Image.Resampling.LANCZOS)
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((89,197,168,249),radius=12,fill=(190,18,28,255))
    d.text((128,222),'TV',font=font(34),fill='white',anchor='mm')
    return encode(im.convert('RGB'))

def neutral_channel():
    im=Image.new('RGB',(192,192),(23,28,35))
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((22,41,170,139),radius=16,outline=(124,137,151),width=6)
    d.line((90,142,90,157),fill=(124,137,151),width=5)
    d.line((67,160,113,160),fill=(124,137,151),width=5)
    d.text((96,91),'?',font=font(65),fill=(180,190,202),anchor='mm')
    return encode(im)

def update_zip(path,changes):
    fd,tmp=tempfile.mkstemp(prefix='neb-patched-',suffix='.zip',dir=path.parent)
    os.close(fd)
    try:
        with ZipFile(path) as original, ZipFile(tmp,'w') as out:
            if original.testzip() is not None:raise ValueError('Damaged source ZIP')
            for entry in original.infolist():
                data=original.read(entry.filename)
                fn=changes.pop(entry.filename,None)
                if fn: data=fn(data)
                out.writestr(entry,data)
            for name,fn in changes.items():
                out.writestr(name,fn(b''))
        with ZipFile(tmp) as checked:
            if checked.testzip() is not None:raise ValueError('Damaged patched ZIP')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    plan=json.loads((ROOT/'release_versions.json').read_text(encoding='utf8'))
    if plan.get(SKIN)!='0.5.82' or plan.get(TV)!='0.2.3':
        print('Fire TV and branding patch not scheduled; skipping')
        return
    skin=ROOT/'packages'/SKIN/'skin.nebelflix-0.5.82.zip'
    tv=ROOT/'packages'/TV/'plugin.video.nebelflix.tv-0.2.3.zip'
    with ZipFile(skin) as z:
        brand=z.read(SKIN+'/resources/nebelflix-icon.png')
    def python(patch):
        return lambda content: patch(content.decode('utf8').replace('\r\n','\n')).encode('utf8')
    update_zip(skin, {
        SKIN+'/resources/artwork.py':python(artwork),
        SKIN+'/resources/library.py':python(library),
    })
    update_zip(tv, {
        TV+'/icon.png':lambda _:branded_icon(brand),
        TV+'/resources/media/channel-placeholder.png':lambda _:neutral_channel(),
        TV+'/resources/lib/guide.py':python(tv_guide),
        TV+'/resources/lib/ui.py':python(tv_ui),
        TV+'/resources/lib/playback.py':python(tv_playback),
    })
    print('Patched Fire TV load and NebelFlix TV graphics safely')

if __name__=='__main__':main()
