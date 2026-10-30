import pytest


@pytest.fixture(autouse=True)
def offline_package_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never allow package-tool integration tests to contact registries."""
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
