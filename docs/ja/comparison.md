# 既存ツールとの比較(2026-11-01 時点の調査メモ)

> 英語版が正本です: [docs/comparison.md](../comparison.md)

> **非公式の調査メモです。** AWS のプレビュー機能や公式スキルは変わりうるため、数値や仕様は古くなっている可能性があります。使う前に公式ドキュメントで確認してください。AWS とは提携しておらず、ここでの比較は AWS による評価ではありません。


| 既存のもの | できること | 今回の用途に足りない点 |
|---|---|---|
| `aws-well-architected-review` スキル(Agent Toolkit for AWS の aws-core プラグイン) | IaC・コード・CI を読んでピラー別に所見を出す(Implemented / Partially Implemented / Not Implemented / Not Applicable / Cannot Determine)。`main` の SKILL.md を 2026-11-01 に確認: 5段階の判定、すべての所見に証拠が必要、既定は非対話、結果はチャットへのインライン Markdown。https://github.com/aws/agent-toolkit-for-aws の PR #464(各 BP ページの読み込み・中断再開・JSON 出力)は未マージ(2026-11-02 時点でも open) | AWS API を呼ばず実環境を見ない。組織・プロセス項目は「判断不能」のまま(解説記事では307項目中62項目)。実行ごとに結果が揺れる。W-A Tool に回答を記録しない |
| AWS Well-Architected Agent(プレビュー。2026-10-01 発表、2026-11-01 に公式ドキュメントで確認) | 読み取り専用ロールで稼働中のアカウントを週1回スキャンし、ビジネスゴールとアプリ情報で提案を順位付けする。修正用の IaC・CLI 手順も出す。IaC の Architecture review は手動で実行(Framework レンズのみ) | 対象は4ピラーのみ(Operational Excellence と Sustainability なし)。監視する全アカウントで **Business+ 以上**のサポートが前提(通常の Business と Developer は対象外)。プロファイルは米国3リージョンにしか作れず、1回の生成は30件まで。出力は提案で、W-A の質問への回答ではない。組織・プロセスの質問や面談の仕組みはなく、W-A Tool への回答記録も、読んだドキュメントには記載がない |
| AWS MCP Server(マネージド) | ドキュメント検索(`aws___search_documentation`・`aws___read_documentation`)と、Python を sandbox で実行する API 呼び出し(`aws___run_script`) | W-A のレビュー手順そのものは持たない。実環境の読み取りはスキャナー(書き込みをコードで遮断・結果が再現する)が担う。認証なしで使えるドキュメント検索だけを既定で同梱し(`aws-docs`)、API を実行できる構成は**任意の部品**にしている([docs/aws-mcp.md](aws-mcp.md)) |
| `awslabs.well-architected-security-mcp-server` | Security ピラーの確認 | Security のみ(任意で併用可) |
| コミュニティ製 `wellarchitected-mcp-server` | WA Tool の読み取り中心 | 非公式。依存しない |
| W-A Tool API(`UpdateAnswer` など) | 回答の記録が可能 | これを使って回答を記録する既存ツールは見当たらない |

**「skill はこっちで用意した方がよいか」→ はい。ただし AWS のものを作り直さず、呼び出す前提のオーケストレーション用スキルを用意する。**
AWS のスキル/MCP は「IaC を読む」「ドキュメントを引く」部品として優秀で、Agent は稼働中の環境を読む。ただし、(1) 実環境と IaC の両方を見る、(2) ビジネス要件と組織項目を面談で埋める、(3) 6ピラー全項目を根拠付きで埋めて W-A Tool に記録する、という3つを合わせた流れは、調べた範囲では見当たらない((1) だけなら Agent が持つが、4ピラーでプレビュー)。この流れを持つのが本プラグイン。公式スキルの出力は `apply` で取り込める(`kensa-review/references/status-mapping.md`)。Agent の提案を取り込む対応表はまだない。

## 出典

上の事実は、次の公開ページで確認した(URL は 2026-11-02 に開けることを再確認した。内容は変わりうるので、各ページを見ること)。

- `aws-well-architected-review` スキル: <https://github.com/aws/agent-toolkit-for-aws/tree/main/skills/core-skills/aws-well-architected-review>
- PR #464(2026-11-02 時点で open): <https://github.com/aws/agent-toolkit-for-aws/pull/464>
- AWS Well-Architected Agent(プレビュー): <https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent.html>
- AWS Well-Architected Agent の上限と制限: <https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-quotas.html>
- AWS MCP Server: <https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html>
- `awslabs.well-architected-security-mcp-server`: <https://github.com/awslabs/mcp/tree/main/src/well-architected-security-mcp-server>

URL を記録していないため載せていないもの: 「307項目中62項目」の出典の解説記事、コミュニティ製 `wellarchitected-mcp-server`。
