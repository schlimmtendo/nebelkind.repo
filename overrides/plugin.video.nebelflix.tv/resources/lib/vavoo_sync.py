"""Quiet adapter to Vavoo's installed group synchronizer, with identical modes."""
import importlib
import hashlib
from pathlib import Path
import xbmc
from .vavoo_runtime import PREFIX


def sync_favorite_groups(group_file='',force_reload=False,mode='quick',*,
                         addon_name,api_module,group_favs,get_active_countries,
                         append_internal_log,background=True,monitor=None):
    if mode not in ('quick','defects'):raise ValueError('Unsupported automatic mode')
    def canceled():
        return monitor.abortRequested() or xbmc.getCondVisibility('Player.HasVideo') or xbmc.getCondVisibility('System.HasModalDialog')
    native=importlib.import_module(PREFIX+'.favorites.repair_engine')
    catalog={}
    for country in get_active_countries(api_module.get_available_countries()):
        if canceled():return False
        for name,urls in native._repair_catalog(api_module,country,force_reload).items():
            entries=catalog.setdefault(name,[])
            for url in urls if isinstance(urls,list) else [urls]:
                item={'url':url,'country':str(country),'quality':''}
                if url and item not in entries:entries.append(item)
    if not catalog:return False
    groups=group_favs.load_groups()
    if group_file:groups=[g for g in groups if g.get('file')==group_file]
    for index,group in enumerate(groups,1):
        if canceled():return False
        filename=group['file']
        if Path(filename).name!=filename or not filename.lower().endswith('.m3u'):return False
        path=Path(group_favs._group_m3u_path(filename))
        if not path.resolve().is_relative_to(Path(group_favs._profile_dir()).resolve()):return False
        initial=hashlib.sha256(path.read_bytes()).digest()
        def guarded_save(name,rows):
            if hashlib.sha256(path.read_bytes()).digest()!=initial:return False
            return group_favs.save_group_channels(name,rows)
        engine=importlib.import_module(PREFIX+'.favorites.sync_engine')
        stats=engine.sync_group_channels(filename,catalog,api_module=api_module,
            max_candidates=0 if mode=='quick' else 3,validation_timeout=4,
            validate_streams=mode!='quick',defective_only=mode=='defects',
            progress_callback=lambda info:not canceled(),group_index=index,group_total=len(groups),
            load_group_channels=group_favs.load_group_channels,
            load_source_meta=group_favs.load_source_meta,source_meta_key=group_favs._source_meta_key,
            save_source_meta=group_favs.save_source_meta,profile_dir=group_favs._profile_dir,
            group_m3u_path=group_favs._group_m3u_path,save_group_channels=guarded_save)
        if stats.get('canceled') or stats.get('error'):return False
    append_internal_log('Favoritenpflege '+mode+' abgeschlossen')
    return True
