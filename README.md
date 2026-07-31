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
| Gateway Intents | Guilds、Guild Members |
| 対応Minecraftログ | チャット、進捗・達成、参加、退出 |
| 通知方向 | Minecraftログ通知、Discordからwhitelist管理 |
| 設定方法 | Discordスラッシュコマンド |
| 秘密情報 | `DISCORD_TOKEN`、`MINECRAFT_RCON_PASSWORD` |
| 永続データ | `/data/settings.json`、`/data/cursor.json`、`/data/accounts.db` |
| CPU上限 | 0.25 CPU |
| メモリ上限 | 128 MiB |
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

```text
client.jar: 4e618f09a0c649dde3fdf829df443ce0b8831e65
ja_jp.json: 82ae51a68e114943fd95cc870643317dc02fe5e4
```

## 構成

```text
Minecraftコンテナ
  └─ minecraft-crossplay-data:/data
                    │ 読み取り専用で共有
                    ▼
mc-botコンテナ
  ├─ /minecraft/logs/latest.log を監視
  ├─ /minecraft/whitelist.json から既存登録を保護対象として取り込み
  ├─ /data/cursor.json に送信済み位置を保存
  ├─ /data/settings.json にDiscord上で行った設定を保存
  ├─ /data/accounts.db にDiscordとMinecraftアカウントの紐付けを保存
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
/mc-config show
```

一般ユーザーは参加パネルのボタンと入力画面だけで、Java版・Bedrock版を問わず
複数アカウントを登録できます。管理パネルでは代理登録と、既存whitelistをDiscord
アカウントへ紐付けできます。取り込まれた既存登録は初期状態で保護され、Botが
自動削除することはありません。

管理パネルの「サーバー操作」は、「サーバーの管理」権限を持つ管理者だけが
操作できるエフェメラルUIです。Minecraftを再起動せず、RCON経由で次の
操作ができます。

- オンラインプレイヤーの取得
- オンラインプレイヤーを選択し、理由と最終確認付きでキック
- JSONエスケープ済みの `tellraw` によるサーバー告知
- Whitelistを15分、30分、1時間停止し、期限後に自動再開
- Whitelistの手動再開
- 天候を晴れ・雨・雷雨へ変更、時刻を朝・夜へ変更
- sparkのローカルヘルスレポートによるTPS、MSPT、CPU、メモリ状況の確認

Whitelistの再開予定時刻は永続化され、mc-botの再起動後も引き継がれます。
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

Botトークンは秘密情報として扱い、Git、README、Issue、ログへ貼らないでください。

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

`MINECRAFT_DATA_VOLUME` はBotの動作設定ではなく、コンテナ起動前に外部ボリュームを
解決するDocker Compose側のインフラ設定です。Coolifyの変数一覧に表示されるよう、
サービスの `environment` にも必須変数として宣言しています。通知先はDiscordコマンドで
設定します。

Minecraftアプリを作り直した場合は、ホスト上の `docker volume ls` で新しい名前を確認し、
`MINECRAFT_DATA_VOLUME` を上書きします。Raw Compose Deploymentにより、この外部
ボリュームをmc-bot側の `/minecraft` へ読み取り専用で直接マウントします。

Minecraftアプリとmc-botアプリをデプロイする前に、Dockerホストで制御用ネットワークを
一度だけ作成します。

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
表示します。通知はEmbedで送り、さらに `AllowedMentions.none()` を指定しているため、
この表示によるメンション通知や、Minecraftチャットからの `@everyone`・ロール通知は
発生しません。
