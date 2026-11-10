"""Step-level options for a page step, applied to the page's fields before actions are built."""


def fields_for_step(fields: dict, step: dict) -> dict:
    """A step with "index": N replaces the index of every field flagged "indexed": true, so a repeated section
    of a page (the second principal, the third row) is the same fields in a second step instead of a copy of
    each field. Fields without the flag keep their own index. Without "index" the fields are returned as is."""
    if not isinstance(step, dict) or "index" not in step:
        return fields
    try:
        index = int(str(step["index"]).strip())
    except ValueError:
        raise ValueError(f"Step index must be a whole number, got {step['index']!r}.") from None
    if index < 0:
        raise ValueError(f"Step index must not be negative, got {index}.")
    return {name: {**cfg, "index": index} if isinstance(cfg, dict) and cfg.get("indexed") else cfg
            for name, cfg in fields.items()}
