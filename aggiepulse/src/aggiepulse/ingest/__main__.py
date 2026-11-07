"""Run all batch sources: python -m aggiepulse.ingest all."""
import argparse

from aggiepulse.ingest.run_all import run_all


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['all'])
    parser.parse_args()
    results = run_all()
    raise SystemExit(any(row['status'] != 'OK' for row in results.values()))


if __name__ == '__main__':
    main()
