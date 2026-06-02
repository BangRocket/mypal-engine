"""Unit tests for visual-clara helpers: pose-tag stripping + system message collection."""

from mypalclara.gateway.api._visual import collect_system_extra, strip_pose_tags


def test_strip_pose_tags_removes_expression_tag():
    assert strip_pose_tags("Hi [expression:happy] there") == "Hi there"


def test_strip_pose_tags_removes_pose_and_multiple_tags():
    assert strip_pose_tags("[pose:left]Hello [expression:surprised] world") == "Hello world"


def test_strip_pose_tags_no_tags_unchanged():
    assert strip_pose_tags("plain text, nothing to strip") == "plain text, nothing to strip"


def test_strip_pose_tags_preserves_newlines_and_trims_line_edges():
    assert strip_pose_tags("line1 [pose:center]\nline2") == "line1\nline2"


def test_strip_pose_tags_case_insensitive():
    assert strip_pose_tags("Yo [EXPRESSION:Happy] you") == "Yo you"


def test_strip_pose_tags_empty():
    assert strip_pose_tags("") == ""


def test_collect_system_extra_joins_system_messages():
    msgs = [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "B"},
    ]
    assert collect_system_extra(msgs) == "A\n\nB"


def test_collect_system_extra_ignores_empty_and_nonstring():
    msgs = [
        {"role": "system", "content": "  "},
        {"role": "system", "content": 123},
        {"role": "system", "content": "X"},
    ]
    assert collect_system_extra(msgs) == "X"


def test_collect_system_extra_empty_list():
    assert collect_system_extra([]) == ""
