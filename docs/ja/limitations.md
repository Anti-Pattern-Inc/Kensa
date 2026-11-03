# 既知の制約

> 英語版が正本です: [docs/limitations.md](../limitations.md)

- 質問カードの推奨は「4名・GitHub・Slack・AWS(コンテナ中心)」を想定した例。別の体制では `.kensa/cards.local.json` で `recommend` / `alternatives` を上書きする(`covers` は `routing.json` の `card` で決まる)。
- 経路の分類(AWS/リポジトリ/面談)は設計上の判断。実際の環境で確認できない場合は面談にフォールバックする。
- Slack・Drive などを検索して証拠にする機能は持たない。そこにある文書や定例の有無は、質問カードの回答に `--evidence URL` を付けるか、`apply` の `evidence` に書く。判定だけをまとめて入れるなら CSV の取り込み(`seed --from-csv --import-verdicts`)も使えるが、確認元は「取込み(要再確認)」・確信度 L になり、根拠は固定の文言になる。
- 「下書き」の証拠は、**存在・活動**しか示さない。ポストモーテムが書かれていても質が高いとは限らず、チャンネルが賑わっていてもエスカレーションが機能しているとは限らない。確定は人が行う。
- 文化・優先順位・経営の関与(標準レンズでは面談のみ 41 項目)は、証拠では埋まらない。
- `ReadOnly` の範囲でも、Organizations/SCP・請求・Cost Explorer は管理アカウントでしか見えないことがある(OpenItems に残る)。
- 推定 HRI は参考値。未判定の項目や仮置き(要確認)を含む質問は「判定未確定」として数え、HRI に入れない。FTR の判定は `push --apply` 後に WA Tool から読み戻した値(`precheck`)を使う。
- 独自のピラーを持つレンズの、独自ピラーの項目は取得できない(0件になり、エラーも出ない)。`seed --from-wa-tool` が質問を取得するのは、標準の6ピラー(`kensa_wa.PILLAR_IDS`)だけ。標準のピラーを使うレンズは取得できるはずだが、実際の W-A Tool では未確認。
- 回答済みのカードに、レンズ更新で新しい項目が増えると、`cards next` がそのカードをもう一度出す。回答すると、そのカードの全項目が同じ判定で上書きされる(前回の `--override` は引き継がれない)。`choice_ids` を記録する前の回答(旧データ)は、従来どおりカード単位で回答済みと扱う。
- 「証拠が古い」は**記録した時刻**(`evidence_at`)からの経過日数で、その事実がいつから成り立っているかではない。`diff` が比べるのは、`snapshot` を取った時点との差だけ(自動では取らない)。
- ActionPlan の期限・実施状態を xlsx 上で編集しても取り込まれない(再 export で戻る)。更新は `apply` の `due` / `action_status` で行う。xlsx/Sheets の編集を取り込み直す機能は未実装。
- 公式スキル(`aws-well-architected-review`)の JSON 出力の取り込みは未実装。PR #464(https://github.com/aws/agent-toolkit-for-aws)がマージされ、出力のスキーマが決まってから作る。それまでは、公式の判定語彙を `status-mapping.md` に従って `apply` に写す。
- AWS Well-Architected Agent の提案を取り込む対応表は無い(`status-mapping.md` は公式スキル用)。Agent の出力は提案の形式で、ベストプラクティスごとの判定ではないため、使う場合は証拠として手で `apply` に写す。
- IAM 認証つきの AWS MCP Server(任意)の `aws___run_script` で実環境を読む場合、読み取り専用にできるのは IAM だけ。プロキシの `--read-only` はツール単位の絞り込みで、`readOnlyHint` の付き方は認証なしの `tools/list` で確認した([docs/aws-mcp.md](aws-mcp.md))が、プロキシが実際に隠すかは未確認。スキャナー(`scan-aws`)のように、書き込みをコードで遮断する仕組みは MCP 側には無い。同梱の `aws-docs` は認証なしで、利用者のアカウントには触れない。
- Sheets を正本にする運用(Sheets への直接書き込みと読み戻し)は行わない。Drive に置く場合は xlsx のアップロードまで。
- `mode iac` の判定は、IaC の**宣言**を読んだだけ。手作業の変更・別のスタック・未 deploy の変更は見えない。確信度は M まで、『IaC(実環境未確認)』として OpenItems に残る。FTR などの提出の前に、実環境で確認する。
- `scan-iac` はファイルを文字列として読む近似。CDK のソース、Pulumi、Terraform の `count` / `for_each` / 変数による展開、モジュールの複数回の呼び出し、`terraform plan` の出力は扱わない。CloudFormation の `!Ref` やパラメータで渡す値は「宣言なし」として数える(確認できなかった側に倒れる)。フラグは「その設定を宣言しているリソースの数」で、別リソースで宣言される設定(S3 の暗号化など)を件数に足すだけで、どのリソースの設定かは対応づけない。ポートの判定は近傍の行を見る近似。Aurora はクラスタにだけ暗号化などを指定するので、`rds` の件数との単純な比較は合わない。
- モードは `both` と `iac` の2つだけ。実環境だけを読む専用のモードは無い(リポジトリを読まないなら `plan --skip repo`)。

## 安全性に関する制約

- **スキャナーのガード(`ReadOnlyClient`)は許可リスト方式**で、list/describe/get 系だけを通し、値・本文・認証情報を返す既知の読み取り(`DENY_RE`)を遮断する。スキャナーが呼ばない読み取り API をすべて遮断するものではない。境界は IAM。
- **IAM の Deny** は `iam/scan-role.yaml` の一覧(`iam-policy --kind scan-deny`)で、`ReadOnlyAccess` / `SecurityAudit` が含むアクションへの追加の拒否で、**網羅ではない**(例: `ReadOnlyAccess` は `codecommit:GitPull` や `codebuild:BatchGet*` を許可する。一覧に足したもの以外にも、本文を返すアクションが残りうる。詳細は [権限(IAM)](aws-iam.md))。`apigateway:GET` は全ての GET を含むので Deny できず、API キーの値はコードの遮断(`get_api_keys`)だけで守っている。実アカウントで拒否が効くことを確認したのは `ssm:GetParameter` だけ。これらのアクションを `ReadOnlyAccess` / `SecurityAudit` が現行でどこまで含むかは確認していない(含まれなければ Deny は無害)。
- **Lambda(`lambda:ListFunctions`)と ECS(`ecs:DescribeTaskDefinition`)の取得結果には、環境変数が含まれうる。** 保存するのは件数・フラグだけだが、スキャナーのメモリには届く。値を読ませたくない場合は、IAM でそれらの API を拒否する(スキャナーのチェックは減る)。
- **`push --apply` は既存の回答を上書きし、事前のスナップショットを取らない。** `--milestone` は記録に成功した後に作る。回答済みのワークロードでは、先に W-A Tool でマイルストーンを保存する。
- **`--apply` の対象アカウントを固定する仕組みは無い。** W-A Tool 側は `aws.account_ids` と照合しない。表示されたアカウントをユーザーが確認する。`--confirm-account` のような確認は未実装。
- **W-A Tool 用ロールの `UpdateAnswer` / `CreateMilestone` は `Resource: "*"`(全ワークロード)。** ワークロードの ARN に絞るパラメータは未実装。
- **信頼ポリシー**は、アカウントの root を信頼し、`aws:PrincipalArn` のパターンで絞る。パターンを `arn:aws:iam::<account>:*` にするとアカウント全体に開く(`AllowedPattern` は許す)。MFA の条件は無い。`aws-cn` / `aws-us-gov` のパーティションには未対応(`arn:aws:` のみ)。`GLOBAL_REGION`(`us-east-1`)も固定。
- **伏せ字(`redact()`)の対象は、証跡・根拠・備考など**(`apply` とカードの回答)。`na_reason`、`business answer --text`、`context set`、スキャン結果のファイルには掛けない。Bearer トークン、接続文字列、40 文字のキーだけの文字列は検出しない。`--mask-account-ids` は既定でオフ。
- 状態ファイルのパーミッションは既定の umask のまま(0600 にしていない)。`scan-repo` はファイルのシンボリックリンクを辿る。
- **成果物の言語は `en`(既定)と `ja` だけ。** CLI のメッセージ・スキル・参考資料は英語で、エージェントが利用者の言語で伝える。他の言語の成果物は英語で出力され、エージェントが提示時に訳す。日本語の質問カード(`assets/cards.ja.json`)は英語版(`assets/cards.json`)とは別に保守しており、内容がずれることがある。CSV・xlsx の取り込みは、英語と日本語のどちらの列名(`Verdict` / `判定`、`Org shape / tools` / `組織の形、ツール`)も受け付ける。
- `gh api` は `--method GET` を明示していない(`gh api` の既定は GET)。
- **`gh skill install` ではスキルだけが入る。** `docs/`・`iam/`・`optional/` は入らない(IAM テンプレートはリポジトリから取得する)。ドキュメント検索の MCP(`aws-docs`)は Claude Code 向けに同梱されていて、この方法では入らない。使いたければ `mcp.json` の `mcpServers` を自分で追加する([AWS MCP Server](aws-mcp.md))。未接続の場合は `improvement_url` を直接開く手順になる。
- Kiro Power としての動作は、実機では未確認([検証状況](verification.md))。
- `precheck` が確認するのは、ワークロード**名**にソリューション名が含まれるか(`--solution-name` を指定した場合のみ)。AWS はワークロードの説明にソリューション名を書く形も認めているが、`precheck` はそちらを確認しない。
