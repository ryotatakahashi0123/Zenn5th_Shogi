import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ANON = ROOT / "samples" / "anon"


def read_sample(name: str, crlf: bool = True) -> str:
    raw = (ANON / name).read_bytes().decode("utf-8")
    raw = raw.replace("\r\n", "\n")
    return raw.replace("\n", "\r\n") if crlf else raw


@pytest.fixture
def s01():
    return read_sample("sample01.kif")


@pytest.fixture
def s02():
    return read_sample("sample02.kif")
