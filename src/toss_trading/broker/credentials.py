import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TossCredentials:
    base_url: str
    client_id: str
    client_secret: str
    account_seq: str | None
    api_env: str = "unknown"


def _load_local_dotenv() -> None:
    """Loads a local .env file for desktop development only.

    Existing process environment variables win.
    """

    env_path = Path(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
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

    return TossCredentials(
        base_url=os.environ.get("TOSS_BROKER_BASE_URL", "https://openapi.tossinvest.com"),
        client_id=os.environ["TOSS_CLIENT_ID"],
        client_secret=os.environ["TOSS_CLIENT_SECRET"],
        account_seq=os.environ.get("TOSS_ACCOUNT_SEQ") or None,
        api_env=os.environ.get("TOSS_API_ENV", "unknown"),
    )
