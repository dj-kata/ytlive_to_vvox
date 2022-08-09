#!/usr/bin/python3
import PySimpleGUI as sg
from multiprocessing.dummy import freeze_support
import requests, json, wave, sys, time, pytchat, re, csv, unicodedata
import winsound
import threading
from multiprocessing import Process, Pipe
from apiclient.discovery import build
import webbrowser, urllib

# global
lock = threading.Lock()
msg_queue = []
stop_thread = False
youtube_api_key = ''

convert_dict = []
ng_dict = []
with open('dict.csv', encoding='shift_jis') as f:
    reader = csv.reader(f)
    for r in reader:
        #print(r)
        if len(r) > 0:
            if (r[0][0] not in  ('/', '#', '\n')):
                convert_dict.append(r)
with open('ngword.csv', encoding='shift_jis') as f:
    reader = csv.reader(f)
    for r in reader:
        if len(r) > 0:
            if (r[0][0] not in  ('/', '#', '\n')):
                ng_dict.append(r[0])
#print(convert_dict)
#print(ng_dict)

### 日本語でも文字揃えをする
def align_left(digit, msg):
    #tmp = ''
    for c in msg:
        #tmp += unicodedata.east_asian_width(c)+' '
        if unicodedata.east_asian_width(c) in ('F', 'W', 'A'):
            digit -= 2
        else:
            digit -= 1
    #print(digit, tmp)
    return msg + ' '*digit

### 音声合成実行及び再生
def generate_wav(text, speaker=8, filepath='./audio.wav'):
    #print('generate_wav ->',text)
    host = 'localhost'
    port=50021
    try:
        params = {'text':text, 'speaker':speaker}
        res1 = requests.post(f"http://{host}:{port}/audio_query",params=params)
        headers= {'Content-Type':'application/json',}
        res2 = requests.post(f"http://{host}:{port}/synthesis",headers=headers,params=params,data=json.dumps(res1.json()))

        wf=wave.open(filepath, 'wb')
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(res2.content)
        wf.close() 

        winsound.PlaySound(filepath, winsound.SND_FILENAME)
    except requests.exceptions.ConnectionError:
        print('## ERROR！！！ VOICEVOXが起動していません')

def write_series_xml(title, viewers):
    dat = re.findall('\S+', title)
    series = ''
    for dd in dat:
        if "#" in dd:
            series = dd
    #print(f'series: {series}')
    #print(f'title: {title}')
    if series == '':
        series = '#???'
    f=open('series.xml', 'w')
    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<Items>
    <series>{series}</series>
    <viewers>{viewers}</viewers>
</Items>''')
    f.close()

### 1. ライブチャットサーバから書き込みを受信するスレッド
def parse_comment(window, chat): # youtube liveのコメント受信及びpipeへのsend
    print(f"## info コメント受信スレッド開始")
    comment_data = []
    while 1:
        if (not chat.is_alive()):
            window.write_event_value('-THREAD-', 'コメントサーバが落ちたかも?')
            print('## warning! コメントサーバが落ちたかも?')
            break # アーカイブに対して実行する場合はコメントアウトする?
        for d in chat.get().sync_items():
            print(f"{align_left(16, d.author.name)} {d.message}") # ターミナルへの表示はここでやる。デバッグ時に早すぎる時は消す。
            senddata = f"{d.author.name}さん {d.message}"
            window.write_event_value('-THREAD-', senddata)
            #comment_data.append([d.author.name, d.message])
            #window['comments'].update(values=comment_data)
    window.write_event_value('-RESET_PYTCHAT-', 'メインスレッド停止') # メイン側でresetできるようにイベント送出

### 2. 書き込みを受けて動くスレッド。辞書及びNG処理後の文字列をFIFOにpush
def proc_comment(msg): # pipeのreceive及びその先のコメント処理
    has_ngword = False
    ### 辞書を使った単語の変換はここでやる
    for dd in convert_dict:
        tmp_msg = re.sub(dd[0].upper(), dd[1], msg)
        #if msg != tmp_msg:
        #    print(f"dd={dd}, before:{msg}, after:{tmp_msg}")
        msg = tmp_msg
    mod = re.sub(':[a-zA-Z0-9_]+:', '', msg) # その他の絵文字は消す
    for ngw in ng_dict:
        if ngw in msg:
            has_ngword = True
            print(f"{ngw}が引っかかりました。読み上げをスキップします。")
    if not has_ngword:
        lock.acquire()
        global msg_queue
        msg_queue.append(mod)
        #print(f'{mod}をキューに追加')
        lock.release()
    #time.sleep(0.5)

### 3. FIFOからメッセージをpop()して音声合成を実行するスレッド
def yomiage():
    print('読み上げ用スレッド開始')
    while 1:
        lock.acquire()
        global msg_queue, stop_thread
        if stop_thread:
            print('yomiage end')
            break
        ### FIFOに複数登録されている場合、メッセージをつなげる
        msg = ''
        #print(f"len(msg_queue) = {len(msg_queue)}") # debug
        while len(msg_queue) > 0:
            tmp = msg_queue.pop(0)
            msg += tmp + ' '
        #print(f"pop完了。(len={len(msg_queue)}), msg={msg}") # debug
        if msg != '':
            generate_wav(msg)
        lock.release()
        time.sleep(0.1)

### 4. 同接数を取得
def get_viewers(window, title, liveid):
    yt = build('youtube', 'v3', developerKey=youtube_api_key)
    url = 'https://www.googleapis.com/youtube/v3/videos'

    while 1:
        params = {'key': youtube_api_key, 'id': liveid, 'part': 'liveStreamingDetails'}
        data   = requests.get(url, params=params).json()
        try:
            liveStreamingDetails = data['items'][0]['liveStreamingDetails']
            viewers = liveStreamingDetails['concurrentViewers']
            write_series_xml(title, viewers)
            window.write_event_value('-VIEWERS-', viewers)
        except Exception as e: # API制限超過時への対策
            sleep_time = 300
            print(f'API制限を超過しています。{sleep_time//60}分待ちます。')
            time.sleep(sleep_time)
            #window.write_event_value('-VIEWERS-', '-')
        time.sleep(10)

def gui(argv):
    sg.theme('GrayGrayGray')
    init_url = ''
    if len(argv) > 0:
        init_url = argv[0]
    FONT = ('Meiryo',10)
    titlebar = []
    layout = [titlebar,
        [sg.Text('配信URL:', font=FONT), sg.InputText(init_url, key='youtube_url', font=FONT), sg.Button('取得', key='get_comment'), sg.Button('Tweet', key='Tweet'),sg.Text('📌', enable_events=True, k='-PIN-',  font='_ 12', pad=(40,0),  metadata=False,
                            text_color=sg.theme_background_color(), background_color=sg.theme_text_color())],
        [sg.Text('title:', font=FONT), sg.Text('', font=FONT, key='title')],
        [sg.Text('視聴者数:', font=FONT), sg.Text('0', font=FONT, key='viewers')],
        [sg.Output(size=(76,20), key='output', font=('Myrica M', 16))],
        #[sg.Table([], key='comments', justification='left',headings=['name', 'comment'], col_widths=[10,45], display_row_numbers=False, vertical_scroll_only=False, auto_size_columns=False, font=FONT)]
    ]
    th_parse = False
    th_yomiage = False
    th_viewers = False
    running  = False
    window = sg.Window('CommentViewer for VOICEVOX', grab_anywhere=True, layout=layout, keep_on_top=True, resizable=True, finalize=True)
    window['output'].expand(expand_x=True, expand_y=True)
    
    while True:
        ev, val = window.read()
        #print(ev, val)
        if ev in (sg.WIN_CLOSED, 'Escape:27', '-WINDOW CLOSE ATTEMPTED-'):
            #TODOコメント取得の停止
            break
        elif ev.startswith('get_comment'):
            if not running: ### 処理開始
                if val['youtube_url'][-13:] == 'livestreaming':
                    liveid = val['youtube_url'].split('/')[-2]
                else:
                    liveid = re.sub('.*/', '', re.sub('.*=', '', val['youtube_url'].strip()))
                print('コメント取得スレッド開始')
                print(f'コメント欄のURL(OBS用):\nhttps://www.youtube.com/live_chat?is_popout=1&v={liveid}')
                chat = pytchat.create(video_id = liveid)
                th_parse = threading.Thread(target=parse_comment, args=(window, chat), daemon=True)
                th_yomiage = threading.Thread(target=yomiage, daemon=True)
                th_yomiage.start()
                th_parse.start()
                #window['get_comment'].update('停止')
                running = not running

                ### 動画タイトル取得
                title = ''
                try:
                    yt = build('youtube', 'v3', developerKey=youtube_api_key)
                    videos_response = yt.videos().list(
                        part='snippet,statistics,liveStreamingDetails',
                        id='{},'.format(liveid)
                    ).execute()
                    snippetInfo = videos_response["items"][0]["snippet"]
                    title = snippetInfo['title']
                    window['title'].update(title)
                except Exception:
                    print('タイトル取得時にGoogleAPIでエラーが発生')

                #encoded_title = urllib.parse.quote(title +'\n' + val['youtube_url'])
                #webbrowser.open(f"https://twitter.com/intent/tweet?text={encoded_title}")

                th_viewers = threading.Thread(target=get_viewers, args=(window, title, liveid,), daemon=True)
                th_viewers.start()
            else:
                # pytchatがブロッキングなので諦める、ちゃんとやるならasyncかなぁ
                window['get_comment'].update('取得')

            #running = not running
        elif ev.startswith('-THREAD-'):
            msg = val[ev]
            th2 = threading.Thread(target=proc_comment, args=(msg.upper(),), daemon=True)
            th2.start()
        elif ev.startswith('-RESET_PYTCHAT-'):
            th_parse.join()
            chat = pytchat.create(video_id = liveid)
            th_parse = threading.Thread(target=parse_comment, args=(window, chat), daemon=True)
            th_parse.start()
        elif ev.startswith('-VIEWERS-'):
            window['viewers'].update(val[ev])
        elif ev.startswith('Tweet'):
            ### 動画タイトル取得
            if len(val['youtube_url']) > 0:
                if val['youtube_url'][-13:] == 'livestreaming':
                    liveid = val['youtube_url'].split('/')[-2]
                else:
                    liveid = re.sub('.*/', '', re.sub('.*=', '', val['youtube_url'].strip()))
                yt = build('youtube', 'v3', developerKey=youtube_api_key)
                videos_response = yt.videos().list(
                    part='snippet,statistics,liveStreamingDetails',
                    id='{},'.format(liveid)
                ).execute()
                snippetInfo = videos_response["items"][0]["snippet"]
                title = snippetInfo['title']
                window['title'].update(title)

                encoded_title = urllib.parse.quote(title +'\nhttps://www.youtube.com/watch?v=' + liveid)
                webbrowser.open(f"https://twitter.com/intent/tweet?text={encoded_title}")
        elif ev == '-PIN-':
            window['-PIN-'].metadata = not window['-PIN-'].metadata     # use metadata to store current state of pin
            if window['-PIN-'].metadata:
                window['-PIN-'].update(text_color='red')
                window.keep_on_top_set()
            else:
                window['-PIN-'].update(text_color='black')
                window.keep_on_top_clear()

if __name__ == '__main__':
    freeze_support()
    voice = 14 # ひまり
    voice = 8 # 春日部つむぎ
    gui(sys.argv[1:])
