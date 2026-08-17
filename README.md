# mc-bot

Minecraftサーバーのログを監視し、Discord Botとして指定チャンネルへ通知する
独立型の常駐アプリケーションです。Minecraftサーバーと同じDockerホスト上で、
永続ボリュームを読み取り専用で共有します。

## 実装仕様

| 項目 | 仕様 |
| --- | --- |
| 実行環境 | Python 3.14.6 |
| Discordライブラリ | discord.py 2.7.1 |
| パッケージ管理 | uv 0.11.8 |
| デプロイ方式 | Docker Compose |
| Discord接続方式 | BotアカウントによるGateway接続（Webhook不使用） |
| Gateway Intents | Guilds、Guild Members、Voice States |
| 対応Minecraftログ | チャット、進捗・達成、参加、退出、死亡 |
| 通知方向 | Minecraftログ通知、Discordからwhitelist管理 |
| 設定方法 | Discordスラッシュコマンド |
| 秘密情報 | `DISCORD_TOKEN`、`MINECRAFT_RCON_PASSWORD`、`VOICEVOX_TTS_API_TOKEN` |
| 永続データ | `/data/settings.json`、`/data/cursor.json`、`/data/accounts.db` |
| CPU上限 | 0.25 CPU |
| メモリ上限 | 192 MiB |
| 実行ユーザー | UID/GID 1000（Minecraftデータの読み取り権限と一致） |

Geyser/Floodgate経由のBedrockプレイヤーを含む次のログを扱います。

- プレイヤーのチャット
- 公開される進捗・達成
- サーバーへの参加と退出

通知の定型文とバニラの進捗名は日本語です。プレイヤーのチャット本文は改変しません。
カスタム進捗など翻訳表に存在しない名前だけは原文へフォールバックします。

進捗翻訳表はMinecraft Java Edition 26.1.2の公式 `en_us.json` と `ja_jp.json` から、
`advancements.*.title` の126項目を突き合わせて生成しています。使用した公式アセットの
SHA-1は次のとおりです。

Discord連携済みのプレイヤーが進捗を達成すると、既存の進捗ログはそのままに、
level-bot XPを100、RCON設定時はMinecraft内の経験値ポイントも100付与します。Minecraft内では
標準の進捗通知の後に `tellraw` の報酬通知を流し、Discordでは進捗Embedの次に
報酬Embedを別投稿します。固定の進捗報酬はVCボーナスで倍化せず、同じアカウントの
同じ進捗に重複付与しません。

Discord連携済みのプレイヤーが釣るとMinecraft内の釣りボーナスXPを付与し、
90秒以内に釣りを続けると連続回数に応じて増加します。1回目は2、2回目は5、
3〜4回目は7、5〜9回目は10、10〜19回目は15、20回目以降は20 XPです。
獲得表示は対象プレイヤー本人の
アクションバーだけに表示し、公開チャット・Discord通知・追加効果音は使用しません。
このボーナスはlevel-botへ監査記録だけを送り、level-bot側のXPには加算しません。
釣果はMinecraft側の `UsapoEventBridge` Paperプラグインが即時検知し、UUID付きの
構造化ログを既存ログ監視へ渡します。scoreboardの定期照会は行いません。

Discord連携済みのプレイヤーが原木・表皮を剥いだ原木・ネザーの幹を連続で壊すと、
5本で5 XP、10本で15 XP、20本以降は10本ごとに30 XPを付与します。次の原木までの
猶予は常に30秒で、壊すたびに更新されます。
通知は本人のアクションバーだけに表示し、経験値オーブ取得音も本人だけに再生します。
板材と葉は対象外です。釣りと同様、level-botには監査記録だけを残します。
伐採もPaperのブロック破壊イベントから即時検知し、RCONポーリングは行いません。

通常プレイで自然に獲得したMinecraft経験値もPaperイベントで検知します。高頻度な
経験値オーブ取得をそのまま外部送信せず、Paper側でプレイヤーごとに5秒分を合算してから
UUID付き構造化ログへ出力します。mc-botは増加量を直接level-botへ送り、30秒ごとの
`experience query`は行いません。起動時のオンライン状態確認を除き、参加・退出は
Minecraftログから追従します。

釣りは10回以降10回ごと、木こりは20本・50本・以降50本ごとを公開節目として、
Minecraft全体チャットとDiscord通知チャンネルへ記録を流します。公開節目では本人用の
アクションバーを省略して表示を重複させません。釣りは常に無音、木こりの経験値取得音は
公開節目でも本人だけに再生します。

Discord連携済みのプレイヤーがMinecraftとDiscord VCに同時接続している間は、
level-botのVC XPと、通常プレイで獲得したMinecraft内の経験値が2倍になります。
VC倍率のON/OFFは状態が変わった時だけPaperプラグインへ伝え、自然経験値イベントの
獲得量をサーバー内で直接2倍にします。経験値オーブごとのRCON追加は行いません。
同時接続が始まった時だけ、Minecraft内の
`tellraw` とDiscordの通知チャンネルへ開始通知を送ります。短時間の再接続は60秒の
クールダウンで連投を防ぎ、終了通知は送りません。

Minecraft 資源交換所では、既存のサーバーXPから資源への交換に加えて、オンライン中の
連携アカウントが手持ちのエメラルドをダイヤモンドへ交換できます。交換率は
32個→1個、64個→2個です。交換パネルの「交換内容」にはサーバーXPから資源への交換と
手持ち資源から資源への交換を両方表示します。消費と付与はPaperプラグイン内で一括処理し、
所持数不足または受取用の空き不足では何も消費しません。同じリクエストのRCON再送は
プレイヤーデータに保持した履歴で二重交換を防ぎます。完了時はMinecraft全体チャットへ
通知し、UUID付き構造化ログを通じてDiscordの通知チャンネルにも再送可能な記録を残します。

連携済みプレイヤーはゲーム内の `/exchange` から、サーバーXP→Minecraft XP、
サーバーXP→資源、手持ちエメラルド→ダイヤモンド、XP残高確認を利用できます。
スマホ版を含むFloodgate/Bedrockでは選択フォームと確認画面を表示します。Java版、または
フォームを表示できない場合は `/exchange xp <50|250|500|5000>`、
`/exchange resource <diamond|emerald> <個数>`、`/exchange emerald-diamond <32|64>`、
`/exchange balance` を使います。フォームとコマンドが送る表示価格は、XP消費前に
level-botの現在の交換内容と再照合し、改定済みなら交換せずメニューの開き直しを案内します。
受付・残高・エラーは実行者本人だけへ返し、交換完了後の既存ログ通知は維持します。
同じリクエストIDは既存APIまたはPaperの交換履歴で重複処理されません。クライアント側の
アドオンは不要です。

Minecraft アイテムガチャは、オンライン中の連携プレイヤーがDiscordの常設パネル、または
ゲーム内の `/gacha` から利用できます。スマホ版を含むFloodgate/Bedrockでは、通常100 XPと
R以上確定1,000 XPをタップで選び、確認画面を経て実行します。Java版とフォームを表示できない
場合は `/gacha normal` または `/gacha rare` を使います。どちらの入口も日本時間0:00区切りで
合計1日3回までです。Discordの確認画面では現在XPと抽選後のXPを表示し、ゲーム内の処理結果は
実行者だけに表示します。公開するランク確率は、通常が
N 52%、R 29%、SR 12%、SSR 4.5%、UR 2%、幻 0.5%、R以上確定が
R 65%、SR 22%、SSR 8%、UR 4%、幻 1%です。景品内容と個別確率は抽選・受取が
完了するまで公開しません。抽選結果をSQLiteへ先に固定してから、固定許可リストの
景品だけをRCONで付与します。同日連打やBot再起動で再抽選せず、RCON応答を失って付与結果を
断定できない場合も二重配布を避けるため自動再送しません。Nを含む全結果をMinecraft全体
チャットとDiscordログへ投稿します。Discordで通知を許可するのは抽選者本人だけで、
`@everyone`、`@here`、ロールメンションは許可しません。
景品配布前にXPを予約し、配布成功後に消費を確定します。Minecraftが配布を明確に拒否した
場合は予約を取り消して同じ景品を再試行できます。通信断などで配布成否が不明な場合は、
二重配布を防ぐためXP消費を維持して管理者確認とします。
ゲーム内の確認画面で表示した料金も構造化要求へ含め、mc-botの現行料金と一致する場合だけ
予約します。旧Paper形式または料金改定後の古い画面から届いた要求ではXPを消費しません。
景品の入手経路とエンチャント本のID・最大レベルは、Minecraft Java 26.2の公式
レシピ・ルートテーブル・エンチャント定義と照合します。安価にクラフトできる名札などは
レア景品へ含めません。

```text
client.jar: 2dc72797acbc1b63fc16a11c4ac393605f453754
ja_jp.json: 53e15b2f69a51d0c4291d4d453acd81a1828f416
```

## 構成

```text
Minecraftコンテナ
  ├─ UsapoEventBridge → 釣り・伐採イベントを構造化ログへ出力
  └─ minecraft-crossplay-data:/data
                    │ 読み取り専用で共有
                    ▼
mc-botコンテナ
  ├─ /minecraft/logs/latest.log を監視
  ├─ /minecraft/whitelist.json から既存登録を保護対象として取り込み
  ├─ /data/cursor.json に送信済み位置を保存
  ├─ /data/settings.json にDiscord上で行った設定を保存
  ├─ /data/accounts.db にアカウント紐付け・交換・ガチャ結果を保存
  ├─ Discord Gateway → 指定チャンネルへEmbed通知
  └─ 内部DockerネットワークのRCON → whitelist/fwhitelist
```

初回起動時は既存ログを再送せず、ファイル末尾から監視します。Botの再起動後は保存した
位置から再開し、Minecraft再起動による `latest.log` のローテーションも検出します。
カーソルはDiscordへの送信成功後にだけ更新します。送信に失敗した場合は同じログを
指数バックオフ付きで再試行するため、障害時に通知を読み飛ばしません。送信成功直後、
カーソル保存前にプロセスが停止した場合は、欠落を避ける代わりに同じ通知が再送されます。

## Discord側の準備

1. [Discord Developer Portal](https://discord.com/developers/applications) でApplicationとBotを作ります。
2. OAuth2のスコープ `bot` と `applications.commands` を付けて、Botを対象サーバーへ招待します。
3. Developer PortalのBot設定で **Server Members Intent** を有効にします。
4. 使用するチャンネルで「チャンネルを見る」「メッセージを送信」
   「埋め込みリンク」の権限を与えます。
5. mc-botをデプロイした後、「サーバーの管理」権限を持つユーザーがDiscordで設定します。

```text
/mc-config channel channel:#ログ
/mc-config panel channel:#minecraft参加
/mc-config admin-panel channel:#minecraft管理
/mc-config approval mode:自動承認
/mc-config approval mode:管理者承認 channel:#minecraft申請
/mc-config player-count action:有効化
/mc-config item-gacha-panel channel:#minecraftガチャ
/mc-config show
```

一般ユーザーは参加パネルのボタンと入力画面だけで、Java版・Bedrock版を問わず
複数アカウントを登録できます。管理パネルでは代理登録、既存whitelistのDiscord
アカウントへの紐付け、Discord側の紐付け先修正、誤登録したMinecraft IDの修正、
現在のWhitelist全件の一覧表示ができます。Discord側を修正する場合はWhitelistを維持し、
削除反映待ちなら削除予約を取り消して復旧します。Minecraft ID側を修正する場合は誤IDの
削除を継続し、同じDiscordユーザーへ正しいIDを登録します。一覧はJava版・
Bedrock版とDiscord連携状況を表示し、実行した管理者だけに見えます。取り込まれた
既存登録は初期状態で保護され、Botが自動削除することはありません。
Bedrock版でモダンゲーマータグの `名前#数字` が入力された場合は、Minecraftで使われる
クラシック形式の `名前数字` へ正規化してから確認・保存します。
Whitelist反映に失敗した登録・解除は最大5回まで自動再試行し、上限後は
状態と最後のエラーを保持したまま自動再試行を停止します。解除に失敗した本人は
「登録内容を確認・変更」からWhitelist解除を手動で再試行できます。

MinecraftアカウントとDiscordユーザーの紐付けは、変更される可能性があるプレイヤー名では
なくJava UUIDまたはBedrock XUID由来のFloodgate UUIDを本人識別子として扱います。新規登録時に
UUIDを確認できない場合は名前だけの登録を作りません。既存Whitelistの取り込み、追加・削除、
反映確認もUUIDを優先し、プレイヤー名はMinecraft内コマンド用の現在名として更新します。
過去の名前基準処理で同じUUIDの登録が重複している場合は、同じDiscordユーザーの行だけを
現行Whitelist名へ統合し、旧行は削除せず `missing` として退避します。所有者が異なる衝突は
自動統合せず、安全側で停止します。UUID付きの削除済み登録も、別のDiscordユーザーからの
通常登録では引き継げません。管理パネルの紐付け先修正を使って明示的に変更します。
UUID競合などでWhitelistの取り込みに失敗した場合は、古い情報による誤操作を防ぐため
未連携アカウントの選択画面も表示しません。

管理パネルの「サーバー操作」は、「サーバーの管理」権限を持つ管理者だけが
操作できるエフェメラルUIです。Minecraftを再起動せず、RCON経由で次の
操作ができます。

- オンラインプレイヤーの取得
- オンラインプレイヤーを選択し、理由と最終確認付きでキック
- JSONエスケープ済みの `tellraw` によるサーバー告知、Discord通知チャンネルへの記録、
  接続中の読み上げVCでのVOICEVOX読み上げ
- Whitelistを15分、30分、1時間停止し、期限後に自動再開
- Whitelistの手動再開
- 天候を晴れ・雨・雷雨へ変更、時刻を朝・夜へ変更
- sparkのローカルヘルスレポートによるTPS、MSPT、CPU、メモリ状況の確認
- 専用VCでのMinecraftチャット、参加・退出、進捗、死亡のVOICEVOX読み上げ

Discordへ送るゲーム参加・退出・進捗のEmbedでは、Minecraft名に「さん」を
付けて表示します。コロンで発言に続くチャットには付けません。連携済みの
Discordユーザー表示は通知なしのクリック可能なメンションのままです。
Discord連携済みの場合は `Minecraft名 (@Discord名) さん` の順で表示します。

Whitelistの再開予定時刻は永続化され、mc-botの再起動後も引き継がれます。
Minecraftへのアカウント追加・削除は、RCON応答だけでなく実際の `whitelist.json` にある
UUIDへの反映を確認してから登録状態を更新します。Bedrockの `fwhitelist` にはゲーマータグでは
なくFloodgate UUIDを渡します。Java版は保存UUIDからMojang Session APIで現在名とUUIDの一致を
確認し、検証済みの現在名だけを名前必須のWhitelistコマンドへ渡します。ゲーム内イベントは
`usercache.json` から得たUUIDでDiscord登録を照合し、UUID付き登録についてはキャッシュが
取得できない場合に名前照合へ戻りません。Botの登録情報と実Whitelistは定期的に照合され、
未反映の管理対象アカウントは再追加されます。管理一覧では両方の件数と未反映状態を確認できます。
RCON操作が実ファイルへ反映されない場合は、Java UUIDまたはBedrock XUIDをサーバーキャッシュ、
公式API、公開XboxプロフィールAPIの順で確認し、既存項目を保持したまま対象UUIDを
`whitelist.json` へ原子的に追加・削除して `whitelist reload` を実行します。
キック・告知・Whitelist・ワールド操作は、実行者のDiscordユーザーIDとともに
mc-botのログへ記録されます。任意のMinecraftコマンドを入力する機能はありません。

`/mc-config player-count action:有効化` は、コマンドを実行したチャンネルと同じ
カテゴリーに閲覧専用の「マイクラステータス」ボイスチャンネルを自動作成し、
Java版・Bedrock版を合わせた人数をボイスチャンネルステータスへ
`🟢オンライン2人` のように表示します。参加・退出から約1秒後に更新し、10秒ごとの
再確認でも追従します。RCONでサーバーへ接続できない間は `🔴サーバー停止中` と
表示します。
同じコマンドの「更新停止」でチャンネルを残したまま停止、「チャンネル削除」で作成した
チャンネルごと削除できます。この機能にはBotの「チャンネルの管理」と
「ボイスチャンネルステータスの設定」権限が必要です。

コマンドの応答と設定表示は実行者だけに見えます。コマンドはグローバル登録のため、
Botの初回起動直後はDiscordへの反映に少し時間がかかる場合があります。通知先が未設定、
または古い通知先が利用不能でもBotは起動し続けるため、コマンドから修正できます。

BotトークンとVOICEVOX内部TTS APIトークンは秘密情報として扱い、Git、README、Issue、
ログへ貼らないでください。

## Minecraft専用VC読み上げ

VCへ参加して `/vc` を実行するか、管理パネルの「Minecraft読み上げ」から接続先VCを
選択すると、mc-bot自身がVCへ接続し、接続内容を実行チャンネルへEmbedで案内してから、
Minecraftログを構造化したままVOICEVOX内部TTS APIへ送信します。Discordへ投稿したEmbedを
読み直さないため、Markdownやメンション表現に依存しません。読み上げは上限付きキューで
順番を維持し、API障害時もMinecraftログのDiscord転送を止めません。Discord連携済みの
プレイヤー名は、サーバー内の表示名（ニックネーム優先）で読み上げます。
ただし、Minecraft内チャットは名前を付けず、発言内容だけを読み上げます。
ゲームへの入退室は「ゲームに参加しました」「ゲームから退出しました」と読み上げ、
Discord VCの接続・切断と区別します。
名前を読み上げる入退室・進捗・死亡では、名前に「さん」を付けます。死亡原因は
Paper標準の英語ログから判定し、落下、溶岩、溺死、炎、爆発、モブ・プレイヤーによる
死亡などを日本語でDiscordへ表示して読み上げます。
接続中にもう一度 `/vc` を実行すると、読み上げを停止してVCから切断します。
VCから人間の利用者がいなくなった場合も自動切断します。mc-botを含むBotアカウントは
利用者数に含めません。

VOICEVOX Discord側では内部APIを有効化し、両アプリで同じトークンを設定します。

```text
INTERNAL_TTS_API_ENABLED=true
INTERNAL_TTS_API_TOKEN=<強い共有トークン>
```

mc-bot側では次を設定します。

```text
VOICEVOX_TTS_API_TOKEN=<同じ共有トークン>
VOICEVOX_TTS_API_URL=http://<voicevox-discordホストのLAN IP>:<公開ポート>
```

同じDockerホストで運用する場合は、両アプリを共通ネットワークへ接続し、Docker DNS名を
URLに指定する構成も利用できます。別ホストの場合、VOICEVOX側は内部APIをLANアドレスへ
公開し、ファイアウォールでmc-botホストからの接続だけを許可してください。Bearerトークンが
平文で流れるHTTPをインターネットへ公開してはいけません。mc-botには対象VCの「接続」
「発言」権限が必要です。接続先は永続化され、mc-bot再デプロイ後に自動再接続します。
Minecraftサーバーの再起動は必要ありません。

## Coolifyへのデプロイ

このリポジトリをMinecraftとは別のDocker Composeアプリとして登録します。同じ
`usapo-server_2` に配置します。別プロジェクトのMinecraftボリューム名をCoolifyに
書き換えさせないため、mc-botのAdvanced設定で **Raw Compose Deployment** を有効にします。
そのうえで次の変数をCoolifyに設定します。

| 変数 | 必須 | 値 |
| --- | --- | --- |
| `DISCORD_TOKEN` | はい | Discord Botトークン。Secret扱いにする |
| `MINECRAFT_DATA_VOLUME` | はい | Minecraftの実際のDockerボリューム名 |
| `MINECRAFT_RCON_PASSWORD` | はい | Minecraft側と同じ強いRCONパスワード |
| `MINECRAFT_CONTROL_NETWORK` | いいえ | 事前作成した内部Dockerネットワーク名 |
| `FLOODGATE_USERNAME_PREFIX` | いいえ | Bedrock名のprefix。既定値は `.` |
| `VOICEVOX_TTS_API_URL` | 読み上げ時 | mc-botから到達可能なVOICEVOX内部TTS API URL |
| `VOICEVOX_TTS_API_TOKEN` | 読み上げ時 | VOICEVOX側と共有するBearerトークン |
| `VOICEVOX_SPEAKER_ID` | いいえ | 話者ID。既定値は小夜/SAYOの `46` |
| `VOICEVOX_SPEED` | いいえ | 読み上げ速度。既定値は `1.0` |
| `MINECRAFT_INTEGRATION_SYNC_SECONDS` | いいえ | level-bot交換・VC状態の同期間隔。既定値は30秒。Minecraft XP量は照会しない |
| `MINECRAFT_BONUSES_ENABLED` | いいえ | Minecraft由来の全ボーナスを有効化する。既定値は`true`。診断時は`false`で一括停止できる |

`MINECRAFT_DATA_VOLUME` はBotの動作設定ではなく、コンテナ起動前に外部ボリュームを
解決するDocker Compose側のインフラ設定です。Coolifyの変数一覧に表示されるよう、
サービスの `environment` にも必須変数として宣言しています。通知先はDiscordコマンドで
設定します。

Minecraftアプリを作り直した場合は、ホスト上の `docker volume ls` で新しい名前を確認し、
`MINECRAFT_DATA_VOLUME` を上書きします。Raw Compose Deploymentにより、この外部
ボリュームをmc-bot側の `/minecraft` へ読み取り専用で直接マウントします。

Minecraftアプリとmc-botアプリをデプロイする前に、Dockerホストで制御用ネットワークを
一度だけ作成します。VOICEVOXが別ホストの場合、VOICEVOX用Dockerネットワークは不要です。

```sh
docker network create minecraft-control
```

両アプリを同じネットワークへ接続します。RCONのTCP/25575はホストへ公開しません。

Docker Compose location:

```text
/docker-compose.yml
```

mc-bot自身のカーソルとDiscord上で行った設定は `mc-bot-data` ボリュームへ保存され、
再デプロイ後も維持されます。未設定時はコマンド受付を、設定後はDiscord接続、
Minecraftログ、転送タスクを監視するDockerヘルスチェックも有効です。異常が継続した
場合は `restart: unless-stopped` により再起動されます。

## ローカルでの検証

Python 3.14とuvがある場合:

```sh
uv sync --locked
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

Dockerでイメージ全体を検証する場合:

```sh
docker build -t mc-bot:local .
```

通知はイベント種別ごとに色分けしたDiscord Embedです。紐付け済みプレイヤーは
`**.hoge (<@DiscordユーザーID>)**` の形式で、クリック可能なDiscordメンションを
表示します。通常の通知はEmbedと `AllowedMentions.none()` で送り、Minecraftチャットからの
`@everyone`・ロール通知は発生しません。アイテムガチャ結果だけは抽選者本人を通常投稿で
メンションしますが、許可対象をそのユーザーIDだけに限定します。
