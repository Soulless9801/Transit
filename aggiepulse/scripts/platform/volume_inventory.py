"""Print an auditable inventory of immutable landing objects via profile transit."""
import argparse
import json
import subprocess


def listing(path, profile):
    result = subprocess.run(['databricks', 'fs', 'ls', 'dbfs:' + path,
                             '-p', profile, '-o', 'json'], check=True,
                            capture_output=True, text=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/Volumes/transit/raw/landing')
    parser.add_argument('--profile', default='transit')
    args = parser.parse_args()
    summary = {}
    for source in listing(args.root, args.profile):
        if not source['is_directory']:
            continue
        counts, sizes, dates = 0, 0, []
        path = args.root + '/' + source['name']
        for partition in listing(path, args.profile):
            if not partition['is_directory']:
                continue
            files = [item for item in listing(path + '/' + partition['name'], args.profile)
                     if not item['is_directory']]
            counts += len(files)
            sizes += sum(item['size'] for item in files)
            if files and partition['name'].startswith('date='):
                dates.append(partition['name'][5:])
        summary[source['name']] = {'files': counts, 'bytes': sizes,
                                    'first_partition': min(dates) if dates else None,
                                    'last_partition': max(dates) if dates else None}
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
