"""Share the existing bounded search pool between both provider engines."""


def fair_provider_order(providers):
    # Preserve configured priority within each engine. Do not leave all remote
    # providers behind native requests which may occupy the whole deadline.
    native = sorted((p for p in providers if not p[0].startswith('MS-')),
                    key=lambda p: int(p[2]))
    scout = sorted((p for p in providers if p[0].startswith('MS-')),
                   key=lambda p: int(p[2]))
    result = []
    for index in range(max(len(native), len(scout))):
        if index < len(native):
            result.append(native[index])
        if index < len(scout):
            result.append(scout[index])
    return result


def provider_label(provider):
    provider = str(provider or 'Unbekannt')
    return 'MovieScout · ' + provider[3:] if provider.startswith('MS-') else provider
