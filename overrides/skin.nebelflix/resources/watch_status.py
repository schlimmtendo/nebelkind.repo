"""Read-only shared watched/progress markers from Hybrid's authoritative DB."""
import sqlite3
from pathlib import Path
import threading

_CACHE={}
_LOCK=threading.RLock()


def _identity(row,media):
    ident=str(row.get('tmdb_id') or row.get('TMDB') or row.get('id') or '')
    imdb=str(row.get('imdb_id') or row.get('imdb') or '')
    token='tmdb:'+ident if ident.isdigit() else 'imdb:'+imdb if imdb.startswith('tt') else ''
    if not token:return None
    try:return media,token,int(row.get('season') or row.get('Season') or 0) if media=='episode' else 0,int(row.get('episode') or row.get('Episode') or 0) if media=='episode' else 0
    except (ValueError,TypeError):return None


def _value(row,legacy=False):
    done=bool(row.get('playcount') or 0) if legacy else bool(row.get('completed'))
    try:
        pos=float(row.get('resume_position' if legacy else 'position') or 0)
        total=float(row.get('total_time' if legacy else 'duration') or 0)
    except (ValueError,TypeError):pos=total=0
    percent=min(99,max(1,int(100*pos/total))) if pos>=10 and total>0 else 0
    return {'WatchState':'watched' if done else 'partial' if percent else '', 'WatchPercent':0 if done else percent}


def index(database):
    path=Path(database)
    try:
        stamp=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None for p in (path,Path(str(path)+'-wal')))
        if not path.is_file():return {}
    except OSError:return {}
    key=str(path.resolve())
    with _LOCK:
        if _CACHE.get(key,{}).get('stamp')==stamp:return _CACHE[key]['rows']
        result={};con=None
        try:
            con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.25);con.row_factory=sqlite3.Row
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('movie','episode'):
                if table not in tables:continue
                for raw in con.execute('SELECT * FROM '+table):
                    row=dict(raw);identity=_identity(row,table)
                    if identity:result[identity]=_value(row,True)
            if 'nebelflix_history' in tables:
                for raw in con.execute('SELECT * FROM nebelflix_history'):
                    row=dict(raw);identity=_identity(row,row['media'])
                    if identity:result[identity]=_value(row)
        except (sqlite3.Error,OSError):return _CACHE.get(key,{}).get('rows',{})
        finally:
            if con:con.close()
        _CACHE.clear();_CACHE[key]={'stamp':stamp,'rows':result}
        return result


def decorate(items,database):
    states=index(database);result=[]
    for item in items:
        media='episode' if item.get('DBType')=='episode' or item.get('Episode') or item.get('episode') else 'movie'
        if (item.get('Type')=='tv' or item.get('DBType')=='tvshow') and media!='episode':state={}
        else:state=states.get(_identity(item,media),{})
        result.append(dict(item,WatchState=state.get('WatchState',''),WatchPercent=state.get('WatchPercent',0)))
    return result


def bar_art(percent,directory):
    """Tiny local UI texture: exact saved percent, no network/image dependency."""
    import struct,zlib
    value=max(0,min(100,int(percent)))
    directory=Path(directory);target=directory/('progress-%03d.png'%value)
    try:
        with _LOCK:
            if target.is_file():return str(target)
            directory.mkdir(parents=True,exist_ok=True)
            width=270;height=6;filled=int(width*value/100)
            row=b'\x35\xd8\x75\xff'*filled+b'\xff\xff\xff\x80'*(width-filled)
            def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
            image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+row)*height))+chunk(b'IEND',b'')
            temporary=target.with_suffix('.tmp')
            try:temporary.write_bytes(image);temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
            return str(target)
    except OSError:return ''
