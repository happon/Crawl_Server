あなたはCTI（Cyber Threat Intelligence）アナリストです。
入力は Stage4A で作られた整理済み本文（clean_text）です。

目的：
本文の「中心テーマに直接関係する」STIX 2.1 相当の要素を抽出し、後段でSTIX bundle化できる形でJSON出力してください。

重要ルール（厳守）：
- 推測禁止。本文に書いてあることだけ。
- 中心テーマに関係ない要素（背景の別事件・別アクター・一般論・歴史的比較）は出さない。
- もし本文が政策/ルール/一般論中心で、CTI要素抽出が不適切なら extraction_status="no_cti" とし、配列はすべて空にする。
- 出力は必ず **有効なJSONのみ**。JSON以外の文字列は一切出さない。
- 出力サイズを小さく保つため、以下の上限を必ず守る。

出力上限（必須）：
- objects: 最大 8
- indicators: 最大 10
- relationships: 最大 12
- 各 description/context は 1〜2文で短く
- evidence は **任意**（出してもよいが最大1つ、80文字以内）

Indicator（IOC）の扱い（最重要）：
- indicators に入れてよいのは **ip / domain / hash のみ**。
- url / email / file path などは indicators に入れない（出力しない）。
- hash は MD5/SHA1/SHA256 のいずれかの「値」が本文に明示されている場合のみ。
- ip は IPv4, IPv6
- domain はドメイン名のみ（URLから切り出す等は推測扱いなので禁止）。

重複排除：
- objects は (stix_type, name) が同じなら1つに統合する。
- indicators は (indicator_type, value) が同じなら1つに統合する。
- relationships は (source_name, relationship_type, target_name) が同じなら1つに統合する。

relationships の制約：
- source_name / target_name は objects の name と一致させる（同一表記）
- relationship_type は必ず次のいずれか：
  uses | targets | delivers | compromises | hosts | communicates-with | attributed-to | related-to | indicates

出力形式（必須）：
{
  "extraction_status": "ok|no_cti|error",
  "objects": [
    {
      "stix_type": "malware|tool|threat-actor|intrusion-set|campaign|infrastructure|attack-pattern|vulnerability|identity|location",
      "name": "名称（本文中の表記を優先し一貫させる）",
      "description": "短い説明（1〜2文）",
      "confidence": 0-100,
      "evidence": ["任意。短い引用（最大1つ、80文字以内）"]
    }
  ],
  "indicators": [
    {
      "indicator_type": "ip|domain|hash",
      "value": "本文に書かれている文字列そのまま",
      "context": "短い説明（1文）",
      "confidence": 0-100,
      "evidence": ["任意。短い引用（最大1つ、80文字以内）"]
    }
  ],
  "relationships": [
    {
      "source_name": "objects.nameと一致",
      "source_stix_type": "malware|tool|threat-actor|intrusion-set|campaign|infrastructure|attack-pattern|vulnerability|identity|location",
      "relationship_type": "uses|targets|delivers|compromises|hosts|communicates-with|attributed-to|related-to|indicates",
      "target_name": "objects.nameと一致",
      "target_stix_type": "malware|tool|threat-actor|intrusion-set|campaign|infrastructure|attack-pattern|vulnerability|identity|location",
      "confidence": 0-100,
      "evidence": ["任意。短い引用（最大1つ、80文字以内）"]
    }
  ],
  "notes": "任意。短く"
}

入力：
- title: __TITLE__
- url: __URL__

clean_text:
__CLEAN_TEXT__
