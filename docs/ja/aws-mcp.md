# AWS MCP Server

> 英語版が正本です: [docs/aws-mcp.md](../aws-mcp.md)

kensa は、AWS が管理する公開エンドポイント(AWS MCP Server)を、**認証なし・ドキュメント検索専用**で既定に同梱している(`aws-docs`)。AWS の認証情報を使って API を呼べる構成は、**任意**の部品として別に用意している(`aws-mcp`)。実環境の読み取りは、どちらの場合も `scan-aws` が行う。`kensa*.py` は MCP を呼ばない。MCP を使わなくても、スキャナーと面談だけで動く。

## 既定(同梱): `aws-docs` — ドキュメント検索専用、認証なし

- **設定**: `.mcp.json`(Claude Code 用。`type: http`)と `mcp.json`(Agent Plugins 用。`type: streamable-http`)。どちらも `https://aws-mcp.us-east-1.api.aws/mcp` へのリモート接続だけで、資格情報・プロファイル・`uvx` を使わない。プラグインを有効にすると接続される。
- **使うツール**: `aws___search_documentation`(検索)と `aws___read_documentation`(ページの取得)で、個別項目の公式ドキュメントを引く(`SKILL.md` の Phase 1)。補助として `aws___list_regions`、`aws___get_regional_availability`、`aws___retrieve_skill`。
- **使わないツール**: `aws___run_script`(Python を sandbox で実行する)と `aws___get_presigned_url`。どちらも `readOnlyHint=false` で、公式の説明では API 実行に IAM 認証が要る。この構成は認証情報を渡さないので、利用者の AWS アカウントには触れない。`SKILL.md` でも使わないよう指示している。
- **確認した範囲**(2026-11-02、認証なしで接続): `initialize` と `tools/list` が通り、8 ツールが見えた。`readOnlyHint` は、`aws___search_documentation`・`aws___read_documentation`・`aws___get_tasks`・`aws___list_regions`・`aws___get_regional_availability`・`aws___retrieve_skill` が `true`、`aws___run_script`・`aws___get_presigned_url` が `false`。**未確認**: 認証なしで `aws___run_script` を呼んだ時の挙動(呼んでいない)、検索・取得の品質と速度、AWS 側の仕様変更(エンドポイントとツールは変わりうる)。
- **外に出るもの**: 検索語と取得するページの URL が、AWS のエンドポイントに送られる。スキャン結果やアカウント情報は送らない(`SKILL.md` で、アカウント ID・リソース名・社内の情報を検索語に含めないよう指示している)。ツールの定義(8 件)が、会話の文脈を少し使う。
- **無効にする**: Claude Code では `/mcp` から `aws-docs` を無効にするか、`.mcp.json` を削除する。

## 任意: `aws-mcp` — IAM 認証つき(API の実行もできる)

`aws___run_script` で、読み取りの API 呼び出しを追加で実行したい場合の構成(`SKILL.md` の Phase 2。`aws` CLI でも代替できる)。**既定では同梱しない**: 利用者の IAM 権限で Python を実行できるので、管理者権限のプロファイルでは書き込みもできてしまう。`uvx` と AWS 認証が無い環境では起動エラーの元にもなる。

使いたい時は `optional/aws-mcp.json` の `mcpServers` を、利用するエージェントの MCP 設定に追加する(SigV4 方式。`uvx` と AWS CLI の認証が必要)。プロキシは、AWS の README が `uvx` 用に勧める `mcp-proxy-for-aws-cli`(依存関係を固定した配布)で、版を固定している。**同じツール名が重複するので、追加する時は `aws-docs` を無効にする。**

- **読み取り専用の絞り込み**: プロキシの `--read-only` は、`readOnlyHint=true` が付いていないツールを隠す**ツール単位**の絞り込みで、`aws___run_script` の中の API 呼び出しを読み取りだけに絞るものではない。IAM の `aws-mcp:CallReadOnlyTool` などは、公式が「効果がない」としている。操作の種類を絞れるのは、下流の AWS サービスの権限(IAM)だけ。
- **接続の作法**: 読み取り専用のプロファイルを、`--profile` に**1つだけ**指定する(複数指定すると、エージェントが呼び出しごとに切り替えられる)。[権限(IAM)](aws-iam.md) の Deny を付ける。MCP 経由の呼び出しにだけ Deny を効かせたい場合は、条件キー `aws:ViaAWSMCPService` が使える(公式のトラブルシューティングに、`Action: *` を拒否する例がある)。デフォルトリージョンは `--metadata AWS_REGION=ap-northeast-1` で指定できる(未指定なら `--region` の値が入る)。
- **`--read-only` を付ける場合**: `args` に足す。例: `["mcp-proxy-for-aws-cli@1.7.0", "https://aws-mcp.us-east-1.api.aws/mcp", "--profile", "readonly", "--read-only"]`。上の確認のとおり、`aws___run_script` と `aws___get_presigned_url` は `readOnlyHint=false` なので隠れ、ドキュメント検索は残るはず。**プロキシが実際にそう動くかは、接続後にツール一覧で確かめる**(未確認)。`aws___run_script` が隠れた場合、Phase 2 の追加の読み取りは `aws` CLI で行う。
- 旧ツール `aws___call_aws` は、公式の現行ツール一覧に無い。
