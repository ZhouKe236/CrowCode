"""黑名单单测：危险命令命中、普通命令不命中（AC1/F1/N1）。"""

import pytest

from crowcode.permission.dangerous import DangerousCommandDetector

_DETECTOR = DangerousCommandDetector()


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -fr ~",
        "rm -rf $HOME",
        "rm -rf /*",
        ":(){ :|:& };:",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda1",
        "cat foo > /dev/sda",
        "chmod -R 777 /",
    ],
)
def test_blacklist_hits(cmd):
    assert _DETECTOR.detect(cmd)[0] is True


@pytest.mark.parametrize(
    "cmd",
    ["rm -rf ./build", "git status", "ls -la", "echo hello", "rm build"],
)
def test_blacklist_misses(cmd):
    assert _DETECTOR.detect(cmd)[0] is False
