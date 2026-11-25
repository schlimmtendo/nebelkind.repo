"""Sequential quality fallback; a started or user-stopped player ends the queue."""
from .quality_policy import order_sources


def run(items,resolve,start,canceled,report=lambda *a:None):
    ordered=order_sources(items,'0')
    for index,source in enumerate(ordered,1):
        if canceled():return 'cancelled'
        report(index,len(ordered),'resolve')
        try:url=resolve(source)
        except TimeoutError:return 'timeout'  # Never pile jobs onto a still-running resolver.
        except Exception:
            report(index,len(ordered),'resolve_failed');continue
        if canceled():return 'cancelled'
        if not url:
            report(index,len(ordered),'resolve_failed');continue
        try:result=start(url,index)
        except Exception:
            # An unknown player exception is not evidence that nothing started.
            return 'player_error'
        if result.get('started'):return 'played'
        if result.get('stopped') or canceled():return 'cancelled'
        if not result.get('failed'):return 'cancelled'
        report(index,len(ordered),'start_failed')
    return 'exhausted'
