# BeamerPlus

LaTeX / Beamer 等の PDF スライドによるプレゼンテーションを快適に行うための、デュアルスクリーン対応・発表者ビューツールです。

---

## 主な機能

- **デュアルスクリーン対応**:
  - プロジェクタ/外部ディスプレイに自動全画面表示する「Viewer」と、手元でノートやタイマーを確認できる「発表者ビュー」が完全同期。
- **iPad / タブレット連携 (Web リモート手書き)**:
  - 専用アプリ不要！同じ Wi-Fi 内の iPad の Safari から開くだけで、スライドを見ながら Apple Pencil で手書き書き込みが可能。
  - Apple Pencil の筆圧感知、パームリジェクション（Pencilのみ描画）、指スワイプでのページめくりに対応。
  - レーザーポインタ、蛍光ペン、消しゴム、Undo/Clear も iPad 上からフル操作でき、PC & プロジェクタ画面へリアルタイム同期。
- **手書き描画 & テキスト書き込み**:
  - キーボード入力による任意位置へのテキスト注釈書き込み（フォントサイズ・文字色変更、Return確定、Escキャンセル）。
  - レーザーポインタ、手書きペン、蛍光ペン（半透明ハイライト）。
  - Wacom や液晶タブレットの筆圧検知・ホバーインジケータ・ペン尻消しゴム自動認識。
  - 水平・垂直 ±5度 スナップ対応の「直線描画」モード。
  - 手書きストロークとテキストを時系列で正確に取り消せる統合 Undo 機能。
- **消しゴム機能**:
  - なぞった部分のみを切り取る「通常消しゴム」と、ストローク全体を消去する「オブジェクト消しゴム」のワンクリック切り替え。
- **hyperref / PDF ハイパーリンク完全対応**:
  - 目次（`\tableofcontents`）、定理・数式参照（`\ref`, `\eqref`）、補足スライド（`\hyperlink`, `\beamerbutton` 等）への内部ジャンプ、および Web URL（`\href`, `\url`）に対応。
  - リンクホバー時の指カーソル（Pointing Hand）・ツールチップ・視覚的ハイライト枠表示。
  - 描画ツール使用中でも `Ctrl` (`Cmd`) + クリックで安全にリンク操作可能。
  - ジャンプ元へワンタッチで復帰できる Jump Back（履歴）機能。
- **Markdown スライドノート**:
  - ページごとに Markdown 形式で発表原稿やメモを記述・プレビュー可能。
  - スライド PDF と同じディレクトリに JSON ファイルとして自動保存。
- **プレゼンテーションタイマー & 画面暗転**:
  - 経過時間タイマー（開始・一時停止・リセット）。
  - `B` キーやボタン、または最後のスライドを次に送る操作で瞬時にプロジェクタ画面をブラックアウトする暗転機能（暗転中に戻る操作やBキーで解除可能）。

---

## インストール方法

### 1. pipx のインストール（初回のみ）

`pipx` は Python アプリケーションをシステム環境から隔離して安全にグローバルコマンドとして管理できるツールです。まだインストールしていない場合は、お使いの OS に応じてインストールし、パスを通してください。

#### 🐧 Linux (Ubuntu, Debian, Fedora, Arch 等)
- **Ubuntu / Debian**:
  ```bash
  sudo apt update
  sudo apt install pipx
  pipx ensurepath
  ```
- **Fedora**:
  ```bash
  sudo dnf install pipx
  pipx ensurepath
  ```
- **Arch Linux**:
  ```bash
  sudo pacman -S python-pipx
  pipx ensurepath
  ```

#### 🍎 macOS
Homebrew を使用してインストールします：
```bash
brew install pipx
pipx ensurepath
```

#### 🪟 Windows
PowerShell または コマンドプロンプトからインストールします：
```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```
*(Python ランチャー環境では `py -m pip install --user pipx` および `py -m pipx ensurepath`)*

※ Scoop を利用する場合:
```powershell
scoop install pipx
pipx ensurepath
```

> [!NOTE]
> `pipx ensurepath` を実行した後は、環境変数の変更を反映するために**ターミナル（シェルやコマンドプロンプト）を再起動**してください。

---

### 2. BeamerPlus のインストール

#### pipx を使う場合（推奨）
Python アプリケーションをシステム環境から隔離してグローバルコマンドとしてインストールできます。

```bash
pipx install git+https://github.com/lambda-haru/BeamerPlus.git
```

#### pip を使う場合
```bash
pip install git+https://github.com/lambda-haru/BeamerPlus.git
```

---

## アップデート方法

最新バージョンにアップデートする手順です。

### pipx を使う場合（推奨）

```bash
pipx upgrade beamerplus
```

### pip を使う場合

```bash
pip install --upgrade beamerplus
```

---

## 使い方

ターミナルからスライド PDF を指定して起動します：

```bash
beamerplus slides.pdf
```

引数を省略して起動し、ツールバーの「📁 開く」から PDF を選択することもできます：

```bash
beamerplus
```

### 主なショートカットキー
| キー | 操作 |
| :--- | :--- |
| `◀` / `▶` / `PageUp` / `PageDown` / `Space` / `Enter` | ページ送り / 戻し |
| `Alt + ◀` / `Backspace` / マウス戻るボタン | 直前のスライドに戻る (Jump Back) |
| `Ctrl + クリック` (`Cmd + クリック`) | 描画ツール選択中のハイパーリンク実行 |
| `B` | 画面の暗転（ブラックアウト）のトグル |
| `F5` | プロジェクタ画面（Viewer）の表示 / 非表示 |
| `F11` | プロジェクタ画面の全画面表示切替 |
| `Ctrl + N` (`Cmd + N`) | ノートパネルの表示 / 非表示 |
| `Ctrl + T` (`Cmd + T`) | 下部スライド一覧サムネイルの表示 / 非表示 |

---

### 📱 iPad / タブレットから書き込む方法
1. BeamerPlus を起動し、ツールバーの「**📱 iPad 連携**」ボタン（またはメニューの「ツール」→「iPad / タブレット連携」）をクリックします。
2. 表示されるダイアログの **QR コードを iPad の標準カメラで読み取るだけ**（または Safari で記載の URL を開く）で即座に接続されます。
   - ※ 複数のネットワーク（Wi-Fi、有線LAN、テザリングなど）がある場合は、ダイアログ上部のドロップダウンから接続したい IP アドレスやデバイス名（`.local` ドメイン）を選択して切り替えることができます。
3. iPad の画面に現在表示中のスライドが表示されます：
   - **Apple Pencil**: スライド上に手書き描画（安定した均一な線幅描画）。
   - **完全な色選択（カラーピッカー）**: カラープレビューアイコンをタップすると、iOS 標準のカラーピッカー（カラーホイール、RGBスライダー、HEX指定等）が開き、自由な色を選択可能。
   - **線の太さ調整**: iPad のツールバーにある太さスライダーで線の太さを自由に変更でき、PC側の設定とリアルタイムに双方向同期。
   - **指スワイプ**: 「Pencilのみ描画」が ON の場合、画面を左右にスワイプしてスライドのページめくりができます。
   - **ツールバー**: 蛍光ペン、消しゴム、レーザーポインタ、Undo、クリア等の操作が iPad 側からも自由に行えます。
   - iPad 上で描いた内容は、手元の PC 画面およびプロジェクタ画面（Viewer）へリアルタイムに同期されます。

---

## デスクトップショートカットの追加

ターミナルを開かずにアイコンのダブルクリックや、PDF ファイルのドラッグ＆ドロップから起動できるように、各 OS でデスクトップショートカットを作成できます。

### 🐧 Linux (Ubuntu, Debian, Fedora, Arch 等)

デスクトップエントリーファイル（`.desktop`）を作成することで、アプリメニューやデスクトップからワンクリックで起動できるようになります。

1. **エントリーファイルの作成**:
   ターミナルで以下のコマンドを実行します：
   ```bash
   mkdir -p ~/.local/share/applications
   cat << 'EOF' > ~/.local/share/applications/beamerplus.desktop
   [Desktop Entry]
   Version=1.0
   Type=Application
   Name=BeamerPlus
   Comment=LaTeX/Beamer PDF Presenter View
   Exec=beamerplus %f
   Icon=x-office-presentation
   Terminal=false
   Categories=Office;Presentation;
   MimeType=application/pdf;
   EOF
   ```
2. **デスクトップにアイコンを置く場合**:
   ```bash
   cp ~/.local/share/applications/beamerplus.desktop ~/Desktop/
   chmod +x ~/Desktop/beamerplus.desktop
   ```
   ※ Ubuntu (GNOME) の場合、デスクトップに置いたアイコンを右クリックし、「**起動を許可する (Allow Launching)**」を選択してください。
3. **右クリックから開く**:
   PDF ファイルを右クリック →「別のアプリケーションで開く」から `BeamerPlus` を選択して開くことも可能になります。

---

### 🍎 macOS

macOS 標準アプリ「Automator」を使って、PDF のドラッグ＆ドロップにも対応したアプリ（`.app`）を作成できます。

1. **Automator を起動**:
   - `Spotlight`（`Cmd + Space`）で「Automator」と入力して開きます。
2. **書類の種類を選択**:
   - 「新規書類」から「**アプリケーション**」を選択して「選択」をクリックします。
3. **シェルスクリプトを追加**:
   - 左側ライブラリの「ユーティリティ」から「**シェルスクリプトを実行**」を右側のワークフロー領域にドラッグ＆ドロップします。
   - **入力の引き渡し方法** を「**引数として**」に変更します。
   - スクリプト入力欄に以下を記述します：
     ```bash
     export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
     if [ $# -eq 0 ]; then
         beamerplus
     else
         beamerplus "$1"
     fi
     ```
4. **保存**:
   - `Cmd + S` を押し、名前を `BeamerPlus`、保存先を「アプリケーション」フォルダ（またはデスクトップ）にして保存します。
5. **使い方**:
   - アイコンをダブルクリックで起動できるほか、PDF ファイルをこのアイコンに **ドラッグ＆ドロップ** して直接開くこともできます。

---

### 🪟 Windows (Windows 10 / 11)

Windows 標準のショートカット機能で簡単に作成できます。

1. **ショートカット作成ウィザード**:
   - デスクトップの何もない場所で **右クリック →「新規作成」→「ショートカット」** を選択します。
2. **コマンドの指定**:
   - 「項目の場所を入力してください」に以下を入力して「次へ」をクリックします：
     - **pipx でインストールした場合**:
       ```text
       %USERPROFILE%\.local\bin\beamerplus.exe
       ```
     - **pip でインストールした場合**:
       ```text
       python -m beamerplus.main
       ```
3. **名前の指定**:
   - ショートカットの名前を「`BeamerPlus`」にして「完了」をクリックします。
4. **アイコンの変更（お好みで）**:
   - 作成されたショートカットを右クリック →「プロパティ」を開きます。
   - 「ショートカット」タブの「アイコンの変更」から、お好みのアイコンを選択して「OK」をクリックします。
5. **便利な使い方**:
   - スライド PDF ファイルをこのデスクトップアイコン上に **ドラッグ＆ドロップ** すると、該当スライドを直接開いて起動できます。

---

## ライセンス
MIT License
