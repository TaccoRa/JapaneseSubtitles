"""Nonblocking dictionary lookup shared by subtitle and popup result windows."""
import threading
import time
from utils import dispatch_to_tk


def active_marker(value):
    if isinstance(value, dict):
        return tuple(value.get(key) for key in ('base', 'lookup', 'line_index', 'char_start', 'char_end', 'sentence_lookup'))
    return value


def dictionary_layer_text(owner, region, layers):
    query = str(region.get('lookup') or region.get('base') or '').strip()
    entries = region.get('dictionary_entries') if 'dictionary_together' not in layers else None
    entries = tuple(entries or ((region.get('base') or query, query),))
    cache = getattr(owner, '_dictionary_result_cache', None)
    if cache is None:
        cache = owner._dictionary_result_cache = {}
    active = active_marker(getattr(owner, '_hover_active_region', None))
    target = (id(getattr(owner, '_cue_snapshot', None)), active, entries, tuple(layers))
    owner._dictionary_result_target = target
    widget = getattr(owner, 'canvas', None) or getattr(owner, '_popup', None)
    output = []
    for label, lookup_query in entries:
        if not lookup_query:
            continue
        expiry = getattr(owner, '_dictionary_error_expiry', {})
        if lookup_query in expiry and expiry[lookup_query] < time.monotonic():
            cache.pop(lookup_query, None)
            expiry.pop(lookup_query, None)
        if lookup_query not in cache:
            output.append(f'{label} — Looking up…')
            def worker(query=lookup_query):
                try:
                    callback = getattr(owner, '_dictionary_lookup', None)
                    if not callable(callback):
                        raise RuntimeError('Dictionary service unavailable')
                    value = str(callback(query) or '').strip()
                    text = value or f'No dictionary entry found for {query}'
                except Exception as exc:
                    text = f'Dictionary lookup failed for {query}: {exc}'
                def finish():
                    owner._dictionary_pending.discard((query, target))
                    cache[query] = text
                    if text.startswith('Dictionary lookup failed'):
                        if not hasattr(owner, '_dictionary_error_expiry'):
                            owner._dictionary_error_expiry = {}
                        owner._dictionary_error_expiry[query] = time.monotonic() + 3
                    if len(cache) > 256:
                        cache.pop(next(iter(cache)))
                    if (getattr(owner, '_dictionary_result_target', None) == target
                            and active_marker(getattr(owner, '_hover_active_region', None)) == active
                            and id(getattr(owner, '_cue_snapshot', None)) == target[0]):
                        owner._show_hover_layers(region, layers)
                dispatch_to_tk(widget, finish)
            # Each result refresh can encounter another outstanding query.
            pending = getattr(owner, '_dictionary_pending', None)
            if pending is None:
                pending = owner._dictionary_pending = set()
            marker = (lookup_query, target)
            if marker not in pending:
                pending.add(marker)
                threading.Thread(target=worker, daemon=True, name='dictionary-lookup').start()
        else:
            output.append(f'{label} — {cache[lookup_query]}')
    return '\n'.join(output) or 'No dictionary query available'


def retain_voice_results(owner, region, parts):
    if not str(getattr(owner, '_hover_active_mode', '')).startswith('voice'):
        return parts
    key = (id(getattr(owner, '_cue_snapshot', None)), region.get('base'), region.get('line_index'),
           region.get('char_start'), region.get('char_end'))
    if getattr(owner, '_voice_result_target', None) != key:
        owner._voice_result_parts = {}
        owner._voice_result_target = key
    stored = owner._voice_result_parts
    stored.update(dict(parts))
    return list(stored.items()) + [('action', 'Looking up…' if any('Looking up…' in value for _, value in parts) else 'Done')]


def copy_dictionary_text(owner, region):
    query = str(region.get('lookup') or region.get('base') or '').strip()
    cache = getattr(owner, '_dictionary_result_cache', {})
    if query not in cache:
        owner._show_hover_layers(region, ('dictionary',))
        return ''
    value = cache[query]
    return '' if value.startswith(('No dictionary entry found', 'Dictionary lookup failed')) else value
