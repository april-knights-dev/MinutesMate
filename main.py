import io
import os
from datetime import datetime
from os.path import dirname, join

import openai
import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

load_dotenv(verbose=True)
dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)

# ボットトークンとソケットモードハンドラーを使ってアプリを初期化します
app = App(token=os.environ.get("SLACK_BOT_TOKEN"),
                signing_secret=os.environ.get("SLACK_SIGNING_SECRET"))

#
# Socket Mode
#
from slack_bolt.adapter.socket_mode import SocketModeHandler

socket_handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
# Use connect() method as start() blocks the current thread
socket_handler.connect()


client = WebClient(os.environ["SLACK_BOT_TOKEN"])
openai.api_key = os.environ["OPENAI_API_KEY"]


# イベント API
@app.message("hello")
def handle_messge_evnts(message, say):
    say(f"こんにちは <@{message['user']}> さん！")


# メッセージショートカットのハンドラー
@app.shortcut("create_summary")
def handle_shortcut(ack, body, logger):
    ack()
    logger.info(body)

    message_id = body['message']['ts']

    try:

        # このメッセージIDからファイルを取得する
        response = client.conversations_replies(
            channel=body['channel']['id'], ts=message_id)
        elements = response["messages"][0]["files"]

        # typeがfileである要素からfile_id, file_typeを取得する
        file_id = None
        file_type = None
        file_name = None
        for element in elements:
            file_id = element["id"]
            file_type = element["filetype"]
            file_name = element["name"]
            break

        # ファイル情報を取得する
        if file_id and file_type:
            print("file type = " + file_type)  # for debug
            if file_type == "mp3" or file_type == "mp4" or file_type == "mpeg" or file_type == "m4a" or \
                    file_type == "mpga" or file_type == "webm" or file_type == "wav":
                response = client.files_info(file=file_id, file_type=file_type)
                channel = body["channel"]["id"]
                client.chat_postMessage(channel=channel, text="書き起こしを開始します。", thread_ts=message_id)

                file_url_private = response["file"]["url_private"]
                print(file_url_private)

                # ファイルをダウンロードする
                filepath = download_from_slack(
                    file_url_private, os.environ.get(
                        "SLACK_BOT_TOKEN"), file_type
                )
                transcript = whisper(filepath)
                output = f"{filepath}\n----\n```"
                output += transcript.text + "\n```"

                print(output)
                # 途中経過をスレッドに投稿する
                client.chat_postMessage(channel=channel, text="書き起こしが終わりました。もう少し待ってね", thread_ts=message_id)

                # chatgpt apiを使ってサマリーする
                system_template = """会議の書き起こしが渡されます。
                この会議のサマリーをMarkdown形式で作成してください。サマリーは、以下のような形式で書いてください。

                - 会議の目的
                - 会議の内容
                - 会議の結果
                - 次回の会議までのタスク
                """
                full_text = transcript.text  # テキスト全体を取得
                # system_tempalteのトークンを計算
                system_template_token_count = len(system_template.split())
                # system_template_token_countを足して10000になるように分割
                segments = [full_text[i:i+10000-system_template_token_count]
                            for i in range(0, len(full_text), 10000-system_template_token_count)]
                messages = []
                for i, segment in enumerate(segments):
                    if i == 0:
                        messages.append({"role": "system", "content": system_template})
                    messages.append({"role": "user", "content": segment})

                final_text = ""
                for message in messages:
                    response = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo-16k-0613",
                        messages=[message],
                        max_tokens=16000,
                        temperature=0.9
                    )
                    generated_text = response['choices'][0]['message']['content']
                    final_text += generated_text

                print(final_text)

                upload_to_slack(channel, full_text, file_name, final_text, message_id)

            else:
                return

    except Exception as e:
        print("Error:", e)
        # errorをスレッドに投稿する
        client.chat_postMessage(channel=channel, text=f"エラーが発生しました。\n```{e}```", thread_ts=message_id)
    finally:
        os.remove(filepath)

def whisper(filepath):
    print(filepath)
    if os.path.getsize(filepath) > 26000000:
        output = "ファイルサイズオーバー。ファイルサイズは26MBにしてください。"
        return output
    else:
        try:
            language = "ja"
            with open(filepath, "rb") as audio_file:
                transcript = openai.Audio.transcribe(
                    "whisper-1", audio_file, language=language)
            audio_file.close()
        except Exception as e:
            print(e)
            transcript.text = "書き起こしに失敗しました。"
        finally:
            return transcript


def download_from_slack(download_url: str, auth: str, filetype: str) -> str:
    """Slackから音声ファイルダウンロードして保存し、保存したパスを返す。

    Args:
        download_url (str): ファイルのURL
        auth (str): ファイルの閲覧に必要なSlackの認証キー

    Returns:
        str: ファイルが保存されているパス
    """
    print(download_url)

    # download_urlからファイルをローカルにダウンロード
    filename = 'temp.'+filetype
    headers = {'Authorization': 'Bearer '+os.environ.get("SLACK_USER_TOKEN")}
    r = requests.get(download_url, stream=True, headers=headers)
    with open(filename, 'wb') as f:
        # ファイルを保存する
        f.write(r.content)
    f.close()
    return filename


def upload_to_slack(channel: str, transcript: str, title: str, summary: str = None, thread_ts: str = None):
    # transcriptをtemp.textファイルに書き込む
    with open("temp.txt", "w") as f:
        f.write(transcript)
    f.close()

    try:
        with open("temp.txt", "rb") as f:
            client.files_upload_v2(
                channels=channel,
                thread_ts=thread_ts,
                file=f,
                filetype="text",
                title=title + ".txt",
                initial_comment=summary
            )
        f.close()
    except Exception as e:
        print("Error uploading file: {}".format(e))
    finally:
        # temp.txtを削除
        os.remove("temp.txt")

# アプリを起動します
if __name__ == "__main__":
    socket_handler.start()
