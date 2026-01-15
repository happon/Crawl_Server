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

from src.common.paths import repo_root


CREATOR_NAME = "Geopolitical Collector"
CREATOR_CLASS = "organization"
DEFAULT_REPORT_TYPES = ["threat-report"]

ALLOWED_INDICATOR_TYPES = {"ip", "domain", "hash"}


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


# ----------------------------
# Indicator (IOC only)
# ----------------------------
def is_ip(value: str) -> bool:
    """
    IPv4 / IPv6 を許容
    """
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def is_domain(value: str) -> bool:
    """
    最低限のドメイン検証（過度に厳密にはしない）
    - '.' が含まれる
    - 全体長 <= 253
    - 許容文字: a-z0-9.-（簡略）
    - 先頭/末尾が '.' or '-' でない
    - '..' を含まない
    """
    v = value.strip().lower()
    if not v or len(v) > 253:
        return False
    if "." not in v:
        return False
    if v.startswith((".", "-")) or v.endswith((".", "-")):
        return False
    if ".." in v:
        return False
    if not re.fullmatch(r"[a-z0-9.-]+", v):
        return False
    return True


def build_indicator(ind: Dict[str, Any], created: str, modified: str, created_by_ref: str) -> Optional[Dict[str, Any]]:
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
        if not is_ip(value):
            return None
        # IPv4/IPv6両方許容
        try:
            ip_obj = ipaddress.ip_address(value)
            if isinstance(ip_obj, ipaddress.IPv4Address):
                pattern = f"[ipv4-addr:value = '{value}']"
            else:
                pattern = f"[ipv6-addr:value = '{value}']"
        except Exception:
            return None

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


# ----------------------------
# SDO creation
# ----------------------------
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
        base.setdefault("identity_class", "organization")

    return base


def build_relationship(
    relationship_type: str,
    source_ref: str,
    target_ref: str,
    created: str,
    modified: str,
    created_by_ref: str,
    confidence: int = 0,
) -> Dict[str, Any]:
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


def build_note_for_author_and_raw(
    *,
    created: str,
    modified: str,
    created_by_ref: str,
    report_id: str,
    article_url: str,
    publisher_name: str,
    authors: List[str],
    raw_saved_path: str,
    raw_sha256: str,
    raw_char_len: int,
    raw_truncated: bool,
) -> Dict[str, Any]:
    # OverviewのNotesで最初に見せたい行
    author_line = "author: " + ("; ".join(authors) if authors else "Unknown")
    publisher_line = f"publisher: {publisher_name or 'Unknown Publisher'}"

    lines = [author_line, publisher_line]

    # rawの参照（無い場合も「not available」を明示）
    if raw_saved_path or (raw_sha256 and sha256_like(raw_sha256)):
        lines.extend(
            [
                "raw_text_ref:",
                f"- article_url: {article_url}",
                f"- raw_saved_path: {raw_saved_path}",
                f"- raw_sha256: {raw_sha256}",
                f"- raw_char_len: {raw_char_len}",
                f"- raw_truncated: {raw_truncated}",
            ]
        )
    else:
        lines.append("raw_text_ref: (not available)")

    content = "\n".join(lines).strip()

    return {
        "type": "note",
        "spec_version": "2.1",
        "id": new_stix_id("note"),
        "created": created,
        "modified": modified,
        # Noteは「取り込みパイプラインが付与したメタ情報」なので creator(collector) を使用
        "created_by_ref": created_by_ref,
        "content": content[:20000],
        "object_refs": [report_id],
    }


# ----------------------------
# Author / Publisher handling
# ----------------------------
def normalize_author_key(name: str) -> str:
    s = safe_str(name).lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[.,;:()\"'`’“”\-_/\\]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_authors(author_value: Any) -> List[str]:
    """
    authorが欠落/空/None/文字列/配列のどれでも安全にList[str]へ正規化する。
    文字列の場合は一般的な区切り（, ; | and）に対応。
    """
    if author_value is None:
        return []

    if isinstance(author_value, list):
        out: List[str] = []
        seen = set()
        for x in author_value:
            s = safe_str(x)
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    s = safe_str(author_value)
    if not s:
        return []

    s = s.replace("|", ",").replace(";", ",")
    s = re.sub(r"\s+and\s+", ",", s, flags=re.IGNORECASE)
    parts = [p.strip() for p in s.split(",")]

    out2: List[str] = []
    seen2 = set()
    for p in parts:
        if not p:
            continue
        if p in seen2:
            continue
        seen2.add(p)
        out2.append(p)
    return out2


def make_publisher_identity(name: str, created: str, modified: str) -> Dict[str, Any]:
    """
    推奨修正:
    - publisher identity は “取り込み側が作った” という印象を避けるため created_by_ref を付けない
      （必要なら戻せる）
    """
    return {
        "type": "identity",
        "spec_version": "2.1",
        "id": new_stix_id("identity"),
        "created": created,
        "modified": modified,
        "name": name[:256],
        "identity_class": "organization",
    }


def make_author_identity(name: str, created: str, modified: str, created_by_ref: str) -> Dict[str, Any]:
    desc = (
        "Author name as listed in the source article. "
        "May represent a pseudonym/handle/persona; not asserted as a verified real-world individual at ingestion time."
    )
    return {
        "type": "identity",
        "spec_version": "2.1",
        "id": new_stix_id("identity"),
        "created": created,
        "modified": modified,
        "created_by_ref": created_by_ref,
        "name": name[:256],
        "identity_class": "individual",
        "description": desc,
    }


@dataclass
class GlobalRegistry:
    # (type,name) -> id
    sdo_ids: Dict[Tuple[str, str], str]
    # (indicator_type,value) -> id
    ind_ids: Dict[Tuple[str, str], str]
    # publisher_name -> identity_id
    publisher_ids: Dict[str, str]
    # (publisher_name, normalized_author_key) -> identity_id
    author_ids: Dict[Tuple[str, str], str]

    def __init__(self) -> None:
        self.sdo_ids = {}
        self.ind_ids = {}
        self.publisher_ids = {}
        self.author_ids = {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage4C: build STIX 2.1 Bundle from extracted entities + cleaned info.")
    parser.add_argument("--extracted", default=None, help="default: <root>/data/stage4b_extracted_stix.json")
    parser.add_argument("--cleaned", default=None, help="default: <root>/data/stage4_articles_cleaned.json")
    parser.add_argument("--included", default=None, help="optional: <root>/data/stage4_input_included.json (for source/author補完)")
    parser.add_argument("--out-bundle", default=None, help="default: <root>/data/stage4_stix_bundle.json")
    parser.add_argument("--out-manifest", default=None, help="default: <root>/data/stage4c_manifest.json")
    args = parser.parse_args()

    root = repo_root()

    input_extracted = Path(args.extracted).expanduser().resolve() if args.extracted else (root / "data" / "stage4b_extracted_stix.json")
    input_cleaned = Path(args.cleaned).expanduser().resolve() if args.cleaned else (root / "data" / "stage4_articles_cleaned.json")
    input_included = Path(args.included).expanduser().resolve() if args.included else (root / "data" / "stage4_input_included.json")
    output_bundle = Path(args.out_bundle).expanduser().resolve() if args.out_bundle else (root / "data" / "stage4_stix_bundle.json")
    output_manifest = Path(args.out_manifest).expanduser().resolve() if args.out_manifest else (root / "data" / "stage4c_manifest.json")

    if not input_extracted.exists():
        raise FileNotFoundError(f"missing input: {input_extracted}")

    extracted = load_json(input_extracted)
    items = extracted.get("items", [])
    if not isinstance(items, list):
        raise ValueError("stage4b_extracted_stix.json: items must be a list")

    # cleaned: url -> item
    cleaned_by_url: Dict[str, Dict[str, Any]] = {}
    if input_cleaned.exists():
        cleaned = load_json(input_cleaned)
        for it in cleaned.get("items", []):
            u = safe_str(it.get("url"))
            if u:
                cleaned_by_url[u] = it

    # included: url/_row_num -> item（source/author補完用）
    included_by_url: Dict[str, Dict[str, Any]] = {}
    included_by_row: Dict[int, Dict[str, Any]] = {}
    if input_included.exists():
        inc = load_json(input_included)
        rows = inc.get("rows", [])
        if isinstance(rows, list):
            for r in rows:
                u = safe_str(r.get("url"))
                rn = r.get("_row_num")
                if u:
                    included_by_url[u] = r
                if isinstance(rn, int):
                    included_by_row[rn] = r

    created = now_utc_iso()
    modified = created

    # “生成者”（取り込みパイプライン）は全オブジェクトの created_by_ref に使う（ReportのAuthor表現とは別）
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
        "input_included": str(input_included) if input_included.exists() else None,
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

    def get_or_create_publisher_id(publisher_name: str) -> str:
        name = safe_str(publisher_name) or "Unknown Publisher"
        if name in registry.publisher_ids:
            return registry.publisher_ids[name]
        pub = make_publisher_identity(name, created, modified)
        objects.append(pub)
        registry.publisher_ids[name] = pub["id"]
        return pub["id"]

    def get_or_create_author_id(publisher_name: str, author_name: str) -> str:
        pub_name = safe_str(publisher_name) or "Unknown Publisher"
        key_norm = normalize_author_key(author_name)
        key = (pub_name, key_norm)
        if key in registry.author_ids:
            return registry.author_ids[key]
        auth = make_author_identity(author_name, created, modified, creator_identity_id)
        objects.append(auth)
        registry.author_ids[key] = auth["id"]
        return auth["id"]

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

        # --- source/author 補完（優先: included -> 次: cleaned -> 最後: extracted） ---
        inc_item: Dict[str, Any] = {}
        if url and url in included_by_url:
            inc_item = included_by_url[url]
        elif isinstance(row_num, int) and row_num in included_by_row:
            inc_item = included_by_row[row_num]

        publisher_name = (
            safe_str(inc_item.get("source"))
            or safe_str(cleaned_item.get("source"))
            or safe_str(it.get("source"))
            or "Unknown Publisher"
        )
        authors = parse_authors(inc_item.get("author") or cleaned_item.get("author") or it.get("author"))

        publisher_id = get_or_create_publisher_id(publisher_name)

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

        # --- Indicator登録（IOCのみ） ---
        indicator_ids: List[str] = []
        skipped_indicators: List[Dict[str, Any]] = []

        for ind in it.get("indicators", []):
            itype = safe_str(ind.get("indicator_type")).lower()
            value = safe_str(ind.get("value"))
            if not itype or not value:
                continue

            candidate = build_indicator(ind, created, modified, creator_identity_id)
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

        # --- Relationship（抽出結果に基づく） ---
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

            rel_obj = build_relationship(r_type, s_ref, t_ref, created, modified, creator_identity_id)
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

        # ReportのAuthor＝出版社（created_by_ref を出版社にする）
        report: Dict[str, Any] = {
            "type": "report",
            "spec_version": "2.1",
            "id": new_stix_id("report"),
            "created": created,
            "modified": modified,
            "created_by_ref": publisher_id,
            "name": report_name,
            "description": report_description,
            "published": created,
            "report_types": DEFAULT_REPORT_TYPES,
            "external_references": [{"source_name": "source", "url": url}] if url else [],
            "object_refs": object_refs,
        }

        # OpenCTI運用：cleanはReport内に保持（標準外プロパティx_）
        if clean_text:
            report["x_opencti_content"] = clean_text
        if clean_sha256 and sha256_like(clean_sha256):
            report["x_opencti_clean_sha256"] = clean_sha256

        objects.append(report)

        # ★ author用Noteを常に「1つ」作る（著者0でもOK / raw無くてもOK）
        author_note = build_note_for_author_and_raw(
            created=created,
            modified=modified,
            created_by_ref=creator_identity_id,
            report_id=report["id"],
            article_url=url,
            publisher_name=publisher_name,
            authors=authors,
            raw_saved_path=raw_saved_path,
            raw_sha256=raw_sha256,
            raw_char_len=raw_char_len,
            raw_truncated=raw_truncated,
        )
        objects.append(author_note)
        report["object_refs"].append(author_note["id"])

        # ★著者（Individual）を作成し、Reportと created-by、著者と出版社を related-to で接続
        author_ids: List[str] = []
        for a in authors:
            aid = get_or_create_author_id(publisher_name, a)
            author_ids.append(aid)

            rel_created_by = build_relationship(
                "created-by", report["id"], aid, created, modified, creator_identity_id, confidence=60
            )
            objects.append(rel_created_by)
            report["object_refs"].append(rel_created_by["id"])

            rel_related = build_relationship(
                "related-to", aid, publisher_id, created, modified, creator_identity_id, confidence=40
            )
            objects.append(rel_related)
            report["object_refs"].append(rel_related["id"])

        manifest["reports"].append(
            {
                "_row_num": row_num,
                "title": title,
                "url": url,
                "report_id": report["id"],
                "publisher": publisher_name,
                "authors": authors,
                "object_refs_count": len(report["object_refs"]),
                "has_clean_text": bool(clean_text),
                "has_raw_ref": bool(raw_saved_path or raw_sha256),
                "skipped_indicators": skipped_indicators,
            }
        )

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
