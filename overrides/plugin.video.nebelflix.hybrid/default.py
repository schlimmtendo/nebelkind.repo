
# 2023-05-10
# edit 2026-08-09

import sys, json
import time
_entry_started=time.monotonic()

# Nebelkind installed-skin gate (not an activation check)
from resources.lib.nebelkind_skin_gate import require_skin as _nebelkind_require_skin
if not _nebelkind_require_skin(sys.argv):
    raise SystemExit(0)

from resources.lib import control

# Upgrade once: set source selection to the new list even on existing installations.
# Later user changes in the settings remain respected.
if control.getSetting("source.list.migrated") != "true":
    control.setSetting("hosts.mode", "1")
    control.setSetting("source.list.migrated", "true")

params = dict(control.parse_qsl(control.urlsplit(sys.argv[2]).query))

if params.get('action') == 'skinSearch':
    params.update(info='search', tmdb_type=params.get('media_type', 'movie'))
if params.get('info'):
    from resources.lib.skin_routes import route
    params = route(params)
    if params is None:
        sys.exit()

action = params.get('action')

# xShip als aktiv markieren (fuer Precacher: erst starten wenn xShip geoeffnet wurde)
import xbmcgui
xbmcgui.Window(10000).setProperty('xship.active', '1')

name = params.get('name')
table = params.get('table')
title = params.get('title')
source = params.get('source')

# ------ navigator --------------
if (action == None or action == 'root') and name==None:
    from resources.lib.indexers import navigator
    navigator.navigator().root()

elif action == 'pluginInfo':
    from resources.lib import supportinfo
    supportinfo.pluginInfo()

elif action == 'hybridSearch':
    from resources.lib.hybrid_search import search
    search(params)

elif action == 'skinMenu':
    from resources.lib.indexers import navigator
    navigator.navigator().skinMenu(params.get('tab', 'home'))

elif action == 'movieNavigator':
    from resources.lib.indexers import navigator
    navigator.navigator().movies()

elif action == 'tvNavigator':
    from resources.lib.indexers import navigator
    navigator.navigator().tvshows()

elif action == 'toolNavigator':
    from resources.lib.indexers import navigator
    navigator.navigator().tools()

# -------------------------------------------
elif action == 'mediaInfo':
    import xbmcgui
    dialog = xbmcgui.DialogProgress()
    dialog.create('Medien-Info', 'Löse Stream-URL auf... (20 Sek.)')
    dialog.update(0)
    from resources.lib import sources
    sources.sources().mediaInfo(source, dialog)

elif action == 'playExtern':
    import json
    if not control.visible(): control.busy()
    try:
        sysmeta = {}
        for key, value in params.items():
            if key == 'action': continue
            elif key == 'year' or key == 'season' or key == 'episode': value = int(value)
            if value == 0: continue
            sysmeta.update({key : value})
        if int(params.get('season')) == 0:
            mediatype = 'movie'
        else:
            mediatype = 'tvshow'
        sysmeta.update({'mediatype': mediatype})
        # if control.getSetting('hosts.mode') == '2':
        #     sysmeta.update({'select': '2'})
        # else:
        #     sysmeta.update({'select': '1'})
        override = control.getSetting('series.play.mode' if mediatype == 'tvshow' else 'movie.play.mode')
        selection = {'1': '0', '2': '1', '3': '2'}.get(override, control.getSetting('hosts.mode'))
        sysmeta.update({'select': selection})
        sysmeta = json.dumps(sysmeta)
        params.update({'sysmeta': sysmeta})
        from resources.lib import sources
        sources.sources().play(params)
    except:
        pass

elif action == 'clearCache':
    try:
        from resources.lib.requestHandler import cRequestHandler
        cRequestHandler('').clearCache()
        control.infoDialog('HTML-Cache geleert', icon='INFO', time=2000)
    except Exception as e:
        from resources.lib import log_utils
        log_utils.log('Cache leeren fehlgeschlagen: %s' % type(e).__name__, log_utils.LOGERROR)
        control.infoDialog('Cache konnte nicht geleert werden', icon='WARNING')

elif action == 'importXshipAccounts':
    from resources.lib.xship_accounts import import_accounts
    import_accounts()
    from resources.lib.indexers.navigator import navigator
    navigator()._endDirectory()

elif action == 'resetWatched':
    from resources.lib import hybrid_history
    import xbmc,xbmcgui
    dialog=xbmcgui.Dialog()
    if xbmc.Player().isPlayingVideo():
        dialog.ok('Gesehen-Status zurücksetzen','Bitte die Wiedergabe zuerst beenden.')
    elif dialog.yesno('Gesehen-Status zurücksetzen',
            'Alle Gesehen-Markierungen in Hybrid und im Skin entfernen? Angefangene Inhalte und ihre Fortsetzungspunkte bleiben erhalten. Vorher wird eine Sicherung erstellt.'):
        try:
            count,backup=hybrid_history.reset_watched_with_backup()
            xbmcgui.Window(10000).setProperty('NF.RefreshWidgets',str(__import__('time').time_ns()))
            xbmc.executebuiltin('Container.Refresh')
            dialog.ok('Gesehen-Status','Gesehen-Markierungen wurden zurückgesetzt. Weiterschauen und Merkliste bleiben erhalten.')
        except Exception as exc:
            xbmc.log('[Hybrid] Watched reset failed: '+type(exc).__name__,xbmc.LOGERROR)
            dialog.ok('Gesehen-Status','Zurücksetzen konnte nicht abgeschlossen werden. Bitte das Log prüfen.')

elif action == 'resetPlaybackStatus':
    from resources.lib import hybrid_history
    import xbmc, xbmcgui
    dialog = xbmcgui.Dialog()
    if xbmc.Player().isPlayingVideo():
        dialog.ok('Wiedergabestatus zurücksetzen', 'Bitte die Wiedergabe zuerst beenden.')
    elif dialog.yesno('Weiterschauen und Gesehen zurücksetzen?',
            'Alle Fortsetzungspunkte, Weiterschauen-Einträge und Gesehen-Markierungen in Hybrid und Nebelflix entfernen? Merkliste und Einstellungen bleiben erhalten. Vorher wird eine Sicherung erstellt.'):
        try:
            hybrid_history.clear_all_history_with_backup()
            xbmcgui.Window(10000).setProperty('NF.RefreshWidgets', str(__import__('time').time_ns()))
            xbmc.executebuiltin('Container.Refresh')
            xbmc.log('[Hybrid] Full playback status reset completed', xbmc.LOGINFO)
            dialog.ok('Wiedergabestatus zurückgesetzt', 'Weiterschauen, Fortsetzungspunkte und Gesehen-Markierungen wurden zurückgesetzt.')
        except Exception as exc:
            xbmc.log('[Hybrid] Full playback status reset failed: '+type(exc).__name__, xbmc.LOGERROR)
            dialog.ok('Zurücksetzen fehlgeschlagen', 'Die Bereinigung konnte nicht abgeschlossen werden. Bitte das Log prüfen.')

elif action == 'historyManager':
    from resources.lib import hybrid_history
    import xbmc, xbmcgui
    dialog = xbmcgui.Dialog()
    while True:
        rows = hybrid_history.entries(include_completed=True, limit=1000)
        legacy = hybrid_history.legacy_entries(limit=1000)
        keys = {(r['media'],r['identity'],r['season'],r['episode']) for r in rows}
        legacy = [r for r in legacy if (r['media'],r['identity'],r['season'],r['episode']) not in keys]
        options = ['Alles zurücksetzen: Gesehen und Weiterschauen (mit Sicherung)']
        for row in rows:
            label = '%s · %s' % ('Gesehen' if row['completed'] else 'Fortsetzen', row['title'])
            if row['media'] == 'episode':
                label += ' S%02dE%02d' % (row['season'], row['episode'])
            options.append(label)
        for row in legacy:
            label = 'Älterer Eintrag · ' + str(row['title'])
            if row['media'] == 'episode':
                label += ' S%02dE%02d' % (row['season'], row['episode'])
            options.append(label)
        choice = dialog.select('Hybrid – Wiedergabeverlauf', options)
        if choice < 0:
            break
        if xbmc.Player().isPlayingVideo():
            dialog.ok('Verlauf', 'Bitte die Wiedergabe vor dem Bereinigen beenden.')
            break
        if choice == 0:
            if dialog.yesno('Gesamten Wiedergabestatus löschen?',
                    'Alle Gesehen-Markierungen, Verlaufseinträge und Fortsetzungspunkte entfernen? '
                    'Vorher wird eine Datenbank-Sicherung erstellt. Favoriten bleiben erhalten.'):
                try:
                    count, old, backup = hybrid_history.clear_all_history_with_backup()
                    xbmc.executebuiltin('Container.Refresh')
                    dialog.ok('Bereinigung abgeschlossen', '%d neue und %d ältere Einträge bereinigt. '
                              'Datenbank-Backup: %s' % (count, old, backup))
                    xbmcgui.Window(10000).setProperty('NF.RefreshWidgets', str(__import__('time').time_ns()))
                except Exception as exc:
                    dialog.ok('Bereinigung fehlgeschlagen', str(exc))
            continue
        row = (rows + legacy)[choice - 1]
        if dialog.yesno('Nur diesen Eintrag entfernen?', row['title']):
            if row.get('legacy'):
                hybrid_history.clear_legacy_entry(row['media'], row['identity'], row['season'], row['episode'])
                hybrid_history.forget_bookmark(row.get('name'))
            else:
                hybrid_history.remove_with_bookmark(row['media'], row['identity'], row['season'], row['episode'])
            xbmcgui.Window(10000).setProperty('NF.RefreshWidgets', str(__import__('time').time_ns()))
    from resources.lib.indexers.navigator import navigator
    navigator()._endDirectory()

elif action == 'historyRemove':
    # Used by the context menu in the skin's Continue Watching widget.
    # Report the outcome to the caller: RunPlugin is asynchronous in Kodi.
    from resources.lib import hybrid_history
    import json as _json
    import xbmc
    media = params.get('media', '')
    identity = params.get('identity', '')
    token = params.get('request', '')
    success = False
    error = ''
    try:
        if xbmc.Player().isPlayingVideo():
            error = 'Wiedergabe erst beenden'
        else:
            success = hybrid_history.remove_with_bookmark(
                media, identity, params.get('season', '0'), params.get('episode', '0'))
            if not success:
                error = 'Eintrag nicht gefunden oder bereits entfernt'
    except Exception as exc:
        error = str(exc)
    window = xbmcgui.Window(10000)
    if success:
        window.setProperty('NF.RefreshWidgets', str(__import__('time').time_ns()))
    if token and len(token) <= 64:
        window.setProperty('NF.HistoryRemoveAck', _json.dumps({
            'request': token, 'success': success, 'error': error,
            'media': media, 'identity': identity,
        }))
    if not success:
        import xbmc
        xbmc.log('[Nebelflix Hybrid] Entfernen aus Weiterschauen: ' + error, xbmc.LOGWARNING)

elif action == 'movieScoutManager':
    from resources.lib.movie_scout.bridge import manager
    manager()

elif action == 'resolverSettings':
    import resolveurl as resolver
    resolver.display_settings()

elif action == 'domainCheck':
    # Manueller Domain-Check. check_domains() hat eine 90-Min-Sperre (fuer den Service-
    # Autocheck korrekt). Manuell soll er IMMER laufen -> Sperre durch Reset umgehen.
    try:
        control.busy()
        control.setSetting('lastDomainCheck', '0')
        import service
        service.check_domains(manual=True)
        control.idle()
        control.infoDialog('Domain Check abgeschlossen', icon='INFO', time=3000)
    except Exception:
        control.idle()
        control.infoDialog('Domain Check fehlgeschlagen', icon='WARNING', time=3000)

elif action == 'resolverUpdate':
    # Manueller ResolveURL-Update (zieht frischen Branch von Gujal00/ResolveURL).
    # busy()/idle() fuer visuelles Feedback waehrend GitHub-Call laeuft.
    # Notifications (erfolgreich / Fehler / kein Update) kommen aus updateManager selbst.
    try:
        control.busy()
        from resources.lib import updateManager
        updateManager.manualResolverUpdate()
        control.idle()
    except Exception:
        control.idle()
        control.infoDialog('Fehler bei ResolveURL Update.', icon='ERROR', sound=True)

elif action == 'playURL':
    try:
        import resolveurl
        import xbmcgui, xbmc
        #url = 'https://streamvid.net/embed-uhgo683xes41'
        #url = 'https://moflix-stream.click/v/gcd0aueegeia'
        url = xbmcgui.Dialog().input("URL Input")
        hmf = resolveurl.HostedMediaFile(url=url, include_disabled=True, include_universal=False)
        try:
            if hmf.valid_url(): url = hmf.resolve()
        except:
            pass
        item = xbmcgui.ListItem('URL-direkt')
        kodiver = int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])
        # Header zuerst abtrennen, maxsplit=1 (Fix ggue 0.9er .split('|')).
        strhdr = None
        if '|' in url:
            url, strhdr = url.split('|', 1)
        # HLS UND DASH komplett ueber InputStream Adaptive (wie 0.9er).
        # ISA-Auto-Install passiert beim Kodi-Start im service.py, nicht hier.
        if ".m3u" in url or '.mpd' in url:
            item.setProperty("inputstream", "inputstream.adaptive")
            item.setProperty('inputstream.adaptive.config', '{"ssl_verify_peer":false}')
            if '.mpd' in url:
                if kodiver < 21: item.setProperty('inputstream.adaptive.manifest_type', 'mpd')
                item.setMimeType('application/dash+xml')
            else:
                if kodiver < 21: item.setProperty('inputstream.adaptive.manifest_type', 'hls')
                item.setMimeType('application/x-mpegURL')
            item.setContentLookup(False)
            if strhdr:
                item.setProperty('inputstream.adaptive.stream_headers', strhdr)
                if kodiver > 19: item.setProperty('inputstream.adaptive.manifest_headers', strhdr)
        else:
            # Sonstige (mp4 etc.): Header ggf. wieder anhaengen, sonst clean durchreichen.
            if strhdr:
                url = url + '|' + strhdr
        item.setPath(url)
        xbmc.Player().play(url, item)
    except:
        #print('Kein Video Link gefunden')
        control.infoDialog("Keinen Video Link gefunden", sound=True, icon='WARNING', time=1000)

elif action == 'playTrailer':
    if not control.visible(): control.busy()
    try:
        from resources.lib.trailer import playTrailer
        playTrailer(
            tmdb_id   = params.get('tmdb_id', ''),
            mediatype = params.get('mediatype', 'movie'),
            title     = params.get('title', ''),
            year      = params.get('year', ''),
            poster    = params.get('poster', ''),
            pref_lang = 'de',
            season    = params.get('season', None),
        )
        if control.visible(): control.idle()
    except Exception:
        if control.visible(): control.idle()
        control.infoDialog('Trailer-Suche fehlgeschlagen', sound=True, icon='WARNING')

elif action == 'UpdatePlayCount':
    from resources.lib import playcountDB
    playcountDB.UpdatePlaycount(params)
    control.execute('Container.Refresh')

# listings -------------------------------
elif action == 'listings':
    from resources.lib.indexers import listings
    listings.listings().get(params)

elif action == 'movieYears':
    from resources.lib.indexers import listings
    listings.listings().movieYears()

elif action == 'movieYearsByDecade':
    from resources.lib.indexers import listings
    listings.listings().movieYearsByDecade(params)

elif action == 'movieGenres':
    from resources.lib.indexers import listings
    listings.listings().movieGenres()

elif action == 'tvGenres':
    from resources.lib.indexers import listings
    listings.listings().tvGenres()

elif action == 'movieProviders':
    from resources.lib.indexers import listings
    listings.listings().movieProviders()

elif action == 'tvProviders':
    from resources.lib.indexers import listings
    listings.listings().tvProviders()

elif action == 'tvYears':
    from resources.lib.indexers import listings
    listings.listings().tvYears()

elif action == 'tvYearsByDecade':
    from resources.lib.indexers import listings
    listings.listings().tvYearsByDecade(params)

# search ----------------------
elif action == 'searchNew':
    from resources.lib import searchDB
    searchDB.search_new(table)

elif action == 'searchClear':
    from resources.lib import searchDB
    searchDB.remove_all_query(table)
    # if len(searchDB.getSearchTerms()) == 0:
    #     control.execute('Action(ParentDir)')

elif action == 'searchDelTerm':
    from resources.lib import searchDB
    searchDB.remove_query(name, table)
    # if len(searchDB.getSearchTerms()) == 0:
    #     control.execute('Action(ParentDir)')

# person ----------------------
elif action == 'person':
    from resources.lib.indexers import person
    person.person().get(params)

elif action == 'personSearch':
    from resources.lib.indexers import person
    person.person().search()

elif action == 'personCredits':
    from resources.lib.indexers import person
    person.person().getCredits(params)

elif action == 'playfromPerson':
    if not control.visible(): control.busy()
    sysmeta = json.loads(params['sysmeta'])
    if sysmeta['mediatype'] == 'movie':
        from resources.lib.indexers import movies
        sysmeta = movies.movies().super_meta(sysmeta['tmdb_id'])
        sysmeta = json.dumps(sysmeta)
    else:
        from resources.lib.indexers import tvshows
        sysmeta = tvshows.tvshows().super_meta(sysmeta['tmdb_id'])
        sysmeta = control.quote_plus(json.dumps(sysmeta))

    params.update({'sysmeta': sysmeta})
    from resources.lib import sources
    sources.sources().play(params)

# similar movies ----------------------
elif action == 'similarMovies':
    from resources.lib.indexers import similar
    similar.similar().showCategories(params)

elif action == 'similarByTmdb':
    from resources.lib.indexers import similar
    similar.similar().byTmdbRecommendations(params)

elif action == 'similarByDirector':
    from resources.lib.indexers import similar
    similar.similar().byDirector(params)

elif action == 'similarByStudio':
    from resources.lib.indexers import similar
    similar.similar().byStudio(params)

elif action == 'similarByCast':
    from resources.lib.indexers import similar
    similar.similar().byCast(params)

elif action == 'similarByCastYear':
    from resources.lib.indexers import similar
    similar.similar().byCastYear(params)

elif action == 'similarByGenreYear':
    from resources.lib.indexers import similar
    similar.similar().byGenreYear(params)

elif action == 'similarDirectorList':
    from resources.lib.indexers import similar
    similar.similar().showDirectorList(params)

elif action == 'similarStudioList':
    from resources.lib.indexers import similar
    similar.similar().showStudioList(params)

elif action == 'similarCastList':
    from resources.lib.indexers import similar
    similar.similar().showCastList(params)

elif action == 'similarGenreYearSelect':
    from resources.lib.indexers import similar
    similar.similar().showYearList(params)

# movies ----------------------
elif action == 'movies':
    from resources.lib.indexers import movies
    movies.movies().get(params)

elif action == 'moviesSearch':
    from resources.lib.indexers import movies
    movies.movies().search()

# tvshows ---------------------------------
elif action == 'tvshows': # 'tvshowPage'
    from resources.lib.indexers import tvshows
    tvshows.tvshows().get(params)

elif action == 'tvshowsSearch':
    from resources.lib.indexers import tvshows
    tvshows.tvshows().search()

# seasons ---------------------------------
elif action == 'seasons':
    from resources.lib.indexers import seasons
    seasons.seasons().get(params)  # params

# episodes ---------------------------------
elif action == 'episodes':
    from resources.lib.indexers import episodes
    episodes.episodes().get(params)

# sources ---------------------------------
elif action == 'play':
    from resources.lib import sources
    from resources.lib import load_timing
    load_timing.record('hybrid','playback.prepare',_entry_started)
    sources.sources().play(params)

elif action == 'addItem':
    from resources.lib import sources
    sources.sources().addItem(title)

elif action == 'playItem':
    if not control.visible(): control.busy()
    from resources.lib import sources
    sources.sources().playItem(title, source)

# Settings ------------------------------
elif action == "settings":  # alle Quellen aktivieren / deaktivieren
    from resources import settings
    settings.run(params)

elif action == 'addonSettings':
    # query = None
    query = params.get('query')
    control.openSettings(query)

elif action == 'resetSettings':
    status = control.resetSettings()
    from resources.lib.control import infoDialog
    if status:
        infoDialog("Vorgang erfolgreich abgeschlossen", sound=False, icon='INFO', time=1000)
    else:
        infoDialog("Vorgang mit Fehler beendet", sound=True, icon='ERROR', time=1000)

elif action == 'resolverSettings':
    import resolveurl as resolver
    resolver.display_settings()

# try:
#     import pydevd
#     if pydevd.connected: pydevd.kill_all_pydev_threads()
# except:
#     pass
# finally:
#     exit()
