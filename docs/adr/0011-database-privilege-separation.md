# ADR-0011: データベースの権限分離 — マイグレーションを実行時 ID から切り離す

- ステータス: 承認済み
- 日付: 2026-09-06

## コンテキスト

`infra/modules/postgres.bicep` は **API の実行時 ID（UAMI）を PostgreSQL の Entra 管理者として登録している**。Azure 実機で確認した状態（2026-09-05）:

```
Entra 管理者        : id-<suffix> (ServicePrincipal) = API の実行時 ID そのもの
POSTGRES_USER       : id-<suffix>
publicNetworkAccess : Enabled（VNet 統合なし）
ファイアウォール    : AllowAllAzureServicesAndResources (0.0.0.0) のみ
マイグレーション    : API の docker-entrypoint.sh で起動時に実行
```

この構成は Task 8 の実機検証で「追加の GRANT なしで起動時 DDL が通る」ため採用され、**動いてしまったために誰も困らず、最小権限化の欠落が発覚しなかった**（Task 8 レビューの差分外指摘）。

帰結は 2 つある。

1. **API が侵害されると `azure_pg_admin` 権限が奪われる。** サーバー全体に対して DROP DATABASE を含む任意の操作ができる
2. **監査証跡が攻撃者から保護されない。** アプリのロールがテーブルの所有者であるため `DROP` / `TRUNCATE` が可能で、`audit_events` を消せる。[ADR-0006](0006-ontology-versioning-and-audit.md) の中核価値である「誰が承認した定義に基づく答えかを説明できること」が、API を掌握した相手に対して成立しない

2 のほうが重い。オントロジー本体は Blob が正本なので復旧できるが、**監査の履歴は PostgreSQL にしかない**。

### 設計を詰める中で判明した制約

**(a) 管理者の資格情報が API コンテナから到達可能なら、最小権限は達成されない。**

Container Apps は 1 つのコンテナアプリに複数のユーザー割り当て ID を持たせられる。「起動時は管理者 ID、実行時はアプリ ID」という案を検討したが、**侵害されたコンテナは管理者 ID のトークンも取得できる**。見せかけの分離である。

したがって管理者の経路は **API コンテナの外** になければならない。

**(b) アプリのロールがテーブルを所有していると、DDL 権限の有無に関わらず削除できる。**

「アプリのロールに `public` スキーマの DDL だけを与える」という案（サーバー管理者ではなくなる）は 1 を緩和するが 2 を解決しない。所有者は常に `DROP` / `TRUNCATE` できる。**監査を守るには、テーブルの所有者とアプリのロールを分ける必要がある。**

その結果、**マイグレーションはアプリのロールでは実行できない**（所有者でないため）。実行主体を分けざるを得ない。

**(c) ACA Job は azd のイメージ更新の問題を再来させる。**

マイグレーション専用の Container Apps Job を置く案は「本番の形」だが、[ADR-0002 の補記](0002-triple-store-as-rebuildable-projection.md)が記録しているとおり、**azd deploy が更新するのはサービスとして定義されたコンテナのイメージだけ**である。init コンテナがこの問題で `azd up` 一発を壊し、廃止した経緯がある。Job も同じ轍を踏む危険が高い。

**(d) 運用者のマシンから PostgreSQL に届かない。**

ファイアウォールは `AllowAllAzureServicesAndResources`（`0.0.0.0`、= Azure 内のみ）だけである。運用者がマイグレーションを実行するには、自身の IP を許可する規則が必要になる。

## 決定

### 1. Entra 管理者を「デプロイ実行者」にする

UAMI を Entra 管理者から外し、**`azd up` を実行するユーザー**を管理者として登録する。API の実行時 ID はサーバー管理者ではなくなる。

### 2. テーブルの所有者を専用ロールにし、アプリのロールと分ける

ブートストラップで**ログインしないロール `ontology_owner`** を作り、これがテーブルを所有する。アプリのロール（UAMI 名）には**所有権を与えず DML のみ**を与える。

```sql
-- ログインしない所有者ロール。マイグレーションを実行する者に GRANT する。
CREATE ROLE ontology_owner NOLOGIN;
GRANT ontology_owner TO "<Entra 管理者>";

-- UAMI を PostgreSQL のロールとして登録する（pgaadauth 拡張）。
SELECT * FROM pgaadauth_create_principal('<UAMI 名>', false, false);

-- アプリには DML のみ。所有権も DDL も与えない。
GRANT USAGE ON SCHEMA public TO "<UAMI 名>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "<UAMI 名>";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "<UAMI 名>";
ALTER DEFAULT PRIVILEGES FOR ROLE ontology_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "<UAMI 名>";
```

**`audit_events` への `DELETE` は与えない。**監査は追記専用にする。

```sql
REVOKE DELETE ON audit_events FROM "<UAMI 名>";
```

`ontology_owner` を `NOLOGIN` にするのは、このロール自体では接続できないようにするためである。マイグレーションを実行する者が `SET ROLE ontology_owner` で一時的に所有者になる。

### 3. マイグレーションを API コンテナから外す

`packages/api/docker-entrypoint.sh` からマイグレーションの実行を削除する。API のロールには DDL が無いため、残すと起動時に必ず失敗する。

代わりに **`postdeploy` フックで運用者が実行する**。運用者は Entra 管理者であり、`ontology_owner` を持っている。

```
postdeploy:
  1. ブートストラップ SQL（初回のみ。冪等に書く）
  2. SET ROLE ontology_owner; alembic upgrade head
  3. サンプルの投入（既存の P1-10 の処理）
```

`packages/api/src/ontology_api/migrate.py` は残す。`just migrate`（ローカル開発）と `postdeploy` の両方から使う。**アドバイザリロックも残す** — 運用者が複数人で同時に実行する可能性は残るため。

### 4. 運用者の IP を許可するファイアウォール規則を追加する

`postdeploy` が PostgreSQL に届くようにする。`azd up` の実行元 IP を検出して規則を作る。

**規則は `postdeploy` の中で作り、処理の最後に削除する。**常時開けたままにしない。

### 5. API 起動時にテーブルが無い状態を許容する

マイグレーションが `postdeploy`（deploy の後）になるため、API コンテナは**テーブルが無い状態で起動する**。

- `/healthz` は DB を触らないため、Startup / Liveness プローブは通る
- DB を触るエンドポイントは `postdeploy` 完了までエラーを返す
- これは一時的な状態であり、`postdeploy` が失敗すれば `azd up` 全体が失敗する（`continueOnError: false`）

### 6. 本番プロファイルでの完全な分離は Phase 4 に送る

`publicNetworkAccess: Disabled` + VNet 統合の `production` プロファイル（[`docs/roadmap.md`](../roadmap.md) Phase 4）では、**運用者のマシンから届かないため決定 3・4 が成立しない**。そこでは VNet 内で実行される経路（Container Apps Job、あるいはデプロイパイプラインの実行環境）が必要になる。

Phase 4 の `production` プロファイル設計に含める。**Phase 1 で先に作らない** — 決定 (c) のとおり azd のイメージ更新の問題を再来させる危険があり、`production` の設計と併せて決めるべきである。

## 根拠

### 監査の保護が最小権限の主目的である

「サーバー管理者を外す」だけでは足りないことが、この ADR の中心的な発見である。所有者は常に `DROP` / `TRUNCATE` できるため、**アプリのロールに DDL を与えない**だけでは監査を守れない。所有権を分けるのが必須条件である。

そして所有権を分けると、マイグレーションはアプリのロールでは実行できない。**「監査を守る」という要求が、実行主体の分離を強制する。**

### 運用者の経路を選ぶのは、既知の失敗を繰り返さないため

ACA Job は構造としては正しいが、[ADR-0002 の補記](0002-triple-store-as-rebuildable-projection.md)が記録した「azd deploy はサービス以外のイメージを更新しない」問題に当たる可能性が高い。init コンテナで一度踏んだ轍である。

運用者の経路は Phase 1 の `minimal` プロファイル（`publicNetworkAccess: Enabled`）では成立し、**新しい Azure リソースを増やさない**。`production` で成立しないことは決定 6 として明記する。

### ファイアウォール規則を開けたままにしない

`postdeploy` の中で作って消す。開けたままにすると、運用者の IP が変わったときに古い規則が残り、意図しない範囲を許可し続ける。

## 検討した代替案

### API のロールに `public` スキーマの DDL を与える（サーバー管理者は外す）

**却下。** 1（サーバー全体の権限）は緩和するが、2（監査の保護）を解決しない。所有者は常に `DROP` / `TRUNCATE` できる。実装は最も安いが、この ADR の主目的を満たさない。

### 同一コンテナアプリに管理者 ID とアプリ ID の 2 つを持たせる

**却下。** 侵害されたコンテナは管理者 ID のトークンも取得できる。**見せかけの分離**であり、最小権限の主張が虚偽になる。

### `SET ROLE` で実行時に権限を落とす

**却下。** 管理者として接続してマイグレーション後にアプリ用ロールへ切り替える案。`RESET ROLE` で戻せるため、SQL を実行できる攻撃者には境界にならない。偶発的な DDL は防げるが、脅威モデルに対して弱い。

### Container Apps Job でマイグレーションを実行する（Phase 1 で）

**却下（Phase 4 へ）。** 構造としては最も正しく、CI/CD でも動く。しかし決定 (c) のとおり azd のイメージ更新の問題を再来させる危険があり、`production` プロファイル（VNet 統合）の設計と併せて決めるべきである。Phase 1 で先に作ると、`minimal` と `production` の両方に対応する Job の設計を今決めることになる。

### マイグレーションを API の起動時に残し、失敗を許容する

**却下。** `docker-entrypoint.sh` は `set -eu` の下でマイグレーションを実行し、失敗すれば uvicorn を起動しない（Task 8 の設計。スキーマ未適用の API を起動させないため）。失敗を許容するように変えると、**スキーマ未適用の API が起動する**という Task 8 が防いだ状態に戻る。

## 結果（トレードオフ・影響）

### 受け入れるコスト

- **デプロイに運用者のマシンが必要になる。** `postdeploy` が PostgreSQL へ接続するため、Python 環境（`uv` + `alembic`）と一時的なファイアウォール規則が要る。**無人の CI/CD では動かない** — これは Phase 4 の `production` プロファイルで解決する
- **API 起動時にテーブルが無い窓がある。** `postdeploy` 完了までの一時的な状態
- **所有者ロールの管理が増える。** 複数の運用者がマイグレーションを実行するなら、それぞれに `ontology_owner` を GRANT する必要がある
- **ブートストラップ SQL が IaC の外にある。** Bicep だけでは完結しない（`pgaadauth_create_principal` の実行が必要）。冪等に書き、`postdeploy` が毎回実行する

### 得られるもの

- API が侵害されてもサーバー全体の権限は奪われない
- **`audit_events` を削除できない。**監査が攻撃者に対しても成立する（ADR-0006 の中核価値が守られる）
- テーブルの所有権とアプリの権限が分離され、偶発的な DDL も防げる

### 影響を受ける他の決定

- **Task 8 の設計**（マイグレーションを API の起動時に実行し、アドバイザリロックで直列化する）: 実行場所が `postdeploy` に移る。`migrate.py` とアドバイザリロックは残す（`just migrate` と `postdeploy` から使う）
- **[ADR-0006](0006-ontology-versioning-and-audit.md)**: 監査証跡が攻撃者に対しても保護されるようになる。中核価値の実効性が上がる
- **[`docs/roadmap.md`](../roadmap.md) Phase 4**: `production` プロファイルに「VNet 内で実行されるマイグレーション経路」を追加する

### 未解決のまま残す問い

- `production` プロファイルでのマイグレーション実行経路の具体形（Container Apps Job か、パイプラインの実行環境か）
- 複数運用者での `ontology_owner` の付与手順（今は手動）
- `audit_events` の `UPDATE` も禁止すべきか（追記専用を厳密にするなら禁止だが、`projected_at` 相当の更新が将来必要になる可能性がある）
