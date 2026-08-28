"""Dependency-neutral locale, plural, and visible-text services.

The dashboard is rendered in several hosts, including static files where a
browser i18n runtime is unavailable.  This module keeps that boundary
deterministic and stdlib-only: BCP 47 catalog fallback, cardinal plural
selection, and Unicode grapheme-aware truncation all happen before markup is
projected.  Catalogs are loaded per call rather than through mutable process
state so tests, concurrent renders, and nested locale overrides cannot leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Mapping


DEFAULT_LOCALE = "en"
LOCALE_ENVIRONMENT = (
    "TASKPLANE_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG",
)
_LOCALE_RE = re.compile(
    r"^[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*$"
)


@dataclass(frozen=True)
class MessageCatalog:
    """One resolved immutable message view and its fallback diagnostics."""

    requested_locale: str
    resolved_locales: tuple[str, ...]
    messages: Mapping[str, str]
    errors: tuple[str, ...] = ()

    def format(self, key: str, **values: object) -> str:
        try:
            template = self.messages[key]
        except KeyError as exc:
            raise KeyError(f"message {key!r} is absent from locale catalog") \
                from exc
        return format_message(template, locale=self.requested_locale,
                              values=values)


@dataclass(frozen=True)
class VisibleText:
    """A grapheme-safe visible projection paired with the lossless source."""

    visible: str
    full: str
    truncated: bool
    visible_graphemes: int


def normalize_locale(locale: str | None) -> str:
    """Return a stable BCP 47-like locale or the explicit English default."""

    raw = str(locale or "").strip()
    if not raw:
        for name in LOCALE_ENVIRONMENT:
            raw = os.environ.get(name, "").strip()
            if raw:
                break
    raw = raw.split(".", 1)[0].split("@", 1)[0]
    if raw.upper() in {"C", "POSIX"} or not _LOCALE_RE.fullmatch(raw):
        return DEFAULT_LOCALE
    bits = raw.replace("_", "-").split("-")
    canonical = [bits[0].lower()]
    for bit in bits[1:]:
        if len(bit) == 4 and bit.isalpha():
            canonical.append(bit.title())
        elif len(bit) in {2, 3} and bit.isalpha():
            canonical.append(bit.upper())
        else:
            canonical.append(bit.lower())
    return "-".join(canonical)


def locale_fallbacks(locale: str | None) -> tuple[str, ...]:
    """Return most-specific to default fallback names without duplicates."""

    normalized = normalize_locale(locale)
    bits = normalized.split("-")
    chain = ["-".join(bits[:size]) for size in range(len(bits), 0, -1)]
    if DEFAULT_LOCALE not in chain:
        chain.append(DEFAULT_LOCALE)
    return tuple(chain)


def _catalog_directory(catalog_dir: str | os.PathLike[str] | None) -> Path:
    return (Path(catalog_dir) if catalog_dir is not None
            else Path(__file__).with_name("locales"))


def load_catalog(locale: str | None = None, *,
                 catalog_dir: str | os.PathLike[str] | None = None
                 ) -> MessageCatalog:
    """Load a catalog with deterministic BCP 47 fallback to English.

    Missing optional regional catalogs simply fall through.  A present but
    malformed catalog is ignored and recorded in ``errors`` so the dashboard
    can surface the locale-fallback signal without losing English output.
    """

    requested = normalize_locale(locale)
    chain = locale_fallbacks(requested)
    root = _catalog_directory(catalog_dir)
    merged: dict[str, str] = {}
    loaded: list[str] = []
    errors: list[str] = []
    # Apply the default first, then increasingly specific overrides.
    for candidate in reversed(chain):
        path = root / f"{candidate}.json"
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            rows = document.get("messages") if isinstance(document, dict) \
                else None
            if not isinstance(rows, dict):
                raise ValueError("top-level messages object is required")
            invalid = [key for key, value in rows.items()
                       if not isinstance(key, str) or not isinstance(value, str)]
            if invalid:
                raise ValueError("message keys and values must be strings")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        merged.update(rows)
        loaded.append(candidate)
    if not merged:
        raise RuntimeError(f"no valid locale catalog found under {root}")
    return MessageCatalog(
        requested_locale=requested,
        resolved_locales=tuple(reversed(loaded)),
        messages=merged,
        errors=tuple(errors),
    )


def message(key: str, *, locale: str | None = None,
            catalog_dir: str | os.PathLike[str] | None = None,
            **values: object) -> str:
    """Resolve and format one complete catalog message."""

    return load_catalog(locale, catalog_dir=catalog_dir).format(key, **values)


def _number(value: object) -> tuple[Decimal, int, int]:
    """Return CLDR's absolute n, integer i, and visible-fraction digit v."""

    try:
        text = str(value).strip()
        number = abs(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"plural value must be numeric, got {value!r}") from exc
    plain = text.lstrip("+-")
    visible = len(plain.partition(".")[2]) if "." in plain else 0
    return number, int(number), visible


def plural_category(locale: str | None, value: object) -> str:
    """Select a CLDR cardinal category for common Taskplane locales.

    These rules cover all six cardinal categories and the locale families
    called out by the review (Arabic, Polish, Russian, and Welsh), while
    retaining a deterministic ``one``/``other`` rule for unknown locales.
    """

    language = normalize_locale(locale).split("-", 1)[0]
    n, i, v = _number(value)
    n10, n100 = i % 10, i % 100
    if language == "ar":
        if n == 0:
            return "zero"
        if n == 1:
            return "one"
        if n == 2:
            return "two"
        if 3 <= n100 <= 10:
            return "few"
        if 11 <= n100 <= 99:
            return "many"
        return "other"
    if language == "cy":
        return {0: "zero", 1: "one", 2: "two", 3: "few",
                6: "many"}.get(n, "other")
    if language in {"ru", "uk", "be"} and v == 0:
        if n10 == 1 and n100 != 11:
            return "one"
        if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
            return "few"
        if n10 == 0 or 5 <= n10 <= 9 or 11 <= n100 <= 14:
            return "many"
        return "other"
    if language == "pl" and v == 0:
        if i == 1:
            return "one"
        if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
            return "few"
        if i != 1 and (n10 in {0, 1} or 5 <= n10 <= 9
                       or 12 <= n100 <= 14):
            return "many"
        return "other"
    if language in {"cs", "sk"}:
        if i == 1 and v == 0:
            return "one"
        if 2 <= i <= 4 and v == 0:
            return "few"
        return "many" if v else "other"
    if language == "sl" and v == 0:
        if n100 == 1:
            return "one"
        if n100 == 2:
            return "two"
        if n100 in {3, 4}:
            return "few"
        return "other"
    if language == "ro":
        if i == 1 and v == 0:
            return "one"
        if v != 0 or n == 0 or 1 <= n100 <= 19:
            return "few"
        return "other"
    if language == "lt" and v == 0:
        if n10 == 1 and not 11 <= n100 <= 19:
            return "one"
        if 2 <= n10 <= 9 and not 11 <= n100 <= 19:
            return "few"
        if v:
            return "many"
        return "other"
    if language == "lv" and v == 0:
        if n10 == 0 or 11 <= n100 <= 19:
            return "zero"
        if n10 == 1 and n100 != 11:
            return "one"
        return "other"
    if language == "ga" and v == 0:
        if i == 1:
            return "one"
        if i == 2:
            return "two"
        if 3 <= i <= 6:
            return "few"
        if 7 <= i <= 10:
            return "many"
        return "other"
    if language in {"fr", "pt"} and v == 0 and i in {0, 1}:
        return "one"
    return "one" if i == 1 and v == 0 else "other"


def _balanced_end(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced message braces")


def _plural_options(source: str) -> dict[str, str]:
    options: dict[str, str] = {}
    index = 0
    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        key_start = index
        while index < len(source) and not source[index].isspace() \
                and source[index] != "{":
            index += 1
        key = source[key_start:index]
        while index < len(source) and source[index].isspace():
            index += 1
        if not key or index >= len(source) or source[index] != "{":
            raise ValueError("malformed plural option")
        end = _balanced_end(source, index)
        options[key] = source[index + 1:end]
        index = end + 1
    if "other" not in options:
        raise ValueError("plural message requires an other branch")
    return options


def format_message(template: str, *, locale: str | None,
                   values: Mapping[str, object]) -> str:
    """Format named variables and ICU-shaped cardinal plural expressions."""

    output: list[str] = []
    index = 0
    while index < len(template):
        if template[index] != "{":
            output.append(template[index])
            index += 1
            continue
        end = _balanced_end(template, index)
        expression = template[index + 1:end]
        pieces = expression.split(",", 2)
        name = pieces[0].strip()
        if name not in values:
            raise KeyError(f"message variable {name!r} was not supplied")
        if len(pieces) == 1:
            rendered = str(values[name])
        elif len(pieces) == 3 and pieces[1].strip() == "plural":
            options = _plural_options(pieces[2])
            value = values[name]
            exact = f"={value}"
            selected = options.get(exact)
            if selected is None:
                selected = options.get(plural_category(locale, value),
                                       options["other"])
            selected = selected.replace("#", str(value))
            rendered = format_message(selected, locale=locale, values=values)
        else:
            raise ValueError(f"unsupported message expression {{{expression}}}")
        output.append(rendered)
        index = end + 1
    return "".join(output)


def _is_extend(character: str) -> bool:
    codepoint = ord(character)
    return (unicodedata.category(character).startswith("M")
            or 0xFE00 <= codepoint <= 0xFE0F
            or 0xE0100 <= codepoint <= 0xE01EF
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
            or codepoint == 0x20E3)


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def grapheme_clusters(text: object) -> tuple[str, ...]:
    """Segment user-visible text without splitting common extended clusters.

    The stdlib has no UAX #29 iterator.  This bounded implementation preserves
    combining sequences, emoji modifiers and ZWJ families, flag pairs, and
    Indic virama conjuncts—the destructive cases found in the dashboard.
    """

    source = str(text)
    if not source:
        return ()
    clusters: list[str] = []
    current = ""
    regional_count = 0
    for character in source:
        if not current:
            current = character
            regional_count = 1 if _is_regional_indicator(character) else 0
            continue
        previous = current[-1]
        previous_name = unicodedata.name(previous, "")
        join = (
            _is_extend(character)
            or previous == "\u200d"
            or character in {"\u200c", "\u200d"}
            or "VIRAMA" in previous_name
            or (previous == "\r" and character == "\n")
            or (_is_regional_indicator(character)
                and regional_count % 2 == 1)
        )
        if join:
            current += character
            if _is_regional_indicator(character):
                regional_count += 1
            continue
        clusters.append(current)
        current = character
        regional_count = 1 if _is_regional_indicator(character) else 0
    clusters.append(current)
    return tuple(clusters)


def truncate_graphemes(text: object, limit: int, *,
                       ellipsis: str = "…") -> VisibleText:
    """Return a lossless/grapheme-safe visible pair with an explicit marker."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("grapheme limit must be a non-negative integer")
    full = str(text)
    clusters = grapheme_clusters(full)
    if len(clusters) <= limit:
        return VisibleText(full, full, False, len(clusters))
    visible = "".join(clusters[:limit]) + ellipsis
    return VisibleText(visible, full, True, limit)
