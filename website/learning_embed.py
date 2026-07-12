import json


def _script_json(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def embed_learning_runtime(template, models, history, runtime_source):
    history_marker = "/*__LEARNING_HISTORY__*/"
    runtime_marker = "/*__LEARNING_RUNTIME__*/"
    if template.count(history_marker) != 1 or template.count(runtime_marker) != 1:
        raise ValueError("learning runtime markers must each appear exactly once")
    rendered = template.replace(history_marker, "var LEARNING_HISTORY=" + _script_json(history) + ";")
    return rendered.replace(
        runtime_marker,
        "var EMBEDDED_MODELS=" + _script_json(models) + ";\n" + runtime_source,
    )
