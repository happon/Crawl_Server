# stage4d_import_to_opencti.py
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from pycti import OpenCTIApiClient


BUNDLE_FILE = "stage4_stix_bundle.json"


def bool_env(name: str, default: bool = True) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def main():
    load_dotenv()

    opencti_url = os.getenv("OPENCTI_URL")
    opencti_token = os.getenv("OPENCTI_TOKEN")
    ssl_verify = bool_env("OPENCTI_SSL_VERIFY", True)

    if not opencti_url or not opencti_token:
        raise SystemExit("OPENCTI_URL と OPENCTI_TOKEN を .env に設定してください。")

    path = Path(BUNDLE_FILE)
    if not path.exists():
        raise SystemExit(f"{BUNDLE_FILE} が見つかりません（実行ディレクトリ: {Path.cwd()}）")

    # 事前に内容だけ確認（objects数など）
    bundle = json.loads(path.read_text(encoding="utf-8"))
    obj_count = len(bundle.get("objects", []))
    obj_types = {}
    for o in bundle.get("objects", []):
        t = o.get("type", "unknown")
        obj_types[t] = obj_types.get(t, 0) + 1

    print(f"📦 Bundle: type={bundle.get('type')}  objects={obj_count}  types={obj_types}")

    # OpenCTI 接続
    client = OpenCTIApiClient(opencti_url, opencti_token, ssl_verify=ssl_verify)

    # import（update=True は「既存があれば更新」を許可）
    print("🚀 Importing STIX bundle to OpenCTI...")
    result = client.stix2.import_bundle_from_file(
        file_path=str(path),
        update=True,
    )

    # result は環境/バージョンで形式が揺れることがあるので、見える範囲で出す
    print("✅ Import request sent.")
    if result is None:
        print("ℹ️ Result: None (OpenCTI側で非同期処理の可能性があります)")
    else:
        try:
            # dict っぽい場合
            if isinstance(result, dict):
                print("🔎 Result keys:", list(result.keys()))
                print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
            else:
                # list 等
                print("🔎 Result type:", type(result))
                print(str(result)[:2000])
        except Exception:
            print("ℹ️ Result: (could not pretty print)")
            print(result)


if __name__ == "__main__":
    main()
