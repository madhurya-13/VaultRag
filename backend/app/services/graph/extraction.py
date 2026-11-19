"""Deterministic extraction plus a strict validator.

Three extractors, in increasing cost and decreasing trust:

1. **Rules** (always on). Regular expressions for policies, organizations,
   identifiers, departments, dates, amounts and explicit relation phrases. Fully
   deterministic, no model, no network.
2. **spaCy** (on when the model is installed). Adds PERSON/LOCATION/ORG/DATE
   labels for text the rules miss. Absent model => the pipeline silently
   degrades to rules; extraction never becomes a hard startup dependency.
3. **Ollama** (off by default). Asks the local model for JSON triples. Its output
   is *untrusted*: every field is type-checked, length-bounded, range-checked and
   its quote must be a verbatim substring of the source chunk.

``validate_extraction_payload`` is the single gate that all three paths pass
through, so no code path can store an unsupported claim.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Iterable, Protocol

from app.core.security_constants import (
    ALLOWED_DEPARTMENTS,
    KG_EXTRACTION_LLM,
    KG_EXTRACTION_RULES,
    KG_EXTRACTION_SPACY,
    NODE_TYPE_DATE,
    NODE_TYPE_DEPARTMENT,
    NODE_TYPE_LOCATION,
    NODE_TYPE_ORGANIZATION,
    NODE_TYPE_OTHER,
    NODE_TYPE_PERSON,
    NODE_TYPE_POLICY,
    NODE_TYPE_PROCESS,
    NODE_TYPE_PRODUCT,
    NODE_TYPE_ROLE,
    NODE_TYPE_SYSTEM,
    NODE_TYPES,
)
from app.services.graph.normalization import (
    clamp_confidence,
    identity_key,
    infer_anchor,
    name_in_quote,
    normalize_name,
    normalize_quote,
    quote_supported,
    relation_key,
    truncate,
)
from app.services.graph.types import (
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelation,
)

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "m8.1"

MAX_SENTENCES_PER_CHUNK = 200

# A boundary is a run of terminal punctuation followed by whitespace, or a
# blank line. Keeping the boundary match means sentence spans map back to exact
# character offsets, which is what quote attribution relies on.
_SENTENCE_SPLIT = re.compile(r"[.!?;]+(?=\s)|\n{2,}")

# ---------------------------------------------------------------------------
# Rule patterns
# ---------------------------------------------------------------------------

_POLICY = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,4})\s+"
    r"(?:Policy|Procedure|Standard|Framework|Guideline|Directive|Controls?)\b"
)
_ORG = re.compile(
    r"\b[A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*){0,4}\s+"
    r"(?:Inc|LLC|Ltd|Limited|Corp|Corporation|GmbH|Pty|Pvt|PLC|Group|Holdings)\b\.?"
)
_SYSTEM = re.compile(
    r"\b(?:[A-Z]{2,}[A-Z0-9]*-\d{2,}(?:-[A-Z0-9]+)?|[A-Z]{3,}(?:-[A-Z]{2,})+)\b"
)
_ROLE = re.compile(
    r"\b(?:Chief\s+[A-Z][a-z]+\s+Officer|(?:Senior|Lead|Head|Deputy)\s+"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|(?:Manager|Engineer|Analyst|Director|"
    r"Officer|Administrator|Specialist|Coordinator))\b"
)
_DATE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4})\b"
)
_QUOTED_TERM = re.compile(r"[“\"]([A-Z][A-Za-z0-9 &./_\-]{2,60})[”\"]")

_CONFIDENCE_POLICY = 0.86
_CONFIDENCE_ORG = 0.84
_CONFIDENCE_SYSTEM = 0.88
_CONFIDENCE_ROLE = 0.72
_CONFIDENCE_DATE = 0.9
_CONFIDENCE_QUOTED = 0.7
_CONFIDENCE_SPACY = 0.74
_CONFIDENCE_RELATION = 0.8

_RELATION_TRIGGERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\breports?\s+to\b", re.IGNORECASE), "REPORTS_TO"),
    (re.compile(r"\bmanaged\s+by\b", re.IGNORECASE), "MANAGED_BY"),
    (re.compile(r"\bmanages?\b", re.IGNORECASE), "MANAGES"),
    (re.compile(r"\bowns?\b|\bowned\s+by\b", re.IGNORECASE), "OWNS"),
    (re.compile(r"\bapproves?\b|\bapproved\s+by\b", re.IGNORECASE), "APPROVES"),
    (re.compile(r"\brequires?\b|\brequired\s+by\b", re.IGNORECASE), "REQUIRES"),
    (re.compile(r"\bsupersedes?\b|\breplaces?\b", re.IGNORECASE), "SUPERSEDES"),
    (re.compile(r"\bapplies\s+to\b", re.IGNORECASE), "APPLIES_TO"),
    (re.compile(r"\bdepends\s+on\b", re.IGNORECASE), "DEPENDS_ON"),
    (
        re.compile(r"\bis\s+responsible\s+for\b|\bresponsible\s+for\b", re.IGNORECASE),
        "RESPONSIBLE_FOR",
    ),
    (re.compile(r"\bmust\s+comply\s+with\b", re.IGNORECASE), "COMPLIES_WITH"),
    (re.compile(r"\bis\s+part\s+of\b", re.IGNORECASE), "PART_OF"),
    (re.compile(r"\buses?\b|\bused\s+by\b", re.IGNORECASE), "USES"),
    (re.compile(r"\breviewed\s+by\b|\breviews?\b", re.IGNORECASE), "REVIEWS"),
)

# Relations that can hold only one value per subject. A second, different target
# from another document is a conflict worth surfacing.
FUNCTIONAL_RELATIONS = frozenset(
    {"MANAGED_BY", "OWNS", "APPROVES", "REPORTS_TO", "RESPONSIBLE_FOR"}
)

_SPACY_LABEL_MAP = {
    "PERSON": NODE_TYPE_PERSON,
    "ORG": NODE_TYPE_ORGANIZATION,
    "GPE": NODE_TYPE_LOCATION,
    "LOC": NODE_TYPE_LOCATION,
    "FAC": NODE_TYPE_LOCATION,
    "DATE": NODE_TYPE_DATE,
    "PRODUCT": NODE_TYPE_PRODUCT,
    "NORP": NODE_TYPE_ORGANIZATION,
    "LAW": NODE_TYPE_POLICY,
}


class ExtractionValidationError(ValueError):
    """Raised when an extraction payload cannot be trusted."""


class SupportsGenerate(Protocol):  # pragma: no cover - structural typing only
    def generate_answer(self, question: str, context: str) -> Any: ...


# ---------------------------------------------------------------------------
# Sentence helpers
# ---------------------------------------------------------------------------


def _sentence_spans(text: str, *, limit: int = MAX_SENTENCES_PER_CHUNK) -> list[tuple[int, int]]:
    """Character spans of the first ``limit`` sentences."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_SPLIT.finditer(text):
        spans.append((start, match.end()))
        start = match.end()
        if len(spans) >= limit:
            return spans
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def split_sentences(text: str) -> list[str]:
    """Split into bounded sentences, keeping the original surface text."""
    if not text:
        return []
    sentences = [
        text[start:end].strip()
        for start, end in _sentence_spans(text)
        if text[start:end].strip()
    ]
    return sentences[:MAX_SENTENCES_PER_CHUNK]


def _sentence_for(text: str, index: int) -> str:
    """Return the sentence containing ``index`` (for quote attribution)."""
    if not text:
        return ""
    for start, end in _sentence_spans(text):
        if start <= index < end:
            return text[start:end].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Rules extractor
# ---------------------------------------------------------------------------


def _iter_pattern_matches(
    text: str,
    pattern: re.Pattern[str],
    node_type: str,
    confidence: float,
) -> Iterable[tuple[int, int, str, str, float]]:
    for match in pattern.finditer(text):
        surface = match.group(0).strip()
        if not surface:
            continue
        if pattern is _POLICY and len(surface) > 120:
            continue
        yield match.start(), match.end(), surface, node_type, confidence


def extract_with_rules(chunk_text: str) -> ChunkExtraction:
    """Deterministic entity and relation extraction. Never fails on odd text."""
    if not chunk_text or not chunk_text.strip():
        return ChunkExtraction(method=KG_EXTRACTION_RULES)

    mentions: list[tuple[int, int, str, str, float]] = []
    mentions.extend(
        _iter_pattern_matches(chunk_text, _POLICY, NODE_TYPE_POLICY, _CONFIDENCE_POLICY)
    )
    mentions.extend(
        _iter_pattern_matches(
            chunk_text, _ORG, NODE_TYPE_ORGANIZATION, _CONFIDENCE_ORG
        )
    )
    mentions.extend(
        _iter_pattern_matches(chunk_text, _SYSTEM, NODE_TYPE_SYSTEM, _CONFIDENCE_SYSTEM)
    )
    mentions.extend(
        _iter_pattern_matches(chunk_text, _ROLE, NODE_TYPE_ROLE, _CONFIDENCE_ROLE)
    )
    mentions.extend(
        _iter_pattern_matches(chunk_text, _DATE, NODE_TYPE_DATE, _CONFIDENCE_DATE)
    )
    for match in _QUOTED_TERM.finditer(chunk_text):
        surface = match.group(1).strip()
        if 2 < len(surface) <= 60:
            mentions.append(
                (
                    match.start(),
                    match.end(),
                    surface,
                    NODE_TYPE_PROCESS,
                    _CONFIDENCE_QUOTED,
                )
            )

    for department in ALLOWED_DEPARTMENTS:
        for match in re.finditer(rf"\b{department}\b", chunk_text):
            mentions.append(
                (
                    match.start(),
                    match.end(),
                    department.title(),
                    NODE_TYPE_DEPARTMENT,
                    _CONFIDENCE_POLICY,
                )
            )

    # Prefer the longest mention when two patterns overlap the same span.
    mentions.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str, str, float]] = []
    for mention in mentions:
        if selected and mention[0] < selected[-1][1]:
            continue
        selected.append(mention)

    entities: list[ExtractedEntity] = []
    for start, _end, surface, node_type, confidence in selected:
        sentence = _sentence_for(chunk_text, start)
        if not sentence or not name_in_quote(surface, sentence):
            continue
        entities.append(
            ExtractedEntity(
                name=truncate(surface, 255),
                node_type=node_type,
                quote=truncate(sentence, 600),
                confidence=confidence,
                anchor=infer_anchor(sentence),
            )
        )

    relations = _relations_from_sentences(chunk_text, entities)
    return ChunkExtraction(
        entities=tuple(_dedupe_entities(entities)),
        relations=tuple(relations),
        method=KG_EXTRACTION_RULES,
    )


def _relations_from_sentences(
    chunk_text: str, entities: list[ExtractedEntity]
) -> list[ExtractedRelation]:
    """Derive relations only when an explicit trigger joins two entities."""
    if len(entities) < 2:
        return []

    by_sentence: dict[str, list[ExtractedEntity]] = {}
    for entity in entities:
        by_sentence.setdefault(normalize_quote(entity.quote), []).append(entity)

    relations: list[ExtractedRelation] = []
    for sentence in split_sentences(chunk_text):
        key = normalize_quote(sentence)
        sentence_entities = by_sentence.get(key, [])
        if len(sentence_entities) < 2:
            continue
        for trigger, relation_type in _RELATION_TRIGGERS:
            match = trigger.search(sentence)
            if match is None:
                continue
            before = [
                entity
                for entity in sentence_entities
                if normalize_name(entity.name)
                in normalize_name(sentence[: match.start()])
            ]
            after = [
                entity
                for entity in sentence_entities
                if normalize_name(entity.name)
                in normalize_name(sentence[match.end() :])
            ]
            if not before or not after:
                continue
            source = before[-1]
            target = after[0]
            if normalize_name(source.name) == normalize_name(target.name):
                continue
            relations.append(
                ExtractedRelation(
                    source_name=source.name,
                    source_type=source.node_type,
                    target_name=target.name,
                    target_type=target.node_type,
                    relation_type=relation_type,
                    quote=truncate(sentence, 600),
                    confidence=min(
                        _CONFIDENCE_RELATION, source.confidence, target.confidence
                    ),
                )
            )
            break
    return relations


def _dedupe_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    seen: dict[tuple[str, str], ExtractedEntity] = {}
    for entity in entities:
        key = (entity.node_type, normalize_name(entity.name))
        existing = seen.get(key)
        if existing is None or entity.confidence > existing.confidence:
            seen[key] = entity
    return list(seen.values())


# ---------------------------------------------------------------------------
# spaCy extractor (optional dependency)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=2)
def _load_spacy(model_name: str):
    """Load a spaCy pipeline, or return None when unavailable.

    A missing model must not break ingestion, extraction or startup. The caller
    treats ``None`` as "rules only".
    """
    try:  # pragma: no cover - depends on the deployment image
        import spacy  # type: ignore
    except Exception:
        logger.info("spaCy is not installed; knowledge graph runs rules only")
        return None
    try:  # pragma: no cover - depends on the deployment image
        return spacy.load(model_name)
    except Exception:
        logger.info(
            "spaCy model %s is unavailable; knowledge graph runs rules only",
            model_name,
        )
        return None


def extract_with_spacy(chunk_text: str, model_name: str) -> ChunkExtraction:
    """Add spaCy entities on top of rule extraction. Degrades to rules."""
    baseline = extract_with_rules(chunk_text)
    pipeline = _load_spacy(model_name)
    if pipeline is None or not chunk_text.strip():
        return baseline

    try:  # pragma: no cover - depends on the deployment image
        document = pipeline(chunk_text[:20000])
    except Exception:
        logger.exception("spaCy extraction failed; keeping rule extraction")
        return baseline

    extra: list[ExtractedEntity] = list(baseline.entities)
    for entity in getattr(document, "ents", ()):  # pragma: no cover
        node_type = _SPACY_LABEL_MAP.get(str(entity.label_))
        if node_type is None:
            continue
        surface = str(entity.text or "").strip()
        if len(surface) < 2 or len(surface) > 255:
            continue
        sentence = _sentence_for(chunk_text, int(entity.start_char))
        if not sentence or not name_in_quote(surface, sentence):
            continue
        extra.append(
            ExtractedEntity(
                name=surface,
                node_type=node_type,
                quote=truncate(sentence, 600),
                confidence=_CONFIDENCE_SPACY,
                anchor=infer_anchor(sentence),
            )
        )

    return ChunkExtraction(
        entities=tuple(_dedupe_entities(extra)),
        relations=baseline.relations,
        method=KG_EXTRACTION_SPACY,
    )


# ---------------------------------------------------------------------------
# LLM extractor (optional, disabled by default)
# ---------------------------------------------------------------------------

LLM_INSTRUCTIONS = (
    "Extract entities and relationships from the reference text. Reply with "
    'JSON only: {"entities":[{"name":"...","type":"PERSON|ORGANIZATION|'
    'LOCATION|POLICY|PROCESS|SYSTEM|PRODUCT|DATE|ROLE|DEPARTMENT|OTHER",'
    '"quote":"exact substring of the text","confidence":0.0}],'
    '"relations":[{"source":"entity name","target":"entity name",'
    '"relation":"VERB_PHRASE","quote":"exact substring","confidence":0.0}]}'
)


def _parse_json_object(answer: str) -> dict:
    """Extract the first JSON object from a model reply, tolerating fences."""
    if not isinstance(answer, str) or not answer.strip():
        raise ExtractionValidationError("Empty extraction response")
    text = answer.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ExtractionValidationError("Response is not a JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExtractionValidationError("Response is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ExtractionValidationError("Response is not a JSON object")
    return parsed


def extract_with_llm(
    chunk_text: str,
    *,
    llm: SupportsGenerate,
    max_quote_chars: int,
) -> ChunkExtraction:
    """Ask the local model for triples, then validate them strictly."""
    question = "Extract knowledge graph triples from the reference text."
    context = f"{LLM_INSTRUCTIONS}\n\nREFERENCE TEXT:\n{chunk_text}"
    try:
        response = llm.generate_answer(question, context)
    except Exception:
        logger.exception("LLM graph extraction failed; keeping deterministic output")
        return ChunkExtraction(method=KG_EXTRACTION_LLM)

    payload = _parse_json_object(getattr(response, "answer", "") or "")
    validated = validate_extraction_payload(
        payload,
        chunk_text=chunk_text,
        max_quote_chars=max_quote_chars,
        method=KG_EXTRACTION_LLM,
    )
    return validated


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------


def _coerce_node_type(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip().upper().replace(" ", "_")
        if candidate in NODE_TYPES:
            return candidate
    return NODE_TYPE_OTHER


def _require_text(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ExtractionValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ExtractionValidationError(f"{field} must not be blank")
    if len(text) > limit:
        raise ExtractionValidationError(f"{field} exceeds {limit} characters")
    return text


def validate_extraction_payload(
    payload: Any,
    *,
    chunk_text: str,
    max_quote_chars: int,
    method: str,
    max_entities: int = 12,
    max_relations: int = 16,
    min_confidence: float = 0.0,
) -> ChunkExtraction:
    """Validate an untrusted extraction payload and reject unsupported claims.

    Enforced rules:
    * the payload is an object with list-valued ``entities`` and ``relations``;
    * every name, type, quote and confidence has the right type and bounds;
    * every entity name appears inside its own quote;
    * every quote is a normalized substring of the source chunk;
    * every relation endpoint refers to an entity declared in the same payload;
    * no self-relations, no unknown relation labels;
    * counts are capped so one hostile chunk cannot flood the graph.
    """
    if not isinstance(payload, dict):
        raise ExtractionValidationError("Extraction payload must be an object")
    raw_entities = payload.get("entities", [])
    raw_relations = payload.get("relations", [])
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise ExtractionValidationError("entities and relations must be lists")
    if len(raw_entities) > max_entities:
        raise ExtractionValidationError(
            f"Too many entities in one chunk (max {max_entities})"
        )
    if len(raw_relations) > max_relations:
        raise ExtractionValidationError(
            f"Too many relations in one chunk (max {max_relations})"
        )

    entities: list[ExtractedEntity] = []
    names: set[str] = set()
    for item in raw_entities:
        if not isinstance(item, dict):
            raise ExtractionValidationError("Each entity must be an object")
        name = _require_text(item.get("name"), field="entity.name", limit=255)
        quote = _require_text(
            item.get("quote"), field="entity.quote", limit=max_quote_chars
        )
        if not quote_supported(quote, chunk_text):
            raise ExtractionValidationError(
                "entity.quote is not present in the source chunk"
            )
        if not name_in_quote(name, quote):
            raise ExtractionValidationError(
                "entity.name does not appear in its own quote"
            )
        confidence = clamp_confidence(item.get("confidence"), default=0.0)
        if confidence < min_confidence:
            continue
        raw_aliases = item.get("aliases") or []
        if not isinstance(raw_aliases, list):
            raise ExtractionValidationError("entity.aliases must be a list")
        aliases = tuple(
            _require_text(alias, field="entity.alias", limit=255)
            for alias in raw_aliases[:8]
            if isinstance(alias, str) and alias.strip()
        )
        anchor_value = item.get("anchor")
        anchor = (
            _require_text(anchor_value, field="entity.anchor", limit=120)
            if anchor_value is not None
            else infer_anchor(quote)
        )
        normalized = normalize_name(name)
        if normalized in names:
            continue
        names.add(normalized)
        entities.append(
            ExtractedEntity(
                name=name,
                node_type=_coerce_node_type(item.get("type")),
                quote=quote,
                confidence=confidence,
                aliases=aliases,
                anchor=anchor,
            )
        )

    relations: list[ExtractedRelation] = []
    declared = {normalize_name(entity.name): entity for entity in entities}
    for item in raw_relations:
        if not isinstance(item, dict):
            raise ExtractionValidationError("Each relation must be an object")
        source_name = _require_text(
            item.get("source"), field="relation.source", limit=255
        )
        target_name = _require_text(
            item.get("target"), field="relation.target", limit=255
        )
        quote = _require_text(
            item.get("quote"), field="relation.quote", limit=max_quote_chars
        )
        if not quote_supported(quote, chunk_text):
            raise ExtractionValidationError(
                "relation.quote is not present in the source chunk"
            )
        source = declared.get(normalize_name(source_name))
        target = declared.get(normalize_name(target_name))
        if source is None or target is None:
            raise ExtractionValidationError(
                "relation endpoints must be declared entities"
            )
        if normalize_name(source.name) == normalize_name(target.name):
            continue
        relation_type = _require_text(
            item.get("relation"), field="relation.relation", limit=64
        )
        if not re.search(r"[A-Za-z]", relation_type):
            raise ExtractionValidationError("relation label must contain letters")
        if not name_in_quote(source.name, quote) or not name_in_quote(
            target.name, quote
        ):
            raise ExtractionValidationError(
                "relation endpoints must appear in the relation quote"
            )
        confidence = clamp_confidence(item.get("confidence"), default=0.0)
        if confidence < min_confidence:
            continue
        relations.append(
            ExtractedRelation(
                source_name=source.name,
                source_type=source.node_type,
                target_name=target.name,
                target_type=target.node_type,
                relation_type=relation_key(relation_type),
                quote=quote,
                confidence=confidence,
            )
        )

    return ChunkExtraction(
        entities=tuple(entities), relations=tuple(relations), method=method
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def extract_chunk(
    chunk_text: str,
    *,
    method: str,
    spacy_model: str,
    min_confidence: float,
    max_quote_chars: int,
    max_entities: int = 12,
    max_relations: int = 16,
    llm: SupportsGenerate | None = None,
) -> ChunkExtraction:
    """Run the configured extractors and return only validated output."""
    if method == KG_EXTRACTION_RULES or not method:
        result = extract_with_rules(chunk_text)
    elif method == KG_EXTRACTION_SPACY:
        result = extract_with_spacy(chunk_text, spacy_model)
    else:
        result = extract_with_spacy(chunk_text, spacy_model)
        if llm is not None:
            generated = extract_with_llm(
                chunk_text, llm=llm, max_quote_chars=max_quote_chars
            )
            result = _merge_extractions(result, generated, method=method)

    # Even rule output passes the same gate: this keeps one code path and one
    # definition of "trustworthy" for every extractor.
    payload = {
        "entities": [
            {
                "name": entity.name,
                "type": entity.node_type,
                "quote": entity.quote,
                "confidence": entity.confidence,
                "aliases": list(entity.aliases),
                "anchor": entity.anchor,
            }
            for entity in result.entities
            if len(entity.quote) <= max_quote_chars
        ],
        "relations": [
            {
                "source": relation.source_name,
                "target": relation.target_name,
                "relation": relation.relation_type,
                "quote": relation.quote,
                "confidence": relation.confidence,
            }
            for relation in result.relations
            if len(relation.quote) <= max_quote_chars
        ],
    }
    try:
        return validate_extraction_payload(
            payload,
            chunk_text=chunk_text,
            max_quote_chars=max_quote_chars,
            method=result.method,
            max_entities=max_entities,
            max_relations=max_relations,
            min_confidence=min_confidence,
        )
    except ExtractionValidationError:
        logger.warning(
            "Discarding an invalid extraction batch; keeping no claims for chunk"
        )
        return ChunkExtraction(method=result.method)


def _merge_extractions(
    deterministic: ChunkExtraction,
    generated: ChunkExtraction,
    *,
    method: str,
) -> ChunkExtraction:
    entities = list(deterministic.entities)
    seen = {(entity.node_type, normalize_name(entity.name)) for entity in entities}
    for entity in generated.entities:
        key = (entity.node_type, normalize_name(entity.name))
        if key in seen:
            continue
        seen.add(key)
        entities.append(entity)

    declared = {
        (entity.node_type, normalize_name(entity.name)) for entity in entities
    }
    relations = list(deterministic.relations)
    for relation in generated.relations:
        if (
            relation.source_type,
            normalize_name(relation.source_name),
        ) not in declared:
            continue
        if (
            relation.target_type,
            normalize_name(relation.target_name),
        ) not in declared:
            continue
        relations.append(relation)

    return ChunkExtraction(
        entities=tuple(entities), relations=tuple(relations), method=method
    )


def entity_identity_key(entity: ExtractedEntity) -> str:
    """Convenience wrapper so extraction output maps to a merge identity."""
    return identity_key(entity.node_type, entity.name, entity.anchor)
