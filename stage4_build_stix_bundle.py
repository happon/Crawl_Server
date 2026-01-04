import json
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ===== ディレクトリ固定（この .py と同じ場所）=====
BASE_DIR = Path(__file__).resolve().parent

# ===== 設定 =====
INPUT_JSON = BASE_DIR / "stage4_input_included.json"   # 前段の抽出結果
OUTPUT_STIX = BASE_DIR / "stage4_stix_bundle.json"     # 生成されるSTIX Bundle

CREATOR_NAME = "Geopolitical Collector"
CREATOR_CLASS = "organization"
DEFAULT_REPORT_TYPES = ["threat-report"]


# ===== ユーティリティ =====
def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def now_utc() -> str:
    return iso_utc(datetime.now(timezone.utc))


def stix_id(stix_type: str) -> str:
    return f"{stix_type}--{uuid.uuid4()}"


def parse_datetime_any(value) -> datetime | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None

    # RFC822/RFC2822
    try:
        dt = parsedate_to_datetime(v)
        if dt is not None:
            return dt
    except Exception:
        pass

    # ISO
    try:
        if v.endswith("Z"):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return datetime.fromisoformat(v)
    except Exception:
        return None


def normalize_tags(tags_val) -> list[str]:
    if tags_val is None:
        return []
    if isinstance(tags_val, list):
        return [str(x).strip() for x in tags_val if str(x).strip()]

    s = str(tags_val).strip()
    if not s:
        return []

    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass

    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]

    return [s]


def pick_published(row: dict) -> datetime:
    for key in ["published", "published_date", "pubDate", "modified_date", "updated", "date"]:
        dt = parse_datetime_any(row.get(key))
        if dt:
            return dt
    return datetime.now(timezone.utc)


def safe_str(row: dict, key: str) -> str:
    v = row.get(key, "")
    return str(v).strip() if v is not None else ""


# ===== メイン =====
def main():
    print(f"INPUT : {INPUT_JSON}")
    print(f"OUTPUT: {OUTPUT_STIX}")

    if not INPUT_JSON.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {INPUT_JSON}")

    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    print(f"rows: {len(rows)}")

    created_ts = now_utc()

    identity_id = stix_id("identity")
    creator_identity = {
        "type": "identity",
        "spec_version": "2.1",
        "id": identity_id,
        "created": created_ts,
        "modified": created_ts,
        "name": CREATOR_NAME,
        "identity_class": CREATOR_CLASS,
    }

    objects = [creator_identity]

    for row in rows:
        url = safe_str(row, "url")
        title = safe_str(row, "title")
        logic_title = safe_str(row, "logic_title") or title
        category_main = safe_str(row, "category_main")
        summary = safe_str(row, "summary")
        summary_detail = safe_str(row, "summary_detail") or summary
        publisher = safe_str(row, "source") or safe_str(row, "publisher")

        tags = normalize_tags(row.get("tags"))
        published_ts = iso_utc(pick_published(row))

        # Indicator（URL）
        indicator_id = stix_id("indicator")
        url_escaped = url.replace("'", "\\'")

        indicator = {
            "type": "indicator",
            "spec_version": "2.1",
            "id": indicator_id,
            "created": created_ts,
            "modified": created_ts,
            "created_by_ref": identity_id,
            "name": title or logic_title or "Article URL",
            "description": summary or "",
            "pattern_type": "stix",
            "pattern": f"[url:value = '{url_escaped}']" if url else "[url:value = '']",
            "valid_from": published_ts,
            "labels": (["url"] + ([category_main] if category_main else []) + tags)[:20],
        }

        # Report
        report_id = stix_id("report")

        report_labels = []
        if category_main:
            report_labels.append(category_main)
        report_labels += tags

        external_refs = []
        if url:
            external_refs.append({"source_name": publisher or "article", "url": url})

        report = {
            "type": "report",
            "spec_version": "2.1",
            "id": report_id,
            "created": created_ts,
            "modified": created_ts,
            "created_by_ref": identity_id,
            "name": logic_title or title or "Report",
            "description": summary_detail or summary or "",
            "published": published_ts,
            "report_types": DEFAULT_REPORT_TYPES,
            "labels": report_labels[:20] if report_labels else ["report"],
            "external_references": external_refs,
            "object_refs": [indicator_id],
        }

        objects.append(indicator)
        objects.append(report)

    bundle = {
        "type": "bundle",
        "id": stix_id("bundle"),
        "objects": objects,
    }

    OUTPUT_STIX.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ STIX Bundle を生成しました: {OUTPUT_STIX}")
    print(f"objects: {len(objects)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 失敗: {e}")
        import traceback
        traceback.print_exc()
