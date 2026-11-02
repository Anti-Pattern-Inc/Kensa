# Contributing

> 英語版が正本です: [CONTRIBUTING.md](../../CONTRIBUTING.md)

Issue と Pull Request は、日本語でも英語でも構いません。スキル・参考資料・質問カード・CLI のメッセージ・ドキュメントは英語で書かれています。日本語は、成果物の日本語ロケール(日本語の質問カード `skills/kensa-review/assets/cards.ja.json` と日本語の出力ラベル)と、日本語のドキュメント(`README.ja.md`、`docs/ja/`)だけです。

## テスト

テストはオフラインで動きます(boto3 もネットワークも要りません)。

```bash
pip install openpyxl                 # xlsx 出力のテストに必要。無ければそのテストは skip される
python3 -B -m unittest discover -s tests
```

- Python 3.11 以上。
- IAM テンプレートは `cfn-lint iam/scan-role.yaml` でも確認してください。
- マニフェストを変えたら `claude plugin validate .` で確認してください。

## 方針

- **AWS が書いた文章を入れない。** 質問文・ベストプラクティス文は、実行時に W-A Tool から取得します。AWS のドキュメントは CC-BY-SA-4.0(その中のコードは MIT-0)で公開されているので、その文章を翻訳・改変して入れる場合は、出典・帰属表示・ShareAlike の扱いを先に決めてください(本プロジェクトの Apache-2.0 とは別の条件になります)。フィクスチャは合成データにしてください(`tests/fixtures/mini_checklist.csv` は、質問タイトル・ベストプラクティス名・`Description` まで合成。`QuestionId` と `ChoiceId` は経路表のキーなので実際の識別子を使う)。
- **実在のアカウント ID、プロファイル名、顧客名、メールアドレスをコミットしない。** 例には `123456789012` や `example.com` を使います。
- **AWS には読み取りだけで触る。** スキャナーに API を足す時は、`iam/scan-role.yaml` の Deny と `DENY_ACTIONS` の対応(`kensa.py iam-policy --kind scan-deny`)を更新し、テストを通してください。
- **「確認していないこと」を確認済みとして書かない。** README や `docs/verification.md` の記述は、実際に行った確認に合わせてください。
- **2 つのロケールをそろえる。** カード・スキル・参考資料は英語が先です。`assets/cards.json` を変えたら、`assets/cards.ja.json` にも同じキー・ID・プレースホルダー(`{service}`、`{po}`、`{chat}`、`{vcs}`、`{ticket}` など)を保ち、文章の値だけが違うようにしてください。英語のドキュメントが正本で、`docs/ja/` と `README.ja.md` の日本語版はそれに従います。
- 名前に AWS のマークを組み込まない(AWS Trademark Guidelines)。説明文での記述的な使用(「for AWS Well-Architected」など)は構いません。
