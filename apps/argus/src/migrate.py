"""Offline schema maintenance for Argus."""
import argparse
import subprocess
from packages.runtime_environment import get_icarus_data_dir
from .dao.schema import migrate


def main():
    parser=argparse.ArgumentParser(description='Back up and upgrade stopped Argus database')
    parser.parse_args()
    processes=subprocess.run(['ps','-ww','-ax','-o','args='],check=True,capture_output=True,text=True).stdout
    if any('-m apps.argus.src.main' in line for line in processes.splitlines()):
        raise SystemExit('Stop all Argus service processes before migrating')
    backup=migrate(get_icarus_data_dir()/'argus.db')
    print(f'Upgraded; backup: {backup}' if backup else 'Schema already current')


if __name__=='__main__': main()
