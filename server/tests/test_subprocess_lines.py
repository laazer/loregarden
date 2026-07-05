import os

from loregarden.services.subprocess_lines import SubprocessLineReader


def test_subprocess_line_reader_emits_partial_lines():
    read_fd, write_fd = os.pipe()
    reader = SubprocessLineReader(os.fdopen(read_fd, "rb", closefd=True))

    os.write(write_fd, b'{"type":"system"}\n{"type":"assistant"')
    line = reader.readline(timeout=0.1)
    assert line == '{"type":"system"}\n'

    os.write(write_fd, b',"message":{}}\n')
    line = reader.readline(timeout=0.1)
    assert line == '{"type":"assistant","message":{}}\n'

    os.close(write_fd)


def test_subprocess_line_reader_returns_none_when_idle():
    read_fd, write_fd = os.pipe()
    reader = SubprocessLineReader(os.fdopen(read_fd, "rb", closefd=True))
    assert reader.readline(timeout=0.05) is None
    os.close(write_fd)
