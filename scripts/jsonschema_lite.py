"""Minimal JSON Schema validator (Python 3.7 stdlib only)."""
import re

_KEYWORDS = {"type", "enum", "const", "required", "properties", "additionalProperties",
             "items", "minItems", "maxItems", "minLength", "maxLength", "minimum",
             "maximum", "pattern", "anyOf",
             # annotations that carry no validation semantics
             "$schema", "title", "description", "$id"}


def _is_type(v, t):
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "null":
        return v is None
    raise ValueError("unsupported type: %s" % t)


def validate(instance, schema, path="$"):
    for k in schema:
        if k not in _KEYWORDS:
            raise ValueError("unsupported keyword: %s" % k)
    errors = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            errors.append("%s: expected %s" % (path, " or ".join(types)))
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: not one of %s" % (path, schema["enum"]))
    if "const" in schema and instance != schema["const"]:
        errors.append("%s: expected const %r" % (path, schema["const"]))
    if "anyOf" in schema:
        if not any(not validate(instance, s, path) for s in schema["anyOf"]):
            errors.append("%s: does not match any allowed schema" % path)
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in instance:
                errors.append("%s: missing required property '%s'" % (path, r))
        for key, val in instance.items():
            if key in props:
                errors.extend(validate(val, props[key], "%s.%s" % (path, key)))
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errors.append("%s: unexpected property '%s'" % (path, key))
                elif isinstance(ap, dict):
                    errors.extend(validate(val, ap, "%s.%s" % (path, key)))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: expected at least %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: expected at most %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], "%s[%d]" % (path, i)))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than %d characters" % (path, schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: longer than %d characters" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: does not match pattern %s" % (path, schema["pattern"]))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: below minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: above maximum %s" % (path, schema["maximum"]))
    return errors
