# OWL 推論コンテナ (Phase 4)

このディレクトリは **Phase 4 で実装する**。現時点では意図の記録のみ。

## 役割

OWL DL の整合性検査と分類(classification)を行う。Container Apps Jobs として
**非同期に**実行し、API のリクエスト経路には入れない。推論はレイテンシとメモリの
両方で読み取り経路と性質が違うため分離する
(`docs/adr/0005-reasoner-boundary.md`)。

## 検証との棲み分け

| 対象 | 手段 | 言語 | フェーズ |
| --- | --- | --- | --- |
| データ形状の検証 (SHACL) | pyshacl | Python | Phase 2 |
| OWL DL の整合性検査・分類 | ELK / HermiT + OWL API | Java | Phase 4 |

SHACL が純 Python で完結するため、Java への依存は Phase 4 まで発生しない。

## ライセンス上の注意

- **ELK は Apache-2.0**。既定の推論器はこちらを使う
- **HermiT は LGPL-3.0**。イメージに同梱せず、任意で有効化したときにビルド時取得と
  する。別プロセス(Jobs)として動かす構成が LGPL 上もっとも安全であり、
  `NOTICE` に明記する
- OWL API のライセンスも実装時に確認し `docs/third-party-licenses.md` を更新する
