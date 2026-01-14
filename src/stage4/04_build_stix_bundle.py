from __future__ import annotations

import argparse
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CREATOR_NAME = "Geopolitical Collector"
CREATOR_CLASS = "organization"
DEFAULT_REPORT_TYPES = ["threat-report"]

ALLOWED_INDICATOR_TYPES = {"ip", "domain", "hash"}


def project_root() -> Path:
    # .../src/stage4/04_build_stix_bundle.py -> 3階層上がプロジェクトルート想定
    return Path(__file__).resolve().parents[3]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_stix_id(stix_type: str) -> str:
    return f"{stix_type}--{uuid.uuid4()}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def sha256_like(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", s or ""))


def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except Exception:
        return False


def is_domain(value: str) -> bool:
    # 厳密すぎると取りこぼすので、最低限の妥当性チェック
    v = value.strip().lower()
    if len(v) > 253 or "." not in v:
        return False
    if v.startswith("-") or v.endswith("-"):
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+", v))


def build_indicator(
    ind: Dict[str, Any], created: str, modified: str, created_by_ref: str
) -> Optional[Dict[str, Any]]:
    """
    IndicatorはIOCのみに限定する。
    - ip: IPv4のみ
    - domain: ドメイン名のみ
    - hash: MD5/SHA1/SHA256のみ
    それ以外は作成しない（Noneを返す）。
    """
    itype = safe_str(ind.get("indicator_type")).lower()
    value = safe_str(ind.get("value"))
    context = safe_str(ind.get("context"))
    confidence = int(ind.get("confidence", 0) or 0)

    if not itype or not value:
        return None
    if itype not in ALLOWED_INDICATOR_TYPES:
        return None

    pattern: Optional[str] = None

    if itype == "ip":
        if not is_ipv4(value):
            return None
        pattern = f"[ipv4-addr:value = '{value}']"

    elif itype == "domain":
        if not is_domain(value):
            return None
        pattern = f"[domain-name:value = '{value}']"

    elif itype == "hash":
        v = value.lower()
        if re.fullmatch(r"[0-9a-f]{32}", v):
            pattern = f"[file:hashes.MD5 = '{v}']"
        elif re.fullmatch(r"[0-9a-f]{40}", v):
            pattern = f"[file:hashes.SHA1 = '{v}']"
        elif re.fullmatch(r"[0-9a-f]{64}", v):
            pattern = f"[file:hashes.SHA256 = '{v}']"
        else:
            return None

    if not pattern:
        return None

    out: Dict[str, Any] = {
        "type": "indicator",
        "spec_version": "2.1",
        "id": new_stix_id("indicator"),
        "created": created,
        "modified": modified,
        "created_by_ref": created_by_ref,
        "name": f"{itype}:{value}"[:256],
        "valid_from": created,
        "pattern_type": "stix",
        "pattern": pattern,
    }
    if confidence:
        out["confidence"] = confidence
    if context:
        out["description"] = context[:2048]
    return out


def stix_object_from_extracted(obj: Dict[str, Any], created: str, modified: str, created_by_ref: str) -> Dict[str, Any]:
    stix_type = safe_str(obj.get("stix_type"))
    name = safe_str(obj.get("name"))
    description = safe_str(obj.get("description"))
    confidence = int(obj.get("confidence", 0) or 0)

    base: Dict[str, Any] = {
        "type": stix_type,
        "spec_version": "2.1",
        "id": new_stix_id(stix_type),
        "created": created,
        "modified": modified,
        "created_by_ref": created_by_ref,
        "name": name[:256],
    }
    if description:
        base["description"] = description[:4096]
    if confidence:
        base["confidence"] = confidence

    if stix_type == "identity":
        base["identity_class"] = "organization"

    return base


def build_relationship(
    rel: Dict[str, Any],
    source_ref: str,
    target_ref: str,
    created: str,
    modified: str,
    created_by_ref: str,
) -> Dict[str, Any]:
    relationship_type = safe_str(rel.get("relationship_type") or "related-to")
    confidence = int(rel.get("confidence", 0) or 0)

    out: Dict[str, Any] = {
        "type": "relationship",
        "spec_version": "2.1",
        "id": new_stix_id("relationship"),
        "created": created,
        "modified": modified,
        "created_by_ref": created_by_ref,
        "relationship_type": relationship_type,
        "source_ref": source_ref,
        "target_ref": target_ref,
    }
    if confidence:
        out["confidence"] = confidence
    return out


def build_note_for_raw_ref(
    *,
    created: str,
    modified: str,
    created_by_ref: str,
    report_id: str,
    article_url: str,
    raw_saved_path: str,
    raw_sha256: str,
    raw_char_len: int,
    raw_truncated: bool,
) -> Dict[str, Any]:
    lines = [
        "Raw text reference (external file):",
        f"- article_url: {article_url}",
        f"- raw_saved_path: {raw_saved_path}",
        f"- raw_sha256: {raw_sha256}",
        f"- raw_char_len: {raw_char_len}",
        f"- raw_truncated: {raw_truncated}",
    ]
    content = "\n".join(lines).strip()

    return {
        "type": "note",
        "spec_version": "2.1",
        "id": new_stix_id("note"),
        "created": created,
        "modified": modified,
        "created_by_ref": created_by_ref,
        "content": content[:20000],
        "object_refs": [report_id],
    }


@dataclass
class GlobalRegistry:
    # (type,name) -> id
    sdo_ids: Dict[Tuple[str, str], str]
    # (indicator_type,value) -> id
    ind_ids: Dict[Tuple[str, str], str]

    def __init__(self) -> None:
        self.sdo_ids = {}
        self.ind_ids = {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage4C: build STIX 2.1 Bundle from extracted entities + cleaned info.")
    parser.add_argument("--extracted", default=None, help="default: <root>/data/stage4b_extracted_stix.json")
    parser.add_argument("--cleaned", default=None, help="default: <root>/data/stage4_articles_cleaned.json")
    parser.add_argument("--out-bundle", default=None, help="default: <root>/data/stage4_stix_bundle.json")
    parser.add_argument("--out-manifest", default=None, help="default: <root>/data/stage4c_manifest.json")
    args = parser.parse_args()

    root = project_root()

    input_extracted = Path(args.extracted) if args.extracted else (root / "data" / "stage4b_extracted_stix.json")
    input_cleaned = Path(args.cleaned) if args.cleaned else (root / "data" / "stage4_articles_cleaned.json")
    output_bundle = Path(args.out_bundle) if args.out_bundle else (root / "data" / "stage4_stix_bundle.json")
    output_manifest = Path(args.out_manifest) if args.out_manifest else (root / "data" / "stage4c_manifest.json")

    if not input_extracted.exists():
        raise FileNotFoundError(f"missing input: {input_extracted}")

    extracted = load_json(input_extracted)
    items = extracted.get("items", [])
    if not isinstance(items, list):
        raise ValueError("stage4b_extracted_stix.json: items must be a list")

    cleaned_by_url: Dict[str, Dict[str, Any]] = {}
    if input_cleaned.exists():
        cleaned = load_json(input_cleaned)
        for it in cleaned.get("items", []):
            u = safe_str(it.get("url"))
            if u:
                cleaned_by_url[u] = it

    created = now_utc_iso()
    modified = created

    creator_identity_id = new_stix_id("identity")
    creator_identity: Dict[str, Any] = {
        "type": "identity",
        "spec_version": "2.1",
        "id": creator_identity_id,
        "created": created,
        "modified": modified,
        "name": CREATOR_NAME,
        "identity_class": CREATOR_CLASS,
    }

    objects: List[Dict[str, Any]] = [creator_identity]
    registry = GlobalRegistry()

    manifest: Dict[str, Any] = {
        "generated_at": created,
        "input_extracted": str(input_extracted),
        "input_cleaned": str(input_cleaned) if input_cleaned.exists() else None,
        "reports": [],
        "skipped": [],
    }

    def get_or_create_sdo_id(stix_type: str, name: str, make_obj_fn):
        key = (stix_type, name)
        if key in registry.sdo_ids:
            return registry.sdo_ids[key]
        obj = make_obj_fn()
        registry.sdo_ids[key] = obj["id"]
        objects.append(obj)
        return obj["id"]

    def get_or_create_indicator_id(itype: str, value: str, make_obj_fn):
        key = (itype, value)
        if key in registry.ind_ids:
            return registry.ind_ids[key]
        obj = make_obj_fn()
        registry.ind_ids[key] = obj["id"]
        objects.append(obj)
        return obj["id"]

    for it in items:
        row_num = it.get("_row_num")
        title = safe_str(it.get("title"))
        url = safe_str(it.get("url"))
        retrieval_status = safe_str(it.get("retrieval_status"))
        extraction_status = safe_str(it.get("extraction_status"))

        if retrieval_status != "ok" or extraction_status != "ok":
            manifest["skipped"].append(
                {
                    "_row_num": row_num,
                    "title": title,
                    "url": url,
                    "retrieval_status": retrieval_status,
                    "extraction_status": extraction_status,
                    "reason": "skip: not ok",
                }
            )
            continue

        cleaned_item = cleaned_by_url.get(url, {})
        focus_summary = safe_str(cleaned_item.get("focus_summary"))
        clean_text = safe_str(cleaned_item.get("clean_text"))
        clean_sha256 = safe_str(cleaned_item.get("clean_sha256"))

        raw_saved_path = safe_str(cleaned_item.get("raw_saved_path"))
        raw_sha256 = safe_str(cleaned_item.get("raw_sha256"))
        raw_char_len = int(cleaned_item.get("raw_char_len", 0) or 0)
        raw_truncated = bool(cleaned_item.get("raw_truncated", False))

        # --- SDO登録（global dedupe） ---
        id_map: Dict[Tuple[str, str], str] = {}

        for obj in it.get("objects", []):
            st = safe_str(obj.get("stix_type"))
            nm = safe_str(obj.get("name"))
            if not st or not nm:
                continue

            def _mk():
                return stix_object_from_extracted(obj, created, modified, creator_identity_id)

            sdo_id = get_or_create_sdo_id(st, nm, _mk)
            id_map[(st, nm)] = sdo_id

        # --- Indicator登録（IOCのみ。作れないものは作らない） ---
        indicator_ids: List[str] = []
        skipped_indicators: List[Dict[str, Any]] = []

        for ind in it.get("indicators", []):
            itype = safe_str(ind.get("indicator_type")).lower()
            value = safe_str(ind.get("value"))
            if not itype or not value:
                continue

            def _mk2():
                return build_indicator(ind, created, modified, creator_identity_id)

            candidate = _mk2()
            if candidate is None:
                skipped_indicators.append(
                    {
                        "indicator_type": itype,
                        "value": value,
                        "reason": "invalid_or_not_allowed_for_indicator",
                    }
                )
                continue

            ind_id = get_or_create_indicator_id(itype, value, lambda: candidate)
            indicator_ids.append(ind_id)

        # --- Relationship（同一記事内だけ生成。参照が無いものは作らない） ---
        relationship_ids: List[str] = []
        seen_rel: set[Tuple[str, str, str]] = set()

        for rel in it.get("relationships", []):
            s_name = safe_str(rel.get("source_name"))
            s_type = safe_str(rel.get("source_stix_type"))
            t_name = safe_str(rel.get("target_name"))
            t_type = safe_str(rel.get("target_stix_type"))
            r_type = safe_str(rel.get("relationship_type") or "related-to")

            if not (s_name and s_type and t_name and t_type):
                continue
            s_ref = id_map.get((s_type, s_name))
            t_ref = id_map.get((t_type, t_name))
            if not s_ref or not t_ref:
                continue

            rel_key = (s_ref, r_type, t_ref)
            if rel_key in seen_rel:
                continue
            seen_rel.add(rel_key)

            rel_obj = build_relationship(rel, s_ref, t_ref, created, modified, creator_identity_id)
            objects.append(rel_obj)
            relationship_ids.append(rel_obj["id"])

        # --- Report作成 ---
        object_refs: List[str] = []
        object_refs.extend(list(id_map.values()))
        object_refs.extend(indicator_ids)
        object_refs.extend(relationship_ids)

        if not object_refs:
            manifest["skipped"].append(
                {
                    "_row_num": row_num,
                    "title": title,
                    "url": url,
                    "retrieval_status": retrieval_status,
                    "extraction_status": extraction_status,
                    "reason": "skip: no objects/indicators/relationships",
                }
            )
            continue

        report_name = (title or focus_summary or "Untitled Report")[:256]
        report_description = focus_summary[:4096] if focus_summary else ""

        report: Dict[str, Any] = {
            "type": "report",
            "spec_version": "2.1",
            "id": new_stix_id("report"),
            "created": created,
            "modified": modified,
            "created_by_ref": creator_identity_id,
            "name": report_name,
            "description": report_description,
            "published": created,
            "report_types": DEFAULT_REPORT_TYPES,
            "external_references": [{"source_name": "source", "url": url}] if url else [],
            "object_refs": object_refs,
        }

        # OpenCTI運用：cleanはReport内に保持（標準外プロパティx_として格納）
        if clean_text:
            report["x_opencti_content"] = clean_text
        if clean_sha256 and sha256_like(clean_sha256):
            report["x_opencti_clean_sha256"] = clean_sha256

        # manifestに「Indicatorでスキップしたもの」を残す（後で検証できる）
        report_meta = {
            "_row_num": row_num,
            "title": title,
            "url": url,
            "report_id": report["id"],
            "object_refs_count": len(report["object_refs"]),
            "has_clean_text": bool(clean_text),
            "has_raw_ref": bool(raw_saved_path or raw_sha256),
            "skipped_indicators": skipped_indicators,
        }

        objects.append(report)

        # rawはNoteに“参照のみ”を残す
        if raw_saved_path or (raw_sha256 and sha256_like(raw_sha256)):
            note = build_note_for_raw_ref(
                created=created,
                modified=modified,
                created_by_ref=creator_identity_id,
                report_id=report["id"],
                article_url=url,
                raw_saved_path=raw_saved_path,
                raw_sha256=raw_sha256,
                raw_char_len=raw_char_len,
                raw_truncated=raw_truncated,
            )
            objects.append(note)
            report["object_refs"].append(note["id"])

        manifest["reports"].append(report_meta)

    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "spec_version": "2.1",
        "objects": objects,
    }

    output_bundle.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    output_bundle.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    output_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ wrote bundle  : {output_bundle}")
    print(f"✅ wrote manifest: {output_manifest}")
    print(f"reports={len(manifest['reports'])} skipped={len(manifest['skipped'])} objects={len(objects)}")


if __name__ == "__main__":
    main()
