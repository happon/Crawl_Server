from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.common.paths import repo_root


CREATOR_NAME = "Geopolitical Collector"  # bundle内のcollector identity名を探すヒント（無くても動く）
NOTE_MAX_CHARS = 20000


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_stix_id(stix_type: str) -> str:
    return f"{stix_type}--{uuid.uuid4()}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def normalize_author_key(name: str) -> str:
    s = safe_str(name).lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[.,;:()\"'`’“”\-_/\\]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_authors_from_byline(byline: str) -> List[str]:
    """
    "Bill Toulas" / "Bill Toulas and Jane Roe" / "Bill Toulas, Jane Roe" 等を想定して分割。
    """
    s = safe_str(byline)
    if not s:
        return []

    # 余計な末尾（例: "By X in Security" 等）への簡易対処
    s = re.sub(r"\s+\|\s+.*$", "", s).strip()

    # "and" / "&" をカンマに寄せる
    s = re.sub(r"\s+and\s+", ",", s, flags=re.IGNORECASE)
    s = s.replace("&", ",")

    # 区切り統一
    s = s.replace(";", ",").replace("|", ",")

    parts = [p.strip() for p in s.split(",") if p.strip()]
    out: List[str] = []
    seen: Set[str] = set()
    for p in parts:
        if p.lower().startswith("by "):
            p = p[3:].strip()
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def extract_author_from_raw_text(raw_text: str) -> List[str]:
    """
    raw txt の冒頭付近から "By ..." を拾う。
    BleepingComputer は 2行目に "By Bill Toulas" などが出やすい想定。
    """
    if not raw_text:
        return []

    # 先頭付近だけで十分（誤検出も減る）
    head = raw_text[:5000]
    lines = head.splitlines()[:30]

    # 行単位で "By " を探索
    for line in lines:
        m = re.match(r"^\s*By\s+(.+?)\s*$", line, flags=re.IGNORECASE)
        if m:
            byline = m.group(1).strip()
            authors = split_authors_from_byline(byline)
            if authors:
                return authors

    # 保険：HTML由来で "By&nbsp;X" 等が混ざるケース
    m2 = re.search(r"\bBy\s+([A-Z][^\n\r<]{2,80})", head)
    if m2:
        return split_authors_from_byline(m2.group(1))

    return []


def parse_raw_saved_path_from_note(note_content: str) -> str:
    """
    Note内の
      - raw_saved_path: /path/to/file.txt
    を抜く。
    """
    if not note_content:
        return ""
    m = re.search(r"^- raw_saved_path:\s*(.+?)\s*$", note_content, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def update_note_author_line(note_content: str, authors: List[str]) -> str:
    """
    Noteの先頭行を author: ... に統一して更新。
    既存が無ければ先頭に追加。
    """
    author_line = "author: " + ("; ".join(authors) if authors else "Unknown")
    if not note_content:
        return author_line

    lines = note_content.splitlines()
    if lines and lines[0].lower().startswith("author:"):
        lines[0] = author_line
        updated = "\n".join(lines)
    else:
        updated = author_line + "\n" + note_content

    return updated[:NOTE_MAX_CHARS]


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage4/05: Enrich bundle with author identities + relationships from raw text 'By ...'.")
    parser.add_argument("--in-bundle", default=None, help="default: <root>/data/stage4_stix_bundle.json")
    parser.add_argument("--out-bundle", default=None, help="default: <root>/data/stage4_stix_bundle_enriched.json")
    parser.add_argument("--dry-run", action="store_true", help="Do not write output; only report what would change.")
    args = parser.parse_args()

    root = repo_root()
    in_bundle = Path(args.in_bundle).expanduser().resolve() if args.in_bundle else (root / "data" / "stage4_stix_bundle.json")
    out_bundle = Path(args.out_bundle).expanduser().resolve() if args.out_bundle else (root / "data" / "stage4_stix_bundle_enriched.json")

    if not in_bundle.exists():
        raise FileNotFoundError(f"missing input bundle: {in_bundle}")

    bundle = load_json(in_bundle)
    objs: List[Dict[str, Any]] = bundle.get("objects", [])
    if not isinstance(objs, list):
        raise ValueError("bundle.objects must be a list")

    # index
    by_id: Dict[str, Dict[str, Any]] = {}
    reports: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []
    for o in objs:
        oid = safe_str(o.get("id"))
        if oid:
            by_id[oid] = o
        if o.get("type") == "report":
            reports.append(o)
        if o.get("type") == "note":
            notes.append(o)

    created = now_utc_iso()
    modified = created

    # collector identity id (created_by_ref for new objects)
    collector_id = ""
    for o in objs:
        if o.get("type") == "identity" and safe_str(o.get("name")) == CREATOR_NAME:
            collector_id = safe_str(o.get("id"))
            break
    if not collector_id:
        # fallback: first identity
        for o in objs:
            if o.get("type") == "identity":
                collector_id = safe_str(o.get("id"))
                break
    if not collector_id:
        raise ValueError("No identity found in bundle to use as collector (created_by_ref).")

    # existing author identities (dedupe)
    # key: normalized_name -> identity_id (bundle全体で統一)
    existing_author_ids: Dict[str, str] = {}
    for o in objs:
        if o.get("type") == "identity" and safe_str(o.get("identity_class")) == "individual":
            nm = safe_str(o.get("name"))
            if nm:
                existing_author_ids[normalize_author_key(nm)] = safe_str(o.get("id"))

    # existing relationships dedupe
    rel_keys: Set[Tuple[str, str, str]] = set()
    for o in objs:
        if o.get("type") == "relationship":
            rel_keys.add((safe_str(o.get("source_ref")), safe_str(o.get("relationship_type")), safe_str(o.get("target_ref"))))

    changed_reports = 0
    added_identities = 0
    added_relationships = 0
    updated_notes = 0

    # Helper: find note that references report_id (the "author/publisher/raw_text_ref" note)
    def find_note_for_report(report_id: str) -> Optional[Dict[str, Any]]:
        for n in notes:
            refs = n.get("object_refs")
            if isinstance(refs, list) and report_id in refs:
                # author_lineを持っている可能性が高いnoteを優先
                content = safe_str(n.get("content"))
                if content.lower().startswith("author:"):
                    return n
        # fallback: any note referencing report
        for n in notes:
            refs = n.get("object_refs")
            if isinstance(refs, list) and report_id in refs:
                return n
        return None

    for rep in reports:
        rep_id = safe_str(rep.get("id"))
        if not rep_id:
            continue

        publisher_id = safe_str(rep.get("created_by_ref"))  # publisher
        publisher_obj = by_id.get(publisher_id, {})
        publisher_name = safe_str(publisher_obj.get("name")) or "Unknown Publisher"

        note = find_note_for_report(rep_id)
        if not note:
            # 既存設計では必ずnoteがある想定だが、無ければスキップ
            continue

        raw_path = parse_raw_saved_path_from_note(safe_str(note.get("content")))
        if not raw_path:
            # raw参照が無いならスキップ
            continue

        raw_file = Path(raw_path)
        if not raw_file.exists():
            continue

        raw_text = raw_file.read_text(encoding="utf-8", errors="ignore")
        authors = extract_author_from_raw_text(raw_text)

        # 著者が取れないなら何もしない（＝このReportでは05は実質不実行）
        if not authors:
            continue

        # 1) Noteの先頭行を更新
        new_content = update_note_author_line(safe_str(note.get("content")), authors)
        if new_content != safe_str(note.get("content")):
            note["content"] = new_content
            updated_notes += 1

        # 2) Author identity + relationships
        # report.object_refs が無いケースに備えて補完
        if not isinstance(rep.get("object_refs"), list):
            rep["object_refs"] = []
        obj_refs: List[str] = rep["object_refs"]  # type: ignore[assignment]

        for a in authors:
            key = normalize_author_key(a)
            if not key:
                continue

            if key in existing_author_ids:
                author_id = existing_author_ids[key]
            else:
                author_identity = make_author_identity(a, created, modified, collector_id)
                author_id = safe_str(author_identity["id"])
                objs.append(author_identity)
                by_id[author_id] = author_identity
                existing_author_ids[key] = author_id
                added_identities += 1

            # Report -> Author (created-by)
            rk1 = (rep_id, "created-by", author_id)
            if rk1 not in rel_keys:
                r1 = build_relationship("created-by", rep_id, author_id, created, modified, collector_id, confidence=60)
                objs.append(r1)
                rel_keys.add(rk1)
                added_relationships += 1
                obj_refs.append(safe_str(r1["id"]))

            # Author -> Publisher (related-to)
            if publisher_id:
                rk2 = (author_id, "related-to", publisher_id)
                if rk2 not in rel_keys:
                    r2 = build_relationship("related-to", author_id, publisher_id, created, modified, collector_id, confidence=40)
                    objs.append(r2)
                    rel_keys.add(rk2)
                    added_relationships += 1
                    obj_refs.append(safe_str(r2["id"]))

        changed_reports += 1

    # bundle更新
    bundle["objects"] = objs
    bundle.setdefault("x_enriched_at", now_utc_iso())
    bundle.setdefault("x_enrichment", {})
    bundle["x_enrichment"] = {
        "stage": "05_enrich_authors",
        "changed_reports": changed_reports,
        "added_identities": added_identities,
        "added_relationships": added_relationships,
        "updated_notes": updated_notes,
    }

    print(f"changed_reports={changed_reports} added_identities={added_identities} added_relationships={added_relationships} updated_notes={updated_notes} publisher_hint={publisher_name}")

    if args.dry_run:
        print("dry-run: no file written.")
        return

    write_json(out_bundle, bundle)
    print(f"✅ wrote enriched bundle: {out_bundle}")


if __name__ == "__main__":
    main()
