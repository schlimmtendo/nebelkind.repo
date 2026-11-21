"""Fast local Kodi directory provider for controller-owned widget snapshots."""
import sys
from urllib.parse import parse_qs, urlencode, urlparse
import xbmcplugin
import widget_snapshot

handle = int(sys.argv[1])
params = parse_qs(sys.argv[2].lstrip('?'))
vavoo_mode = (params.get('vavoo') or [''])[0]
if params.get('direct', ['0'])[0] == '1':
    import direct_widgets
    direct_widgets.run(handle, params)
elif vavoo_mode in ('groups', 'group'):
    import xbmc
    import library
    import vavoo_live
    source = 'nf://vavoo/groups'
    if vavoo_mode == 'group':
        filename = (params.get('file') or [''])[0]
        source = 'nf://vavoo/group?' + urlencode({'file': filename})
    items, _ = vavoo_live.directory(source)
    xbmcplugin.setContent(handle, 'videos')
    xbmcplugin.setProperty(handle, 'NF.VavooGuide', 'true' if vavoo_mode == 'group' else '')
    if vavoo_mode == 'group':
        groups, _ = vavoo_live.directory('nf://vavoo/groups')
        selected_file = (params.get('file') or [''])[0]
        group = next((item for item in groups
                      if (parse_qs(urlparse(item['Path']).query).get('file') or [''])[0]
                      == selected_file), {})
        xbmcplugin.setProperty(handle, 'NF.VavooGroup', group.get('Title', 'TV-Programm'))
    for item in items:
        li = library.listitem(item)
        if item.get('EPGNext'):
            li.setProperty('NF.EPGNext', item['EPGNext'])
        if item.get('EPGProgress') is not None:
            li.setProperty('NF.EPGProgress', str(item['EPGProgress']))
        xbmcplugin.addDirectoryItem(handle, item.get('Path') or 'nf://empty', li,
                                    isFolder=bool(item.get('Folder')))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    if vavoo_mode == 'group':
        xbmc.executebuiltin('Container.SetViewMode(504)')
else:
    row = int(params.get('row', ['600'])[0])
    if not 600 <= row < 648:
        raise ValueError('Invalid widget row')
    page = (params.get('page') or [''])[0]
    if params.get('placeholder', ['0'])[0] == '1':
        image='special://skin/media/nebelflix/poster.png'
        snapshot=[dict(Title='Wird beim Anwählen geladen',Path=image,Poster=image,Fanart=image,Empty=True,Hub=page)]
    else:
        snapshot = widget_snapshot.read(page, row)
    is_continue = params.get('continue', ['0'])[0] == '1'
    is_top10 = params.get('top10', ['0'])[0] == '1'
    xbmcplugin.setContent(handle, 'videos')
    entries = [(item.get('Path') or 'nf://empty',
                widget_snapshot.listitem(item, rank, is_top10, is_continue),
                bool(item.get('Folder'))) for rank, item in enumerate(snapshot, 1)]
    import xbmcgui
    stale = bool(page and xbmcgui.Window(10000).getProperty('NF.Page') != page)
    if stale:
        entries = []
    xbmcplugin.addDirectoryItems(handle, entries, len(entries))
    xbmcplugin.endOfDirectory(handle, succeeded=not stale, cacheToDisc=False)
