"""Vavoo-only LiveTV category cards. Provider remains the separate TV add-on."""
import json
import re
from pathlib import Path
import xbmcaddon
import xbmcvfs

DEFAULT_GROUPS = [
    ('tv_spielfilm_favoriten_hd','TV Spielfilm HD'), ('private','Private'),
    ('serien','Serien'), ('kids','Kids'), ('sky','Sky'),
    ('cinema_tv','Cinema TV'), ('cinedome','Cinedome'),
    ('sky_box','Sky Box Office'), ('bluetv','BlueTV'),
    ('popcorn','Popcorn TV'), ('sport','Sport'),
    ('bundesliga','Bundesliga'), ('dazn','DAZN'),
    ('pluto_tv','Pluto TV'), ('rakuten_tv','Rakuten TV'),
    ('doku','Doku'), ('news','News'), ('musik','Musik'),
]


def _groups():
    root=Path(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.vavoo.tv.plus/'))
    try:
        saved=json.loads((root/'groups.json').read_text(encoding='utf8'))
        groups=[]
        if isinstance(saved,list):
            for row in saved[:100]:
                if not isinstance(row,dict): continue
                stem=Path(str(row.get('file') or '')).name
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}\.m3u',stem,re.I):continue
                title=str(row.get('name') or '').strip()[:70]
                if title:groups.append((stem[:-4].lower(),title))
        return groups or DEFAULT_GROUPS
    except (OSError,TypeError,ValueError):
        return DEFAULT_GROUPS


def directory(_rpc=None, path='nf://nebelflix/tv'):
    if path not in ('nf://nebelflix/tv','nf://moviescout/germany'):
        return [], ''
    aid='plugin.video.nebelflix.tv'
    root='plugin://'+aid+'/?action='
    media='special://home/addons/'+aid+'/resources/media/categories/'
    cards=[{'Title':'Programmübersicht', 'Plot':'Programmführer von Vavoo TV Plus öffnen.',
            'Path':root+'guide','GuideOpen':True,'Folder':False,
            'Poster':media+'guide.png','DBType':'addon'}]
    known={s for s,_ in DEFAULT_GROUPS}
    for stem,name in _groups():
        slug='vavoo_'+stem
        icon=(media+slug+'.png' if stem in known else
              'special://profile/addon_data/plugin.video.vavoo.tv.plus/favo_icons/'+stem+'.png')
        cards.append({'Title':name,'Plot':'Vavoo-TV-Plus-Sendergruppe: '+name,
                      'Path':root+'list&group='+slug,'Folder':True,
                      'AddonBrowse':True,'DBType':'addon','Poster':icon,'Fanart':icon})
    return cards, ''
