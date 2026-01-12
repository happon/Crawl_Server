import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXTRACTED = BASE_DIR / "stage4b_extracted_stix.json"
INPUT_CLEANED = BASE_DIR / "stage4_articles_cleaned.json"  # 任意（focus_summaryをReportに入れる）
OUTPUT_BUNDLE = BASE_DIR / "stage4c1_stix_bundle.json"
OUTPUT_MANIFEST = BASE_DIR / "stage4c1_manifest.json"

CREATOR_NAME = "Geopolitical Collector"
CREATOR_CLASS = "organization"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_stix_id(stix_type: str) -> str:
    return f"{stix_type}--{uuid.uuid4()}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_identity(created: str) -> Dict[str, Any]:
    return {
        "type": "identity",
        "spec_version": "2.1",
        "id": new_stix_id("identity"),
        "created": created,
        "modified": created,
        "name": CREATOR_NAME,
        "identity_class": CREATOR_CLASS,
    }


def build_sdo(obj: Dict[str, Any], created: str, created_by_ref: str) -> Dict[str, Any]:
    stix_type = (obj.get("stix_type") or "").strip()
    name = (obj.get("name") or "").strip()
    description = (obj.get("description") or "").strip()
    confidence = obj.get("confidence", None)

    sdo = {
        "type": stix_type,
        "spec_version": "2.1",
        "id": new_stix_id(stix_type),
        "created": created,
        "modified": created,
        "created_by_ref": created_by_ref,
        "name": name,
    }

    if description:
        sdo["description"] = description

    try:
        if confidence is not None and str(confidence).strip() != "":
            sdo["confidence"] = int(confidence)
    except Exception:
        pass

    # identity は identity_class が必須なので補完
    if stix_type == "identity":
        sdo["identity_class"] = "organization"

    return sdo


def build_report(
    title: str,
    url: str,
    description: str,
    object_refs: List[str],
    created: str,
    created_by_ref: str,
) -> Dict[str, Any]:
    report = {
        "type": "report",
        "spec_version": "2.1",
        "id": new_stix_id("report"),
        "created": created,
        "modified": created,
        "created_by_ref": created_by_ref,
        "name": (title or "Untitled Report")[:256],
        "report_types": ["threat-report"],
        "published": created,
        "object_refs": object_refs,
    }

    if description:
        report["description"] = description

    if url:
        report["external_references"] = [{"source_name": "source", "url": url}]

    return report


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

    identity = build_identity(created)
    identity_id = identity["id"]

    bundle_objects: List[Dict[str, Any]] = [identity]

    manifest = {
        "generated_at": created,
        "input_extracted": str(INPUT_EXTRACTED.name),
        "input_cleaned": str(INPUT_CLEANED.name) if INPUT_CLEANED.exists() else None,
        "output_bundle": str(OUTPUT_BUNDLE.name),
        "count_total": len(items),
        "count_reports": 0,
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
                "reason": "skip: not ok (e.g., no_cti)",
            })
            continue

        # Report説明：cleaned側のfocus_summaryがあれば優先、無ければnotes
        focus_summary = ""
        if url and url in cleaned_by_url:
            focus_summary = str(cleaned_by_url[url].get("focus_summary", "")).strip()

        notes = str(it.get("notes", "")).strip()
        report_desc = focus_summary or notes

        # この記事のSDOを生成（重複排除：同じ(stix_type,name)は1つ）
        id_map: Dict[Tuple[str, str], str] = {}
        article_sdos: List[Dict[str, Any]] = []

        for obj in it.get("objects", []):
            stix_type = str(obj.get("stix_type", "")).strip()
            name = str(obj.get("name", "")).strip()
            if not stix_type or not name:
                continue

            key = (stix_type, name)
            if key in id_map:
                continue

            sdo = build_sdo(obj, created, identity_id)
            id_map[key] = sdo["id"]
            article_sdos.append(sdo)

        if not article_sdos:
            manifest["skipped"].append({
                "_row_num": row_num,
                "title": title,
                "url": url,
                "retrieval_status": retrieval_status,
                "extraction_status": extraction_status,
                "reason": "skip: ok but no objects",
            })
            continue

        # Report（object_refsにSDOを紐づけ）
        report = build_report(
            title=title,
            url=url,
            description=report_desc,
            object_refs=[o["id"] for o in article_sdos],
            created=created,
            created_by_ref=identity_id,
        )

        bundle_objects.extend(article_sdos)
        bundle_objects.append(report)

        manifest["reports"].append({
            "_row_num": row_num,
            "title": title,
            "url": url,
            "report_id": report["id"],
            "sdo_count": len(article_sdos),
        })
        manifest["count_reports"] += 1

    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "spec_version": "2.1",
        "objects": bundle_objects,
    }

    OUTPUT_BUNDLE.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ wrote: {OUTPUT_BUNDLE}")
    print(f"✅ wrote: {OUTPUT_MANIFEST}")
    print(f"reports={manifest['count_reports']} skipped={len(manifest['skipped'])} objects={len(bundle_objects)}")


if __name__ == "__main__":
    main()
