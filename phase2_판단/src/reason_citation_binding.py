"""Literal quotation coverage diagnostics, not semantic or verdict validation.

Only explicit quoted fragments in reason are examined. A finding means that a
literal occurs in supplied source text but selected citations do not cover it.
It does not mean the fact is false or the item's result is wrong. Empty findings
do not establish support: paraphrases, short terms, criteria and source-absent
quotations are deliberately outside this narrow check.
"""
from collections import defaultdict
from collections.abc import Mapping
import re


VERSION = "phase2_reason_literal_citation_binding_v1"
_QUOTED = re.compile(
    r'"(?P<double>(?:\\.|[^"\\])+)"'
    r"|(?<![A-Za-z0-9_])'(?P<single>(?:\\.|[^'\\])+)'(?![A-Za-z0-9_])"
    r"|“(?P<curly_double>[^”]+)”|‘(?P<curly_single>[^’]+)’"
    r"|「(?P<corner>[^」]+)」|『(?P<double_corner>[^』]+)』"
    r"|`(?P<backtick>[^`]+)`",
    re.DOTALL,
)


def _normalized_with_offsets(value):
    """Collapse only whitespace; retain raw source offsets for every character."""
    characters, starts, ends = [], [], []
    for match in re.finditer(r"\s+|\S", value):
        characters.append(" " if match.group().isspace() else match.group())
        starts.append(match.start())
        ends.append(match.end())
    return "".join(characters), starts, ends


def _normal(value):
    return re.sub(r"\s+", " ", value).strip()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _strings(nested)


def _occurrences(needle, haystack):
    start = haystack.find(needle)
    while start >= 0:
        yield start, start + len(needle)
        start = haystack.find(needle, start + 1)


def _covered(start, end, text, citation_intervals):
    """Allow multiple citations covering this same occurrence, never row mixing.

    Whitespace and table separators between adjacent selected fragments carry
    no source claim; all other characters must be covered by a valid citation.
    """
    cursor = start
    for left, right in sorted(citation_intervals):
        if right <= cursor or left >= end:
            continue
        if left > cursor and text[cursor:min(left, end)].strip(" \t\r\n|"):
            return False
        cursor = max(cursor, min(right, end))
        if cursor >= end:
            return True
    return not text[cursor:end].strip(" \t\r\n|")


def inspect_reason_citation_binding(item, output, context):
    """Return source locations for uncovered explicit literals; never mutate input.

    Offsets are zero-based, end-exclusive Python string offsets. Source line
    numbers are one-based. Exact matching permits whitespace normalization only.
    A twelve-nonspace-character floor excludes terminology-sized quotations;
    it is a diagnostic scope limit, not an evidence-sufficiency rule.
    """
    report = {"version": VERSION, "diagnostic_only": True, "findings": [], "abstentions": []}
    reason = output.get("reason", "")
    if not isinstance(reason, str):
        return report
    chunks = [chunk for chunk in context.get("chunks", []) if isinstance(chunk, Mapping)]
    criteria = [_normal(value) for value in _strings(item) if value.strip()]
    metadata = [_normal(str(chunk[key])) for chunk in chunks
                for key in ("chunk_id", "source_file", "heading")
                if chunk.get(key) is not None]
    sources = []
    for index, chunk in enumerate(chunks):
        raw = chunk.get("text")
        if isinstance(raw, str):
            normalized, starts, ends = _normalized_with_offsets(raw)
            sources.append((index, chunk, raw, normalized, starts, ends))

    # Coverage belongs to a concrete source occurrence, not a bag of quote words.
    citation_intervals = defaultdict(list)
    for citation in output.get("citations", []):
        if not isinstance(citation, Mapping) or not isinstance(citation.get("quote"), str):
            continue
        quote = _normal(citation["quote"])
        if not quote:
            continue
        for index, chunk, _, text, _, _ in sources:
            if chunk.get("chunk_id") == citation.get("chunk_id"):
                citation_intervals[index].extend(_occurrences(quote, text))

    for quoted in _QUOTED.finditer(reason):
        group = quoted.lastgroup
        raw_fragment = quoted.group(group)
        fragment = _normal(raw_fragment)
        leading = len(raw_fragment) - len(raw_fragment.lstrip())
        trailing = len(raw_fragment.rstrip())
        span = {"text": raw_fragment.strip(), "start": quoted.start(group) + leading,
                "end": quoted.start(group) + trailing}
        if not fragment:
            continue
        if any(fragment in value for value in criteria + metadata):
            report["abstentions"].append({"reason_span": span, "cause": "CRITERION_OR_METADATA_QUOTATION"})
            continue
        if len(re.sub(r"\s", "", fragment)) < 12:
            report["abstentions"].append({"reason_span": span, "cause": "NON_SPECIFIC_LITERAL"})
            continue
        matches = []
        has_coverage = False
        for index, chunk, raw, text, starts, ends in sources:
            for start, end in _occurrences(fragment, text):
                raw_start, raw_end = starts[start], ends[end - 1]
                matches.append({
                    "chunk_id": chunk.get("chunk_id"), "field": "text",
                    "source_file": chunk.get("source_file"),
                    "page_start": chunk.get("page_start"), "page_end": chunk.get("page_end"),
                    "start": raw_start, "end": raw_end,
                    "line_start": raw.count("\n", 0, raw_start) + 1,
                    "line_end": raw.count("\n", 0, raw_end) + 1,
                    "source_text": raw[raw_start:raw_end],
                })
                if _covered(start, end, text, citation_intervals[index]):
                    has_coverage = True
        if not matches:
            report["abstentions"].append({"reason_span": span, "cause": "NO_LITERAL_SOURCE_MATCH"})
        elif not has_coverage:
            report["findings"].append({
                "code": "REASON_LITERAL_NOT_COVERED_BY_CITATIONS",
                "reason_span": span, "source_locations": matches,
            })
    return report
