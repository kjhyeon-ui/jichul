from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_rules(config_dir: Path | None = None) -> dict:
    d = Path(config_dir) if config_dir else ROOT / "config"
    return load_yaml(d / "rules.yaml")


def load_accounts(config_dir: Path | None = None) -> dict:
    d = Path(config_dir) if config_dir else ROOT / "config"
    return load_yaml(d / "accounts.yaml")
