"""Distribution rename preserves the flat import and executable API."""
from pathlib import Path
import tomllib


def test_distribution_name_is_unambiguous():
    project=tomllib.loads((Path(__file__).parent/'pyproject.toml').read_text())
    assert project['project']['name']=='selfconnect-sdk'
    assert project['project']['version']=='0.12.0'
    assert 'sc_cli' in str(project['project']['scripts'])
