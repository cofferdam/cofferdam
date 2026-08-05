"""Rules every registry shares: IDs, aliases, strict field reading.

Why these rules and not others
------------------------------

**Stable IDs.** Every registry item is addressed by an immutable ASCII
kebab-case ID. Human-facing names change ("Büyük monitör" becomes "Salon TV");
references between registries must not. Keeping IDs ASCII also keeps them safe
to embed in URLs, unit names, log lines and filenames without escaping, while
names and aliases stay fully Unicode.

**Aliases are normalized, never guessed.** A person says "büyük monitör"; the
registry stores "Büyük monitör". Matching those needs Unicode-aware case
folding, and Turkish makes that concrete: ``"İ".casefold()`` is not ``"i"`` and
``"I".lower()`` is not ``"ı"``, so ASCII lowering is wrong here. Normalization
is therefore: NFC → trim → collapse internal whitespace → casefold → NFC. The
original text is always preserved for display.

**Ambiguity fails, it never resolves.** Two items whose names or aliases
normalize to the same string are a validation failure, and the resolver itself
returns *no* match rather than picking one when more than one candidate exists.
A control plane that guesses which display "büyük monitör" meant is worse than
one that asks.

**Unknown fields fail closed.** A typo (``adaptor_key``) that silently loaded as
"absent" would look like a working config with a wrong meaning. There is no
forward-compatibility escape hatch in version 1; adding a field means bumping
:data:`SUPPORTED_VERSION` and documenting the migration.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import errors as reasons
from .errors import RegistryError

SUPPORTED_VERSION = 1

ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_ID_LENGTH = 64
MAX_NAME_LENGTH = 120
MAX_ALIAS_LENGTH = 120
MAX_ALIASES = 24
MAX_NOTES_LENGTH = 500
MAX_ITEMS = 200

# Field names that would turn declarative configuration back into a command,
# an executable path, or a credential store. Unknown fields already fail, so
# this list changes nothing about what is *accepted* — it exists so the refusal
# names the actual problem instead of saying "unknown field", and so the
# boundary is greppable and testable as an explicit artifact.
FORBIDDEN_FIELDS = frozenset(
    {
        "api_key",
        "args",
        "argv",
        "cmd",
        "command",
        "conversation_id",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "desktop_file",
        "desktop_path",
        "env",
        "environment",
        "exec",
        "executable",
        "executable_path",
        "extension_id",
        "message",
        "password",
        "passwords",
        "path",
        "paths",
        "profile_dir",
        "profile_directory",
        "profile_path",
        "prompt",
        "prompts",
        "script",
        "secret",
        "secrets",
        "selector",
        "selectors",
        "shell",
        "tab_id",
        "token",
        "tokens",
        "user_data_dir",
        "working_directory",
    }
)


# ---------------------------------------------------------------------------
# identifiers and aliases
# ---------------------------------------------------------------------------


def is_valid_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LENGTH
        and ID_PATTERN.match(value) is not None
    )


# Turkish tailoring, applied to the *comparison key only*.
#
# Unicode's language-neutral case folding is deliberately reversible, so
# ``"İ".casefold()`` is ``"i"`` + U+0307 COMBINING DOT ABOVE rather than plain
# ``"i"``, and ``"ı"`` folds to itself. Left alone, that means "MONİTÖR" would
# not match "monitör" and "IŞIK" would not match "ışık" — exactly the phrases
# this product exists to understand. Both dotted and dotless I are therefore
# folded onto ``i`` for matching.
#
# The cost is that two aliases differing only by ı/i become the same key. That
# is not a silent conflation: duplicate keys are a validation failure, so the
# registry refuses to load instead of guessing. Display text is never touched.
_COMBINING_DOT_ABOVE = "̇"
_DOTLESS_I = "ı"


def normalize_alias(value: str) -> str:
    """Fold a human phrase to its comparison key.

    NFC first so composed and decomposed spellings of the same Turkish letter
    agree, then whitespace trim/collapse, then Unicode case folding, then NFC
    again because folding can decompose, then the dotted/dotless I tailoring
    described above.
    """
    text = unicodedata.normalize("NFC", value)
    text = " ".join(text.split())
    text = unicodedata.normalize("NFC", text.casefold())
    return text.replace("i" + _COMBINING_DOT_ABOVE, "i").replace(_DOTLESS_I, "i")


# ---------------------------------------------------------------------------
# strict field reading
# ---------------------------------------------------------------------------


class ItemReader:
    """Reads one item's fields and refuses anything it was not asked for.

    Each accessor consumes a key; :meth:`finish` fails if any key is left over.
    That inverts the usual "read what I know about" pattern into "account for
    every field", which is what makes unknown-field rejection structural.
    """

    def __init__(self, registry: str, where: str, raw: Any) -> None:
        self.registry = registry
        self.where = where
        if not isinstance(raw, dict):
            raise self.fail(reasons.INVALID_VALUE, "each item must be a JSON object")
        for key in raw:
            if not isinstance(key, str):  # pragma: no cover - json keys are str
                raise self.fail(reasons.INVALID_VALUE, "field names must be strings")
            if key in FORBIDDEN_FIELDS:
                raise self.fail(
                    reasons.FORBIDDEN_FIELD,
                    "registries never carry executables, commands, filesystem paths, "
                    "or credentials; this field is not accepted anywhere",
                    field=key,
                )
        self._raw: Dict[str, Any] = dict(raw)
        self._seen: set = set()

    # -- failures ------------------------------------------------------------

    def fail(self, reason: str, message: str, field: Optional[str] = None) -> RegistryError:
        where = self.where if field is None else f"{self.where}.{field}"
        return RegistryError(self.registry, reason, message, where=where)

    # -- accessors -----------------------------------------------------------

    def _take(self, field: str) -> Any:
        self._seen.add(field)
        return self._raw.get(field, _MISSING)

    def string(
        self,
        field: str,
        *,
        required: bool = True,
        default: Optional[str] = None,
        max_length: int = MAX_NAME_LENGTH,
        allow_none: bool = False,
        allow_empty: bool = False,
    ) -> Optional[str]:
        value = self._take(field)
        if value is _MISSING:
            if required:
                raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
            return default
        if value is None:
            if allow_none:
                return None
            raise self.fail(reasons.INVALID_VALUE, "this field must not be null", field=field)
        if not isinstance(value, str):
            raise self.fail(reasons.INVALID_VALUE, "this field must be a string", field=field)
        if len(value) > max_length:
            raise self.fail(
                reasons.INVALID_VALUE,
                f"this field must be at most {max_length} characters",
                field=field,
            )
        if _has_control_characters(value):
            raise self.fail(
                reasons.INVALID_VALUE, "this field must not contain control characters", field=field
            )
        if not allow_empty and not value.strip():
            raise self.fail(reasons.INVALID_VALUE, "this field must not be blank", field=field)
        return value

    def identifier(self, field: str, *, required: bool = True, allow_none: bool = False) -> Optional[str]:
        value = self._take(field)
        if value is _MISSING:
            if required:
                raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
            return None
        if value is None:
            if allow_none or not required:
                return None
            raise self.fail(reasons.INVALID_VALUE, "this field must not be null", field=field)
        if not is_valid_id(value):
            raise self.fail(
                reasons.INVALID_VALUE,
                "must be a stable ASCII kebab-case id matching "
                "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
                field=field,
            )
        return value

    def boolean(self, field: str, *, required: bool = True, default: bool = False) -> bool:
        value = self._take(field)
        if value is _MISSING:
            if required:
                raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
            return default
        if not isinstance(value, bool):
            raise self.fail(reasons.INVALID_VALUE, "this field must be true or false", field=field)
        return value

    def choice(self, field: str, allowed: Sequence[str], *, required: bool = True) -> str:
        value = self.string(field, required=required, max_length=MAX_ID_LENGTH)
        if value not in allowed:
            raise self.fail(
                reasons.INVALID_VALUE,
                "must be one of: " + ", ".join(allowed),
                field=field,
            )
        return value

    def aliases(self, field: str = "aliases") -> Tuple[str, ...]:
        value = self._take(field)
        if value is _MISSING:
            raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
        if not isinstance(value, list):
            raise self.fail(reasons.INVALID_VALUE, "this field must be a list", field=field)
        if len(value) > MAX_ALIASES:
            raise self.fail(
                reasons.INVALID_VALUE, f"at most {MAX_ALIASES} aliases are allowed", field=field
            )
        collected: List[str] = []
        for index, entry in enumerate(value):
            location = f"{field}[{index}]"
            if not isinstance(entry, str):
                raise self.fail(reasons.INVALID_VALUE, "each alias must be a string", field=location)
            if len(entry) > MAX_ALIAS_LENGTH:
                raise self.fail(
                    reasons.INVALID_VALUE,
                    f"an alias must be at most {MAX_ALIAS_LENGTH} characters",
                    field=location,
                )
            if _has_control_characters(entry):
                raise self.fail(
                    reasons.INVALID_VALUE,
                    "an alias must not contain control characters",
                    field=location,
                )
            if not normalize_alias(entry):
                raise self.fail(reasons.INVALID_VALUE, "an alias must not be blank", field=location)
            collected.append(entry)
        return tuple(collected)

    def mapping(self, field: str, *, required: bool = True) -> Optional["ItemReader"]:
        value = self._take(field)
        if value is _MISSING or value is None:
            if required:
                raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
            return None
        return ItemReader(self.registry, f"{self.where}.{field}", value)

    def string_list(self, field: str, *, max_entries: int) -> Tuple[str, ...]:
        value = self._take(field)
        if value is _MISSING:
            raise self.fail(reasons.MISSING_FIELD, "this field is required", field=field)
        if not isinstance(value, list):
            raise self.fail(reasons.INVALID_VALUE, "this field must be a list", field=field)
        if len(value) > max_entries:
            raise self.fail(
                reasons.INVALID_VALUE, f"at most {max_entries} entries are allowed", field=field
            )
        for index, entry in enumerate(value):
            if not isinstance(entry, str):
                raise self.fail(
                    reasons.INVALID_VALUE, "each entry must be a string", field=f"{field}[{index}]"
                )
        return tuple(value)

    def finish(self) -> None:
        leftover = sorted(set(self._raw) - self._seen)
        if leftover:
            raise self.fail(
                reasons.UNKNOWN_FIELD,
                "unknown field; version 1 accepts no additional fields",
                field=leftover[0],
            )


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def parse_envelope(registry: str, document: Any) -> List[Any]:
    """Validate ``{"version": 1, "items": [...]}`` and return the raw items."""
    if not isinstance(document, dict):
        raise RegistryError(
            registry,
            reasons.INVALID_ENVELOPE,
            "the file must contain a JSON object with 'version' and 'items'",
        )
    unknown = sorted(set(document) - {"version", "items"})
    if unknown:
        raise RegistryError(
            registry,
            reasons.UNKNOWN_FIELD,
            "unknown top-level field; only 'version' and 'items' are accepted",
            where=unknown[0],
        )
    if "version" not in document:
        raise RegistryError(registry, reasons.MISSING_FIELD, "'version' is required", where="version")
    version = document["version"]
    # ``True`` is an int in Python; a boolean version is a malformed file.
    if isinstance(version, bool) or not isinstance(version, int):
        raise RegistryError(
            registry, reasons.INVALID_VALUE, "'version' must be an integer", where="version"
        )
    if version != SUPPORTED_VERSION:
        # Fail closed: a newer file may mean something this build cannot honour,
        # and guessing at it is how a permission boundary quietly widens.
        raise RegistryError(
            registry,
            reasons.UNSUPPORTED_VERSION,
            f"unsupported registry version; this build understands version {SUPPORTED_VERSION}",
            where="version",
        )
    if "items" not in document:
        raise RegistryError(registry, reasons.MISSING_FIELD, "'items' is required", where="items")
    items = document["items"]
    if not isinstance(items, list):
        raise RegistryError(registry, reasons.INVALID_VALUE, "'items' must be a list", where="items")
    if len(items) > MAX_ITEMS:
        raise RegistryError(
            registry,
            reasons.INVALID_VALUE,
            f"at most {MAX_ITEMS} items are allowed in one registry",
            where="items",
        )
    return items


# ---------------------------------------------------------------------------
# uniqueness
# ---------------------------------------------------------------------------


def build_id_index(registry: str, items: Sequence[Any]) -> Dict[str, Any]:
    """Map id → item, failing on duplicates. IDs are compared exactly."""
    index: Dict[str, Any] = {}
    for position, item in enumerate(items):
        if item.id in index:
            raise RegistryError(
                registry,
                reasons.DUPLICATE_ID,
                f"duplicate id '{item.id}'; ids must be unique within a registry",
                where=f"items[{position}].id",
            )
        index[item.id] = item
    return index


def build_alias_index(registry: str, items: Sequence[Any]) -> Dict[str, Tuple[str, ...]]:
    """Map normalized phrase → the ids it names.

    Both ``name`` and every entry of ``aliases`` are indexed: the display
    registry's *name* is "Büyük monitör", so the phrase a person would actually
    say has to resolve through the name as well as the alias list.

    The value is a tuple rather than a single id on purpose. Duplicates are a
    validation failure, so a well-formed registry only ever yields one — but
    resolution then cannot silently pick a winner even if that check were ever
    bypassed.
    """
    index: Dict[str, List[str]] = {}
    for position, item in enumerate(items):
        phrases = [("name", item.name)] + [(f"aliases[{i}]", a) for i, a in enumerate(item.aliases)]
        for field, phrase in phrases:
            key = normalize_alias(phrase)
            if key in index:
                raise RegistryError(
                    registry,
                    reasons.DUPLICATE_ALIAS,
                    "this name or alias normalizes to one already used in this registry; "
                    "an ambiguous phrase can never be resolved safely",
                    where=f"items[{position}].{field}",
                )
            index[key] = [item.id]
    return {key: tuple(value) for key, value in index.items()}


def require_reference(
    registry: str,
    field_path: str,
    value: Optional[str],
    known: Mapping[str, Any],
    target_registry: str,
) -> None:
    if value is None:
        return
    if value not in known:
        raise RegistryError(
            registry,
            reasons.DANGLING_REFERENCE,
            f"'{value}' is not an id in the {target_registry} registry",
            where=field_path,
        )


def enabled_ids(items: Iterable[Any]) -> Dict[str, Any]:
    return {item.id: item for item in items if item.enabled}
