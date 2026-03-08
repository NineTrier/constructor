import json
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from django.conf import settings
from django.db.models import Q

from database_manager.application.services import ObjectDataService, ObjectLinkService
from database_manager.domain.normalize import canonicalize_value
from database_manager.models import (
    Object,
    ObjectLinkMeta,
    ObjectRecord,
    Parameter,
    ParameterValue,
    RecordLink,
)
from document.models import DocumentPattern_Objects


TOKEN_RE = re.compile(r"\{\:.*?\:\}")
TOKEN_ARTIFACT_MARKERS = ("{:", "data-token", "data-invis", "obj(", "link(")


@dataclass(frozen=True)
class ParsedLinkStep:
    link_meta_id: int
    selector: str  # first|all|index
    index: Optional[int] = None


@dataclass(frozen=True)
class ParsedToken:
    token: str
    object_id: int
    param_id: int
    link_steps: Tuple[ParsedLinkStep, ...]

    @property
    def depth(self) -> int:
        return len(self.link_steps)


def canonical_token_from_parsed(parsed: ParsedToken) -> str:
    canonical = "{:obj(" + str(parsed.object_id) + ")"
    for step in parsed.link_steps:
        canonical += ".link(" + str(step.link_meta_id) + ")"
        if step.selector == "all":
            canonical += "[*]"
        elif step.selector == "index" and step.index is not None:
            canonical += "[" + str(step.index) + "]"
    canonical += ".param(" + str(parsed.param_id) + "):}"
    return canonical


class TokenResolveError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
        status: str = "error",
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status


def _normalise_name(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = text.replace("ё", "е")
    return text


def _normalise_token_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalise_role_name(value: Any) -> str:
    text = _normalise_name(value)
    if text.startswith("связь с "):
        text = text[len("связь с ") :].strip()
    elif text.startswith("связь:"):
        text = text[len("связь:") :].strip()
    return text


def _name_variants(value: Any, *, role_mode: bool = False) -> Set[str]:
    base = _normalise_role_name(value) if role_mode else _normalise_name(value)
    variants: Set[str] = set()
    if base:
        variants.add(base)
    if role_mode:
        raw_normalized = _normalise_name(value)
        if raw_normalized:
            variants.add(raw_normalized)
    for item in list(variants):
        if item.endswith("s") and len(item) > 3:
            variants.add(item[:-1])
        for suffix in (
            "ы",
            "и",
            "а",
            "я",
            "ов",
            "ев",
            "ом",
            "ем",
            "ой",
            "ей",
            "ами",
            "ями",
            "ах",
            "ях",
            "ым",
            "им",
            "ого",
            "ему",
        ):
            if item.endswith(suffix) and len(item) > len(suffix) + 2:
                variants.add(item[: -len(suffix)])
    return {item for item in variants if item}


def _values_match(lhs: Any, rhs: Any, *, role_mode: bool = False) -> bool:
    lhs_variants = _name_variants(lhs, role_mode=role_mode)
    rhs_variants = _name_variants(rhs, role_mode=role_mode)
    if not lhs_variants or not rhs_variants:
        return False
    if lhs_variants.intersection(rhs_variants):
        return True
    for left in lhs_variants:
        for right in rhs_variants:
            if len(left) >= 4 and len(right) >= 4 and (left in right or right in left):
                return True
            if len(left) >= 5 and len(right) >= 5 and SequenceMatcher(None, left, right).ratio() >= 0.84:
                return True
    return False


def _as_int_set(values: Iterable[Any]) -> Set[int]:
    result: Set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _human_token_strict_mode_enabled() -> bool:
    return bool(getattr(settings, "DOC_TOKEN_HUMAN_STRICT", False))


def _collect_scope_object_ids(root_ids: Set[int]) -> Set[int]:
    scope: Set[int] = set(int(item) for item in root_ids)
    frontier = set(scope)
    while frontier:
        rows = ObjectLinkMeta.objects.filter(parent_object_id__in=list(frontier)).values_list(
            "parent_object_id",
            "child_object_id",
        )
        next_frontier: Set[int] = set()
        for _parent_id, child_id in rows:
            child_int = int(child_id)
            if child_int in scope:
                continue
            scope.add(child_int)
            next_frontier.add(child_int)
        frontier = next_frontier
    return scope


def build_doc_token_index(document_id: int) -> Dict[str, Any]:
    root_object_ids = _as_int_set(
        DocumentPattern_Objects.objects.filter(document_id=int(document_id)).values_list("object_id", flat=True)
    )
    scope_object_ids = _collect_scope_object_ids(root_object_ids)

    objects_by_name: Dict[str, int] = {}
    for obj in Object.objects.filter(id__in=list(scope_object_ids)):
        objects_by_name[str(obj.name)] = int(obj.id)

    params_by_object_and_name: Dict[str, Dict[str, int]] = {}
    for parameter in Parameter.objects.filter(object_id__in=list(scope_object_ids)).order_by("id"):
        object_key = str(parameter.object_id)
        params_by_object_and_name.setdefault(object_key, {})
        params_by_object_and_name[object_key][str(parameter.name)] = int(parameter.id)

    links_meta_by_parent_and_display: Dict[str, Dict[str, List[int]]] = {}
    links_meta_by_parent_and_link_param_name: Dict[str, Dict[str, List[int]]] = {}
    links_meta_by_parent_and_child_object_name: Dict[str, Dict[str, List[int]]] = {}
    links_meta_by_id: Dict[str, Dict[str, Any]] = {}

    metas = list(
        ObjectLinkMeta.objects.filter(parent_object_id__in=list(scope_object_ids))
        .select_related("child_object")
        .order_by("parent_object_id", "order", "id")
    )
    meta_ids = [int(meta.id) for meta in metas]
    link_param_by_meta_id: Dict[str, int] = {}
    link_param_name_by_meta_id: Dict[str, str] = {}
    if meta_ids:
        for parameter in (
            Parameter.objects.filter(link_meta_id__in=meta_ids)
            .order_by("-is_managed_link_param", "id")
        ):
            meta_key = str(parameter.link_meta_id or "")
            if not meta_key:
                continue
            if meta_key not in link_param_by_meta_id:
                link_param_by_meta_id[meta_key] = int(parameter.id)
                link_param_name_by_meta_id[meta_key] = str(parameter.name or "")

    for meta in metas:
        parent_key = str(meta.parent_object_id)
        display_map = links_meta_by_parent_and_display.setdefault(parent_key, {})
        display_map.setdefault(str(meta.display_name), []).append(int(meta.id))

        link_param_name = str(link_param_name_by_meta_id.get(str(meta.id), "") or "")
        if link_param_name:
            link_param_map = links_meta_by_parent_and_link_param_name.setdefault(parent_key, {})
            link_param_map.setdefault(link_param_name, []).append(int(meta.id))

        child_name = str(getattr(meta.child_object, "name", "") or "")
        if child_name:
            child_map = links_meta_by_parent_and_child_object_name.setdefault(parent_key, {})
            child_map.setdefault(child_name, []).append(int(meta.id))

        links_meta_by_id[str(meta.id)] = {
            "parent_object_id": int(meta.parent_object_id),
            "child_object_id": int(meta.child_object_id),
            "display_name": str(meta.display_name or ""),
            "code": str(meta.code or ""),
            "link_type": str(meta.link_type or "single"),
            "order": int(meta.order or 0),
            "link_parameter_id": link_param_by_meta_id.get(str(meta.id)),
            "link_parameter_name": link_param_name_by_meta_id.get(str(meta.id), ""),
        }

    return {
        "root_object_ids": sorted(root_object_ids),
        "objects_by_name": objects_by_name,
        "params_by_object_and_name": params_by_object_and_name,
        "links_meta_by_parent_and_display": links_meta_by_parent_and_display,
        "links_meta_by_parent_and_child_object_name": links_meta_by_parent_and_child_object_name,
        "links_meta_by_parent_and_link_param_name": links_meta_by_parent_and_link_param_name,
        "links_meta_by_id": links_meta_by_id,
        "link_param_by_meta_id": link_param_by_meta_id,
    }


def parse_human_token(
    token: str,
    *,
    token_index: Mapping[str, Any],
) -> Tuple[Optional[ParsedToken], Optional[TokenResolveError], Optional[str]]:
    strict_mode = _human_token_strict_mode_enabled()
    raw_token = _normalise_token_text(token)
    inner = raw_token
    if inner.startswith("{:") and inner.endswith(":}"):
        inner = inner[2:-2].strip()
    if inner.endswith(":"):
        inner = inner[:-1].strip()
    if not inner:
        return (
            None,
            TokenResolveError(
                "INVALID_TOKEN",
                "РџСѓСЃС‚РѕР№ С‚РѕРєРµРЅ.",
                status="unresolved",
            ),
            None,
        )
    parts = [str(item or "").strip() for item in re.split(r"\s*\.\s*", inner) if str(item or "").strip()]
    if len(parts) < 2:
        return (
            None,
            TokenResolveError(
                "INVALID_TOKEN",
                "РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚ human-С‚РѕРєРµРЅР°. РћР¶РёРґР°РµС‚СЃСЏ РћР±СЉРµРєС‚.РџР°СЂР°РјРµС‚СЂ РёР»Рё РћР±СЉРµРєС‚.Р РѕР»СЊ.РџР°СЂР°РјРµС‚СЂ.",
                status="unresolved",
            ),
            None,
        )
    object_name = parts[0]
    role_parts = parts[1:-1]
    param_name = parts[-1]

    objects_by_name = token_index.get("objects_by_name", {}) if isinstance(token_index, Mapping) else {}
    object_candidates: List[int] = []
    object_exact = objects_by_name.get(object_name)
    if object_exact is not None:
        object_candidates.append(int(object_exact))
    if not strict_mode:
        for name, value in objects_by_name.items():
            if _values_match(name, object_name):
                object_candidates.append(int(value))
    object_candidates = sorted(set(object_candidates))
    if not object_candidates:
        return (
            None,
            TokenResolveError(
                "OBJECT_NOT_FOUND",
                "РћР±СЉРµРєС‚ РёР· С‚РѕРєРµРЅР° РЅРµ РЅР°Р№РґРµРЅ РІ РёРЅРґРµРєСЃРµ РґРѕРєСѓРјРµРЅС‚Р°.",
                details={"object_name": object_name, "normalized_object_name": _normalise_name(object_name)},
                status="unresolved",
            ),
            None,
        )
    if len(object_candidates) > 1:
        return (
            None,
            TokenResolveError(
                "OBJECT_AMBIGUOUS",
                "РРјСЏ РѕР±СЉРµРєС‚Р° РЅРµРѕРґРЅРѕР·РЅР°С‡РЅРѕ: РЅР°Р№РґРµРЅРѕ РЅРµСЃРєРѕР»СЊРєРѕ СЃРѕРІРїР°РґРµРЅРёР№.",
                details={
                    "object_name": object_name,
                    "normalized_object_name": _normalise_name(object_name),
                    "candidates": object_candidates,
                },
                status="unresolved",
            ),
            None,
        )
    object_id = int(object_candidates[0])
    current_object_id = int(object_id)
    links_by_parent_display = token_index.get("links_meta_by_parent_and_display", {}) if isinstance(token_index, Mapping) else {}
    links_by_parent_param_name = token_index.get("links_meta_by_parent_and_link_param_name", {}) if isinstance(token_index, Mapping) else {}
    links_by_parent_child = token_index.get("links_meta_by_parent_and_child_object_name", {}) if isinstance(token_index, Mapping) else {}
    links_by_id = token_index.get("links_meta_by_id", {}) if isinstance(token_index, Mapping) else {}
    link_steps: List[ParsedLinkStep] = []

    for role_name in role_parts:
        role_raw = _normalise_token_text(role_name)
        selector = "first"
        index: Optional[int] = None
        role_match = re.match(r"^(?P<name>.*?)(?:\[(?P<selector>\*|\d+)\])?$", role_raw)
        role_value = role_raw
        if role_match:
            role_value = str(role_match.group("name") or "").strip()
            selector_raw = role_match.group("selector")
            if selector_raw == "*":
                selector = "all"
            elif selector_raw is not None:
                selector = "index"
                index = int(selector_raw)
        if role_value.endswith(":"):
            role_value = role_value[:-1].strip()
        parent_key = str(current_object_id)
        candidate_ids: List[int] = []
        explicit_meta = re.match(r"^link\((\d+)\)$", role_value, flags=re.IGNORECASE)
        if explicit_meta:
            explicit_meta_id = int(explicit_meta.group(1))
            explicit_meta_entry = links_by_id.get(str(explicit_meta_id), {}) or {}
            if int(explicit_meta_entry.get("parent_object_id") or 0) == int(current_object_id):
                candidate_ids.append(explicit_meta_id)

        display_candidates = (links_by_parent_display.get(parent_key, {}) or {}).get(role_value, [])
        if isinstance(display_candidates, (int, str)):
            display_candidates = [display_candidates]
        candidate_ids.extend(int(item) for item in display_candidates if str(item).strip())
        if not candidate_ids:
            child_candidates = (links_by_parent_child.get(parent_key, {}) or {}).get(role_value, [])
            if isinstance(child_candidates, (int, str)):
                child_candidates = [child_candidates]
            if not strict_mode:
                candidate_ids.extend(int(item) for item in child_candidates if str(item).strip())
        if not candidate_ids:
            alt_candidates = (links_by_parent_param_name.get(parent_key, {}) or {}).get(role_value, [])
            if isinstance(alt_candidates, (int, str)):
                alt_candidates = [alt_candidates]
            candidate_ids.extend(int(item) for item in alt_candidates if str(item).strip())

        if not candidate_ids and not strict_mode:
            # Fallback fuzzy matching.
            for lookup_map in (
                links_by_parent_display.get(parent_key, {}) or {},
                links_by_parent_child.get(parent_key, {}) or {},
                links_by_parent_param_name.get(parent_key, {}) or {},
            ):
                for name, ids in lookup_map.items():
                    if not _values_match(name, role_value, role_mode=True):
                        continue
                    if isinstance(ids, (int, str)):
                        candidate_ids.append(int(ids))
                    else:
                        candidate_ids.extend(int(item) for item in ids if str(item).strip())

        if not candidate_ids and not strict_mode:
            role_without_prefix = _normalise_role_name(role_value)
            for lookup_map in (
                links_by_parent_display.get(parent_key, {}) or {},
                links_by_parent_child.get(parent_key, {}) or {},
                links_by_parent_param_name.get(parent_key, {}) or {},
            ):
                for name, ids in lookup_map.items():
                    if not _values_match(name, role_without_prefix, role_mode=True):
                        continue
                    if isinstance(ids, (int, str)):
                        candidate_ids.append(int(ids))
                    else:
                        candidate_ids.extend(int(item) for item in ids if str(item).strip())

        candidate_ids = sorted(set(candidate_ids))
        if not candidate_ids:
            return (
                None,
                TokenResolveError(
                    "LINK_ROLE_NOT_FOUND",
                    "РЎРІСЏР·СЊ (СЂРѕР»СЊ) РёР· С‚РѕРєРµРЅР° РЅРµ РЅР°Р№РґРµРЅР°.",
                    details={
                        "parent_object_id": current_object_id,
                        "role_name": role_value,
                        "normalized_role_name": _normalise_role_name(role_value),
                    },
                    status="unresolved",
                ),
                None,
            )
        if len(candidate_ids) > 1:
            return (
                None,
                TokenResolveError(
                    "LINK_ROLE_AMBIGUOUS",
                    "Р РѕР»СЊ РёР· С‚РѕРєРµРЅР° РЅРµРѕРґРЅРѕР·РЅР°С‡РЅР°: РЅР°Р№РґРµРЅРѕ РЅРµСЃРєРѕР»СЊРєРѕ СЃРІСЏР·РµР№.",
                    details={"parent_object_id": current_object_id, "role_name": role_value, "candidates": candidate_ids},
                    status="unresolved",
                ),
                None,
            )
        link_meta_id = int(candidate_ids[0])
        link_info = links_by_id.get(str(link_meta_id), {}) or {}
        child_object_id = int(link_info.get("child_object_id") or 0)
        if not child_object_id:
            return (
                None,
                TokenResolveError(
                    "LINK_ROLE_NOT_FOUND",
                    "Р”Р»СЏ СЂРѕР»Рё РЅРµ РЅР°Р№РґРµРЅ РґРѕС‡РµСЂРЅРёР№ РѕР±СЉРµРєС‚.",
                    details={"link_meta_id": link_meta_id},
                    status="unresolved",
                ),
                None,
            )
        link_steps.append(ParsedLinkStep(link_meta_id=link_meta_id, selector=selector, index=index))
        current_object_id = child_object_id

    params_by_object = token_index.get("params_by_object_and_name", {}) if isinstance(token_index, Mapping) else {}
    param_id = (params_by_object.get(str(current_object_id), {}) or {}).get(param_name)
    if not param_id and not strict_mode:
        # Fallback case-insensitive / fuzzy match.
        for name, value in (params_by_object.get(str(current_object_id), {}) or {}).items():
            if _values_match(name, param_name):
                param_id = value
                break
    if not param_id:
        return (
            None,
            TokenResolveError(
                "PARAM_NOT_FOUND",
                "РџР°СЂР°РјРµС‚СЂ РёР· С‚РѕРєРµРЅР° РЅРµ РЅР°Р№РґРµРЅ РІ С†РµР»РµРІРѕРј РѕР±СЉРµРєС‚Рµ.",
                details={"object_id": current_object_id, "param_name": param_name},
                status="unresolved",
            ),
            None,
        )

    parsed = ParsedToken(
        token=raw_token,
        object_id=int(object_id),
        param_id=int(param_id),
        link_steps=tuple(link_steps),
    )
    canonical = canonical_token_from_parsed(parsed)
    return parsed, None, canonical


class ExportFinalizeError(Exception):
    def __init__(self, message: str, *, results: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.results = results or []


def parse_canonical_token(token: str) -> Optional[ParsedToken]:
    raw = _normalise_token_text(token)
    if raw.startswith("{:") and raw.endswith(":}"):
        raw = raw[2:-2].strip()
    if raw.endswith(":"):
        raw = raw[:-1].strip()
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        return None

    object_match = re.match(r"^obj\((\d+)\)", raw, flags=re.IGNORECASE)
    if not object_match:
        return None
    object_id = int(object_match.group(1))
    cursor = object_match.end()
    link_steps: List[ParsedLinkStep] = []

    while cursor < len(raw):
        remainder = raw[cursor:]
        link_match = re.match(r"^\.link\((\d+)\)(?:\[(\*|\d+)\])?", remainder, flags=re.IGNORECASE)
        if link_match:
            selector_raw = link_match.group(2)
            selector = "first"
            index: Optional[int] = None
            if selector_raw == "*":
                selector = "all"
            elif selector_raw is not None:
                selector = "index"
                index = int(selector_raw)
            link_steps.append(
                ParsedLinkStep(
                    link_meta_id=int(link_match.group(1)),
                    selector=selector,
                    index=index,
                )
            )
            cursor += link_match.end()
            continue

        param_match = re.match(r"^\.param\((\d+)\)$", remainder, flags=re.IGNORECASE)
        if not param_match:
            return None
        param_id = int(param_match.group(1))
        return ParsedToken(
            token=str(token or "").strip(),
            object_id=object_id,
            param_id=param_id,
            link_steps=tuple(link_steps),
        )

    return None


def collect_tokens_from_text(text: str) -> List[str]:
    return [match.group(0) for match in TOKEN_RE.finditer(str(text or ""))]


def _looks_like_serialised_json(value: str) -> bool:
    payload = str(value or "").strip()
    if not payload:
        return False
    if payload[0] == "{" and payload[-1] == "}":
        return True
    if payload[0] == "[" and payload[-1] == "]":
        return True
    return False


def _iter_text_values(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "text" and isinstance(value, str):
                yield value
                continue
            yield from _iter_text_values(value)
        return
    if isinstance(node, list):
        for value in node:
            yield from _iter_text_values(value)
        return
    if isinstance(node, str) and _looks_like_serialised_json(node):
        try:
            parsed = json.loads(node)
        except (TypeError, ValueError):
            return
        yield from _iter_text_values(parsed)


def _transform_text_values(node: Any, transform) -> Any:
    if isinstance(node, dict):
        transformed: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "text" and isinstance(value, str):
                transformed[key] = transform(value)
            else:
                transformed[key] = _transform_text_values(value, transform)
        return transformed
    if isinstance(node, list):
        return [_transform_text_values(item, transform) for item in node]
    if isinstance(node, str) and _looks_like_serialised_json(node):
        try:
            parsed = json.loads(node)
        except (TypeError, ValueError):
            return node
        transformed = _transform_text_values(parsed, transform)
        return json.dumps(transformed, ensure_ascii=False)
    return node


def _extract_context_map(context: Any) -> Dict[str, str]:
    if not isinstance(context, Mapping):
        return {}
    result: Dict[str, str] = {}
    for key, value in context.items():
        key_str = str(key).strip()
        value_str = str(value).strip()
        if not key_str or not value_str:
            continue
        result[key_str] = value_str
    return result


class _RequestCaches:
    def __init__(self):
        self.object_cache: Dict[int, Optional[Object]] = {}
        self.parameters_cache: Dict[int, List[Parameter]] = {}
        self.parameter_by_id: Dict[Tuple[int, int], Optional[Parameter]] = {}
        self.sql_record_cache: Dict[Tuple[int, str], Optional[ObjectRecord]] = {}
        self.sql_field_cache: Dict[Tuple[int, int], Optional[ParameterValue]] = {}
        self.file_record_cache: Dict[Tuple[int, str], Optional[Dict[str, Any]]] = {}
        self.links_cache: Dict[Tuple[int, str, int, bool], List[str]] = {}
        self.meta_cache: Dict[int, Optional[ObjectLinkMeta]] = {}


class TokenResolverService:
    def __init__(self, *, logger=None):
        self.logger = logger
        self.data_service = ObjectDataService(logger=logger)
        self.link_service = ObjectLinkService(data_service=self.data_service)

    def resolve_tokens(
        self,
        *,
        document_id: int,
        context: Mapping[str, str],
        tokens: Sequence[str],
        options: Optional[Mapping[str, Any]] = None,
        include_trace: bool = False,
    ) -> Dict[str, Any]:
        options_map = dict(options or {})
        max_depth = int(options_map.get("maxDepth") or 8)
        if max_depth < 0:
            max_depth = 0
        max_depth = min(max_depth, 8)
        validate_only = bool(options_map.get("validateOnly", False))
        joiner = str(options_map.get("joiner", ", "))
        aggregation_mode = str(options_map.get("aggregationMode") or "first").strip().lower()
        if aggregation_mode not in {"first", "join"}:
            aggregation_mode = "first"

        if len(tokens) > 500:
            raise TokenResolveError(
                "VALIDATION_ERROR",
                "Превышен лимит токенов в одном запросе (максимум 500).",
                details={"tokens_limit": 500},
            )

        started_at = time.monotonic()
        cache = _RequestCaches()
        context_map = _extract_context_map(context)
        token_index = build_doc_token_index(int(document_id))
        allowed_root_ids = set(
            DocumentPattern_Objects.objects.filter(document_id=int(document_id))
            .values_list("object_id", flat=True)
        )
        results: List[Dict[str, Any]] = []
        summary = {
            "tokens_total": len(tokens),
            "ok": 0,
            "unresolved": 0,
            "errors": 0,
            "warnings": 0,
        }
        max_depth_used = 0
        debug_enabled = bool(getattr(settings, "DOC_TOKEN_RESOLVE_DEBUG", False))

        for raw_token in tokens:
            token = _normalise_token_text(raw_token)
            warnings: List[str] = []
            trace: List[Dict[str, Any]] = []
            canonical_token = token
            try:
                parsed = parse_canonical_token(token)
                if parsed is None:
                    parsed_human, parse_error, canonical = parse_human_token(
                        token,
                        token_index=token_index,
                    )
                    if parsed_human is None:
                        if parse_error is not None:
                            self._log_event(
                                "human_token_unresolved",
                                document_id=document_id,
                                token=token,
                                token_normalized=_normalise_token_text(token),
                                code=parse_error.code,
                                message=parse_error.message,
                                details=parse_error.details,
                                strict_mode=_human_token_strict_mode_enabled(),
                            )
                            raise parse_error
                        raise TokenResolveError("INVALID_TOKEN", "Некорректный формат токена.", status="unresolved")
                    parsed = parsed_human
                    canonical_token = canonical or canonical_token
                    if debug_enabled:
                        self._log_event(
                            "human_token_debug",
                            document_id=document_id,
                            input_token=token,
                            canonical_token=canonical_token,
                            object_id=parsed.object_id,
                            link_meta_ids=[int(step.link_meta_id) for step in parsed.link_steps],
                            param_id=parsed.param_id,
                        )
                else:
                    canonical_token = canonical_token_from_parsed(parsed)

                max_depth_used = max(max_depth_used, parsed.depth)
                if parsed.depth > max_depth:
                    raise TokenResolveError(
                        "INVALID_TOKEN",
                        f"Превышена допустимая глубина токена (максимум {max_depth}).",
                        details={"max_depth": max_depth},
                    )
                if parsed.object_id not in allowed_root_ids:
                    raise TokenResolveError(
                        "PERMISSION_DENIED",
                        "Токен ссылается на объект, не подключенный к документу.",
                        details={"object_id": parsed.object_id},
                    )
                if validate_only:
                    value = ""
                else:
                    root_uid = context_map.get(str(parsed.object_id), "").strip()
                    if not root_uid:
                        raise TokenResolveError(
                            "MISSING_ROOT",
                            "Не выбрана запись корневого объекта для резолва токена.",
                            details={"object_id": parsed.object_id},
                        )
                    value = self._resolve_token_value(
                        parsed=parsed,
                        root_record_uid=root_uid,
                        max_depth=max_depth,
                        joiner=joiner,
                        aggregation_mode=aggregation_mode,
                        warnings=warnings,
                        trace=trace,
                        cache=cache,
                    )

                result_payload: Dict[str, Any] = {
                    "input_token": token,
                    "token": token,
                    "canonical_token": canonical_token,
                    "status": "ok",
                    "value": value,
                    "warnings": warnings,
                }
                if include_trace:
                    result_payload["trace"] = trace
                results.append(result_payload)
                summary["ok"] += 1
                summary["warnings"] += len(warnings)
            except TokenResolveError as exc:
                status_value = str(exc.status or "error")
                error_payload: Dict[str, Any] = {
                    "input_token": token,
                    "token": token,
                    "canonical_token": canonical_token,
                    "status": status_value,
                    "value": "",
                    "warnings": warnings,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                }
                if include_trace:
                    error_payload["trace"] = trace
                results.append(error_payload)
                if status_value == "unresolved":
                    summary["unresolved"] += 1
                else:
                    summary["errors"] += 1
                summary["warnings"] += len(warnings)
                self._log_event(
                    "token_resolve_errors",
                    token=token,
                    canonical_token=canonical_token,
                    status=status_value,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )

        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._log_event(
            "token_resolve_summary",
            document_id=document_id,
            tokens_total=summary["tokens_total"],
            errors=summary["errors"],
            unresolved=summary["unresolved"],
            warnings=summary["warnings"],
            duration_ms=duration_ms,
            max_depth_used=max_depth_used,
        )
        return {
            "results": results,
            "summary": summary,
        }

    def prefetch_graph(
        self,
        *,
        document_id: int,
        context: Mapping[str, str],
        tokens: Sequence[str],
        options: Optional[Mapping[str, Any]] = None,
        include_trace: bool = False,
    ) -> Dict[str, Any]:
        options_map = dict(options or {})
        max_depth = int(options_map.get("maxDepth") or 8)
        if max_depth < 0:
            max_depth = 0
        max_depth = min(max_depth, 8)
        if len(tokens) > 500:
            raise TokenResolveError(
                "VALIDATION_ERROR",
                "Превышен лимит токенов в одном запросе (максимум 500).",
                details={"tokens_limit": 500},
            )

        started_at = time.monotonic()
        cache = _RequestCaches()
        context_map = _extract_context_map(context)
        token_index = build_doc_token_index(int(document_id))
        allowed_root_ids = set(
            DocumentPattern_Objects.objects.filter(document_id=int(document_id)).values_list("object_id", flat=True)
        )
        use_sql_only = bool(getattr(settings, "DBM_READ_FROM_SQL", False))

        graph_records: Dict[str, Dict[str, Any]] = {}
        graph_links: Dict[str, Dict[str, Any]] = {}
        results: List[Dict[str, Any]] = []
        summary = {
            "tokens_total": len(tokens),
            "ok": 0,
            "unresolved": 0,
            "errors": 0,
            "warnings": 0,
            "records_total": 0,
            "links_total": 0,
        }

        def _register_record(object_id: int, record_uid: str) -> None:
            key = f"{int(object_id)}:{str(record_uid)}"
            if key in graph_records:
                return
            payload = self._build_graph_record_payload(
                object_id=int(object_id),
                record_uid=str(record_uid),
                use_sql_only=use_sql_only,
                cache=cache,
            )
            if payload is None:
                return
            graph_records[key] = payload

        for raw_token in tokens:
            token = _normalise_token_text(raw_token)
            token_warnings: List[str] = []
            trace: List[Dict[str, Any]] = []
            canonical_token = token
            try:
                parsed = parse_canonical_token(token)
                if parsed is None:
                    parsed_human, parse_error, canonical = parse_human_token(
                        token,
                        token_index=token_index,
                    )
                    if parsed_human is None:
                        if parse_error is not None:
                            self._log_event(
                                "human_token_unresolved",
                                document_id=document_id,
                                token=token,
                                token_normalized=_normalise_token_text(token),
                                code=parse_error.code,
                                message=parse_error.message,
                                details=parse_error.details,
                                strict_mode=_human_token_strict_mode_enabled(),
                            )
                            raise parse_error
                        raise TokenResolveError("INVALID_TOKEN", "Некорректный формат токена.", status="unresolved")
                    parsed = parsed_human
                    canonical_token = canonical or canonical_token
                else:
                    canonical_token = canonical_token_from_parsed(parsed)

                if parsed.object_id not in allowed_root_ids:
                    raise TokenResolveError(
                        "PERMISSION_DENIED",
                        "Токен ссылается на объект, не подключенный к документу.",
                        details={"object_id": parsed.object_id},
                    )
                root_uid = context_map.get(str(parsed.object_id), "").strip()
                if not root_uid:
                    raise TokenResolveError(
                        "MISSING_ROOT",
                        "Не выбрана запись корневого объекта для построения графа.",
                        details={"object_id": parsed.object_id},
                    )

                _register_record(parsed.object_id, root_uid)
                states: List[Tuple[int, str, Set[Tuple[int, str]]]] = [
                    (int(parsed.object_id), str(root_uid), {(int(parsed.object_id), str(root_uid))})
                ]

                for depth_index, step in enumerate(parsed.link_steps):
                    if depth_index >= max_depth:
                        raise TokenResolveError(
                            "INVALID_TOKEN",
                            f"Превышена допустимая глубина токена (максимум {max_depth}).",
                            details={"max_depth": max_depth},
                        )
                    meta = self._get_meta(step.link_meta_id, cache=cache)
                    if meta is None:
                        raise TokenResolveError(
                            "INVALID_TOKEN",
                            "Связь не найдена для указанного link(meta_id).",
                            details={"link_meta_id": step.link_meta_id},
                        )
                    next_states: List[Tuple[int, str, Set[Tuple[int, str]]]] = []
                    for object_id, record_uid, visited in states:
                        if int(meta.parent_object_id) != int(object_id):
                            raise TokenResolveError(
                                "INVALID_TOKEN",
                                "Некорректная цепочка link(): parent объекта не совпадает.",
                                details={
                                    "expected_parent_object_id": int(meta.parent_object_id),
                                    "actual_parent_object_id": int(object_id),
                                    "link_meta_id": int(meta.id),
                                },
                            )
                        child_uids = self._get_child_uids(
                            parent_object_id=int(object_id),
                            parent_record_uid=str(record_uid),
                            link_meta=meta,
                            use_sql_only=use_sql_only,
                            cache=cache,
                        )
                        selected = self._select_children(
                            step=step,
                            child_uids=child_uids,
                            warnings=token_warnings,
                            link_meta=meta,
                        )
                        trace.append(
                            {
                                "step": depth_index,
                                "link_meta_id": int(step.link_meta_id),
                                "selector": step.selector,
                                "selected_children": selected,
                            }
                        )
                        for child_uid in selected:
                            visit_key = (int(meta.child_object_id), str(child_uid))
                            if visit_key in visited:
                                raise TokenResolveError("CYCLE_DETECTED", "Обнаружен цикл при построении графа.")
                            link_key = (
                                f"{int(object_id)}:{str(record_uid)}:"
                                f"{int(meta.id)}:{int(meta.child_object_id)}:{str(child_uid)}"
                            )
                            graph_links[link_key] = {
                                "parent_object_id": int(object_id),
                                "parent_record_uid": str(record_uid),
                                "link_meta_id": int(meta.id),
                                "child_object_id": int(meta.child_object_id),
                                "child_record_uid": str(child_uid),
                            }
                            _register_record(int(meta.child_object_id), str(child_uid))
                            new_visited = set(visited)
                            new_visited.add(visit_key)
                            next_states.append((int(meta.child_object_id), str(child_uid), new_visited))
                    states = next_states
                    if not states:
                        break

                result_payload: Dict[str, Any] = {
                    "input_token": token,
                    "canonical_token": canonical_token,
                    "status": "ok",
                    "warnings": token_warnings,
                }
                if include_trace:
                    result_payload["trace"] = trace
                results.append(result_payload)
                summary["ok"] += 1
                summary["warnings"] += len(token_warnings)
            except TokenResolveError as exc:
                status_value = str(exc.status or "error")
                result_payload = {
                    "input_token": token,
                    "canonical_token": canonical_token,
                    "status": status_value,
                    "warnings": token_warnings,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                }
                if include_trace:
                    result_payload["trace"] = trace
                results.append(result_payload)
                if status_value == "unresolved":
                    summary["unresolved"] += 1
                else:
                    summary["errors"] += 1
                summary["warnings"] += len(token_warnings)
                self._log_event(
                    "token_resolve_errors",
                    token=token,
                    canonical_token=canonical_token,
                    status=status_value,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )

        summary["records_total"] = len(graph_records)
        summary["links_total"] = len(graph_links)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._log_event(
            "tree_prefetch_summary",
            document_id=document_id,
            tokens_total=summary["tokens_total"],
            ok=summary["ok"],
            errors=summary["errors"],
            unresolved=summary["unresolved"],
            warnings=summary["warnings"],
            records_total=summary["records_total"],
            links_total=summary["links_total"],
            duration_ms=duration_ms,
            max_depth=max_depth,
        )
        return {
            "results": results,
            "graph": {
                "records": list(graph_records.values()),
                "links": list(graph_links.values()),
                "max_depth": max_depth,
            },
            "summary": summary,
        }

    def _build_graph_record_payload(
        self,
        *,
        object_id: int,
        record_uid: str,
        use_sql_only: bool,
        cache: _RequestCaches,
    ) -> Optional[Dict[str, Any]]:
        obj = self._get_object(object_id, cache=cache)
        if obj is None:
            return None
        if use_sql_only:
            if self._get_sql_record(object_id=object_id, record_uid=str(record_uid), cache=cache) is None:
                return None
        else:
            if self._get_legacy_record(object_id=object_id, identifier=str(record_uid), cache=cache) is None:
                return None
        parameters = self._get_parameters(object_id, cache=cache)
        fields: Dict[str, Dict[str, Any]] = {}
        identificator_value = ""
        for parameter in parameters:
            raw_value = self._get_parameter_value(
                object_id=object_id,
                record_uid=str(record_uid),
                parameter=parameter,
                use_sql_only=use_sql_only,
                cache=cache,
            )
            canonical_value = canonicalize_value(
                parameter.data_type,
                raw_value,
                array_separator=parameter.array_separator,
                date_format=parameter.date_format,
            )
            fields[str(parameter.id)] = {
                "type": str(parameter.data_type),
                "value": canonical_value,
            }
            if parameter.identificator and canonical_value not in (None, ""):
                identificator_value = str(canonical_value)
        return {
            "object_id": int(object_id),
            "record_uid": str(record_uid),
            "identificator": identificator_value,
            "fields": fields,
        }

    def _resolve_token_value(
        self,
        *,
        parsed: ParsedToken,
        root_record_uid: str,
        max_depth: int,
        joiner: str,
        aggregation_mode: str,
        warnings: List[str],
        trace: List[Dict[str, Any]],
        cache: _RequestCaches,
    ) -> str:
        use_sql_only = bool(getattr(settings, "DBM_READ_FROM_SQL", False))
        states: List[Tuple[int, str, Set[Tuple[int, str]]]] = [
            (
                int(parsed.object_id),
                str(root_record_uid),
                {(int(parsed.object_id), str(root_record_uid))},
            )
        ]

        for depth_index, step in enumerate(parsed.link_steps):
            if depth_index >= max_depth:
                raise TokenResolveError("INVALID_TOKEN", "РџСЂРµРІС‹С€РµРЅР° РјР°РєСЃРёРјР°Р»СЊРЅРѕ РґРѕРїСѓСЃС‚РёРјР°СЏ РіР»СѓР±РёРЅР° С‚РѕРєРµРЅР°.")
            meta = self._get_meta(step.link_meta_id, cache=cache)
            if meta is None:
                raise TokenResolveError(
                    "INVALID_TOKEN",
                    "РЎРІСЏР·СЊ РЅРµ РЅР°Р№РґРµРЅР° РґР»СЏ СѓРєР°Р·Р°РЅРЅРѕРіРѕ link(meta_id).",
                    details={"link_meta_id": step.link_meta_id},
                )
            next_states: List[Tuple[int, str, Set[Tuple[int, str]]]] = []
            for object_id, record_uid, visited in states:
                if int(meta.parent_object_id) != int(object_id):
                    raise TokenResolveError(
                        "INVALID_TOKEN",
                        "РќРµРєРѕСЂСЂРµРєС‚РЅР°СЏ С†РµРїРѕС‡РєР° link(): parent РѕР±СЉРµРєС‚Р° РЅРµ СЃРѕРІРїР°РґР°РµС‚.",
                        details={
                            "expected_parent_object_id": int(meta.parent_object_id),
                            "actual_parent_object_id": int(object_id),
                            "link_meta_id": int(meta.id),
                        },
                    )
                child_uids = self._get_child_uids(
                    parent_object_id=int(object_id),
                    parent_record_uid=str(record_uid),
                    link_meta=meta,
                    use_sql_only=use_sql_only,
                    cache=cache,
                )
                selected = self._select_children(step=step, child_uids=child_uids, warnings=warnings, link_meta=meta)
                trace.append(
                    {
                        "step": depth_index,
                        "link_meta_id": step.link_meta_id,
                        "selector": step.selector,
                        "selected_children": selected,
                    }
                )
                for child_uid in selected:
                    visit_key = (int(meta.child_object_id), str(child_uid))
                    if visit_key in visited:
                        raise TokenResolveError("CYCLE_DETECTED", "РћР±РЅР°СЂСѓР¶РµРЅ С†РёРєР» РїСЂРё РѕР±С…РѕРґРµ СЃРІСЏР·РµР№ Р·Р°РїРёСЃРё.")
                    new_visited = set(visited)
                    new_visited.add(visit_key)
                    next_states.append((int(meta.child_object_id), str(child_uid), new_visited))
            states = next_states
            if not states:
                raise TokenResolveError("MISSING_CHILD", "РќРµ РЅР°Р№РґРµРЅР° РґРѕС‡РµСЂРЅСЏСЏ Р·Р°РїРёСЃСЊ РїРѕ СѓРєР°Р·Р°РЅРЅРѕР№ СЃРІСЏР·Рё.")

        resolved_values: List[str] = []
        for object_id, record_uid, _ in states:
            parameter = self._get_parameter(object_id=object_id, parameter_id=parsed.param_id, cache=cache)
            if parameter is None:
                raise TokenResolveError(
                    "INVALID_TOKEN",
                    "РџР°СЂР°РјРµС‚СЂ РЅРµ РїСЂРёРЅР°РґР»РµР¶РёС‚ РѕР¶РёРґР°РµРјРѕРјСѓ РѕР±СЉРµРєС‚Сѓ РІ С†РµРїРѕС‡РєРµ С‚РѕРєРµРЅР°.",
                    details={"object_id": object_id, "parameter_id": parsed.param_id},
                )
            raw_value = self._get_parameter_value(
                object_id=object_id,
                record_uid=record_uid,
                parameter=parameter,
                use_sql_only=use_sql_only,
                cache=cache,
            )
            formatted = self._format_value(parameter, raw_value, joiner=joiner)
            if formatted:
                resolved_values.append(formatted)

        if not resolved_values:
            return ""

        has_all_selector = any(step.selector == "all" for step in parsed.link_steps)
        if has_all_selector:
            return joiner.join(resolved_values)
        if len(resolved_values) > 1:
            if aggregation_mode == "join":
                return joiner.join(resolved_values)
            warnings.append("РћР±РЅР°СЂСѓР¶РµРЅРѕ РЅРµСЃРєРѕР»СЊРєРѕ Р·РЅР°С‡РµРЅРёР№: РІРѕР·РІСЂР°С‰РµРЅРѕ РїРµСЂРІРѕРµ.")
        return resolved_values[0]

    def _get_object(self, object_id: int, *, cache: _RequestCaches) -> Optional[Object]:
        key = int(object_id)
        if key not in cache.object_cache:
            cache.object_cache[key] = Object.objects.filter(id=key).first()
        return cache.object_cache[key]

    def _get_parameters(self, object_id: int, *, cache: _RequestCaches) -> List[Parameter]:
        key = int(object_id)
        if key not in cache.parameters_cache:
            cache.parameters_cache[key] = list(Parameter.objects.filter(object_id=key).order_by("id"))
        return cache.parameters_cache[key]

    def _get_parameter(self, *, object_id: int, parameter_id: int, cache: _RequestCaches) -> Optional[Parameter]:
        key = (int(object_id), int(parameter_id))
        if key not in cache.parameter_by_id:
            params = self._get_parameters(object_id, cache=cache)
            cache.parameter_by_id[key] = next((param for param in params if int(param.id) == int(parameter_id)), None)
        return cache.parameter_by_id[key]

    def _get_meta(self, meta_id: int, *, cache: _RequestCaches) -> Optional[ObjectLinkMeta]:
        key = int(meta_id)
        if key not in cache.meta_cache:
            cache.meta_cache[key] = (
                ObjectLinkMeta.objects.filter(id=key)
                .select_related("parent_object", "child_object")
                .first()
            )
        return cache.meta_cache[key]

    def _get_sql_record(self, *, object_id: int, record_uid: str, cache: _RequestCaches) -> Optional[ObjectRecord]:
        key = (int(object_id), str(record_uid))
        if key not in cache.sql_record_cache:
            cache.sql_record_cache[key] = (
                ObjectRecord.objects.filter(object_id=int(object_id))
                .filter(Q(record_uid=str(record_uid)) | Q(legacy_id_to_connect=str(record_uid)))
                .first()
            )
        return cache.sql_record_cache[key]

    def _get_parameter_value(
        self,
        *,
        object_id: int,
        record_uid: str,
        parameter: Parameter,
        use_sql_only: bool,
        cache: _RequestCaches,
    ) -> Any:
        if use_sql_only:
            record = self._get_sql_record(object_id=object_id, record_uid=record_uid, cache=cache)
            if record is None:
                raise TokenResolveError(
                    "MISSING_CHILD",
                    "РќРµ РЅР°Р№РґРµРЅР° Р·Р°РїРёСЃСЊ РѕР±СЉРµРєС‚Р° РІ SQL РґР»СЏ СЂРµР·РѕР»РІР° С‚РѕРєРµРЅР°.",
                    details={"object_id": object_id, "record_uid": record_uid},
                )
            pv_key = (int(record.id), int(parameter.id))
            if pv_key not in cache.sql_field_cache:
                cache.sql_field_cache[pv_key] = ParameterValue.objects.filter(
                    record_id=record.id,
                    parameter_id=parameter.id,
                ).first()
            field_obj = cache.sql_field_cache[pv_key]
            return self._value_from_parameter_value(parameter=parameter, value_obj=field_obj)

        legacy = self._get_legacy_record(object_id=object_id, identifier=record_uid, cache=cache)
        if legacy is None:
            raise TokenResolveError(
                "MISSING_CHILD",
                "РќРµ РЅР°Р№РґРµРЅР° Р·Р°РїРёСЃСЊ РѕР±СЉРµРєС‚Р° РІ С„Р°Р№Р»РѕРІРѕРј С…СЂР°РЅРёР»РёС‰Рµ РґР»СЏ СЂРµР·РѕР»РІР° С‚РѕРєРµРЅР°.",
                details={"object_id": object_id, "record_uid": record_uid},
            )
        field_payload = legacy.get(str(parameter.id), {})
        if isinstance(field_payload, Mapping):
            return field_payload.get("value")
        return field_payload

    def _get_legacy_record(self, *, object_id: int, identifier: str, cache: _RequestCaches) -> Optional[Dict[str, Any]]:
        key = (int(object_id), str(identifier))
        if key in cache.file_record_cache:
            return cache.file_record_cache[key]
        obj = self._get_object(object_id, cache=cache)
        if obj is None:
            cache.file_record_cache[key] = None
            return None
        rows, _warnings = self.data_service.file_repo.list_raw_rows(
            obj=obj,
            allow_empty=False,
            ensure_record_uid=True,
            persist=False,
        )
        match = None
        for row in rows:
            if str(row.get("record_uid") or "").strip() == str(identifier):
                match = row
                break
            if str(row.get("id_to_connect") or "").strip() == str(identifier):
                match = row
                break
        if match is None:
            cache.file_record_cache[key] = None
            return None

        params = self._get_parameters(object_id, cache=cache)
        record_uid = str(match.get("record_uid") or match.get("id_to_connect") or identifier).strip()
        legacy_payload: Dict[str, Any] = {"id_to_connect": record_uid}
        for parameter in params:
            legacy_payload[str(parameter.id)] = {
                "data_type": parameter.data_type,
                "value": match.get(str(parameter.id), ""),
            }
        cache.file_record_cache[key] = legacy_payload
        return legacy_payload

    def _get_child_uids(
        self,
        *,
        parent_object_id: int,
        parent_record_uid: str,
        link_meta: ObjectLinkMeta,
        use_sql_only: bool,
        cache: _RequestCaches,
    ) -> List[str]:
        key = (int(parent_object_id), str(parent_record_uid), int(link_meta.id), bool(use_sql_only))
        if key in cache.links_cache:
            return cache.links_cache[key]

        if use_sql_only:
            parent_record = self._get_sql_record(
                object_id=parent_object_id,
                record_uid=str(parent_record_uid),
                cache=cache,
            )
            if parent_record is None:
                child_uids: List[str] = []
            else:
                child_uids = sorted(
                    set(
                        RecordLink.objects.filter(
                            object_link_meta_id=link_meta.id,
                            parent_record_id=parent_record.id,
                        )
                        .select_related("child_record")
                        .values_list("child_record__record_uid", flat=True)
                    )
                )
        else:
            parent_object = self._get_object(parent_object_id, cache=cache)
            if parent_object is None:
                child_uids = []
            else:
                links_data = self.link_service.get_row_links(
                    parent_obj=parent_object,
                    parent_identifier=str(parent_record_uid),
                )
                link_entry = next(
                    (item for item in links_data if int(item.get("link_id", 0)) == int(link_meta.id)),
                    None,
                )
                child_uids = sorted(set(str(item) for item in (link_entry or {}).get("child_ident_ids", [])))

        cache.links_cache[key] = child_uids
        return child_uids

    @staticmethod
    def _select_children(
        *,
        step: ParsedLinkStep,
        child_uids: Sequence[str],
        warnings: List[str],
        link_meta: ObjectLinkMeta,
    ) -> List[str]:
        prepared = [str(item or "").strip() for item in child_uids if str(item or "").strip()]
        prepared = sorted(set(prepared))
        if not prepared:
            return []
        if step.selector == "all":
            return prepared
        if step.selector == "index":
            index = int(step.index or 0)
            if index < 0 or index >= len(prepared):
                warnings.append("РРЅРґРµРєСЃ СЃРІСЏР·Рё РІС‹С…РѕРґРёС‚ Р·Р° РїСЂРµРґРµР»С‹ СЃРїРёСЃРєР°.")
                return []
            return [prepared[index]]

        if len(prepared) > 1 or str(link_meta.link_type) == "multiple":
            warnings.append("РЎРІСЏР·СЊ РјРЅРѕР¶РµСЃС‚РІРµРЅРЅР°СЏ: РІС‹Р±СЂР°РЅР° РїРµСЂРІР°СЏ Р·Р°РїРёСЃСЊ.")
        return [prepared[0]]

    @staticmethod
    def _value_from_parameter_value(*, parameter: Parameter, value_obj: Optional[ParameterValue]) -> Any:
        if value_obj is None:
            return None
        data_type = str(parameter.data_type or "").upper()
        if data_type in {"TXT", "TXTS", "DATE"}:
            if value_obj.value_text is not None:
                return value_obj.value_text
            if value_obj.value_datetime is not None:
                return value_obj.value_datetime.isoformat()
            return None
        if data_type == "INT":
            if value_obj.value_int is not None:
                return value_obj.value_int
            if value_obj.value_text is not None:
                return value_obj.value_text
            return None
        if data_type == "ARRAY":
            if isinstance(value_obj.value_json, list):
                return value_obj.value_json
            if value_obj.value_text:
                separator = parameter.array_separator or " "
                return [item.strip() for item in str(value_obj.value_text).split(separator) if item.strip()]
            return []
        if value_obj.value_text is not None:
            return value_obj.value_text
        if value_obj.value_json is not None:
            return value_obj.value_json
        if value_obj.value_int is not None:
            return value_obj.value_int
        if value_obj.value_datetime is not None:
            return value_obj.value_datetime.isoformat()
        return None

    @staticmethod
    def _format_value(parameter: Parameter, value: Any, *, joiner: str) -> str:
        canonical = canonicalize_value(
            parameter.data_type,
            value,
            array_separator=parameter.array_separator,
            date_format=parameter.date_format,
        )
        if canonical in (None, ""):
            return ""
        if isinstance(canonical, list):
            return joiner.join(str(item) for item in canonical if str(item).strip())
        return str(canonical)

    def _log_event(self, event: str, **payload: Any) -> None:
        if self.logger is None:
            return
        self.logger.warning("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))


def finalize_document_json_for_export(
    *,
    document_id: int,
    document_json: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
    resolver: TokenResolverService,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    export_payload: Dict[str, Any] = json.loads(json.dumps(document_json, ensure_ascii=False))
    tokens: List[str] = []
    for text in _iter_text_values(export_payload):
        tokens.extend(collect_tokens_from_text(text))
    unique_tokens = sorted(set(str(token or "").strip() for token in tokens if str(token or "").strip()))
    context_map = _extract_context_map(context or document_json.get("dbm_context") or document_json.get("context") or {})
    allow_unresolved = bool(getattr(settings, "EXPORT_ALLOW_UNRESOLVED", False))
    resolve_result = {"results": [], "summary": {"tokens_total": 0, "ok": 0, "errors": 0, "warnings": 0}}
    resolved_values: Dict[str, str] = {}
    unresolved_tokens: Set[str] = set()

    if unique_tokens:
        resolve_result = resolver.resolve_tokens(
            document_id=int(document_id),
            context=context_map,
            tokens=unique_tokens,
            options=options or {},
            include_trace=bool((options or {}).get("includeTrace", False)),
        )
        for item in resolve_result.get("results", []):
            token = _normalise_token_text(item.get("input_token") or item.get("token") or "")
            if not token:
                continue
            if item.get("status") == "ok":
                resolved_values[token] = str(item.get("value") or "")
            else:
                unresolved_tokens.add(token)

    if unresolved_tokens:
        resolver._log_event(
            "token_resolve_unresolved_export",
            document_id=int(document_id),
            unresolved_count=len(unresolved_tokens),
            unresolved_tokens=sorted(unresolved_tokens)[:20],
        )

    def replace_tokens_in_text(text: str) -> str:
        source = str(text or "")

        def _replace(match):
            token = _normalise_token_text(match.group(0))
            if token in resolved_values:
                return resolved_values[token]
            # Export should be fail-open for unresolved tokens.
            return ""

        return TOKEN_RE.sub(_replace, source)

    export_payload = _transform_text_values(export_payload, replace_tokens_in_text)
    artifact_matches: List[Dict[str, str]] = []
    for text in _iter_text_values(export_payload):
        for marker in TOKEN_ARTIFACT_MARKERS:
            if marker in str(text):
                artifact_matches.append({"marker": marker, "text": str(text)[:200]})
                break

    if artifact_matches and not allow_unresolved:
        raise ExportFinalizeError(
            "Р­РєСЃРїРѕСЂС‚ DOCX РѕСЃС‚Р°РЅРѕРІР»РµРЅ: РѕР±РЅР°СЂСѓР¶РµРЅС‹ СЃР»СѓР¶РµР±РЅС‹Рµ Р°СЂС‚РµС„Р°РєС‚С‹ РїРѕСЃР»Рµ СЂРµР·РѕР»РІР° С‚РѕРєРµРЅРѕРІ.",
            results=[
                {
                    "status": "error",
                    "error": {
                        "code": "UNRESOLVED_ARTIFACTS",
                        "message": "Р’ С‚РµРєСЃС‚Рµ РґР»СЏ СЌРєСЃРїРѕСЂС‚Р° РѕСЃС‚Р°Р»РёСЃСЊ СЃР»СѓР¶РµР±РЅС‹Рµ РјР°СЂРєРµСЂС‹ С‚РѕРєРµРЅРѕРІ.",
                        "details": {"artifacts": artifact_matches[:10]},
                    },
                }
            ],
        )

    return export_payload, resolve_result

