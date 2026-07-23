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
| Gateway Intents | Guildsのみ。Privileged Gateway Intents不使用 |
| 対応Minecraftログ | チャット、進捗・達成、参加、退出 |
| 通知方向 | MinecraftからDiscordへの一方向 |
| 設定方法 | Discordスラッシュコマンド |
| 秘密情報 | `DISCORD_TOKEN` 環境変数 |
| 永続データ | `/data/settings.json`、`/data/cursor.json` |

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
  ├─ /data/cursor.json に送信済み位置を保存
  ├─ /data/settings.json にDiscord上で行った設定を保存
  └─ Discord Gateway → 指定チャンネル
```

初回起動時は既存ログを再送せず、ファイル末尾から監視します。Botの再起動後は保存した
位置から再開し、Minecraft再起動による `latest.log` のローテーションも検出します。
カーソルはDiscordへの送信成功後にだけ更新します。送信に失敗した場合は同じログを
指数バックオフ付きで再試行するため、障害時に通知を読み飛ばしません。送信成功直後、
カーソル保存前にプロセスが停止した場合は、欠落を避ける代わりに同じ通知が再送されます。

## Discord側の準備

1. [Discord Developer Portal](https://discord.com/developers/applications) でApplicationとBotを作ります。
2. OAuth2のスコープ `bot` と `applications.commands` を付けて、Botを対象サーバーへ招待します。
3. 通知先チャンネルで「チャンネルを見る」「メッセージを送信」の権限を与えます。
4. mc-botをデプロイした後、「サーバーの管理」権限を持つユーザーがDiscordで次のコマンドを実行します。

```text
/mc-config channel              今いるチャンネルを通知先にする
/mc-config channel channel:#ログ 任意のテキストチャンネルを通知先にする
/mc-config label name:Chill Cafe 通知に表示するサーバー名を変更する
/mc-config show                 現在の設定と転送状態を確認する
```

コマンドの応答と設定表示は実行者だけに見えます。コマンドはグローバル登録のため、
Botの初回起動直後はDiscordへの反映に少し時間がかかる場合があります。通知先が未設定、
または古い通知先が利用不能でもBotは起動し続けるため、コマンドから修正できます。

Botトークンは秘密情報として扱い、Git、README、Issue、ログへ貼らないでください。

## Coolifyへのデプロイ

このリポジトリをMinecraftとは別のDocker Composeアプリとして登録します。同じ
`usapo-server_2` に配置し、次の変数をCoolifyで設定します。

| 変数 | 必須 | 値 |
| --- | --- | --- |
| `DISCORD_TOKEN` | はい | Discord Botトークン。Secret扱いにする |
| `MINECRAFT_DATA_VOLUME` | はい | Minecraftの実際のDockerボリューム名 |

`MINECRAFT_DATA_VOLUME` はBotの動作設定ではなく、コンテナ起動前に外部ボリュームを
解決するDocker Compose側のインフラ設定です。Coolifyの変数一覧に表示されるよう、
サービスの `environment` にも必須変数として宣言しています。通知先と表示名は
Discordコマンドで設定します。

Minecraftアプリを作り直した場合は、ホスト上の `docker volume ls` で新しい名前を確認し、
`MINECRAFT_DATA_VOLUME` を上書きします。ボリュームはmc-bot側では `/minecraft` へ
読み取り専用でマウントされます。

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

通知ではDiscordのメンションを無効化しているため、Minecraftチャットから
`@everyone` やロールを通知することはできません。
