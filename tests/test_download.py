import zipfile

from eroll.download import parse_parts_spec, unpack


def test_parse_parts_spec():
    parts = list(range(1, 11))
    assert parse_parts_spec(None, parts) == parts
    assert parse_parts_spec("2-4,7,99", parts) == [2, 3, 4, 7]


def test_unpack_extracts_pdfs_from_zip(tmp_path):
    bundle = tmp_path / "batch.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("a-1-WI.pdf", b"%PDF")
        z.writestr("readme.txt", b"")
    assert unpack(bundle) == [tmp_path / "a-1-WI.pdf"]
    assert not bundle.exists()
    assert unpack(tmp_path / "a-1-WI.pdf") == [tmp_path / "a-1-WI.pdf"]
