"""Content sandboxing for untrusted input.

Wraps external content (tool results, attachments, memories) in tagged
boundaries so the LLM treats them as data, not instructions.
"""

from __future__ import annotations


def escape_for_prompt(content: str) -> str:
    """Escape angle brackets to prevent tag injection and sandbox breakout.

    All ``<`` characters are escaped to ``&lt;`` so untrusted content
    cannot close its sandbox boundary, open new tags, inject comments,
    or use any other XML/HTML construct.

    Args:
        content: Raw untrusted content

    Returns:
        Escaped content safe for inclusion inside untrusted tags
    """
    content = content.replace("<", "&lt;")
    return content


def wrap_untrusted(content: str, source: str, scan: bool = True) -> str:
    """Wrap content in <untrusted_{source}> tags with escaping.

    Optionally runs injection scanning and annotates the wrapper
    with risk level if suspicious patterns are detected.

    Args:
        content: Raw untrusted content
        source: Source identifier (e.g. "tool_web_search", "attachment", "memory")
        scan: Whether to run injection scanning (default True)

    Returns:
        Content wrapped in tagged boundaries
    """
    # Scan raw content BEFORE escaping so patterns like <|im_start|> match
    scan_result = None
    if scan:
        from clara_core.security.injection_scanner import scan_for_injection

        scan_result = scan_for_injection(content, source)

    escaped = escape_for_prompt(content)

    if scan_result and scan_result.risk_level != "clean":
        return (
            f'<untrusted_{source} risk="{scan_result.risk_level}">\n'
            f"[SECURITY: {scan_result.warning}]\n"
            f"{escaped}\n"
            f"</untrusted_{source}>"
        )

    return f"<untrusted_{source}>\n{escaped}\n</untrusted_{source}>"
