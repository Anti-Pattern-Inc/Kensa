# kensa — AWS Well-Architected レビュー支援プラグイン

> 英語版が正本です: [README.md](README.md)

## 超クイックスタート

使っているエージェント(Claude Code、Kiro、Codex、Cursor など)に、次をそのまま貼ってください。

```text
https://github.com/Anti-Pattern-Inc/Kensa をつかって W-A 診断をしてください。
```

エージェントがこのリポジトリを読み、プラグインを入れて、レビューを始めます。どこまで自分でやるかはエージェントによって違います。入らなかった場合や、インストールを自分で制御したい場合は、[始める(Claude Code)](#始めるclaude-code) に従ってください。

kensa は、AWS Well-Architected(W-A)レビューを、**実環境(読み取り専用)・IaC/GitHub(設定と活動実績)・ビジネス要件の質問**から埋める、*非公式*のコミュニティ製プラグイン(Claude Code / Agent Plugins 形式)です。最新のレンズ(別のレンズも可)を取得し、証拠で決まらないことだけを質問し、全項目を埋めた xlsx を出力し、回答を AWS Well-Architected Tool(W-A Tool)に記録し、AWS Foundational Technical Review(FTR)の事前チェックまで行います。

> バージョン 0.3.1。オフラインで動く部分は自動テスト済み。AWS スキャナーは 1 つの開発用アカウントで、W-A Tool への書き込みはテスト用ワークロード 1 つで実機確認した。確認した範囲と未確認の範囲は [検証状況](docs/ja/verification.md) にある。

## 始める(Claude Code)

```text
/plugin marketplace add Anti-Pattern-Inc/Kensa
/plugin install kensa@anti-pattern-plugins
```

要件: Python 3.11 以上(`uv` を勧めます。スクリプトの依存を自動で入れます)。そのあと:

| 言う / 実行する | 起きること |
|---|---|
| `/kensa:demo` | **合成データ**のチェックリストで全体の流れを試す。AWS へのアクセスは不要で、後片付けも要らない |
| `/kensa:start`(または「Well-Architected レビューをして」) | 環境を確認してから、レビューを始める: サービス概要・ビジネス要件・スキャン・決定カード・xlsx |
| `/kensa:resume`(次の回は `re-review` を付ける) | 止めたところから続ける。または前回のレビューとの差分を見る |
| `/kensa:record` | 終えたレビューを W-A Tool に記録し、FTR を事前チェックする(まず dry-run) |

Claude Code 以外を使っている、またはインストールできたか分からない場合は、下のプロンプトをエージェントに貼ってください([他のインストール方法](#インストール))。

<details>
<summary>エージェントに貼るプロンプト(Claude Code、Kiro、Codex、Cursor など)</summary>

```text
GitHub のリポジトリ https://github.com/Anti-Pattern-Inc/Kensa にある、AWS Well-Architected レビュー支援プラグイン「kensa」(非公式)を、あなた(このエージェント)に入れてください。

1. あなたが Claude Code / Kiro / それ以外のどれかを判断し、下の対応する方法を選んでください。コマンドを実行する前に、内容を私に見せて確認を取ってください。
   - Claude Code: `claude plugin marketplace add Anti-Pattern-Inc/Kensa` のあと、`claude plugin install kensa@anti-pattern-plugins`
   - Kiro: IDE の操作が要るので、私がやります。Powers パネル → Add Custom Power → Import power from GitHub に、上の URL を入れる手順を案内してください。
   - それ以外(Codex、Cursor、Gemini CLI など): GitHub CLI 2.90 以降で `gh skill install Anti-Pattern-Inc/Kensa --all --agent <あなたのエージェント名>`(スキルだけが入ります。MCP 設定と IAM テンプレートは入らないので、必要なら私に伝えてください)
2. 入れたあと、スキル `kensa-review` と `kensa-partner-record` が使える状態か確認してください。
3. インストール以外はしないでください。AWS には接続せず、認証情報も求めないでください。
4. 最後に、使い始め方(最初に何と頼めばよいか)を 3 行で教えてください。
```

</details>

初めてなら、[docs/getting-started.md](docs/ja/getting-started.md) が最初の 1 回を順に説明しています(環境チェック・デモ・AWS へのアクセスをどこまで渡すか・許可プロンプトを減らす方法)。

## レビューの進み方

1. サービスの概要を 1 回話し、**ビジネス要件の質問(最大 9 問)**に答えます(各問に推奨の選択肢があります)。
2. kensa が最新のレンズを取得して全項目を 確定 / 下書き / 面談のみ に振り分け、リポジトリと(許可すれば)AWS アカウントを**読み取り専用**でスキャンします。
3. 証拠で決まらなかった項目について、**最大 30 枚の決定カード**に答えます。各カードは ○/△/✗ の選択で、先頭に検出済みの証拠、そして推奨(手順・工数・優先度・担当)が付きます。
4. xlsx が出来上がります([出力](#出力xlsx))。誰も確認していない項目は `✗ / 仮置き(要確認)` のままで、確認済みの項目とは別に数えます。

外部の担当者による最初の試用(Kiro)では、初回のレビュー全体で、エージェントの作業だけで約 28 分かかりました(これに回答の時間が加わります)。自分で調整できるのは質問の数です(知っているものに答え、残りは仮置きにできます。`cards next --pillar` でカードを一部のピラーに絞ることもできます)。どこで止めても、`/kensa:resume`(Kiro では `/kensa-resume`)で再開できます。Python・uv・AWS CLI・読み取り専用ロールは事前に準備してください: [事前準備チェックリスト](docs/ja/getting-started.md#0-当日までに-事前準備チェックリスト)。

## AWS へのアクセスレベルを選ぶ

| レベル | 持っているもの | kensa にできること |
|---|---|---|
| **Lv0** | AWS へのアクセスなし | 面談とリポジトリ・IaC の証拠(`mode iac`)。チェックリストは取り込んだもの(`seed --from-csv`)から |
| **Lv1** | 読み取り専用のプロファイル | + 実環境のスキャン(`scan-aws`、37 チェック) |
| **Lv2** | + W-A Tool 用のプロファイル | + 最新のレンズの取得、回答の記録(`push`)、FTR 事前チェック |

詳細と準備: [docs/getting-started.md](docs/ja/getting-started.md#3-aws-へのアクセスをどこまで渡すか選ぶ)。

## 最初に知っておくこと

- **非公式のプロジェクト**です。AWS による提携・推奨・後援はありません。Amazon Web Services、AWS は Amazon.com, Inc. またはその関連会社の商標です。「Well-Architected」などの AWS のサービス・プログラム名も、各権利者に帰属します。
- **AWS の文章を同梱していません。** 質問文・ベストプラクティス文は、実行時に利用者自身の W-A Tool から取得します(`seed --from-wa-tool`)。`tests/fixtures/` は、質問タイトルやベストプラクティス名も含めて合成データです(`QuestionId`・`ChoiceId` は経路表のキーなので、実際の識別子を使っています)。`assets/routing.json` の経路分類は本プロジェクトの設計判断で、AWS 公式の分類ではありません(`QuestionId`・`ChoiceId` は経路表のキーとして、AWS の識別子をそのまま使っています)。
- **実行すると AWS に触れます。** スキャンは読み取りのみですが、`scan-aws` は Cost Explorer の API を数回呼びます(AWS の料金は 1 リクエストあたり 0.01 USD。ページごとに課金)。`push --apply` と `workload-create --apply` は W-A Tool に書き込みます。
- **ドキュメント検索の MCP(`aws-docs`)を同梱しており、Claude Code ではプラグインを有効にすると起動します。** AWS が管理する公開エンドポイントへの、認証なし・ドキュメント検索専用の接続で、資格情報は使いません。検索語とページの URL が、インターネット経由で AWS に送られます(スキャン結果は送りません)。`/mcp` から無効にできます。[docs/aws-mcp.md](docs/ja/aws-mcp.md) を参照。
- **自分のアカウントで、読み取り専用ロールで実行する。** kensa は各自の資格情報で動き、私たちが何かを集めることはありません。エージェントが読んだもの(スキャン結果を含む)は、エージェントのモデルの提供元に送られます。AWS の外に出したくない場合は、Kiro か Amazon Bedrock 上の Claude Code を使ってください(エージェントのデータの設定も確認してください)。
- **まずテスト用のアカウントとワークロードで。** 管理者権限のプロファイルは使わないでください(エージェントはシェルを持つため、プロファイルの資格情報は他の `aws` コマンドにも使われうる)。読み取り専用ロールのテンプレートは [`iam/scan-role.yaml`](iam/scan-role.yaml)([解説](docs/ja/aws-iam.md))。
- 本ソフトウェアは現状有姿で提供されます(Apache-2.0)。IAM テンプレートは第三者の監査を受けていません。

## 言語

英語が正本の言語で、スキル・参考資料・質問カード・CLI のメッセージは英語で書かれています。

- **エージェントはあなたの言語で返答します。** 日本語で話しかければ日本語で答え、質問カードの文面も、その場で日本語に訳して出します(選択肢のキー・ID・コマンド・ファイル名は訳しません)。CLI のメッセージは英語で、エージェントが日本語で伝えます。
- **成果物(xlsx / csv / json の見出しや判定ラベル、質問カードの文面)は `en`(既定)と `ja` で出せます。** 日本語で出すには、`--lang ja`、`kensa.py context set language=ja`、環境変数 `KENSA_LANG=ja` のいずれかを使います(優先順位は `--lang` > `KENSA_LANG` > `context.json` の `language` > `en`)。
- **それ以外の言語**では、成果物は英語で出力され、エージェントが提示するときに訳します。
- 日本語版の質問カード(`skills/kensa-review/assets/cards.ja.json`)は、英語版とは別に保守しています。内容がずれることがあります([既知の制約](docs/ja/limitations.md))。

## 特徴

詳細は [設計メモ](docs/ja/design.md)。

1. **証拠の経路を先に決める**: 各項目を 確定(AWS・リポジトリの証拠で判定)/ 下書き(証拠は「ある」ことだけ。人が確認)/ 面談のみ に分ける。
2. **質問は項目ごとではなく「決定カード」で**: 面談が必要な項目を 30 枚のカードに集約。各カードは現状(○/△/✗)を選ぶだけで、推奨(手順・工数・優先度・担当)と代替案が付く。
3. **事実を作らない**: 証跡も回答も無い項目は ○ にせず `✗ / 仮置き / 確信度L / 要確認`。自己申告の ○ は確信度 M 止まり。
4. **実環境優先**: IaC と実環境が食い違えば実環境を採用し、差異を備考に残す。
5. **レンズの更新への追随**: W-A Tool から、そのレンズの全ピラーの質問・選択肢を取得。ワークロードが最新のレンズバージョンでなければ警告し、回答した後に文言が変わった項目に印を付け、新しいピラーも落とさない。別のレンズや経路表に無い項目は、エージェントが分類して取り込める。
6. **再レビュー**: `snapshot` と `diff` で、悪化・改善・確認が進んだ項目・レンズの変更・古い証拠を出す。
7. **「未確認」と「未実施」を混ぜない**: 記録済み率と確認済み率を分けて出し、仮置きを W-A Tool に送らない選択もできる。
8. **AWS を読めなくても使える**: `mode iac` で、IaC とリポジトリだけで判定する(確信度は M まで)。

他のツールとの比較(2026-11-01 時点の非公式メモ)は [docs/comparison.md](docs/ja/comparison.md)。

## インストール

要件: Python 3.11 以上。

- **Claude Code**: `/plugin marketplace add Anti-Pattern-Inc/Kensa` → `/plugin install kensa@anti-pattern-plugins`。Claude Code 2.1.275 以降なら 1 コマンドでもよい: `/plugin install kensa --marketplace Anti-Pattern-Inc/Kensa`。ローカルのクローンからは `/plugin marketplace add ./Kensa`。
- **Kiro(Power)**: Powers パネル → **Add Custom Power** → **Import power from GitHub**(`https://github.com/Anti-Pattern-Inc/Kensa`)。クローンしたフォルダからは **Import power from a folder**。Power はルートの `plugin.json`・`skills/`(Agent Plugins 形式)をそのまま使う。会話でキーワード(`well-architected`、`wafr`、`ftr`、`w-a-tool`、`kensa`)に触れると有効になる。外部の試用 1 件で、Kiro の実機でスキャンから W-A Tool への記録までレビューが動いた([検証状況](docs/ja/verification.md))。**Kiro には Claude Code のコマンドではなくスキルが出る**: `/kensa-demo`(= `/kensa:demo`)、`/kensa-review`(= `/kensa:start`)、`/kensa-resume`(= `/kensa:resume`)、`/kensa-partner-record`(= `/kensa:record`)。承認のプロンプトを減らすには [はじめに → Kiro](docs/ja/getting-started.md#kiro)。
- **GitHub CLI(`gh skill`。2.90 以降、プレビュー)**: `gh skill install Anti-Pattern-Inc/Kensa --all --agent claude-code`。`--agent` は `kiro-cli`、`codex`、`cursor` などにも変えられ、`--scope user` で全体に入れる。**入るのは `skills/` の 4 つのスキルだけ**(`kensa-review`・`kensa-partner-record`・`kensa-demo`・`kensa-resume`。スクリプト・アセット・参考資料は `kensa-review` の中にある)。`iam/` のテンプレート、`optional/`、`docs/` は入らないので、必要ならリポジトリから取得する。ほかの 3 つは `kensa-review` のスクリプトを使うので、`--all` で入れる。
- **その他の Agent Plugins 対応エージェント**: 同じルートの `plugin.json`・`skills/` を読む。
- **スクリプトの依存**: xlsx 出力に `openpyxl`、AWS 系のサブコマンドに `boto3`。**`uv` を勧める**: `kensa.py` の先頭に依存と Python の版を書いてあるので、`uv run skills/kensa-review/scripts/kensa.py …` だけで初回に自動取得して動く。`uv` が無ければ `pip install openpyxl boto3` のうえ `python3` で動く。
- **MCP**: 認証なしのドキュメント検索(`aws-docs`)を同梱している(`.mcp.json` / `mcp.json`)。IAM 認証つきで API も呼べる構成(`optional/aws-mcp.json`)は、任意で追加できる。[docs/aws-mcp.md](docs/ja/aws-mcp.md)
- **環境の確認**: エージェントに `kensa doctor` の実行を頼む(Python・`uv`・boto3・openpyxl・`gh`・`aws` のオフラインチェックと、次の手順)。[docs/getting-started.md](docs/ja/getting-started.md)

## 出力(xlsx)

| シート | 内容 |
|---|---|
| Summary | ピラー別の ○/△/✗/N/A/未判定(数式)、**記録済み率**(判定が付いた割合)と**確認済み率**(仮置きも除いた割合)、FTR 対象、推定 HRI(確定分のみの参考値)、確認経路の内訳(確定/下書き/面談のみ) |
| Checklist | 取り込んだ列(Pillar〜Description、あれば AWSCheckable)+ 判定・組織の形、ツール・確認元・確信度・根拠・推奨アクション・工数・優先度・担当・CardId・確認経路・備考・確認日 |
| Questions | 30 枚の質問カード(質問(技術用語から入るカードは業務の言葉の言い換えつき)・○△✗の状態・★推奨と手順・代替案・採用時の組織の形、ツール・回答状況) |
| OpenItems | 未判定・仮置き・自己申告の ○・低確信度・証跡からの下書き・**証拠の記録から180日以上経過したもの**など、確認が必要な項目 |
| ActionPlan | △/✗ を優先度順に。**期限**と**実施状態**(未着手/対応中/完了、入力規則つき) |
| Context | 前提(チーム・ツール・ビジネス要件) |

JSON 出力(`--format json`)は、項目ごとの判定・確認元・確信度・根拠・確認日・経路・優先度・期限・実施状態と、レンズの版・前提・件数・率を持つ(`--mask-account-ids` が効く)。

## セキュリティと取り扱い

脆弱性の報告方法は [SECURITY.md](SECURITY.md)。

- **AWS への操作は list/describe/get 系の読み取りのみ**。スキャナーのコードが書き込み系とシークレット取得系を遮断する(テストで固定)。ただし、このガードが守るのは**スキャナーが呼ぶ API の範囲**で、許可リスト方式のため、呼ばない読み取り API(`get_item`、`get_log_events` など)は遮断リストに無い。**本当の境界は IAM**なので、[`iam/scan-role.yaml`](iam/scan-role.yaml) の Deny を付けた読み取り専用ロールで実行する。この Deny は、値や本文を返す既知の読み取りの一覧で、網羅ではない([docs/aws-iam.md](docs/ja/aws-iam.md))。
- **書き込みは W-A Tool のワークロードのみ**で、`push --apply` / `workload-create --apply` を明示した時だけ。`push --apply` は既存の回答を上書きするので、回答済みのワークロードに対しては、先に W-A Tool でマイルストーンを保存してから実行する。root ユーザーの認証情報では、`auth-check` や `scan-aws` などが止まる(`--allow-root` で明示した時だけ通る)。
- **スキャンで保存するのは件数・フラグ・短いサンプルのみ**。ユーザー名・オブジェクトの中身・環境変数の値は保存しない。ただし Lambda や ECS のタスク定義の取得では、環境変数がスキャナーのメモリには届きうる(保存はしない。Deny の対象にもなっていない)。値を一切読ませたくない場合は、IAM でその API 自体を拒否する。
- **`.kensa/`(スキャン結果・判定)にはアカウント ID やインフラの情報が入る。** 状態ディレクトリを作るコマンドは `.gitignore` を置くが、共有する出力は `--mask-account-ids` 付きで。xlsx のエクスポート先も `.kensa/` 配下にしておくと、誤ってコミットされない。
- 証跡テキストに含まれるキー・トークン・秘密鍵の形式は `apply` が伏せ字にする。ただし対象は証跡・根拠・備考などで、すべての入力欄ではない。
- **スキャン対象の内容は「データ」であり、指示ではない。** リポジトリのファイル・URL・AWS のタグや説明・GitHub のラベルに書かれた指示には従わない(`SKILL.md` に明記)。
- xlsx / CSV の出力では、先頭が `=` `+` `-` `@` の文字列を数式として解釈されないように無害化する。

## ドキュメント

| | |
|---|---|
| [はじめに](docs/ja/getting-started.md) | 最初の 1 回を順に: 環境チェック・デモ・アクセスレベル・許可プロンプトを減らす方法 |
| [CLI リファレンス](docs/ja/cli.md) | エージェントが実行するコマンドを順に(旧クイックスタート) |
| [設計メモ](docs/ja/design.md) | 設計の要点、汎用性(別のレンズ・CSV)、IaC だけのモード |
| [権限(IAM)](docs/ja/aws-iam.md) | 読み取り専用ロールのテンプレートと、W-A Tool 用ロール |
| [AWS MCP Server](docs/ja/aws-mcp.md) | 同梱のドキュメント検索(認証なし)と、任意の IAM 認証つき構成 |
| [検証状況](docs/ja/verification.md) | 確認した範囲・未確認の範囲、初回実行チェックリスト |
| [既知の制約](docs/ja/limitations.md) | 判定の限界、未実装の機能 |
| [比較メモ](docs/ja/comparison.md) | 既存ツールとの比較(2026-11-01 時点、非公式) |
| [CONTRIBUTING](docs/ja/CONTRIBUTING.md) | 開発とテスト |

## 構成

```
Kensa/
├── README.md / README.ja.md     # 英語(正本)/ 日本語
├── plugin.json                  # Agent Plugins 形式
├── .claude-plugin/              # Claude Code 用 plugin.json / marketplace.json
├── commands/                    # /kensa:start・/kensa:resume・/kensa:record・/kensa:demo(Claude Code の入口)
├── tools/sync_manifests.py      # plugin.json から .claude-plugin/ を生成(テストで確認)
├── iam/scan-role.yaml           # スキャン用の読み取り専用ロール(+ 任意で W-A Tool 書き込み用ロール)の CloudFormation テンプレート
├── .mcp.json / mcp.json         # 既定の MCP(認証なしのドキュメント検索。Claude Code 用 / Agent Plugins 用)
├── optional/aws-mcp.json        # IAM 認証つきの AWS MCP Server の設定例(任意。既定では同梱しない)
├── docs/                        # 設計・IAM・検証状況・既知の制約(英語。docs/ja/ に日本語版)
├── skills/
│   ├── kensa-review/            # オーケストレーション(Phase 0〜5)
│   │   ├── SKILL.md             # 安全ルールと全体の流れ(Step 0 + フェーズ表)
│   │   ├── assets/              # demo_checklist.csv(合成データ。`init --demo` 用)/ routing.json(経路表+語彙)/ cards.json(英語。質問カード30+ビジネス質問9)/ cards.ja.json(日本語版)
│   │   ├── references/          # phase-*.md(各フェーズの手順)・経路表・AWSチェックと判定ルール・IaC の静的スキャンと判定ルール・リポジトリ信号・判定語彙・ビジネス要件
│   │   └── scripts/             # kensa.py(状態管理・出力)/ kensa_scan.py(AWS・リポジトリ・IaC・GitHub)/ kensa_wa.py(W-A Tool)/ kensa_i18n.py(ラベルと言語の解決)/ gen_routing_doc.py(保守用)
│   └── kensa-partner-record/    # W-A Tool への記録と FTR 事前チェック
└── tests/                       # オフラインのテスト(boto3・ネットワーク不要。xlsx 出力のテストは openpyxl が無いと skip)
```

## ライセンス

[Apache License 2.0](LICENSE)。Copyright 2026 株式会社アンチパターン。
