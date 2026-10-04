"""Offline, script-free reports with explicit actions and escaped evidence."""
import html
import json
import shlex
from pathlib import Path
from .net import normalize
from .workflow import access_label, reported_size


def esc(value):
    return html.escape(str(value))


def human(value):
    labels = {
        'direct_file_observed; full transfer untested': 'Direct file found; full download not tested',
        'publisher_reports_restriction': 'Publisher reports restricted access',
        'media_metadata_retrieved': 'Media information retrieved',
        'extractor_reports_formats': 'Download formats reported; transfer not tested',
        'requires_media_backend': 'Use the media downloader',
        'provider_link': 'Listed by provider; not checked',
        'link_only; unverified': 'Link found; not checked',
        'unverified': 'Not checked', 'unknown': 'Unknown',
    }
    if isinstance(value, dict):
        return esc('; '.join(f'{key.replace("_", " ")}: {val}' for key, val in value.items()))
    return esc(labels.get(str(value), access_label(str(value))))


def link(url, label='Open source', button=False):
    try:
        url = normalize(url)
    except Exception:
        return esc(url)
    style = ' class="button"' if button else ''
    return f'<a{style} href="{esc(url)}" rel="noreferrer noopener" target="_blank">{esc(label)}</a>'


def render(data, output, source_report=None, proxy_dns=False):
    items = data.get('results', [data])
    is_search = 'results' in data
    checked = sum(bool(item.get('check')) for item in items) if is_search else 1
    available = sum(item.get('access') == 'file_bytes_observed' for item in items)
    providers = data.get('providers', [])
    failures = [p for p in providers if p['status'] == 'error']
    cards = []
    for number, item in enumerate(items, 1):
        check = item.get('check') or (item if not is_search else {})
        observed = check.get('access') == 'file_bytes_observed'
        files = []
        for file_number, file in enumerate(item.get('files', []), 1):
            if 'url' not in file:
                continue
            status = check.get('access', 'unknown') if file['url'] == check.get('url') else file.get('status', 'unverified')
            size = reported_size(file, check)
            files.append(f'<li><span class="file-number">File {file_number}</span> {link(file["url"], file.get("format", "File"))}<small>{human(status)}{(" · " + esc(size)) if size else ""}</small></li>')
        actions = link(item.get('url', ''), 'Open source', True)
        if observed:
            actions = link(check.get('url', item.get('url', '')), 'Open checked file', True) + ' ' + actions
        command = ''
        if source_report and is_search and (observed or len(item.get('files', [])) == 1):
            extension = {'pdf': '.pdf', 'text': '.txt', 'mp4': '.mp4', 'webm': '.webm', 'mp3': '.mp3', 'ogg': '.ogg', 'zip_container': '.zip'}.get(check.get('format'), '.bin')
            if check.get('mime', '').split(';')[0] == 'application/epub+zip':
                extension = '.epub'
            parts = ['./scout'] + (['--proxy-dns'] if proxy_dns else [])
            parts += ['download', '--from-report', str(Path(source_report).absolute()), '--result', str(number), '--out', f'downloads/result-{number}{extension}']
            command = f'<div class="command"><p>Download this result from the saved search</p><code>{esc(shlex.join(parts))}</code><small>Choose your output filename with --out. Existing files are preserved.</small></div>'
        elif source_report and len(item.get('files', [])) > 1:
            parts = ['./scout'] + (['--proxy-dns'] if proxy_dns else [])
            parts += ['download', '--from-report', str(Path(source_report).absolute()), '--result', str(number), '--file', '1', '--out', f'downloads/result-{number}.bin']
            command = f'<div class="command"><p>Choose a file to download</p><code>{esc(shlex.join(parts))}</code><small>This command selects File 1. Change --file and --out to choose another file and filename.</small></div>'
        authors = ', '.join(item.get('authors', []))
        error = check.get('error') or item.get('error')
        diagnostic = f'<p class="warning">Check incomplete: {esc(error)}</p>' if error else ''
        cards.append(f'''<article>
<div class="eyebrow">{'RESULT ' + str(number) + ' · ' if is_search else ''}{esc(item.get('provider', 'Inspection'))}</div>
<h2>{esc(item.get('title') or item.get('url', 'Result'))}</h2>
{f'<p class="muted authors">{esc(authors)}</p>' if authors else ''}
<p class="status {'observed' if observed else ''}">{human(item.get('access', 'unknown'))}</p>
<div class="actions">{actions}</div>{diagnostic}
<dl><dt>Download</dt><dd>{human(item.get('downloadability', 'unknown'))}</dd>
<dt>Identity</dt><dd>{esc(item.get('identity', 'Not verified'))}</dd>
<dt>Completeness</dt><dd>{human(item.get('completeness', 'unknown'))}</dd>
<dt>Rights</dt><dd>{human(item.get('rights', 'unknown'))}</dd></dl>
{('<h3>Available file links</h3><ul class="files">' + ''.join(files) + '</ul>') if files else ''}
{command}
<details><summary>Evidence and diagnostics</summary><pre>{esc(json.dumps(item, indent=2, ensure_ascii=False))}</pre></details>
</article>''')
    provider_rows = ''.join(f'<li><strong>{esc(p["name"])}</strong> — {esc(p.get("error", str(p.get("count", 0)) + " candidates"))} {esc(p.get("warning", ""))}</li>' for p in providers)
    source_status = f'<section class="sources"><h2>Sources checked</h2><ul>{provider_rows}</ul></section>' if providers else ''
    warning = f'<p class="warning">{len(failures)} {"source is" if len(failures) == 1 else "sources are"} unavailable. Available results are shown below.</p>' if failures and items else ''
    if not items:
        empty_title = 'Sources could not be reached' if providers and len(failures) == len(providers) else 'No matches in the sources checked'
        cards.append(f'<section class="empty"><h2>{empty_title}</h2><p>Try a shorter title, add the author, or choose another source with <code>--providers</code>. A failed source can be retried with <code>--fresh</code>.</p></section>')
    checks = data.get('checks_performed', checked)
    counts = f'<div class="metrics"><div><strong>{len(items)}</strong> {"candidate" if len(items) == 1 else "candidates"}</div><div><strong>{checks}</strong> {"check" if checks == 1 else "checks"}</div><div><strong>{available}</strong> {"file observed" if available == 1 else "files observed"}</div></div>' if is_search else ''
    title = data.get('query', data.get('title') or 'Resource inspection')
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(title)} · Thing-Getter</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f7;color:#182638;font:16px/1.55 system-ui,sans-serif;overflow-wrap:anywhere}}main{{max-width:1040px;margin:auto;padding:36px 22px}}header{{border-top:4px solid #087c70;padding:24px 0}}h1{{font-size:36px;line-height:1.18;letter-spacing:-.7px;margin:12px 0}}h2{{font-size:23px;line-height:1.3;margin:10px 0}}h3{{font-size:16px;margin-bottom:8px}}.eyebrow{{font-size:12px;letter-spacing:1.2px;text-transform:uppercase;color:#465b6a}}.muted,small{{color:#506474}}small{{display:block;margin-top:4px}}.authors{{margin-top:0}}.metrics{{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}}.metrics>div{{background:#e6eeed;padding:12px 18px;border-radius:8px}}.metrics strong{{font-size:24px;margin-right:6px}}article{{background:white;border:1px solid #d5dfe4;border-radius:12px;padding:26px;margin:20px 0}}a{{color:#076f6b}}a:focus-visible,summary:focus-visible{{outline:3px solid #cb8700;outline-offset:4px}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}}.button{{display:inline-block;border:1px solid #087c70;border-radius:6px;padding:9px 15px;text-decoration:none;font-weight:600}}.button:first-child{{background:#087166;color:white}}.status{{background:#edf1f4;padding:9px 13px;border-radius:6px}}.status.observed{{background:#ddf2e8;color:#14513c}}dl{{display:grid;grid-template-columns:130px minmax(0,1fr);gap:7px}}dt{{font-weight:600}}dd{{margin:0}}pre,code{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}}pre{{background:#f3f5f7;padding:16px}}summary{{cursor:pointer;color:#076f6b;font-weight:600;padding-top:12px}}.note{{color:#506474;max-width:80ch}}.warning{{background:#fff1d5;border-left:3px solid #bb7a12;padding:12px 16px}}.files{{padding-left:20px}}.files li{{padding:5px 0}}.file-number{{font-size:13px;margin-right:8px;color:#506474}}.command{{padding:14px 16px;background:#eef4f4;border-radius:6px;margin:18px 0}}.command p{{font-weight:600;margin:0 0 7px}}.sources{{margin:24px 0}}.sources h2{{font-size:18px}}.empty{{background:white;padding:24px;border:1px solid #d5dfe4;border-radius:10px}}@media(max-width:550px){{main{{padding:22px 16px}}article{{padding:20px}}h1{{font-size:29px}}dl{{grid-template-columns:1fr}}dd{{margin-bottom:8px}}.metrics{{gap:8px}}.metrics>div{{padding:9px 12px;font-size:13px}}.metrics strong{{font-size:20px}}}}
</style></head><body><main><header><div class="eyebrow">THING-GETTER · RESOURCE SCOUT</div><h1>{esc(title)}</h1><p class="muted">{esc(data.get('finished_at', data.get('checked_at', '')))}</p>{counts}<p class="note">Choose a result, review its access evidence, then open or download a file. File access does not establish the correct edition or completeness.</p></header>
{warning}{''.join(cards)}{source_status}</main></body></html>'''
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(document, encoding='utf-8')
