#!/usr/bin/env python3
"""
Open ACE - Setup Script

This script helps set up the open-ace configuration directory
and can be used during installation to ensure consistent paths.
"""

import os
import sys
import argparse
import json
import shutil


def setup_config_dir(config_dir: str) -> str:
    """Create and initialize the configuration directory."""
    expanded_dir = os.path.expanduser(config_dir)
    os.makedirs(expanded_dir, exist_ok=True)

    # Copy sample config if it exists
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sample_config = os.path.join(script_dir, '..', 'config', 'settings.json.sample')

    config_path = os.path.join(expanded_dir, 'config.json')

    if os.path.exists(sample_config):
        if not os.path.exists(config_path):
            shutil.copy(sample_config, config_path)
            print(f"Created config file at: {config_path}")
            print("Please edit the file with your settings.")
        else:
            print(f"Config file already exists at: {config_path}")
    else:
        # Create default config
        default_config = {
            "email": {
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_username": "",
                "smtp_password": "",
                "from_email": "",
                "to_email": "",
                "use_tls": True
            },
            "tools": {
                "openclaw": {"enabled": True},
                "claude": {"enabled": True},
                "qwen": {"enabled": True}
            },
            "cron": {
                "enabled": True,
                "run_time": "00:30"
            }
        }
        with open(config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
        print(f"Created default config at: {config_path}")

    return config_path


def main():
    parser = argparse.ArgumentParser(
        description='Setup Open ACE configuration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Example usage:
  python setup.py --config-dir ~/.open-ace
  python setup.py --init

The default configuration directory is ~/.open-ace
        '''
    )

    parser.add_argument(
        '--config-dir',
        default='~/.open-ace',
        help=f'Configuration directory (default: ~/.open-ace)'
    )

    parser.add_argument(
        '--init',
        action='store_true',
        help='Initialize configuration directory and create config file'
    )

    parser.add_argument(
        '--show',
        action='store_true',
        help='Show current configuration directory'
    )

    args = parser.parse_args()

    if args.show:
        expanded_dir = os.path.expanduser(args.config_dir)
        print(f"Configuration directory: {expanded_dir}")
        print(f"Config file path: {os.path.join(expanded_dir, 'config.json')}")
        return 0

    if args.init:
        config_path = setup_config_dir(args.config_dir)
        print(f"Setup complete. Config file: {config_path}")
        return 0

    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
