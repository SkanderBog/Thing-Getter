"""Human-readable output and explicit selection from saved search evidence."""
import json
import re
from pathlib import Path
from .net import ScoutError, normalize


def plain(value):
    # External catalogue text must not control the user's terminal.
    return ' '.join(re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(value)).split())


def access_label(value):
    return {
        'file_bytes_observed': 'File checked; full download not yet tested',
        'catalogue_only': 'Catalogue listing; file access not checked',
        'provider_reports_restriction': 'Provider reports restricted access',
        'stream_manifest_observed': 'Stream found; use --video to download',
        'page_retrieved; work_access_unknown': 'Page available; file access unknown',
        'unknown': 'Access unknown',
    }.get(value, plain(value).replace('_', ' '))


def reported_size(file, check=None):
    """Server/provider size evidence; a range's prefix length is not its total."""
    value = file.get('bytes_reported')
    if check and file.get('url') == check.get('url'):
        if check.get('http_status') == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', check.get('content_range') or '')
            if match:
                start, end, total = map(int, match.groups())
                if 0 <= start <= end < total:
                    value = total
        elif check.get('http_status') == 200:
            value = check.get('content_length_reported') or value
    if not str(value).isdecimal():
        return ''
    size = int(value)
    for unit, divisor in [('GiB', 1024**3), ('MiB', 1024**2), ('KiB', 1024)]:
        if size >= divisor:
            return f'{size / divisor:.1f} {unit} reported'
    return f'{size} bytes reported'


def progress_text(event):
    stage = event['stage']
    if stage == 'start':
        return 'Searching ' + ', '.join(event['providers']) + '…'
    if stage == 'provider':
        if event['status'] == 'error':
            return f"{event['name']}: unavailable — {plain(event['error'])}"
        titles = '; '.join(plain(t)[:100] for t in event.get('titles', []))
        return f"{event['name']}: {event['count']} candidates" + (f' — {titles}' if titles else '')
    if stage == 'checking':
        return f"Checking result {event['result_number']}: {plain(event['title'])}"
    return f"Result {event['result_number']}: {access_label(event['access'])}"


def summary(data):
    lines = []
    if 'results' in data:
        lines.append(f"{len(data['results'])} candidates for {plain(data.get('query', ''))}")
        for index, item in enumerate(data['results'], 1):
            lines += [f"\n{index}. {plain(item.get('title', item.get('url', 'Result')))}",
                      f"   {plain(', '.join(item.get('authors', [])))} · {plain(item.get('provider', ''))}",
                      '   ' + access_label(item.get('access', 'unknown')),
                      '   ' + plain(item.get('url', ''))]
            for number, file in enumerate(item.get('files', []), 1):
                size = reported_size(file, item.get('check'))
                lines.append(f"   File {number}: {plain(file.get('format', 'File'))}" + (f' ({size})' if size else '') + f" — {plain(file['url'])}")
        if not data['results']:
            lines.append('No matches in the sources checked. Try a shorter title, an author, or another provider.')
        failed = [p['name'] for p in data.get('providers', []) if p['status'] == 'error']
        if failed:
            lines.append('\nUnavailable sources: ' + ', '.join(failed))
        lines.append(f"\nFile/page checks: {data.get('checks_performed', 0)}. Identity and completeness remain unverified.")
    elif 'sha256' in data or data.get('backend') == 'yt-dlp' and 'job_directory' in data:
        for file in data.get('files', [data]):
            lines += [f"Saved {plain(file['path'])} ({file['bytes']:,} bytes)", f"SHA-256: {file['sha256']}"]
    elif 'access' in data:
        lines += [plain(data.get('title') or data.get('url', 'Resource')), access_label(data['access'])]
        if data.get('error'):
            lines.append('Could not check: ' + plain(data['error']))
        if data.get('next_step'):
            lines.append(plain(data['next_step']))
        for index, file in enumerate(data.get('files', []), 1):
            lines.append(f"File {index}: {plain(file['url'])}")
    else:
        lines = [f"{key.replace('_', ' ')}: {plain(value)}" for key, value in data.items()]
    return '\n'.join(lines)


def select_download(report, number, file_number=None, video=False):
    path = Path(report)
    with path.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ScoutError('Saved report exceeds 8 MiB')
    data = json.loads(raw)
    items = data.get('results') if isinstance(data, dict) else None
    if not isinstance(items, list) or not 1 <= number <= len(items):
        raise ScoutError('Choose a --result number present in the saved search JSON')
    item = items[number - 1]
    if not isinstance(item, dict):
        raise ScoutError('Invalid result in saved report')
    files = item.get('files') or []
    if not isinstance(files, list):
        raise ScoutError('Invalid file list in saved report')
    check = item.get('check') or {}
    if not isinstance(check, dict):
        check = {}
    if file_number is not None:
        if not 1 <= file_number <= len(files) or not isinstance(files[file_number - 1], dict):
            raise ScoutError('Choose a --file number listed for that result')
        url = files[file_number - 1].get('url')
    elif check.get('access') == 'file_bytes_observed':
        url = check.get('url')
    elif video:
        url = item.get('url')
    elif len(files) == 1 and isinstance(files[0], dict):
        url = files[0].get('url')
    elif files:
        raise ScoutError('Several files are listed; choose one with --file NUMBER')
    else:
        raise ScoutError('No direct file is listed; inspect the source page or use --video for a media page')
    if not isinstance(url, str):
        raise ScoutError('The selected result has no valid file URL')
    return normalize(url), {'report': str(path.absolute()), 'result_number': number,
                            'file_number': file_number, 'title': plain(item.get('title', ''))}
