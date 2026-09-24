import os

from toss_trading.broker.credentials import load_toss_credentials_from_env


def test_toss_credentials_load_allowlisted_dotenv_values_automatically(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "export TOSS_CLIENT_ID='client-id'\n"
        "TOSS_CLIENT_SECRET=client-secret\n"
        "TOSS_ACCOUNT_SEQ=account-1\n"
        "TOSS_API_ENV=paper\n"
        "TOSS_ALLOW_CUSTOM_BASE_URL=1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_SEQ",
                 "TOSS_API_ENV", "TOSS_BROKER_BASE_URL", "TOSS_ALLOW_CUSTOM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    credentials = load_toss_credentials_from_env()

    assert credentials.client_id == "client-id"
    assert credentials.client_secret == "client-secret"
    assert credentials.account_seq == "account-1"
    assert credentials.api_env == "paper"
    assert credentials.base_url == "https://openapi.tossinvest.com"
    assert "TOSS_ALLOW_CUSTOM_BASE_URL" not in os.environ


def test_toss_process_credentials_override_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "TOSS_CLIENT_ID=dotenv-id\nTOSS_CLIENT_SECRET=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOSS_CLIENT_ID", "process-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "process-secret")

    credentials = load_toss_credentials_from_env()

    assert credentials.client_id == "process-id"
    assert credentials.client_secret == "process-secret"
