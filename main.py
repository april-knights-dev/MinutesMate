import io
import os
import secrets
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
          signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
          )
client = WebClient(os.environ["SLACK_BOT_TOKEN"])
openai.api_key = os.environ["OPENAI_API_KEY"]


# メッセージショートカットのハンドラー
@app.shortcut("create_summary")
def handle_shortcut(ack, body, logger):
    try:
        ack()
        logger.info(body)

        message_id = body['message']['ts']

        # このメッセージIDからファイルを取得する
        response = client.conversations_replies(
            channel=body['channel']['id'], ts=message_id)
        elements = response["messages"][0]["files"]

        # typeがfileである要素からfile_id, file_typeを取得する
        file_id = None
        file_type = None
        for element in elements:
            file_id = element["id"]
            file_type = element["filetype"]
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
                upload_to_slack(channel, output, filepath, "書き起こしが終わりました。要約はもう少し待ってね", message_id)

                 # chatgpt apiを使ってサマリーする
                system_template = """会議の書き起こしが渡されます。
                この会議のサマリーをMarkdown形式で作成してください。
                サマリーは、以下のような形式で書いてください。

                - 会議の目的
                - 会議の内容
                - 会議の結果
                - 次回の会議までのタスク

                """
                user_first_template = """これから文章を渡すので、その内容を要約してください。ただし文章は分割してあるので「作業してください」と伝えるまで、あなたは作業を始めず、代わりに「次の入力を待っています」と回答してください。"""
                full_text = transcript.text  # テキスト全体を取得
                # system_templateのトークンをテキストから計算
                system_template_token_count = len(system_template.split())
                # system_template_token_countを足して10000になるように分割
                split_count = 2000 - system_template_token_count
                segments = [full_text[i:i+split_count]
                            for i in range(0, len(full_text), split_count)]
                messages = []
                for i, segment in enumerate(segments):
                    if i == 0:
                        messages.append({"role": "system", "content": system_template})
                        messages.append({"role": "user", "content": user_first_template})
                        messages.append({"role": "assistant", "content": "次の入力を待っています"})

                    messages.append({"role": "user", "content": segment})

                    if i == len(segments) - 1:
                        messages.append({"role": "user", "content": "作業してください"})
                    else:
                        messages.append({"role": "assistant", "content": "次の入力を待っています"})

                final_text = ""
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo-16k-0613",
                    messages=messages,
                    temperature=0.7
                )
                generated_text = response['choices'][0]['message']['content']
                final_text += generated_text

                print(final_text)

                upload_to_slack(channel, final_text, "", "要約が終わりました。", message_id)
            else:
                return

    except Exception as e:
        print("Error:", e)


def whisper(filepath):
    print(filepath)
    if os.path.getsize(filepath) > 26000000:
        output = "ファイルサイズオーバー。ファイルサイズは26MBにしてください。"
        return output
    else:
        language = "ja"
        audio_file = open(filepath, "rb")
        transcript = openai.Audio.transcribe(
            "whisper-1", audio_file, language=language)
        os.remove(filepath)
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
    # 同時実行に備えてファイル名はランダムにする
    random = secrets.token_hex(8)
    filename = f"{random}.{filetype}"

    headers = {'Authorization': 'Bearer '+os.environ.get("SLACK_USER_TOKEN")}
    r = requests.get(download_url, stream=True, headers=headers)
    with open(filename, 'wb') as f:
        # ファイルを保存する
        f.write(r.content)
    return filename


def upload_to_slack(channel: str, transcript: str, title: str, summary: str = None, thread_ts: str = None):
    # transcriptをtemp.textファイルに書き込む
    with open("temp.txt", "w") as f:
        f.write(transcript)

    try:
        client.files_upload_v2(
            channels=channel,
            thread_ts=thread_ts,
            file="temp.txt",
            title=title + ".txt",
            initial_comment=summary
        )
    except Exception as e:
        print("Error uploading file: {}".format(e))
    finally:
        # temp.txtを削除
        os.remove("temp.txt")


@app.event("message")
def handle_message_events(body, logger):
    try:
        logger.info(body)

        message_id = body['event']['ts']
        full_text = body['event']['text']
        channel = body['event']['channel']

        # chatgpt apiを使ってサマリーする
        system_template = """会議の書き起こしが渡されます。
        この会議のサマリーをMarkdown形式で作成してください。
        サマリーは、以下のような形式で書いてください。

        - 会議の目的
        - 会議の内容
        - 会議の結果
        - 次回の会議までのタスク

        """
        user_first_template = """これから文章を渡すので、その内容を要約してください。ただし文章は分割してあるので「作業してください」と伝えるまで、あなたは作業を始めず、代わりに「次の入力を待っています」と回答してください。"""
        # system_templateのトークンをテキストから計算
        system_template_token_count = len(system_template.split())
        # system_template_token_countを足して10000になるように分割
        split_count = 2000 - system_template_token_count
        segments = [full_text[i:i+split_count]
                    for i in range(0, len(full_text), split_count)]
        messages = []
        for i, segment in enumerate(segments):
            if i == 0:
                messages.append({"role": "system", "content": system_template})
                messages.append({"role": "user", "content": user_first_template})

            messages.append({"role": "user", "content": segment})

            if i == len(segments) - 1:
                messages.append({"role": "user", "content": "作業してください"})
            else:
                messages.append({"role": "assistant", "content": "次の入力を待っています"})

        final_text = ""
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo-16k-0613",
            messages=messages,
            temperature=0.7
        )
        generated_text = response['choices'][0]['message']['content']
        final_text += generated_text

        print(final_text)

        upload_to_slack(channel, final_text, "", "要約が終わりました。", message_id)


    except Exception as e:
        print("Error:", e)

# アプリを起動します
if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()