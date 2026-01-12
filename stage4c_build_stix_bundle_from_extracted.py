import json
import uuid
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Tuple, Any, List

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXTRACTED = BASE_DIR / "stage4b_extracted_stix.json"
INPUT_CLEANED = BASE_DIR / "stage4_articles_cleaned.json"   # focus_summary をReportに入れるため（無ければ空で可）
OUTPUT_BUNDLE = BASE_DIR / "stage4_stix_bundle.json"
OUTPUT_MANIFEST = BASE_DIR / "stage4c_manifest.json"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_stix_id(stix_type: str) -> str:
    # STIX 2.1 のIDはUUIDv4が前提
    return f"{stix_type}--{uuid.uuid4()}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_get_focus_summary(cleaned_by_url: Dict[str, Dict[str, Any]], url: str) -> str:
    item = cleaned_by_url.get(url)
    if not item:
        return ""
    return str(item.get("focus_summary", "")).strip()


def stix_object_from_extracted(obj: Dict[str, Any], created: str, modified: str) -> Dict[str, Any]:
    stix_type = obj["stix_type"]
    name = obj.get("name", "").strip()
    description = obj.get("description", "").strip()
    confidence = int(obj.get("confidence", 0) or 0)

    base = {
        "type": stix_type,
        "spec_version": "2.1",
        "id": new_stix_id(stix_type),
        "created": created,
        "modified": modified,
        "name": name,
    }
    if description:
        base["description"] = description
    if confidence:
        base["confidence"] = confidence

    # typeごとの必須/推奨フィールド補完
    if stix_type == "identity":
        # 組織想定（Koi Security等）
        base["identity_class"] = "organization"
    elif stix_type == "report":
        # ここには来ない想定
        pass

    return base


def build_indicator(ind: Dict[str, Any], created: str, modified: str) -> Dict[str, Any]:
    itype = (ind.get("indicator_type") or "other").strip()
    value = (ind.get("value") or "").strip()
    context = (ind.get("context") or "").strip()
    confidence = int(ind.get("confidence", 0) or 0)

    # STIX pattern の最小対応（必要になったら拡張）
    pattern = None
    if itype == "ip":
        pattern = f"[ipv4-addr:value = '{value}']"
    elif itype == "domain":
        pattern = f"[domain-name:value = '{value}']"
    elif itype == "url":
        pattern = f"[url:value = '{value}']"
    elif itype == "email":
        pattern = f"[email-addr:value = '{value}']"
    elif itype == "hash":
        v = value.lower()
        if re.fullmatch(r"[0-9a-f]{32}", v):
            pattern = f"[file:hashes.MD5 = '{v}']"
        elif re.fullmatch(r"[0-9a-f]{40}", v):
            pattern = f"[file:hashes.SHA1 = '{v}']"
        elif re.fullmatch(r"[0-9a-f]{64}", v):
            pattern = f"[file:hashes.SHA256 = '{v}']"
        else:
            pattern = f"[file:hashes.'UNKNOWN' = '{value}']"
    else:
        # 非標準は安全に "other" として pattern を作らず notes に退避
        pattern = None

    out = {
        "type": "indicator",
        "spec_version": "2.1",
        "id": new_stix_id("indicator"),
        "created": created,
        "modified": modified,
        "name": f"{itype}:{value}"[:256],
        "valid_from": created,
    }
    if pattern:
        out["pattern_type"] = "stix"
        out["pattern"] = pattern
    else:
        out["pattern_type"] = "stix"
        out["pattern"] = "[x-opencti-text:value = 'unsupported-indicator-type']"
        out["description"] = f"Unsupported indicator_type={itype}. value={value}. {context}".strip()

    if confidence:
        out["confidence"] = confidence

    return out


def build_relationship(rel: Dict[str, Any], source_ref: str, target_ref: str, created: str, modified: str) -> Dict[str, Any]:
    relationship_type = (rel.get("relationship_type") or "related-to").strip()
    confidence = int(rel.get("confidence", 0) or 0)

    out = {
        "type": "relationship",
        "spec_version": "2.1",
        "id": new_stix_id("relationship"),
        "created": created,
        "modified": modified,
        "relationship_type": relationship_type,
        "source_ref": source_ref,
        "target_ref": target_ref,
    }
    if confidence:
        out["confidence"] = confidence
    return out


def main():
    if not INPUT_EXTRACTED.exists():
        raise FileNotFoundError(f"入力が見つかりません: {INPUT_EXTRACTED}")

    extracted = load_json(INPUT_EXTRACTED)
    items = extracted.get("items", [])

    cleaned_by_url: Dict[str, Dict[str, Any]] = {}
    if INPUT_CLEANED.exists():
        cleaned = load_json(INPUT_CLEANED)
        for it in cleaned.get("items", []):
            u = str(it.get("url", "")).strip()
            if u:
                cleaned_by_url[u] = it

    created = now_utc_iso()
    modified = created

    all_objects: List[Dict[str, Any]] = []
    manifest = {
        "generated_at": created,
        "input_extracted": str(INPUT_EXTRACTED.name),
        "input_cleaned": str(INPUT_CLEANED.name) if INPUT_CLEANED.exists() else None,
        "reports": [],
        "skipped": [],
    }

    for it in items:
        row_num = it.get("_row_num")
        title = str(it.get("title", "")).strip()
        url = str(it.get("url", "")).strip()
        retrieval_status = str(it.get("retrieval_status", "")).strip()
        extraction_status = str(it.get("extraction_status", "")).strip()

        if retrieval_status != "ok" or extraction_status != "ok":
            manifest["skipped"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": retrieval_status,
                "extraction_status": extraction_status,
                "reason": "skip: not ok",
            })
            continue

        focus_summary = safe_get_focus_summary(cleaned_by_url, url)

        # 1) SDO/Indicator作成（重複排除は「(type,name)」で同一記事内のみ）
        id_map: Dict[Tuple[str, str], str] = {}
        article_objects: List[Dict[str, Any]] = []

        for obj in it.get("objects", []):
            st = (obj.get("stix_type") or "").strip()
            nm = (obj.get("name") or "").strip()
            if not st or not nm:
                continue
            key = (st, nm)
            if key in id_map:
                continue
            sdo = stix_object_from_extracted(obj, created, modified)
            id_map[key] = sdo["id"]
            article_objects.append(sdo)

        indicators_out: List[Dict[str, Any]] = []
        for ind in it.get("indicators", []):
            value = (ind.get("value") or "").strip()
            if not value:
                continue
            ind_obj = build_indicator(ind, created, modified)
            indicators_out.append(ind_obj)

        # 2) Relationship作成（source/targetが見つからない場合は作らない＝推測回避）
        relationships_out: List[Dict[str, Any]] = []
        for rel in it.get("relationships", []):
            s_name = (rel.get("source_name") or "").strip()
            s_type = (rel.get("source_stix_type") or "").strip()
            t_name = (rel.get("target_name") or "").strip()
            t_type = (rel.get("target_stix_type") or "").strip()

            if not (s_name and s_type and t_name and t_type):
                continue

            s_ref = id_map.get((s_type, s_name))
            t_ref = id_map.get((t_type, t_name))
            if not s_ref or not t_ref:
                # 足りないものを勝手に作らない（推測回避）
                continue

            relationships_out.append(build_relationship(rel, s_ref, t_ref, created, modified))

        # 3) Report作成（object_refs は必須なので、最低1つは必要）
        object_refs = [o["id"] for o in article_objects] + [o["id"] for o in indicators_out]
        if not object_refs:
            # extraction ok なのに参照がゼロは不自然なのでスキップ扱いにする
            manifest["skipped"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": retrieval_status,
                "extraction_status": extraction_status,
                "reason": "skip: no object_refs for report",
            })
            continue

        report = {
            "type": "report",
            "spec_version": "2.1",
            "id": new_stix_id("report"),
            "created": created,
            "modified": modified,
            "name": title[:256] if title else (focus_summary[:256] if focus_summary else "Untitled Report"),
            "report_types": ["threat-report"],
            "published": created,
            "object_refs": object_refs,
            "external_references": [{"source_name": "source", "url": url}] if url else [],
            "description": focus_summary if focus_summary else "",
        }

        # 4) Bundleに積む（Report + SDO + Indicator + Relationship）
        bundle_objects = [report] + article_objects + indicators_out + relationships_out
        all_objects.extend(bundle_objects)

        manifest["reports"].append({
            "_row_num": row_num,
            "title": title,
            "url": url,
            "report_id": report["id"],
            "object_count": len(bundle_objects),
            "sdo_count": len(article_objects),
            "indicator_count": len(indicators_out),
            "relationship_count": len(relationships_out),
        })

    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "spec_version": "2.1",
        "objects": all_objects,
    }

    OUTPUT_BUNDLE.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ wrote: {OUTPUT_BUNDLE}")
    print(f"✅ wrote: {OUTPUT_MANIFEST}")
    print(f"reports={len(manifest['reports'])} skipped={len(manifest['skipped'])} objects={len(all_objects)}")


if __name__ == "__main__":
    main()
