# nRF Cloud Device Simulator

nRF9151 DK の代わりに、ソフトウェアで仮想デバイスを作成し nRF Cloud に接続するシミュレータです。
物理デバイスがなくても、クラウド側 (AWS) や iPhone アプリの開発を継続できます。

## アーキテクチャ

```
[Simulator (PC)] --MQTT--> [nRF Cloud] --REST API--> [AWS Backend] --> [iPhone App]
```

シミュレータは nRF9151 DK と同じ経路でデータを送信するため、AWS バックエンドや iPhone アプリから見ると実機と区別がつきません。

## 前提条件

| ソフトウェア | 用途 | インストール方法 |
|---|---|---|
| Python 3.10+ | シミュレータ実行 | `winget install Python.Python.3.12` |
| OpenSSL | 証明書生成 (初回のみ) | `winget install ShiningLight.OpenSSL.Light` |
| paho-mqtt | MQTT クライアント | `pip install -r requirements.txt` |

> **注意**: nRF Connect SDK 同梱の Python ではなく、**システム Python** (`py` コマンド) を使用してください。SDK 同梱版はネットワークモジュールが含まれていません。

## セットアップ手順

### 1. OpenSSL のインストールと PATH 設定

```cmd
winget install ShiningLight.OpenSSL.Light
set "PATH=%PATH%;C:\Program Files\OpenSSL-Win64\bin"
openssl version
```

> OpenSSL のバージョンが表示されれば OK です。PATH 設定はコマンドプロンプトを閉じるとリセットされます。恒久的に設定するには「システム環境変数」に追加してください。

### 2. 依存パッケージのインストール

```cmd
cd tools\simulator
py -m pip install -r requirements.txt
```

### 3. 初期設定 (セットアップウィザード)

```cmd
py setup_credentials.py
```

対話形式で以下を設定します：

- **nRF Cloud API Key**: nRF Cloud ポータル → Account → API Key から取得
- **Device ID**: デフォルト `kid-gps-sim-001` (そのまま Enter で OK)

> **API Key について**: 新しいキーを生成すると、古いキーは最大60分間有効です。すぐに古いキーが無効になるわけではありません。

### 4. シミュレータの起動

```cmd
py device_simulator.py
```

初回起動時に自動で以下が実行されます：
1. Amazon Root CA 証明書のダウンロード
2. デバイス用 EC キーペアと自己署名証明書の生成 (OpenSSL)
3. nRF Cloud への仮想デバイス登録 (REST API)
4. MQTT 接続とデータ送信開始

2回目以降は証明書と接続情報をキャッシュから読み込むため、すぐに接続されます。

## 操作方法

シミュレータ実行中に以下のキーで操作できます：

| キー | 動作 |
|---|---|
| `g` | GPS 位置を即時送信 |
| `t` | 温度を即時送信 |
| `a` | アラート (ボタン押下) を送信 |
| `c` | テストカウンターを送信 |
| `s` | 現在の設定 (shadow config) を表示 |
| `i` | ルート情報を表示 |
| `q` | シミュレータを終了 |

## 送信データフォーマット

### GNSS (GPS位置)
```json
{
  "appId": "GNSS",
  "ts": 1738577400000,
  "data": {
    "lat": 35.6812,
    "lon": 139.7671,
    "acc": 10.5
  }
}
```

### 温度 (TEMP)
```json
{
  "appId": "TEMP",
  "ts": 1738577400000,
  "data": 25.3
}
```

### アラート (ALERT)
```json
{
  "appId": "ALERT",
  "ts": 1738577400000,
  "data": {
    "type": 0,
    "value": 0,
    "description": "Button pressed"
  }
}
```

### カウンター (COUNT)
```json
{
  "appId": "COUNT",
  "ts": 1738577400000,
  "data": 42
}
```

## GPS ルート

東京都心を巡回する20地点のルートを自動的に移動します：

東京駅 → 皇居 → 九段下 → 飯田橋 → 後楽園 → 御茶ノ水 → 秋葉原 → 上野公園 → 浅草 → スカイツリー → 両国 → 清澄庭園 → 築地 → 銀座 → 日比谷公園 → 東京タワー → 六本木 → 赤坂 → 四ツ谷 → 東京駅

各地点間は補間され、位置にはランダムな誤差 (数メートル) が加わります。

## 設定ファイル

`config.json` (config_template.json からコピー):

```json
{
    "nrf_cloud": {
        "api_key": "<YOUR_API_KEY>",
        "api_host": "https://api.nrfcloud.com",
        "device_id": "kid-gps-sim-001"
    },
    "simulation": {
        "location_interval_seconds": 300,
        "temperature_interval_seconds": 300,
        "temperature_base": 25.0,
        "temperature_variation": 3.0,
        "app_version": "0.0.1"
    }
}
```

| パラメータ | 説明 | デフォルト |
|---|---|---|
| `api_key` | nRF Cloud API キー | (必須) |
| `device_id` | 仮想デバイスの ID | `kid-gps-sim-001` |
| `location_interval_seconds` | GPS 送信間隔 (秒) | 300 |
| `temperature_interval_seconds` | 温度送信間隔 (秒) | 300 |
| `temperature_base` | 基準温度 (°C) | 25.0 |
| `temperature_variation` | 時間帯による温度変動幅 (°C) | 3.0 |

## 診断モード

接続トラブル時に使用します：

```cmd
py device_simulator.py --diag
```

以下を順にテストします：
1. REST API でデバイス状態を確認
2. MQTT 接続 (サブスクライブなし) が維持できるか
3. c2d トピックへのサブスクライブが成功するか

## ファイル構成

```
tools/simulator/
  device_simulator.py    # メインシミュレータ
  setup_credentials.py   # セットアップウィザード
  config_template.json   # 設定テンプレート
  requirements.txt       # Python 依存パッケージ
  .gitignore             # 秘密ファイル除外設定
  config.json            # 設定ファイル (git管理外)
  certs/                 # 証明書 (git管理外)
    *.key.pem            #   デバイス秘密鍵
    *.cert.pem           #   デバイス証明書
    AmazonRootCA1.pem    #   AWS Root CA
    *.mqtt_info.json     #   MQTT接続情報キャッシュ
```

## トラブルシューティング

### `openssl` が認識されない
```cmd
set "PATH=%PATH%;C:\Program Files\OpenSSL-Win64\bin"
```

### `ModuleNotFoundError: No module named '_socket'`
nRF Connect SDK の Python を使っています。システム Python を使用してください：
```cmd
py device_simulator.py
```

### MQTT 接続が切断される (rc=7)
キャッシュされた接続情報を削除して再起動：
```cmd
del certs\*.mqtt_info.json
py device_simulator.py
```

### デバイスを再作成したい
nRF Cloud ポータルでデバイスを削除してから：
```cmd
del certs\kid-gps-sim-001.*
py device_simulator.py
```

## セキュリティ注意事項

- `config.json` には API キーが含まれます。**Git にコミットしないでください** (.gitignore で除外済み)
- `certs/` ディレクトリにはデバイスの秘密鍵が含まれます。**Git にコミットしないでください** (.gitignore で除外済み)
- API キーが漏洩した場合は、nRF Cloud ポータルで即座に再生成してください
