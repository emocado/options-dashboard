"""Configuration loading/saving for the Options Wheel Dashboard.

Settings live in `config.toml` at the project root (copied from
`config.example.toml`). They can be edited by hand or from the app's Settings
panel. Reading uses the stdlib `tomllib`; writing uses a tiny hand-rolled
serializer so we don't add a TOML-writer dependency.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, asdict, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"
EXAMPLE_PATH = PROJECT_ROOT / "config.example.toml"
DB_PATH = PROJECT_ROOT / "data" / "dashboard.db"

# Map our friendly names to the moomoo SDK enum member names. We keep these as
# strings here so config.py never needs to import the (optional) moomoo SDK.
SECURITY_FIRMS = ["FUTUSG", "FUTUINC", "FUTUSECURITIES", "FUTUAU"]
TRD_MARKETS = ["US", "HK", "CN"]
TRD_ENVS = ["SIMULATE", "REAL"]


@dataclass
class MoomooConfig:
    host: str = "127.0.0.1"
    port: int = 11111
    security_firm: str = "FUTUSG"
    trd_market: str = "US"
    trd_env: str = "SIMULATE"
    history_days: int = 365


@dataclass
class FeesConfig:
    per_contract: float = 1.50


@dataclass
class AppConfig:
    currency: str = "USD"


@dataclass
class Config:
    moomoo: MoomooConfig = field(default_factory=MoomooConfig)
    fees: FeesConfig = field(default_factory=FeesConfig)
    app: AppConfig = field(default_factory=AppConfig)


def load_config() -> Config:
    """Load config.toml, falling back to config.example.toml, then defaults."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)

    return Config(
        moomoo=MoomooConfig(**{**asdict(MoomooConfig()), **data.get("moomoo", {})}),
        fees=FeesConfig(**{**asdict(FeesConfig()), **data.get("fees", {})}),
        app=AppConfig(**{**asdict(AppConfig()), **data.get("app", {})}),
    )


def _toml_value(value) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def save_config(cfg: Config) -> None:
    """Write config back to config.toml (simple, comment-free serializer)."""
    lines: list[str] = []
    for section_name, section in asdict(cfg).items():
        lines.append(f"[{section_name}]")
        for key, value in section.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    CONFIG_PATH.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
