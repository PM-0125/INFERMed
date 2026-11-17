"""SPL text units using explicit styled subsection headings, never sentence windows."""

import re


def _clean(text):
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n\n", text).strip()


def split_text_units(
    body, section_path, parent_context, *, peer_headings=(), heading_hierarchy=None
):
    """Split only adapter-verified peers; typography alone never establishes scope.

    Without an explicit peer contract, retain the encompassing XML text body.
    Callers must establish peer roles independently of this text parser.
    """
    chunks, markers, styles = [], [], set()

    def visit(node):
        tag = node.tag.rsplit("}", 1)[-1]
        chunks.append(node.text or "")
        children = list(node)
        index = 0
        while index < len(children):
            child = children[index]
            style = child.get("styleCode", "").lower()
            title = "".join(child.itertext()).strip()
            consumed = 1
            # Some SPL documents style the heading and its colon separately.
            if (
                style in {"italics", "bold"}
                and not (child.tail or "").strip()
                and index + 1 < len(children)
            ):
                following = children[index + 1]
                if (
                    "".join(following.itertext()).strip() == ":"
                    and following.get("styleCode", "").lower() == style
                ):
                    title += ":"
                    consumed = 2
            has_break = any(n.tag.rsplit("}", 1)[-1] == "br" for n in child.iter())
            start_of_block = (
                not "".join(chunks).strip()
                or index == 0
                and not (node.text or "").strip()
            )
            numbered = re.match(r"^\d+(?:\.\d+)+\s+\S", title)
            heading = (
                style in {"italics", "bold"}
                and (title.endswith(":") or numbered or start_of_block)
                and len(title) > 1
                and not any(
                    n.tag.rsplit("}", 1)[-1] == "linkHtml" for n in child.iter()
                )
            )
            same_as_section = re.sub(r"[\W_]", "", title).lower() in {
                re.sub(r"[\W_]", "", p).lower() for p in section_path
            }
            reviewed = (
                heading_hierarchy is not None
                and title.rstrip(":").strip().casefold() in heading_hierarchy
            )
            if reviewed or (
                heading_hierarchy is None
                and heading
                and (has_break or start_of_block)
                and not same_as_section
            ):
                markers.append((sum(map(len, chunks)), title))
                styles.add(style)
            for item in children[index : index + consumed]:
                visit(item)
                chunks.append(item.tail or "")
            index += consumed
        if tag in {"paragraph", "item", "br"}:
            chunks.append("\n\n")

    visit(body)
    text = "".join(chunks)
    if heading_hierarchy is not None:
        keys = [title.rstrip(":").strip().casefold() for _, title in markers]
        if len(keys) == len(set(keys)) and set(keys) == set(heading_hierarchy):
            intro = _clean(text[: markers[0][0]])
            segments = [
                _clean(
                    text[pos : markers[i + 1][0] if i + 1 < len(markers) else len(text)]
                )
                for i, (pos, _) in enumerate(markers)
            ]
            result = []
            for i, (_, title) in enumerate(markers):
                ancestors = [keys.index(key) for key in heading_hierarchy[keys[i]]]
                context = (
                    list(parent_context)
                    + ([intro] if intro else [])
                    + [segments[j] for j in ancestors]
                )
                result.append(
                    {
                        "section_path": section_path
                        + [markers[j][1] for j in ancestors]
                        + [title],
                        "text": "\n\n".join(section_path + context + [segments[i]]),
                        "parent_context": context,
                        "boundary_policy": "reviewed_content_bound_hierarchy",
                    }
                )
            return result
        # An incomplete profile never falls through to a guessed peer split.
        markers = []
    numbered = [re.match(r"^(\d+(?:\.\d+)+)\s+\S", title) for _, title in markers]
    if markers and all(numbered):
        numbers = [tuple(map(int, match[1].split("."))) for match in numbered]
        # Require one ordered numbering tree, with no duplicates or skipped parents.
        roots = {number[0] for number in numbers}
        valid = (
            len(roots) == 1
            and numbers == sorted(set(numbers))
            and all(len(number) == 2 or number[:-1] in numbers for number in numbers)
        )
        if valid:
            intro = _clean(text[: markers[0][0]])
            segments = [
                _clean(
                    text[pos : markers[i + 1][0] if i + 1 < len(markers) else len(text)]
                )
                for i, (pos, _) in enumerate(markers)
            ]
            result = []
            for i, (pos, title) in enumerate(markers):
                ancestors = [
                    j
                    for j in range(i)
                    if len(numbers[j]) < len(numbers[i])
                    and numbers[i][: len(numbers[j])] == numbers[j]
                ]
                context = (
                    [p for p in parent_context if p]
                    + ([intro] if intro else [])
                    + [segments[j] for j in ancestors]
                )
                result.append(
                    {
                        "section_path": section_path
                        + [markers[j][1] for j in ancestors]
                        + [title],
                        "text": "\n\n".join(section_path + context + [segments[i]]),
                        "parent_context": context,
                        "boundary_policy": "spl_numbered_hierarchy",
                    }
                )
            return result
    # An open-ended blacklist cannot prove that a heading is not a condition.
    peers = {str(title).strip().rstrip(":").casefold() for title in peer_headings}
    if len(styles) > 1 or any(
        title.rstrip(":").strip().casefold() not in peers for _, title in markers
    ):
        markers = []
    intro = _clean(text[: markers[0][0]]) if markers else ""
    ranges = (
        [
            (pos, markers[i + 1][0] if i + 1 < len(markers) else len(text), title)
            for i, (pos, title) in enumerate(markers)
        ]
        if markers
        else [(0, len(text), None)]
    )
    result = []
    for start, end, title in ranges:
        context = [p for p in parent_context if p] + ([intro] if intro else [])
        path = section_path + ([title] if title else [])
        # Titles can themselves specify population/route conditions. They are
        # part of the binding and applicability input, not disposable metadata.
        rendered = "\n\n".join(section_path + context + [_clean(text[start:end])])
        result.append(
            {
                "section_path": path,
                "text": rendered,
                "parent_context": context,
                "boundary_policy": "spl_explicit_heading_with_governing_context",
            }
        )
    return result
