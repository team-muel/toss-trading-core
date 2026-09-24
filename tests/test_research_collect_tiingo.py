from toss_trading.cli.research_collect_tiingo import _load_tiingo_token


def test_tiingo_token_loads_from_env_file_without_overriding_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER_SECRET=do-not-read\nexport TIINGO_API_TOKEN='local-token'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
    assert _load_tiingo_token(env_file) == "local-token"
    monkeypatch.setenv("TIINGO_API_TOKEN", "process-token")
    assert _load_tiingo_token(env_file) == "process-token"


def test_tiingo_token_ignores_empty_or_comment_only_env_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TIINGO_API_TOKEN= # intentionally empty\nUNRELATED=value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
    assert _load_tiingo_token(env_file) == ""
