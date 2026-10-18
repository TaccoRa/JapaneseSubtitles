"""Safe, structured query plans for GitHub subtitle mirror searches.

The GitHub search wrapper belongs to :mod:`subtitle_manager`; this module only
describes the user-configurable *phrase* portion of a query.  Keeping that
boundary explicit prevents a profile from injecting GitHub search operators
such as ``repo:`` or ``path:``.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import re
import string
from typing import Any, Iterable, Mapping, Sequence


_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UNSAFE_TEMPLATE_RE = re.compile(r'[\"\'\r\n:]')
_ALLOWED_TEMPLATE_FIELDS = frozenset({"show", "season", "season02"})
_MAX_TEMPLATE_LENGTH = 240
_MAX_LABEL_LENGTH = 80


class RemoteSearchProfileError(ValueError):
    """Raised when a configured remote-search profile is unsafe or malformed."""


@dataclass(frozen=True)
class RemoteSearchProfile:
    """One provider phrase that can be searched for a season.

    ``rank`` is normalized from the profile order.  It is intentionally not
    trusted as an arbitrary priority coming from configuration, so identical
    configurations always generate identical plans and cache fingerprints.
    """

    id: str
    label: str
    enabled: bool
    template: str
    rank: int = 0

    def to_config(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RemoteSearchQuery:
    """A single safe phrase to be passed to the fixed GitHub query wrapper."""

    profile_id: str
    profile_label: str
    rank: int
    phrase: str
    season: int
    fallback_kind: str = "provider"
    is_seasonal: bool = True

    @property
    def is_provider(self) -> bool:
        return self.fallback_kind == "provider"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_label": self.profile_label,
            "rank": self.rank,
            "phrase": self.phrase,
            "season": self.season,
            "fallback_kind": self.fallback_kind,
            "is_seasonal": self.is_seasonal,
        }


DEFAULT_REMOTE_SEARCH_PROFILES: tuple[RemoteSearchProfile, ...] = (
    RemoteSearchProfile("netflix", "Netflix", True, "{show} s{season02} Netflix", 0),
    RemoteSearchProfile("amazon", "Amazon", True, "{show} s{season02} Amazon", 1),
    RemoteSearchProfile("hulu", "Hulu", True, "{show} s{season02} Hulu", 2),
)


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise RemoteSearchProfileError(f"{field} must be text.")
    text = " ".join(value.split())
    if not text:
        raise RemoteSearchProfileError(f"{field} cannot be empty.")
    if len(text) > maximum:
        raise RemoteSearchProfileError(f"{field} is too long.")
    return text


def validate_remote_search_template(template: Any) -> str:
    """Validate a phrase-only template and return its normalized form.

    Profiles are deliberately unable to add quotes, GitHub search fields, or
    new lines.  The manager owns ``repo:``, ``path:``, extensions, and the
    query quoting/wrapper.
    """

    text = _clean_text(template, field="template", maximum=_MAX_TEMPLATE_LENGTH)
    if _UNSAFE_TEMPLATE_RE.search(text):
        raise RemoteSearchProfileError(
            "template cannot contain quotes, colons, or line breaks; GitHub query operators are managed by the app."
        )

    formatter = string.Formatter()
    try:
        parts = list(formatter.parse(text))
    except ValueError as exc:
        raise RemoteSearchProfileError(f"template is malformed: {exc}") from exc

    for _literal, field_name, format_spec, conversion in parts:
        if field_name is None:
            continue
        if field_name not in _ALLOWED_TEMPLATE_FIELDS:
            allowed = ", ".join(sorted(_ALLOWED_TEMPLATE_FIELDS))
            raise RemoteSearchProfileError(f"template field {{{field_name}}} is not allowed; use {allowed}.")
        if format_spec or conversion:
            raise RemoteSearchProfileError("template fields cannot use conversions or format specifications.")
    return text


def _profile_from_mapping(raw: Mapping[str, Any], index: int) -> RemoteSearchProfile:
    profile_id = _clean_text(raw.get("id"), field="profile id", maximum=64).casefold()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise RemoteSearchProfileError(
            "profile id must use lowercase letters, digits, '-' or '_', and begin with a letter or digit."
        )
    label = _clean_text(raw.get("label"), field="profile label", maximum=_MAX_LABEL_LENGTH)
    template = validate_remote_search_template(raw.get("template"))
    return RemoteSearchProfile(
        id=profile_id,
        label=label,
        enabled=_coerce_bool(raw.get("enabled"), default=True),
        template=template,
        rank=index,
    )


def normalize_remote_search_profiles(
    profiles: Any,
    *,
    use_defaults_when_empty: bool = True,
) -> tuple[RemoteSearchProfile, ...]:
    """Parse JSON-compatible profile configuration into a deterministic tuple.

    ``None`` uses the built-ins by default; an explicit empty list deliberately
    means that all provider profiles are disabled.  JSON strings are accepted
    so the model can consume settings saved by an older text-only UI, while the
    canonical output remains a list of dictionaries.
    """

    if profiles is None or profiles == "":
        return tuple(DEFAULT_REMOTE_SEARCH_PROFILES) if use_defaults_when_empty else ()
    if isinstance(profiles, str):
        try:
            profiles = json.loads(profiles)
        except json.JSONDecodeError as exc:
            raise RemoteSearchProfileError("profiles must be valid JSON.") from exc
    if not isinstance(profiles, Sequence) or isinstance(profiles, (bytes, bytearray, str)):
        raise RemoteSearchProfileError("profiles must be a list.")
    if not profiles:
        return ()

    normalized: list[RemoteSearchProfile] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(profiles):
        if isinstance(raw, RemoteSearchProfile):
            profile = RemoteSearchProfile(
                id=_clean_text(raw.id, field="profile id", maximum=64).casefold(),
                label=_clean_text(raw.label, field="profile label", maximum=_MAX_LABEL_LENGTH),
                enabled=_coerce_bool(raw.enabled, default=True),
                template=validate_remote_search_template(raw.template),
                rank=index,
            )
            if not _PROFILE_ID_RE.fullmatch(profile.id):
                raise RemoteSearchProfileError(
                    "profile id must use lowercase letters, digits, '-' or '_', and begin with a letter or digit."
                )
        elif isinstance(raw, Mapping):
            profile = _profile_from_mapping(raw, index)
        else:
            raise RemoteSearchProfileError("each profile must be an object.")
        if profile.id in seen_ids:
            raise RemoteSearchProfileError(f"duplicate profile id: {profile.id}")
        seen_ids.add(profile.id)
        normalized.append(profile)
    return tuple(normalized)


def remote_search_profiles_to_config(profiles: Any) -> list[dict[str, Any]]:
    """Return a canonical JSON-compatible profile list for settings/cache data."""

    return [profile.to_config() for profile in normalize_remote_search_profiles(profiles)]


def _clean_show_name(show: Any) -> str:
    if not isinstance(show, str):
        raise ValueError("show must be text.")
    text = " ".join(show.split())
    if not text:
        raise ValueError("show cannot be empty.")
    # Quotes would escape the manager's fixed GitHub quoted phrase.  Removing
    # them is safer than treating a user-entered title as query syntax.
    return text.replace('"', "'")


def build_remote_search_query_plan(
    show: str,
    season: int,
    profiles: Any = None,
    *,
    generic_enabled: bool = False,
    unseasoned_enabled: bool = True,
) -> tuple[RemoteSearchQuery, ...]:
    """Build ordered phrase queries for one show season.

    Provider order is exactly configuration order.  The optional generic
    seasonal fallback runs after providers; the unseasoned fallback runs only
    for season one because it may return every season of a show.
    """

    clean_show = _clean_show_name(show)
    try:
        season_int = int(season)
    except (TypeError, ValueError) as exc:
        raise ValueError("season must be a positive integer.") from exc
    if season_int < 1:
        raise ValueError("season must be a positive integer.")

    normalized_profiles = normalize_remote_search_profiles(profiles)
    values = {"show": clean_show, "season": season_int, "season02": f"{season_int:02d}"}
    plan: list[RemoteSearchQuery] = []
    for profile in normalized_profiles:
        if not profile.enabled:
            continue
        # Templates have already been parsed/validated; format() cannot expose
        # attributes or arbitrary keys after the validation above.
        phrase = profile.template.format(**values)
        plan.append(
            RemoteSearchQuery(
                profile_id=profile.id,
                profile_label=profile.label,
                rank=profile.rank,
                phrase=phrase,
                season=season_int,
            )
        )

    next_rank = len(normalized_profiles)
    if _coerce_bool(generic_enabled, default=False):
        plan.append(
            RemoteSearchQuery(
                profile_id="generic-season",
                profile_label="Generic season",
                rank=next_rank,
                phrase=f"{clean_show} s{season_int:02d}",
                season=season_int,
                fallback_kind="generic_season",
                is_seasonal=True,
            )
        )
        next_rank += 1
    if season_int == 1 and _coerce_bool(unseasoned_enabled, default=True):
        plan.append(
            RemoteSearchQuery(
                profile_id="unseasoned",
                profile_label="Unseasoned fallback",
                rank=next_rank,
                phrase=clean_show,
                season=season_int,
                fallback_kind="unseasoned",
                is_seasonal=False,
            )
        )
    return tuple(plan)


def describe_remote_search_query_plan(plan: Iterable[RemoteSearchQuery]) -> list[str]:
    """Return concise, GUI-ready descriptions without building GitHub syntax."""

    descriptions = []
    for query in plan:
        kind = "provider" if query.is_provider else query.fallback_kind.replace("_", " ")
        descriptions.append(
            f"{query.profile_label} ({kind}, priority {query.rank + 1}): {query.phrase}"
        )
    return descriptions


def remote_search_plan_fingerprint(
    profiles: Any = None,
    *,
    generic_enabled: bool = False,
    unseasoned_enabled: bool = True,
) -> str:
    """Fingerprint all behavior that changes seasonal GitHub search coverage."""

    canonical = {
        "profiles": remote_search_profiles_to_config(profiles),
        "generic_enabled": _coerce_bool(generic_enabled, default=False),
        "unseasoned_enabled": _coerce_bool(unseasoned_enabled, default=True),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def remote_search_plan_metadata(
    profiles: Any = None,
    *,
    generic_enabled: bool = False,
    unseasoned_enabled: bool = True,
) -> dict[str, Any]:
    """Return canonical cache-safe configuration metadata for an executed plan."""

    return {
        "profiles": remote_search_profiles_to_config(profiles),
        "generic_enabled": _coerce_bool(generic_enabled, default=False),
        "unseasoned_enabled": _coerce_bool(unseasoned_enabled, default=True),
    }
