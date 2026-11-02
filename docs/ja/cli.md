# CLI リファレンス: エージェントが実行する順序

> 英語版が正本です: [docs/cli.md](../cli.md)

普通は自分で打つ必要はありません。エージェントに頼めば(「W-Aレビューをして」、Claude Code なら `/kensa:start`)、`kensa-review` スキルが実行します。何が起きるかを見たいとき、1 ステップを自分で実行したいときのために載せています。`W <コマンド> --help` が完全なリファレンスです。

`W` は `uv run skills/kensa-review/scripts/kensa.py`(`uv` が無ければ `python3 …`。`pip install openpyxl boto3` が要る)です。どのステップの後でも、**`W plan` が済んだことと次の手順(番号つき)を出す**ので、現在地を見失ったときに最速です。

```bash
W="uv run skills/kensa-review/scripts/kensa.py"
$W doctor                                          # オフラインの環境チェック(Python・uv・boto3・openpyxl・gh・aws)と、使っているアクセスレベル
# まず AWS なしで試す: 合成データ 12 項目のサンドボックス(ここでは push --apply が無効)
#   $W --dir .kensa-demo init --demo   →   $W --dir .kensa-demo cards next   →   ...
$W init                                            # (.kensa/ に進行中のレビューがあれば、どこまで進んだかを示して `plan` を案内する)
$W context set service.name="…" team.size=4 tools.chat=Slack aws.regions='["ap-northeast-1"]' repo.github=owner/repo
$W business show                                   # ビジネス要件9問(推奨付き)
$W business answer B-CRITICALITY --option high
$W seed --from-wa-tool --workload-id <workload-id>   # 最新のレンズ(または --from-csv)。ID は 32 桁の 16 進数(`workload-create` が出す)
$W coverage                                        # 確定 / 下書き / 面談のみ の内訳
$W scan-repo --path . --gh owner/repo              # IaC・文書・GitHub の設定と直近90日の活動実績・CI
$W context set aws.profile=kensa-scan aws.wa_profile=kensa-wa     # 読む用 / W-A Tool に触る用(wa_profile は任意)
$W auth-check                                      # 認証と接続先アカウントの確認(STS のみ。通らなければ終了コード 3)。W-A Tool 側は --for wa
$W scan-aws --profile kensa-scan                   # 読み取り専用(37チェック)。リージョンは aws.regions から。または --regions ap-northeast-1
# AWS を読めない場合は scan-aws の代わりに: $W mode iac → $W scan-iac --path .   (確信度は M まで。『IaC(実環境未確認)』と出る)
#   → エージェントが解釈ルールに従い判定を書いて: $W apply answers.json
$W cards next -n 3                                 # 確定しなかった項目の質問カード(検出済みの証拠つき)
#   「waiting for evidence collection」= カードが待っているスキャンが未実行(または `plan --skip` していない)。待っているスキャン名も出る
# 別レンズ・新項目: $W route-unknown > unknown.json → 分類して routing.json を作る → $W route-apply routing.json
$W cards answer C-OPS-OWNERSHIP --current partial --choose recommend
$W finalize && $W export --out .kensa/review.xlsx --mask-account-ids   # --format json なら機械可読(csv も可)
#   finalize は手つかずのレビュー(スキャン・記録・回答がゼロ)を拒否する。意図してスキャンを飛ばすなら `plan --skip aws,repo`、--allow-empty で強制
$W push --workload-id <workload-id>                       # dry-run(--apply で記録。--exclude-assumed で仮置きを送らない)
$W precheck --workload-id <workload-id> --solution-name "…"             # FTR 事前チェック
# 再レビュー(四半期ごとなど): 前回の状態を保存 → 再スキャン・apply → 差分
$W snapshot --label 2026Q4                         # .kensa/history/ に保存(レビューを終えた時点で取っておく)
$W diff                                            # 悪化・改善・確認が進んだ・レンズ更新・古い証拠(--since / --stale-days / --json)
$W push --workload-id <workload-id> --apply --milestone "2026Q4 レビュー"   # 全質問の記録に成功した時だけマイルストーンも作る
$W iam-policy --kind scan-deny                     # 権限: 値取得系の追加 Deny(wa-tool-read / wa-tool-write もある)
```

## 終了コードとメッセージ

- 終了コード 2: 使い方か状態のエラー(`error:` の後ろのメッセージが対処を示す)。終了コード 3: AWS の認証情報か接続先アカウントが確認できなかった(スキャンも保存もしていない)。`doctor` が 1 で終わるのは、Python が古い場合だけ。
- CLI のメッセージは常に英語(エージェントがあなたの言語で伝える)。`ja` ロケールがあるのは成果物と質問カードの文面だけ([言語](../../README.ja.md#言語))。
