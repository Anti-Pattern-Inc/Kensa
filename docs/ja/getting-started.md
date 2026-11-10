# はじめに

> 英語版が正本です: [docs/getting-started.md](../getting-started.md)

最初の 1 回を、順を追って説明します。この文書のコマンドは、エージェントに頼む内容か、エージェントが代わりに実行する内容です。コマンドの一覧は [cli.md](cli.md) にあります。

## 0. 当日までに: 事前準備チェックリスト

準備はレビューそのものより時間がかかるので、勉強会やレビューの**前に**済ませてください(初回は 30〜60 分ほど見込む)。間に合わなければ、当日は W-A Tool のコンソールで進めます(そちらには、ここの準備は要りません)。

- [ ] エージェント: Claude Code(Amazon Bedrock 上でも可)か Kiro。そのほかは `gh skill install` で(README を参照)
- [ ] Python 3.11 以上と [uv](https://docs.astral.sh/uv/)(`uv --version`)
- [ ] AWS CLI v2 で、**テスト用**のアカウントにサインインできる(`aws sts get-caller-identity`)
- [ ] [`iam/scan-role.yaml`](../../iam/scan-role.yaml) から読み取り専用ロールを deploy し、出力の `ScanProfileSnippet` を `~/.aws/config` に貼った([権限(IAM)](aws-iam.md)。IAM ユーザーも信頼先にできる)。管理者権限のプロファイルは使わない
- [ ] 任意(回答を記録する場合): `CreateWaToolRole=true` と、W-A Tool のテスト用ワークロード
- [ ] kensa を入れ、`doctor` に `[NG]` が出ない(下の 1)
- [ ] 任意: デモを 1 回試し、AWS なしで流れを見ておく(下の 2)
- [ ] Kiro: ローカルだけのコマンドを事前に許可しておく([6](#6-許可プロンプトを減らす))。承認が続いて進まなくなるのを防ぐ

## 1. インストールして環境を確認する

プラグインを入れます([README → インストール](../../README.ja.md#インストール))。そのあと、エージェントに「`kensa doctor` を実行して」と頼みます(自分で `W doctor` を実行してもよい。`W` は `uv run skills/kensa-review/scripts/kensa.py`)。AWS にもネットワークにも触れない、オフラインのチェックです(出力例。値は環境によって変わります)。

```text
kensa doctor (offline check)
  [ok]   python    3.12.3
  [ok]   uv        /usr/local/bin/uv
  [warn] boto3     not importable in this Python (needed for: scan-aws, seed --from-wa-tool, push --apply, precheck). `uv run` installs it on demand.
  [warn] openpyxl  not importable in this Python (needed for: export --format xlsx). `uv run` installs it on demand.
  [ok]   gh        /usr/bin/gh
  [ok]   aws CLI   /usr/local/bin/aws
access level (from configuration only; `auth-check` verifies it against AWS):
  * Lv0 no AWS access : interview + repository (+ `mode iac`); `init --demo` to try the flow
    Lv1 read-only AWS : `scan-aws` (set aws.profile)
    Lv2 + W-A Tool    : `seed --from-wa-tool` / `push` / `precheck` (set aws.wa_profile (or reuse aws.profile))
state: none here → `init` (or `--dir .kensa-demo init --demo` to try without AWS)
```

`uv` が `[ok]` なら、boto3 / openpyxl の `[warn]` は問題ありません。`uv run` が初回に入れます。`uv` が無ければ `python3 -m pip install openpyxl boto3` で入れます(PEP 668 の環境では venv の中で)。

## 2. AWS なしで試す(1 分ほど)

Claude Code では `/kensa:demo`、Kiro では `/kensa-demo` を実行します。他のエージェントでは「kensa をデモデータで試して」と頼みます。`.kensa-demo/` に**合成データ**の 12 項目のチェックリストを作り、決定カードを 1〜2 枚見せ、CSV を出力します。デモの中のものは W-A Tool に送れません(`push --apply` と `seed` は拒否されます)。本番の `.kensa/` にも触れません。

## 3. AWS へのアクセスをどこまで渡すか選ぶ

初日から全部は要りません。レベルが上がるごとにできることが増え、どのレベルでもレビューは成立します。

| レベル | 持っているもの | kensa にできること | 進め方 |
|---|---|---|---|
| **Lv0** | AWS へのアクセスなし | 面談(決定カード)とリポジトリ・IaC の証拠(`mode iac`)。チェックリストは `seed --from-csv`(自分のエクスポート)から | 準備は不要 |
| **Lv1** | **読み取り専用**の AWS プロファイル | 実環境のスキャン(`scan-aws`、37 チェック)が加わる | [`iam/scan-role.yaml`](../../iam/scan-role.yaml) をデプロイ([解説](aws-iam.md))、`context set aws.profile=…`、`auth-check` |
| **Lv2** | **W-A Tool** 用のプロファイル(読み取り専用とは別) | ワークロードから最新のレンズを取得(`seed --from-wa-tool`)、回答の記録(`push`)、FTR 事前チェック、ワークロード作成 | 同じテンプレートを `CreateWaToolRole=true` で、`context set aws.wa_profile=…`、`auth-check --for wa` |

まずテスト用のアカウントとワークロードで始め、管理者権限のプロファイルは使わないでください([README → 最初に知っておくこと](../../README.ja.md#最初に知っておくこと))。`auth-check` は、管理者権限に見える認証情報だと警告を出しますが、ベストエフォートの目安です。何も出なくても、読み取り専用の証明にはなりません([権限(IAM)](aws-iam.md))。AWS に触れる前に、エージェントは必ずアカウント ID を見せて確認を取ります。`push --apply` は、あなたが指示した時だけ実行されます。

## 4. 実際のレビューを行う

「Well-Architected レビューをして」と頼みます(Claude Code では `/kensa:start`、Kiro では `/kensa-review`。続けてサービスの概要やサイトの URL を書いてもよい)。進み方は次のとおりです。

1. **サービスの概要を 1 回**話し(何のサービスか・誰が使うか・扱うデータ・規制・チーム・ツール)、**ビジネス要件の質問(最大 9 問)**に答えます。各問に推奨の選択肢があります。
2. **kensa がレンズを取得し、全項目を振り分けます**(確定 / 下書き / 面談のみ。`coverage`)。
3. **スキャンが走ります**(`scan-repo`、そして `scan-aws` または `scan-iac`)。AWS では、先にアカウント ID を見せて、あなたの OK を待ちます。
4. 証拠で決まらなかった項目について、**決定カードに答えます**。最大 **30 枚**で、各カードは ○/△/✗ の選択です(先頭に検出済みの証拠が出ます)。エージェントは 1〜3 枚ずつ尋ねます。急いでいる場合は、エージェントが推奨のビジネス回答を仮置きとして記録し、知りようのない項目は「暫定回答」の注記つきで ✗ にして、仮置きをすべて最後に一覧で示します。
5. **xlsx が出来上がります**(希望すれば W-A Tool への記録と FTR 事前チェックも)。誰も確認していない項目は `✗ / 仮置き(要確認)` のままで、別に数えます(確認済み率)。「未実施と確認した」とは表示しません。

**所要時間。** 外部の担当者による最初の試用(Kiro、v0.3.0、[検証状況](verification.md))では、エージェントの作業だけでレビュー全体に約 **28 分**かかり、そのうち `seed` と 1 リージョンのスキャンが約 6.5 分でした。これにあなたの回答の時間が加わります。自分で調整できるのは質問の数(9 問 + 最大 30 枚)です。知っているものに答え、残りは仮置きにできます。**時間が限られている時は、先にピラーを決めて**伝えます。たとえば:

> kensa でレビューして。ただし、これからの 10 分では、セキュリティ・信頼性・運用上の優秀性の決定カードだけを聞いて(`cards next --pillar security,reliability,operationalExcellence`)。ほかのピラーは「後で」として一覧にして。

## 5. 後で戻る

状態は `.kensa/`(実行したディレクトリ)にあるので、どこで止めても構いません。

- **続きから**: `/kensa:resume`(Kiro では `/kensa-resume`。または「kensa のレビューの続きを」)。エージェントが `W plan` と `W status` を実行して現在地を伝え、続けます。
- **再レビュー**(たとえば四半期ごと): `/kensa:resume re-review`(Kiro では `/kensa-resume re-review`)。`snapshot` を取り、レンズを取得し直し、再スキャンして、`diff` で悪化した項目を先に報告します。
- **2 つ目のレビュー**(別のレンズや別のワークロード): 別のディレクトリ(`--dir .kensa-serverless`)を使います。
- すでにレビューがあるディレクトリでの `init` は、上書きせずに、どこまで進んだかを伝えて `plan` を案内します。

## 6. 許可プロンプトを減らす

### Claude Code

`W …` の呼び出しはすべて Bash の呼び出しなので、1 回のレビューで承認が何度も要ります。**ローカルだけで完結する**サブコマンド(読み書きするのは `.kensa/` と指定したリポジトリだけ)は、`.claude/settings.json`(プロジェクト)か `~/.claude/settings.json`(全プロジェクト)で事前に許可できます。

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run *kensa.py doctor*)",
      "Bash(uv run *kensa.py plan*)",
      "Bash(uv run *kensa.py status*)",
      "Bash(uv run *kensa.py coverage*)",
      "Bash(uv run *kensa.py items*)",
      "Bash(uv run *kensa.py diff*)",
      "Bash(uv run *kensa.py business *)",
      "Bash(uv run *kensa.py cards *)",
      "Bash(uv run *kensa.py context *)",
      "Bash(uv run *kensa.py apply *)",
      "Bash(uv run *kensa.py finalize*)",
      "Bash(uv run *kensa.py snapshot*)",
      "Bash(uv run *kensa.py scan-iac*)",
      "Bash(uv run *kensa.py seed --from-csv *)",
      "Bash(uv run *kensa.py iam-policy *)"
    ]
  }
}
```

- Bash のルールでは `*` をどこにでも置けるので([Claude Code の権限](https://code.claude.com/docs/en/permissions))、先頭の `*kensa.py` はエージェントが使う絶対パスに合います。`uv` を使わない場合は、`uv run` を `python3` に替えて同じルールを足します。サブコマンドの前にグローバルオプション(`kensa.py --dir X plan`)を置くとこのルールに合わず、確認が出るだけです。これらのルールはドキュメントから書いたもので、実際のセッションでの動作は、ここでは確認していません。
- `auth-check`、`scan-aws`、`seed --from-wa-tool`、`push`、`precheck`、`workload-create` は**加えないでください**。AWS に触れるコマンドで、承認のプロンプトが止められる最後の場所です。`export`(`--out` の指す場所にファイルを書く)と `scan-repo`(`--gh` を付けると `gh` を呼ぶ)も、意図して外しています。

### Kiro

[`optional/kiro-permissions.yaml`](../../optional/kiro-permissions.yaml) のルールを `~/.kiro/settings/permissions.yaml` に写します(Kiro は権限のルールをホームディレクトリからだけ読み、リポジトリからは読みません。[Kiro の権限](https://kiro.dev/docs/permissions/))。上の一覧と同じ、ローカルだけのサブコマンドを許可し、AWS に触れるものは引き続き確認を求めます。

- 承認のプロンプトで **`uv run *`(や `python3 *`)を「Always allow」にしないでください**。どんなスクリプトも確認なしで動くようになり、`scan-aws`、`push --apply`、`workload-create --apply` も通ります。Kiro では、どこか 1 か所に `allow` があれば足ります。
- kensa のスキルは、呼び出しを毎回 1 行のコマンド(`uv run …/kensa.py <サブコマンド> …`)で、1 回に 1 コマンドずつ書くようエージェントに指示しています。それでもエージェントがシェルの関数や変数(`W() {…}`、`W="uv run …"`)を作っていたら、やめるよう伝えてください。呼び出しのたびに承認が要るようになり、zsh では変数の形は失敗します。

## 7. 言語

エージェントには自分の言語で話しかければ、カードを訳し、CLI のメッセージも伝えてくれます。成果物(xlsx / csv / json とカードの文面)を日本語か英語で出すには、`W context set language=ja`(または `--lang ja`、`KENSA_LANG=ja`)を使います。CLI 自身のメッセージは、エージェントが読んであなたに伝える前提なので、設計として英語のままです([README → 言語](../../README.ja.md#言語))。
