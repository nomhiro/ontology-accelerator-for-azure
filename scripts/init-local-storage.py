"""ローカル開発用の Azurite に正本 Blob のコンテナを作る。

本番では `infra/modules/shared.bicep` の `ontologyContainer` リソースが
コンテナを作るが、ローカルの Azurite には同等の仕組みが無い。コンテナが
無いままだと `OntologyBlobStore.list_versions` / `put_version` が
`BlobStoreError: ... ContainerNotFound` になり、**publish と
DELETE /namespaces/{name} が動かない**(名前空間の作成と SPARQL 参照だけは
Blob を触らないので動いてしまうため、原因が分かりにくい)。

`just up` から呼ばれる。冪等なので何度実行してもよい。
Azurite の起動直後は接続を受け付けないことがあるため、少し待ちながら再試行する。
"""

from __future__ import annotations

import os
import sys
import time

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

# docker-compose.yml の azurite サービスと合わせる。別プロジェクトとポートが
# 衝突する場合は AZURITE_PORT で変更できる(compose 側も同じ変数を見る)。
_PORT = os.environ.get("AZURITE_PORT", "10000")

# Azurite の既定アカウント。**これは秘密ではない** — Azurite が公開している
# 固定の開発用アカウントとキーであり、公式ドキュメントに記載されている。
_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    f"BlobEndpoint=http://localhost:{_PORT}/devstoreaccount1;"
)

_CONTAINER = os.environ.get("ONTOLOGY_BLOB_CONTAINER", "ontologies")
_ATTEMPTS = 20
_INTERVAL_SECONDS = 1.0


def main() -> int:
    """コンテナを作る。既にあれば何もしない。"""
    last_error: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with BlobServiceClient.from_connection_string(_CONNECTION_STRING) as service:
                service.create_container(_CONTAINER)
        except ResourceExistsError:
            print(f"init-local-storage: コンテナ '{_CONTAINER}' は既にあります")
            return 0
        except Exception as exc:  # 起動待ちのため例外の種類を問わず再試行する
            last_error = exc
            if attempt < _ATTEMPTS:
                time.sleep(_INTERVAL_SECONDS)
                continue
        else:
            print(f"init-local-storage: コンテナ '{_CONTAINER}' を作成しました")
            return 0

    print(
        f"init-local-storage: コンテナ '{_CONTAINER}' を作成できませんでした "
        f"({_ATTEMPTS} 回試行): {last_error}",
        file=sys.stderr,
    )
    print(
        "  Azurite が起動しているか確認してください "
        "(`docker compose ps azurite`)。ポートを変えている場合は "
        "AZURITE_PORT を合わせてください。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
