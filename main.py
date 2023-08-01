import io
import os
import secrets
import shutil
import time
from datetime import datetime
from os.path import dirname, join

import moviepy.editor as mp
import openai
import requests
from dotenv import load_dotenv
from pydub import AudioSegment
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
        response = client.files_info(file=file_id, file_type=file_type)
        file_url_private = response["file"]["url_private"]

        # ファイル情報を取得する
        if file_id and file_type:
            print("file type = " + file_type)  # for debug
            if file_type == "mp3" or file_type == "m4a" or file_type == "wav":
                # ファイルをダウンロードする
                file_path = download_from_slack(
                    file_url_private, os.environ.get(
                        "SLACK_BOT_TOKEN"), file_type,
                    message_id=message_id
                )

                create_summary(file_id, file_type, file_path,
                               body["channel"]["id"], message_id)

            elif file_type == "mp4" or file_type == "mpeg" or file_type == "mkv" or file_type == "webm":
                # slackに通知
                client.chat_postMessage(
                    channel=body["channel"]["id"], text="動画ファイルをダウンロードしてmp3に変換します。", thread_ts=message_id)
                # mp4ファイルをmp3に変換する
                mp4_file_path = download_from_slack(
                    file_url_private, os.environ.get(
                        "SLACK_BOT_TOKEN"), file_type,
                    message_id=message_id
                )
                # mp4をmp3に変換する
                mp3_file_path = convert_mp4_to_mp3(mp4_file_path)
                # mp3変換が終わったことをslackに通知する
                client.chat_postMessage(
                    channel=body["channel"]["id"], text="動画ファイルの変換が終了しました。", thread_ts=message_id)
                # 変換したmp3で書き起こしを行う
                create_summary(file_id, "mp3", mp3_file_path,
                               body["channel"]["id"], message_id)

            else:
                return

        # フォルダをファイルごと削除する
        remove_directory_contents('/output/' + message_id)
        remove_directory_contents('/download/' + message_id)

    except Exception as e:
        print("Error:", e)
        # slackに通知
        client.chat_postMessage(
            channel=body["channel"]["id"], text="エラーが発生しました。", thread_ts=message_id)
        # フォルダをファイルごと削除する
        remove_directory_contents('/output/' + message_id)
        remove_directory_contents('/download/' + message_id)


def create_summary(file_id, file_type, file_path, channel, message_id):
    response = client.files_info(file=file_id, file_type=file_type)
    post_response = client.chat_postMessage(
        channel=channel, text="書き起こしを開始します。", thread_ts=message_id)
    progress_message_ts = post_response.data['ts']


    # mp3ファイルを分割する
    output_folder = "./output/" + message_id
    # outputフォルダが無ければ作る
    if not os.path.exists(output_folder):
        if not os.path.exists("./output"):
            os.mkdir("./output")
        os.mkdir(output_folder)
    interval_ms = 480_000  # 60秒 = 60_000ミリ秒
    chat_response = client.chat_update( channel=channel, ts=progress_message_ts, text="音声ファイルを分割します。" )
    progress_message_ts = chat_response.data['ts']
    mp3_file_path_list = split_audio(file_path, interval_ms, output_folder)

    transcription_list = []
    transcript_count = 0
    transcription_total_count = len(mp3_file_path_list)
    for mp3_file_path in mp3_file_path_list:
        chat_response = client.chat_update( channel=channel, ts=progress_message_ts, text="書き起こしを行っています。" + str(transcript_count) + "/" + str(transcription_total_count) + "回目" )
        progress_message_ts = chat_response.data['ts']
        transcription = transcribe_audio(mp3_file_path)
        transcription_list.append(transcription)

    pre_summary = ""
    # 分割したファイル数をslackに通知する
    # slackに分割した要約の作業がどのぐらい進んだか進捗を伝える
    count = 0
    total_count = len(transcription_list)
    chat_response = client.chat_update(channel=channel, text="書き起こしを終了しました。これから要約します。要約は"
                            + str(total_count)
                            + "回に分けておこなうため、"
                            + str(total_count) + "分ほどかかります。", ts=progress_message_ts)
    progress_message_ts = chat_response.data['ts']

    for transcription_part in transcription_list:
        prompt = """
        あなたは、プロの要約作成者です。
        以下の制約条件、内容を元に要点をまとめてください。

        # 制約条件
        ・要点をまとめ、簡潔に書いて下さい。
        ・誤字・脱字があるため、話の内容を予測して置き換えてください。

        # 内容
        """ + transcription_part

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo-16k",
            messages=[
                {'role': 'user', 'content': prompt}
            ],
            temperature=0.0,
            timeout=60,
        )

        pre_summary += response['choices'][0]['message']['content']
        # 分割した回数のうちどのぐらい終わったかをslackに通知する
        count += 1
        chat_response = client.chat_update(channel=channel, text="要約作業が"
                                + str(count)
                                + "回終了しました。あと"
                                + str(total_count - count)
                                + "回です。", ts=progress_message_ts)
        progress_message_ts = chat_response.data['ts']
        if total_count - count >= 1:
            time.sleep(60)

    # 途中経過をスレッドに投稿する
    upload_to_slack(channel, pre_summary, file_path,
                    "書き起こし&要約が終わりました。議事録はもう少し待ってね", message_id)

    # chatgpt apiを使ってサマリーする
    system_template = """会議の書き起こしが渡されます。
        この会議のサマリーをMarkdown形式で作成してください。
        サマリーは、以下のような形式で書いてください。

        - 会議の目的
        - 会議の内容
        - 会議の結果
        - 次回の会議までのタスク

        """
    prompt = """
    以下の制約条件、内容を元に要点をまとめ、議事録を作成してください。

    # 制約条件
    ・誤字・脱字があるため、話の内容を予測して置き換えてください。
    ・No repeat, no remarks, only results, in Japanese
    # 内容
    """ + pre_summary
    # try_count回だけリトライする
    try_count = 3
    for i in range(try_count):
        model = "gpt-4-0613"
        if i > 1:
            model = "gpt-3.5-turbo-16k"
            sendMessage(
                channel, "gpt-4-0613が使えないのでgpt-3.5-turbo-16kを使います。", message_id)
        try:

            response = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': system_template},
                    {'role': 'user', 'content': prompt}
                ],
                temperature=0.0,
                timeout=60
            )
            upload_to_slack(
                channel, response['choices'][0]['message']['content'], "", "要約が終わりました。", message_id)
            # uploadにうまくいったらループを抜ける
            break
        except openai.error.APIError:
            print("APIError")
            time.sleep(1)    # このエラーは1秒待機で十分安定
        except openai.error.InvalidRequestError:
            pass     # 待機不要
        except (openai.error.RateLimitError,
                openai.error.APIConnectionError):
            sendMessage(channel, "RateLimitError: 10秒待機してリトライします。", message_id)
            time.sleep(10)    # 要注意。ある程度待った方が良い。
        except Exception as e:
            print("Error:", e)
            sendMessage(channel, e, message_id)
    sendMessage(
        channel, "処理を終了します。(これ以上リトライしません。うまくいってない場合はもう一度実行してください。)", message_id)


def sendMessage(channel: str, message: str, thread_ts: str = None):
    client.chat_postMessage(channel=channel, text=message, thread_ts=thread_ts)


def download_from_slack(download_url: str, auth: str, filetype: str, message_id) -> str:
    """Slackから音声ファイルダウンロードして保存し、保存したパスを返す。

    Args:
        download_url (str): ファイルのURL
        auth (str): ファイルの閲覧に必要なSlackの認証キー

    Returns:
        str: ファイルが保存されているパス
    """
    print(download_url)

    # download_urlからファイルをローカルにダウンロード
    download_folder = "./download/" + message_id
    # downloadフォルダが無ければ作る
    if not os.path.exists(download_folder):
        if not os.path.exists("./download"):
            os.mkdir("./download")
        os.mkdir(download_folder)
    # 同時実行に備えてファイル名はランダムにする
    random = secrets.token_hex(8)
    filename = f"{random}.{filetype}"

    headers = {'Authorization': 'Bearer '+os.environ.get("SLACK_USER_TOKEN")}
    r = requests.get(download_url, stream=True, headers=headers)
    with open(download_folder + "/" + filename, 'wb') as f:
        # ファイルを保存する
        f.write(r.content)
    return download_folder + "/" + filename


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

# mp4をmp3に変換し、mp3のファイルパスを返す


def convert_mp4_to_mp3(mp4_file_path):
    mp3_file_path = os.path.splitext(mp4_file_path)[0] + '.mp3'
    audio = mp.AudioFileClip(mp4_file_path)
    audio.write_audiofile(mp3_file_path)
    return mp3_file_path

# mp3ファイルを分割し、保存し、ファイルリストを返す


def split_audio(mp3_file_path, interval_ms, output_folder):
    audio = AudioSegment.from_file(mp3_file_path)
    file_name, ext = os.path.splitext(os.path.basename(mp3_file_path))

    mp3_file_path_list = []

    n_splits = len(audio) // interval_ms
    for i in range(n_splits + 1):
        # 開始、終了時間
        start = i * interval_ms
        end = (i + 1) * interval_ms
        # 分割
        split = audio[start:end]
        # 出力ファイル名
        output_file_name = output_folder + "/" + \
            file_name + "_" + str(i) + ".mp3"
        # 出力
        split.export(output_file_name, format="mp3")

        # 音声ファイルリストに追加
        mp3_file_path_list.append(output_file_name)

    # 音声ファイルリストを出力
    return mp3_file_path_list

# mp3ファイルを文字起こしし、テキストを返す


def transcribe_audio(mp3_file_path):
    with open(mp3_file_path, 'rb') as audio_file:
        transcription = openai.Audio.transcribe(
            "whisper-1", audio_file, language='ja')

    return transcription.text

# テキストを保存


def save_text_to_file(text, output_file_path):
    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(text)


def remove_directory_contents(directory):
    # directoryから絶対パスを取得して変換する
    directory = os.path.abspath(directory)
    # directoryの中身のファイルリストを作成する
    file_list = os.listdir("." + directory)
    
    # ファイルを削除する
    for file in file_list:
        file_path = os.path.join(directory, file)
        os.remove("." + file_path)

    # ディレクトリを削除する
    os.rmdir("." + directory)


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
                messages.append(
                    {"role": "user", "content": user_first_template})

            messages.append({"role": "user", "content": segment})

            if i == len(segments) - 1:
                messages.append({"role": "user", "content": "作業してください"})
            else:
                messages.append(
                    {"role": "assistant", "content": "次の入力を待っています"})

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
