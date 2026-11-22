"""Kodi folder UI for groups, channels and channel-specific schedule."""
import sys
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui
import xbmcplugin
from . import channels, epg, art
from .common import ASSETS, canonical, friendly_time, notify

ROOT='plugin://plugin.video.nebelflix.tv/'
ICON=str(ASSETS / 'icon.png')
CHANNEL_ICON=str(ASSETS / 'resources/media/channel-placeholder.png')
FANART=str(ASSETS / 'fanart.jpg')
CATEGORY_ASSETS=ASSETS / 'resources' / 'media' / 'categories'

def category_icon(slug):
    art=CATEGORY_ASSETS / (slug+'.png')
    return str(art) if art.is_file() else ICON



def url(action, **params):
    return ROOT+'?'+urlencode(dict(action=action,**params))


def item(label, path, folder, plot='', logo=None, playable=False, context=None):
    li=xbmcgui.ListItem(label=str(label))
    li.setArt({'thumb':logo or ICON,'icon':logo or ICON,'fanart':FANART})
    try:
        tag=li.getVideoInfoTag()
        tag.setTitle(str(label));tag.setPlot(str(plot))
    except (AttributeError,TypeError):
        li.setInfo('video',{'title':str(label),'plot':str(plot)})
    if playable:
        li.setProperty('IsPlayable','true')
        li.setContentLookup(False)
    if context:
        li.addContextMenuItems(context)
    return path,li,folder


def _finish(handle, entries, content='files'):
    xbmcplugin.addDirectoryItems(handle,entries,len(entries))
    xbmcplugin.setContent(handle,content)
    xbmcplugin.endOfDirectory(handle,cacheToDisc=False)


def root(handle):
    n=len(channels.group_catalog())
    entries=[
      item('📺 Programmführer · Jetzt und nächste Sendungen',url('guide'),False,
           'Programmführer von Vavoo TV Plus öffnen',logo=category_icon('guide')),
      item('🕘 Zuletzt gesehen (TV)',url('list',group='recent'),True,logo=category_icon('all')),
      item('★ TV-Favoriten',url('list',group='favorites'),True,logo=category_icon('all')),
      item('📂 Vavoo-Sendergruppen (%d)' % n,url('groups'),True,logo=category_icon('all')),
      item('🔎 Sender suchen',url('search'),True),
      item('⚙️ TV-Widgets & Vavoo verwalten',url('manage'),True),
    ]
    _finish(handle,entries)


def groups(handle):
    entries=[item('★ TV-Favoriten',url('list',group='favorites'),True,logo=category_icon('all')),
             item('🕘 Zuletzt gesehen (TV)',url('list',group='recent'),True,logo=category_icon('all'))]
    for slug,name in channels.group_catalog():
        entries.append(item(name,url('list',group=slug),True,logo=category_icon(slug)))
    _finish(handle,entries)


def list_channels(handle, group='all', search='', page=0):
    from . import load_timing
    started=time.monotonic()
    entries=[]
    fav=channels.favorites()
    has_epg=bool(epg.source_status())
    with epg.connect() as c:
        index=epg.guide_index(c)
        # Avoid a huge UI rebuild; large groups are split into 80-channel pages.
        items=channels.list_group(group,search)
        page=max(0,min(int(page),max(0,(len(items)-1)//80)))
        for ch in items[page*80:(page+1)*80]:
            info=epg.programme(ch,conn=c,index=index)
            now=info['now']; next_=info['next']
            nowstr=('Jetzt: '+now[2]+' (bis '+friendly_time(now[1])+')') if now else ('Kein EPG verfügbar' if has_epg else 'EPG-Daten werden vorbereitet')
            nextstr=(' · Danach: '+next_[2]) if next_ else ''
            label=('[COLOR gold]★[/COLOR] ' if ch['id'] in fav else '')+ch['name']
            icon=art.logo_for(ch,info)
            path=url('play',id=ch['id'])
            toggle=url('favorite',id=ch['id'],back=group)
            schedule=url('schedule',id=ch['id'])
            context=[('EPG für diesen Sender', 'Container.Update(%s)' % schedule),
                     ('TV-Favorit entfernen' if ch['id'] in fav else 'TV-Favorit hinzufügen',
                      'RunPlugin(%s)' % toggle)]
            entries.append(item(label,path,True,nowstr+nextstr,icon or CHANNEL_ICON,False,context))
    if page:
        entries.insert(0,item('← Vorherige Sender',url('list',group=group,search=search,page=page-1),True))
    if (page+1)*80<len(items):
        entries.append(item('Weitere Sender →',url('list',group=group,search=search,page=page+1),True))
    if not entries:
        entries=[item('Noch kein TV-Sender angesehen' if group == 'recent' else 'Noch keine Sender verfügbar',url('root') if group == 'recent' else url('manage'),True,
                    'Vavoo TV Plus öffnen und Senderliste aktualisieren')]
    _finish(handle,entries)
    load_timing.record('tv','channels.list',started,rows=len(entries))


def schedule(handle,ch):
    from .vavoo_bridge import open_guide
    open_guide(channels.find_channel(ch))
    if handle>=0:xbmcplugin.endOfDirectory(handle,succeeded=False)


def guide():
    from .vavoo_bridge import open_guide
    return open_guide()


def search(handle):
    q=xbmcgui.Dialog().input('Deutschen Sender suchen', type=xbmcgui.INPUT_ALPHANUM)
    list_channels(handle,search=q) if q.strip() else _finish(handle,[])
