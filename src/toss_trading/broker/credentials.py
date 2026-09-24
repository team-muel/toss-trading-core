import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class TossCredentials:
    base_url: str
    client_id: str
    client_secret: str
    account_seq: str | None
    api_env: str = "unknown"


def _load_local_dotenv() -> None:
    """Load allowlisted desktop credentials from a Git-ignored .env file.

    Process variables always win. Only connection identity/secret variables are
    read; local files cannot change safety overrides or live-trading controls.
    """
    names = {"TOSS_BROKER_BASE_URL", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
             "TOSS_ACCOUNT_SEQ", "TOSS_API_ENV"}
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env")
    for env_path in dict.fromkeys(candidates):
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or key not in names or os.environ.get(key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif value.startswith("#"):
                value = ""
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            if value:
                os.environ[key] = value


def load_toss_credentials_from_env() -> TossCredentials:
    """Loads Toss credentials from process environment only.

    Do not store real API keys in Git, source files, checked-in config, or logs.
    """

    _load_local_dotenv()

    missing = [
        name for name in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET") if not os.environ.get(name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"missing Toss credential environment variables: {joined}")

    base_url = os.environ.get("TOSS_BROKER_BASE_URL", "https://openapi.tossinvest.com")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or (
            parsed.hostname != "openapi.tossinvest.com"
            and os.environ.get("TOSS_ALLOW_CUSTOM_BASE_URL") != "1"
        )
    ):
        raise RuntimeError(
            "TOSS_BROKER_BASE_URL must be https://openapi.tossinvest.com "
            "(set TOSS_ALLOW_CUSTOM_BASE_URL=1 only for an approved test endpoint)"
        )
    return TossCredentials(
        base_url=base_url.rstrip("/"),
        client_id=os.environ["TOSS_CLIENT_ID"],
        client_secret=os.environ["TOSS_CLIENT_SECRET"],
        account_seq=os.environ.get("TOSS_ACCOUNT_SEQ") or None,
        api_env=os.environ.get("TOSS_API_ENV", "unknown"),
    )
