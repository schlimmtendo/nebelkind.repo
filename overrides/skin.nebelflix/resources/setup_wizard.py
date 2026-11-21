"""Descriptive fullscreen setup pages with native remote-friendly buttons."""
import time
import xbmc


def choose(home,monitor,step,title,body,options):
    home.setProperty('NF.SetupStep',str(step))
    home.setProperty('NF.SetupTitle',title)
    home.setProperty('NF.SetupBody',body)
    home.setProperty('NF.SetupCount',str(len(options)))
    for index in range(3):home.setProperty('NF.SetupOption'+str(index),options[index] if index<len(options) else '')
    home.setProperty('NF.SetupChoice','');home.setProperty('NF.SetupClosed','')
    xbmc.executebuiltin('ActivateWindow(1195)')
    xbmc.executebuiltin('SetFocus(1900)')
    while not monitor.waitForAbort(0.1):
        home.setProperty('NF.Heartbeat',str(time.time()))
        if home.getProperty('NF.SetupClosed')=='true':return None
        value=home.getProperty('NF.SetupChoice')
        if value.isdigit() and 0<=int(value)<len(options):return int(value)
    return None


def run(home,monitor,existing=False):
    try:
        first=choose(home,monitor,1,'Dein Nebelflix wird vorbereitet',
            ('Deine vorhandenen Reiter, Widget-Zuordnungen, Listen und dein Verlauf bleiben erhalten.' if existing else
             'Die Nebelflix-Vorlage richtet deine Startseite und die Bereiche Filme, Serien und Live TV ein.')+
            '[CR][CR]1. Leistung einstellen[CR]2. Inhalte aller Bereiche speichern[CR]3. Kodi einmal neu starten'+
            '[CR][CR]Während dieser Einrichtung werden im Hintergrund keine Widget-Reihen geladen.',
            ['Weiter zur Leistungseinstellung'])
        if first is None:return None
        profile=choose(home,monitor,2,'Passende Leistung für dein Gerät',
            'Automatisch: Nebelflix erkennt das Gerät und begrenzt gleichzeitige Anfragen und Bildgrößen.'+
            '[CR][CR]Sparsam: geeignet für Fire TV und Geräte mit wenig Arbeitsspeicher.'+
            '[CR][CR]Beibehalten: deine bisherige Leistungseinstellung wird weiter verwendet.',
            ['Automatisch erkennen (empfohlen)','Sparsam – Fire TV / wenig Arbeitsspeicher','Bisherige Leistungseinstellung behalten'])
        if profile is None:return None
        android=xbmc.getCondVisibility('System.Platform.Android')
        last=choose(home,monitor,3,'Jetzt werden alle Bereiche gespeichert',
            'Nebelflix lädt die konfigurierten Widget-Quellen aller Hubs und speichert die Ergebnisse auf diesem Gerät.'+
            '[CR][CR]Ein Fortschrittsbalken zeigt, welche Reihe gerade vorbereitet wird. Dies kann beim ersten Mal einige Minuten dauern.'+
            '[CR][CR]'+('Nach Abschluss bitte Kodi einmal beenden und erneut öffnen.' if android else
             'Nach erfolgreichem Abschluss startet Kodi einmal automatisch neu.')+
            '[CR][CR]Danach erscheinen gespeicherte Inhalte ohne erneute Quellensuche. Neue Inhalte werden später im Hintergrund aktualisiert.',
            ['Vorbereitung starten'])
        return profile if last is not None else None
    finally:xbmc.executebuiltin('Dialog.Close(1195)')
