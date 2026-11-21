"""Lightweight URL/configuration contract shared by startup and providers."""
import hashlib
import json
from urllib.parse import urlencode

ROWS_PER_VIEW=48
VIEWS_PER_HUB=1
WINDOW_BASE=1111
STATIC_TILE_LIMIT=22
STATIC_TILE_PROPERTIES=('NF.Data','NF.Hub','NF.Clearlogo','NF.RankArt','NFTV.Category',
                        'NF.EPG','NF.EPGNext','NF.EPGProgress','tmdb_id','tmdb_type')


def topbar_id(slot):
    return 200+slot if slot<6 else 230+slot


def window_id(slot,segment=0):
    return 10000 if slot==0 and segment==0 else WINDOW_BASE+slot*VIEWS_PER_HUB+segment

METADATA_FIELDS = ('Title','Plot','Year','Rating','Genres','Duration','Fanart',
    'Clearlogo','Path','TMDB','Type','DBType','Season','Episode','Poster',
    'EPG','EPGNext','EPGProgress','Channel')


def request_key(page,row,source,offset=0):
    return hashlib.sha256(json.dumps([page,row,source,offset]).encode('utf-8')).hexdigest()[:20]


def url(page,row,source,top10=False,refresh='',offset=0,back=False,request=''):
    return 'plugin://skin.nebelflix/?'+urlencode(dict(direct='1',page=page,row=row,source=source,
        top10='1' if top10 else '0',refresh=refresh,offset=offset,back='1' if back else '0',
        request=request or request_key(page,row,source,offset)))


def signature(tab,segment=0):
    return json.dumps(dict(tab=tab,view=4,segment=segment),ensure_ascii=False,sort_keys=True)


def widget_ttl(path):
    if path.startswith(('nf://nebelflix/tv','nf://moviescout/germany')):return 300
    if path.startswith('plugin://plugin.video.nebelflix.hybrid/'):return 300
    if path.startswith(('nf://addons/','nf://provider/')):return 300
    if path.startswith(('nf://random/','nf://genre/')):return 21600
    if path.startswith('nf://vavoo/'):return 180
    if path.startswith(('nf://continue','nf://mylist')):return 0
    if path.startswith('nf://'):return 300
    return 1800
