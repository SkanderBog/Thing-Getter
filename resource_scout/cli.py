import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path
from .net import Client, ScoutError
from .workflow import plain, progress_text, select_download, summary


def integer(low, high):
    def parse(value):
        n = int(value)
        if not low <= n <= high:
            raise argparse.ArgumentTypeError(f"Choose an integer from {low} to {high}")
        return n
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scout", description="Find resources, inspect evidence, and download selected URLs.")
    parser.add_argument("--proxy-dns", action="store_true", help="Trust synthetic benchmark DNS from your configured local proxy")
    parser.add_argument("--cache", default=".scout/cache.sqlite")
    parser.add_argument("--format", choices=["auto", "text", "json"], default="auto", help="Readable terminal output or JSON for scripts")
    parser.add_argument("--quiet", action="store_true", help="Hide search progress")
    sub = parser.add_subparsers(dest="action", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--kind", choices=["book", "video", "audio", "all"], default="book")
    s.add_argument("--limit", type=integer(1, 10), default=4, help="Results per provider")
    s.add_argument("--verify", type=integer(0, 10), default=3, help="File/page checks; catalogue-only records do not use this budget")
    s.add_argument("--providers", help="Comma-separated sources: archive,openlibrary,gutendex,searxng")
    s.add_argument("--provider-timeout", type=integer(1, 120), default=12, help="Seconds per source for requests/retries (DNS may exceed this)")
    s.add_argument("--check-timeout", type=integer(1, 120), default=12, help="Seconds per candidate check")
    s.add_argument("--searxng", default=os.environ.get("SCOUT_SEARXNG"), help="Public HTTPS SearXNG root with JSON enabled")
    s.add_argument("--fresh", action="store_true")
    s.add_argument("--json", dest="json_path")
    s.add_argument("--html")
    s.add_argument("--open", action="store_true", help="Open the saved HTML report when ready; requires --html")
    i = sub.add_parser("inspect")
    i.add_argument("url")
    i.add_argument("--video", action="store_true")
    i.add_argument("--json", dest="json_path")
    i.add_argument("--html")
    i.add_argument("--open", action="store_true", help="Open the saved HTML report; requires --html")
    d = sub.add_parser("download")
    d.add_argument("url", nargs="?")
    d.add_argument("--from-report", help="Saved search JSON; select a result without copying its URL")
    d.add_argument("--result", type=integer(1, 10000), help="Result number in the saved report")
    d.add_argument("--file", type=integer(1, 10000), help="File number within the selected result")
    d.add_argument("--out", required=True, help="Exact destination file; a job parent directory with --video")
    d.add_argument("--video", action="store_true")
    d.add_argument("--max-mb", type=integer(1, 2048), default=50)
    doctor_parser = sub.add_parser("doctor")
    # Common options work before or after the action; missing subparser options
    # do not overwrite values supplied on the root parser.
    for command in (s, i, d, doctor_parser):
        command.add_argument("--proxy-dns", action="store_true", default=argparse.SUPPRESS)
        command.add_argument("--cache", default=argparse.SUPPRESS)
        command.add_argument("--format", choices=["auto", "text", "json"], default=argparse.SUPPRESS)
        command.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if getattr(args, 'open', False) and not args.html:
        parser.error('--open requires --html PATH')
    if args.action == 'download':
        if bool(args.url) == bool(args.from_report):
            parser.error('Supply a URL or --from-report PATH')
        if args.from_report and args.result is None:
            parser.error('--from-report requires --result NUMBER')
        if not args.from_report and (args.result is not None or args.file is not None):
            parser.error('--result and --file require --from-report')
    output_format = args.format
    if output_format == 'auto':
        output_format = 'text' if sys.stdout.isatty() else 'json'
    client = None
    try:
        if args.action == "doctor":
            from .media import doctor
            result = doctor()
        else:
            client = Client(args.cache, proxy_dns=args.proxy_dns)
            if args.action == "search":
                from .discovery import search
                def progress(event):
                    print(progress_text(event), file=sys.stderr, flush=True)
                result = search(client, args.query, args.kind, args.limit, args.verify, args.searxng, 0 if args.fresh else 3600,
                                providers=[p.strip() for p in args.providers.split(',')] if args.providers else None,
                                provider_timeout=args.provider_timeout, check_timeout=args.check_timeout,
                                progress=None if args.quiet else progress)
            elif args.action == "inspect":
                if args.video:
                    from .media import inspect_video
                    result = inspect_video(client, args.url)
                else:
                    from .inspect import inspect_url
                    result = inspect_url(client, args.url)
            elif args.action == "download":
                selection = None
                if args.from_report:
                    args.url, selection = select_download(args.from_report, args.result, args.file, args.video)
                if args.video:
                    from .media import download_video
                    result = download_video(client, args.url, args.out, args.max_mb * 1024 * 1024)
                else:
                    from .download import download_file
                    result = download_file(client, args.url, args.out, args.max_mb * 1024 * 1024)
                if selection:
                    result['selected_from'] = selection
                    from .download import atomic_json
                    receipt = Path(result['job_directory']) / 'provenance.json' if args.video else Path(result['path'] + '.provenance.json')
                    atomic_json(receipt, result)
        if getattr(args, "json_path", None):
            Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_path).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if getattr(args, "html", None):
            from .report import render
            render(result, args.html, source_report=getattr(args, 'json_path', None), proxy_dns=args.proxy_dns)
        print(summary(result) if output_format == 'text' else json.dumps(result, indent=2, ensure_ascii=False))
        if output_format == 'text':
            if getattr(args, 'json_path', None):
                print(f'\nSaved JSON: {Path(args.json_path).absolute()}')
            if getattr(args, 'html', None):
                print(f'Report: {Path(args.html).absolute()}')
        if getattr(args, 'open', False):
            if not webbrowser.open(Path(args.html).resolve().as_uri()):
                print('Could not open a browser. Open the saved HTML report manually.', file=sys.stderr)
        if result.get("error") or ("providers" in result and all(p["status"] == "error" for p in result["providers"])):
            return 2
        return 0
    except (ScoutError, ValueError, OSError) as exc:
        print('Error: ' + plain(exc) if output_format == 'text' else json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Cancelled. Any partial download is retained for a later resume.', file=sys.stderr)
        return 130
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
