"""Small persistent logo cache; network access belongs only to background jobs."""
import json
import time
from pathlib import Path

import xbmcvfs

ROOT=Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))/'logos'


def identity(item):
    media=item.get('Type');ident=str(item.get('TMDB') or '')
    return media+'-'+ident if media in ('movie','tv') and ident.isdigit() else ''


def cached(item):
    key=identity(item)
    if not key:return None
    try:
        entry=json.loads((ROOT/(key+'.json')).read_text(encoding='utf-8'))
        ttl=7*86400 if entry.get('logo') else 86400
        return entry if time.time()-entry.get('time',0)<ttl else None
    except (OSError,ValueError):return None


def resolve(item):
    entry=cached(item)
    if entry is not None:return entry.get('logo','')
    key=identity(item)
    if not key:return ''
    from multitrailer import tmdb
    import requests
    response=requests.get(tmdb.BASE+'/'+item['Type']+'/'+str(item['TMDB'])+'/images',
        params=dict(api_key=tmdb.DEFAULT_TMDB_KEY,include_image_language='de,en,null'),timeout=(2,4))
    response.raise_for_status()
    logos=[x for x in response.json().get('logos',[]) if x.get('file_path')
           and x.get('iso_639_1') in ('de','en',None)]
    logos.sort(key=lambda x:({'de':0,'en':1,None:2}[x.get('iso_639_1')],-float(x.get('vote_average') or 0),-int(x.get('width') or 0)))
    logo=tmdb.IMG+'/w500'+logos[0]['file_path'] if logos else ''
    ROOT.mkdir(parents=True,exist_ok=True)
    target=ROOT/(key+'.json');temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(logo=logo,time=time.time())),encoding='utf-8')
    temporary.replace(target)
    return logo
