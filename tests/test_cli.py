import shutil
import pathlib as pl

import pytest
import click.testing
from click.testing import CliRunner

from .context import GTFSWeaver, DATA_DIR
from GTFSWeaver import *
from GTFSWeaver.cli import *


runner = CliRunner()


def rm_paths(*paths):
    """
    Delete the given file paths/directory paths, if they exists.
    """
    for p in paths:
        p = pl.Path(p)
        if p.exists():
            if p.is_file():
                p.unlink()
            else:
                shutil.rmtree(str(p))


@pytest.mark.slow
def test_make_gtfs():
    s_path = DATA_DIR / "auckland"
    t1_path = DATA_DIR / "bingo.zip"
    t2_path = DATA_DIR / "bingo"
    rm_paths(t1_path, t2_path)

    result = runner.invoke(GTFSWeaver, [str(s_path), str(t1_path)])
    assert result.exit_code == 0
    assert t1_path.exists()
    assert t1_path.is_file()

    result = runner.invoke(GTFSWeaver, [str(s_path), str(t2_path)])
    assert result.exit_code == 0
    assert t2_path.exists()
    assert t2_path.is_dir()

    rm_paths(t1_path, t2_path)
