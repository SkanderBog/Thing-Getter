"""Both frozen executables use explicit modes; normal launch opens the app."""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--media-backend':
        import yt_dlp
        yt_dlp.main(sys.argv[2:])
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == '--cli':
        from resource_scout.cli import main as cli
        return cli(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == '--self-test':
        from pathlib import Path
        import traceback
        output = Path(sys.argv[2])
        output.mkdir(parents=True, exist_ok=True)
        try:
            from desktop.smoke import run
            return run(output)
        except Exception:
            (output / 'failure.log').write_text(traceback.format_exc(), encoding='utf-8')
            return 1
    from desktop.app import run
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
