"""One local schema graph for discovery and JSON Schema validation."""
from contextvars import ContextVar
from functools import lru_cache

from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from referencing import Registry


_active_discriminators = ContextVar("active_discriminators", default=frozenset())


def discriminator(validator, rule, instance, schema):
    if not isinstance(instance, dict) or not rule.get("mapping"):
        return
    # UniFi subtypes inherit their base through allOf. Do not select the same
    # discriminator again while validating its selected subtype.
    marker = (id(instance), id(schema))
    active = _active_discriminators.get()
    if marker in active:
        return
    selection = instance.get(rule["propertyName"])
    if not isinstance(selection, str) or selection not in rule["mapping"]:
        yield ValidationError("Missing or unsupported discriminator: " + rule["propertyName"])
        return
    token = _active_discriminators.set(active | {marker})
    try:
        yield from validator.descend(instance, {"$ref": rule["mapping"][selection]})
    finally:
        _active_discriminators.reset(token)


UniFiValidator = validators.extend(Draft202012Validator, {"discriminator": discriminator})


def reject_external_reference(uri):
    raise ValueError("External schema references are not supported")


class SchemaContract:
    def __init__(self, document):
        self.document = document
        self.registry = Registry(retrieve=reject_external_reference)
        self.reachable(document)  # Reject invalid references at catalog load time.

    @lru_cache(maxsize=None)
    def resolve(self, reference):
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError("Only local schema references are supported")
        target = self.document
        try:
            for segment in reference[2:].split("/"):
                target = target[segment.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise ValueError("Unresolved local schema reference: " + reference) from None
        return target

    def reachable(self, value):
        """Return every $ref and discriminator target reachable from a contract."""
        references = {}
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                refs = list(node.get("discriminator", {}).get("mapping", {}).values())
                if "$ref" in node:
                    refs.append(node["$ref"])
                for ref in refs:
                    if ref not in references:
                        references[ref] = self.resolve(ref)
                        pending.append(references[ref])
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
        return references

    def validate(self, value, schema):
        self.reachable(schema)
        root = {**schema, "components": self.document.get("components", {})}
        validator = UniFiValidator(root, registry=self.registry, format_checker=FormatChecker())
        error = next(validator.iter_errors(value), None)
        if error:
            # Never expose submitted values (including credentials) in errors.
            location = ".".join(map(str, error.absolute_path)) or "<root>"
            raise ValueError(f"Invalid input at {location}: {error.validator} constraint")
