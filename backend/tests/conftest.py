import io
import zipfile

import pytest


@pytest.fixture
def test_zip_bytes() -> bytes:
    """Create a zip with 3 known files for testing."""
    return create_test_zip()


def create_test_zip() -> bytes:
    """Create a zip with 3 known files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello world")
        zf.writestr("data/numbers.csv", "a,b,c\n1,2,3\n4,5,6")
        zf.writestr("data/config.json", '{"key": "value"}')
    return buf.getvalue()
