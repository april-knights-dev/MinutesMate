# MinutesMate

このプロジェクトはSlackのボットです。Slack上に添付された音声ファイルからwhisperで書き起こしを行い、GPT3.5でサマリーを作成する機能を持っています。

## 環境設定

`.env`ファイルに以下の環境変数を設定してください。

- `SLACK_BOT_TOKEN`: Slackボットのトークン
- `SLACK_SIGNING_SECRET`: Slackの署名シークレット
- `SLACK_APP_TOKEN`: Slackアプリのトークン
- `OPENAI_API_KEY`: OpenAI APIのキー
- `SLACK_USER_TOKEN`: Slackユーザーのトークン

## コードの実行

以下のコマンドを使ってコードを実行してください。

```shell
python ファイル名.py
```

## 使用方法

### メッセージショートカット

- 音声ファイルを添付したメッセージのメニューからショートカットを起動させます。

### ライブラリの使用

```python
import openai
import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
```

このコードでは、Slackのボットを作成するために必要なライブラリがインポートされています。

## 貢献

このプロジェクトへの貢献は、プルリクエストを送ってください。プルリクエストの前に、以下の手順を実行してください。

1. リポジトリをフォークします。
2. ローカルマシンで作業用ブランチを作成します。
3. 変更を加え、テストを実行します。
4. プッシュして、プルリクエストを作成します。

## ライセンス

このプロジェクトは、[MIT ライセンス](LICENSE)の下で提供されています。
