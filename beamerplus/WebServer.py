import os
import json
import socket
import asyncio
import threading
import secrets
import string
from typing import Callable
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkInterface, QAbstractSocket, QHostInfo
import websockets
from websockets.protocol import State


def generate_passcode(length: int = 8) -> str:
    """判読しやすく手入力しやすい英数字によるランダムパスコードを生成（英大文字・小文字・数字）"""
    # 紛らわしい文字 (0, O, 1, I, l) を除いた英数字セット
    alphabet = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_local_ip() -> str:
    """LAN内のローカルIPアドレスを取得"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # ルーティング可能なIPを取得（実際に通信は発生しない）
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def get_network_interfaces() -> list[dict]:
    """
    接続可能なネットワークインターフェースのIPアドレスおよびホスト名の一覧を取得する。
    戻り値: [{'display': '...', 'host': '...', 'is_default': bool}, ...]
    """
    primary_ip = get_local_ip()
    hostname = ""
    try:
        hostname = QHostInfo.localHostName()
    except Exception:
        pass
    if not hostname:
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = ""

    valid_interfaces: list[tuple[str, str]] = []
    loopback_interfaces: list[tuple[str, str]] = []

    try:
        for iface in QNetworkInterface.allInterfaces():
            flags = iface.flags()
            # 停止中のインターフェースは除外
            if not (flags & QNetworkInterface.InterfaceFlag.IsUp):
                continue

            is_loopback = bool(flags & QNetworkInterface.InterfaceFlag.IsLoopBack)
            raw_name = iface.humanReadableName() or iface.name()
            name = raw_name.strip()

            for entry in iface.addressEntries():
                ip = entry.ip()
                if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                    continue
                ip_str = ip.toString().strip()
                if not ip_str or ip_str == "0.0.0.0":
                    continue

                if is_loopback or ip_str == "127.0.0.1":
                    loopback_interfaces.append((ip_str, name))
                else:
                    valid_interfaces.append((ip_str, name))
    except Exception as e:
        print(f"[BeamerPlus] Error detecting network interfaces: {e}")

    # primary_ip が取得できたが valid_interfaces に入っていない場合は追加
    if primary_ip != "127.0.0.1" and not any(ip == primary_ip for ip, _ in valid_interfaces):
        valid_interfaces.insert(0, (primary_ip, "LAN"))

    results: list[dict] = []
    seen_hosts: set[str] = set()

    # 1. プライマリ（推奨）IPアドレス
    for ip_str, name in valid_interfaces:
        if ip_str == primary_ip:
            label = f"{ip_str} (推奨: {name})" if name else f"{ip_str} (推奨)"
            results.append({"display": label, "host": ip_str, "is_default": True})
            seen_hosts.add(ip_str)
            break

    # 2. ドメイン / デバイス名 (mDNS: .local)
    clean_host = hostname.strip()
    if clean_host and clean_host.lower() not in ("localhost", "127.0.0.1", "0.0.0.0"):
        if "." in clean_host:
            mdns_host = clean_host
        else:
            mdns_host = f"{clean_host}.local"

        if mdns_host not in seen_hosts:
            results.append({
                "display": f"{mdns_host} (デバイス名)",
                "host": mdns_host,
                "is_default": False
            })
            seen_hosts.add(mdns_host)

    # 3. その他の有効なインターフェース
    for ip_str, name in valid_interfaces:
        if ip_str in seen_hosts:
            continue
        label = f"{ip_str} ({name})" if name else ip_str
        results.append({"display": label, "host": ip_str, "is_default": False})
        seen_hosts.add(ip_str)

    # 4. ループバック (ローカルホスト)
    for ip_str, name in loopback_interfaces:
        if ip_str in seen_hosts:
            continue
        results.append({
            "display": f"{ip_str} (ローカル)",
            "host": ip_str,
            "is_default": False
        })
        seen_hosts.add(ip_str)

    # 何も取れなかった場合のフォールバック
    if not results:
        results.append({
            "display": "127.0.0.1 (ローカル)",
            "host": "127.0.0.1",
            "is_default": True
        })

    # is_default がどれにも設定されていない場合は先頭をデフォルトにする
    if not any(r["is_default"] for r in results):
        results[0]["is_default"] = True

    return results


# --- iPad向け Web アプリケーション (HTML / CSS / JavaScript) ---
HTML_CONTENT = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>BeamerPlus iPad Remote</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
      -webkit-touch-callout: none !important;
      -webkit-user-select: none !important;
      user-select: none !important;
      touch-action: none;
    }
    body, html {
      width: 100%;
      height: 100%;
      overflow: hidden;
      background-color: #121212;
      position: fixed;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    #stage {
      position: absolute;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background-color: #121212;
    }
    #slide-wrapper {
      position: relative;
      background-color: #000000;
      box-shadow: 0 4px 20px rgba(0,0,0,0.6);
      display: inline-block;
    }
    #slide-wrapper.sketchbook {
      background-color: #ffffff !important;
    }
    #slide-wrapper.sketchbook #slide-img {
      visibility: hidden;
    }
    #blank-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background-color: #000000;
      z-index: 90;
      display: none;
      align-items: center;
      justify-content: center;
      color: rgba(255, 255, 255, 0.75);
      font-size: 24px;
      font-weight: bold;
      pointer-events: auto;
    }
    #blank-overlay.active {
      display: flex;
    }
    #slide-img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      pointer-events: none;
    }
    #committed-canvas, #active-canvas, #pointer-canvas {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      display: block;
    }
    #committed-canvas,
    #pointer-canvas {
      pointer-events: none;
    }
    #pointer-canvas {
      z-index: 40;
    }
    #active-canvas {
      cursor: crosshair;
      touch-action: none;
    }
    /* フローティングツールバー */
    #ui-container {
      position: fixed;
      top: 12px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 100;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      transition: opacity 0.2s;
      max-width: calc(100vw - 16px);
      width: max-content;
      box-sizing: border-box;
    }
    #ui-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(32, 32, 35, 0.88);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      padding: 6px 14px;
      border-radius: 28px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
      color: #f0f0f0;
      font-size: 13px;
      max-width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
      -webkit-overflow-scrolling: touch;
      white-space: nowrap;
      box-sizing: border-box;
      touch-action: pan-x;
      scrollbar-width: thin;
      scrollbar-color: rgba(255, 255, 255, 0.25) transparent;
    }
    #ui-bar::-webkit-scrollbar {
      height: 4px;
    }
    #ui-bar::-webkit-scrollbar-track {
      background: transparent;
    }
    #ui-bar::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.25);
      border-radius: 4px;
    }
    #ui-bar > * {
      flex-shrink: 0;
    }
    .btn {
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #ffffff;
      padding: 6px 12px;
      border-radius: 18px;
      cursor: pointer;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s ease;
    }
    .btn:active {
      transform: scale(0.94);
    }
    .btn.active {
      background: #007acc;
      border-color: #38bdf8;
      box-shadow: 0 0 10px rgba(56, 189, 248, 0.4);
    }
    .btn-nav {
      font-size: 14px;
      font-weight: bold;
      padding: 6px 14px;
    }
    #btn-eraser-mode, #btn-pointer-trail {
      font-size: 11px;
      font-weight: 600;
      padding: 5px 8px;
      min-width: 68px;
      justify-content: center;
      transition: all 0.2s ease;
    }
    #btn-eraser-mode {
      color: #38bdf8;
      border-color: rgba(56, 189, 248, 0.4);
    }
    #btn-pointer-trail {
      color: #ff6b6b;
      border-color: rgba(255, 107, 107, 0.4);
    }
    #btn-pointer-trail.off-mode {
      color: #888888;
      border-color: rgba(255, 255, 255, 0.15);
    }
    #btn-eraser-mode.disabled, #btn-pointer-trail.disabled, #btn-line.disabled {
      opacity: 0.35;
      pointer-events: none;
      filter: grayscale(1);
    }
    #btn-eraser-mode.stroke-mode {
      color: #fbbf24;
      border-color: rgba(251, 191, 36, 0.5);
    }
    #page-indicator {
      font-weight: 600;
      min-width: 65px;
      text-align: center;
      color: #e0e0e0;
      font-size: 13px;
    }
    .separator {
      width: 1px;
      height: 20px;
      background: rgba(255, 255, 255, 0.2);
      margin: 0 2px;
    }
    /* 完全な色選択ピッカー */
    #color-picker-wrapper {
      position: relative;
      width: 28px;
      height: 28px;
      border-radius: 50%;
      cursor: pointer;
      border: 2px solid rgba(255, 255, 255, 0.6);
      box-shadow: 0 0 10px rgba(0, 0, 0, 0.4);
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: transform 0.15s, border-color 0.2s;
      flex-shrink: 0;
    }
    #color-picker-wrapper:active {
      transform: scale(0.92);
    }
    #color-indicator {
      width: 100%;
      height: 100%;
      border-radius: 50%;
      background-color: #e53935;
      pointer-events: none;
    }
    #native-color-picker {
      position: absolute;
      top: -10px;
      left: -10px;
      width: 48px;
      height: 48px;
      opacity: 0;
      cursor: pointer;
      -webkit-appearance: none;
      border: none;
    }
    /* 太さスライダー */
    #size-wrapper {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 4px;
    }
    #size-slider {
      -webkit-appearance: none;
      width: 75px;
      height: 4px;
      border-radius: 2px;
      background: #444448;
      outline: none;
      cursor: pointer;
    }
    #size-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #ffffff;
      border: 2px solid #007acc;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
      cursor: pointer;
    }
    #size-val {
      min-width: 22px;
      text-align: right;
      font-size: 11px;
      color: #38bdf8;
      font-weight: 600;
    }
    /* ステータスドット */
    #status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: #f43f5e;
      transition: background-color 0.3s;
    }
    #status-dot.online {
      background-color: #10b981;
      box-shadow: 0 0 8px #10b981;
    }
    .switch-label {
      display: flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
      font-size: 12px;
      color: #bbb;
    }
    .switch-label input {
      cursor: pointer;
    }
    /* ポインタ表示 */
    #laser-dot {
      position: absolute;
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: radial-gradient(circle, #ffffff 10%, #ff3b30 40%, rgba(255,0,0,0) 80%);
      transform: translate(-50%, -50%);
      pointer-events: none;
      display: none;
      z-index: 50;
      box-shadow: 0 0 12px rgba(255, 59, 48, 0.8);
    }
    /* ツールバー折りたたみ用トグル */
    #ui-toggle {
      background: rgba(30, 30, 30, 0.6);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border: 1px solid rgba(255,255,255,0.1);
      color: #aaa;
      border-radius: 12px;
      font-size: 10px;
      padding: 2px 10px;
      cursor: pointer;
    }
    /* トースト通知 */
    #toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(16, 185, 129, 0.95);
      color: #ffffff;
      padding: 8px 18px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 4px 14px rgba(0,0,0,0.4);
      z-index: 200;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.3s ease;
    }
    #toast.show {
      opacity: 1;
    }
    /* パスコード認証オーバーレイ */
    #auth-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(15, 15, 18, 0.88);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      z-index: 1000;
      display: none;
      align-items: center;
      justify-content: center;
    }
    #auth-overlay.active {
      display: flex;
    }
    .auth-card {
      background: #1e1e24;
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 20px;
      padding: 28px 24px;
      width: 320px;
      text-align: center;
      box-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
      color: #f0f0f0;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .auth-icon {
      font-size: 36px;
      margin-bottom: 8px;
    }
    .auth-title {
      font-size: 20px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .auth-desc {
      font-size: 13px;
      color: #a0a0b0;
      margin-bottom: 20px;
      line-height: 1.4;
    }
    .auth-input-box {
      width: 100%;
      position: relative;
      display: flex;
      align-items: center;
      margin-bottom: 6px;
    }
    #passcode-input {
      width: 100%;
      height: 48px;
      background: #141418;
      border: 1.5px solid #3f3f50;
      border-radius: 12px;
      font-size: 20px;
      font-weight: bold;
      color: #fbbf24;
      text-align: center;
      letter-spacing: 3px;
      padding: 0 44px 0 16px;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
      -webkit-user-select: text !important;
      user-select: text !important;
      touch-action: manipulation !important;
    }
    #passcode-input:focus {
      border-color: #38bdf8;
      box-shadow: 0 0 12px rgba(56, 189, 248, 0.3);
    }
    #btn-toggle-pass-visibility {
      position: absolute;
      right: 8px;
      background: transparent;
      border: none;
      font-size: 18px;
      cursor: pointer;
      padding: 6px;
      opacity: 0.6;
      transition: opacity 0.15s;
    }
    #btn-toggle-pass-visibility:active {
      opacity: 1;
    }
    #auth-error-msg {
      min-height: 20px;
      font-size: 12px;
      color: #f43f5e;
      margin-top: 4px;
      margin-bottom: 12px;
      line-height: 20px;
    }
    .auth-submit-btn {
      width: 100%;
      height: 46px;
      background: #007acc;
      border: none;
      border-radius: 12px;
      color: #ffffff;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(0, 122, 204, 0.4);
      transition: background 0.15s;
    }
    .auth-submit-btn:active {
      background: #005999;
    }
  </style>
</head>
<body>

  <!-- パスコード認証オーバーレイ -->
  <div id="auth-overlay">
    <div class="auth-card">
      <div class="auth-icon">🔒</div>
      <div class="auth-title">BeamerPlus 認証</div>
      <div class="auth-desc">PC画面の パスコード を入力してください</div>

      <div class="auth-input-box">
        <input type="password" id="passcode-input" placeholder="パスコードを入力" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false">
        <button type="button" id="btn-toggle-pass-visibility" title="パスコードを表示/非表示">👁️</button>
      </div>

      <div id="auth-error-msg"></div>

      <button id="btn-submit-pass" class="auth-submit-btn">接続する</button>
    </div>
  </div>

  <div id="blank-overlay">⬛ 暗転中（タップで解除）</div>
  <div id="toast"></div>

  <div id="stage">
    <div id="slide-wrapper">
      <img id="slide-img" alt="Slide" src="">
      <canvas id="committed-canvas"></canvas>
      <canvas id="active-canvas" inputmode="none"></canvas>
      <canvas id="pointer-canvas"></canvas>
      <div id="laser-dot"></div>
    </div>
  </div>

  <div id="ui-container">
    <div id="ui-bar">
      <div id="status-dot" title="切断中"></div>

      <!-- ページ送り -->
      <button class="btn btn-nav" id="btn-prev">◀</button>
      <span id="page-indicator">- / -</span>
      <button class="btn btn-nav" id="btn-next">▶</button>

      <div class="separator"></div>

      <!-- ツール選択 -->
      <button class="btn" id="btn-pen" data-tool="pen" title="ペン">✏️</button>
      <button class="btn" id="btn-highlighter" data-tool="highlighter" title="蛍光ペン">🖍️</button>
      <button class="btn" id="btn-line" title="直線モード（ペン/蛍光ペンで直線を引く）">📏</button>
      <button class="btn" id="btn-eraser" data-tool="eraser" title="消しゴム">🧹</button>
      <button class="btn disabled" id="btn-eraser-mode" title="消しゴムモード切替（通常 / オブジェクト）">通常</button>
      <button class="btn" id="btn-pointer" data-tool="pointer" title="レーザーポインタ">🔴</button>
      <button class="btn disabled" id="btn-pointer-trail" title="ポインタ軌跡切替（なぞった時に一時的な線を引く / ドットのみ）">軌跡ON</button>

      <div class="separator"></div>

      <!-- 完全な色選択ピッカー -->
      <div id="color-picker-wrapper" title="完全な色選択（タップでピッカーを開く）">
        <input type="color" id="native-color-picker" value="#e53935">
        <div id="color-indicator"></div>
      </div>

      <!-- 太さ調整スライダー -->
      <div id="size-wrapper" title="線の太さを変更">
        <span style="font-size: 11px; color: #888;">太さ</span>
        <input type="range" id="size-slider" min="1" max="30" value="5">
        <span id="size-val">5</span>
      </div>

      <div class="separator"></div>

      <!-- 描画消去・表示切替 -->
      <button class="btn" id="btn-clear" title="描画消去">🗑️</button>
      <button class="btn" id="btn-visibility" title="スケッチの表示/非表示切替">👁️</button>

      <!-- スケッチブック / ブランク -->
      <button class="btn" id="btn-sketchbook" title="スケッチブック（白紙の板書画面に切替）">📋</button>
      <button class="btn" id="btn-blank" title="画面暗転（ブランク）">⬛</button>

      <div class="separator"></div>

      <label class="switch-label" title="スタイラスペン（Apple Pencil / S-Pen 等）のみ描画し、指でのタッチはスワイプ（ページめくり）専用にする">
        <input type="checkbox" id="penOnlyToggle" checked>
        <span>ペンのみ描画</span>
      </label>

      <div class="separator"></div>

      <!-- 全画面表示トグル -->
      <button class="btn" id="btn-fullscreen" title="全画面表示切替">⛶</button>
    </div>
    <button id="ui-toggle">バーを隠す ▲</button>
  </div>

  <script>
    const stage = document.getElementById('stage');
    const slideWrapper = document.getElementById('slide-wrapper');
    const slideImg = document.getElementById('slide-img');
    const committedCanvas = document.getElementById('committed-canvas');
    const committedCtx = committedCanvas.getContext('2d');
    const activeCanvas = document.getElementById('active-canvas');
    const activeCtx = activeCanvas.getContext('2d');
    const pointerCanvas = document.getElementById('pointer-canvas');
    const pointerCtx = pointerCanvas.getContext('2d');
    const laserDot = document.getElementById('laser-dot');

    const statusDot = document.getElementById('status-dot');
    const pageIndicator = document.getElementById('page-indicator');
    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');
    const btnPen = document.getElementById('btn-pen');
    const btnHighlighter = document.getElementById('btn-highlighter');
    const btnLine = document.getElementById('btn-line');
    const btnEraser = document.getElementById('btn-eraser');
    const btnEraserMode = document.getElementById('btn-eraser-mode');
    const btnPointer = document.getElementById('btn-pointer');
    const btnPointerTrail = document.getElementById('btn-pointer-trail');
    const btnClear = document.getElementById('btn-clear');
    const btnVisibility = document.getElementById('btn-visibility');
    const btnFullscreen = document.getElementById('btn-fullscreen');
    const btnSketchbook = document.getElementById('btn-sketchbook');
    const btnBlank = document.getElementById('btn-blank');
    const blankOverlay = document.getElementById('blank-overlay');
    const toast = document.getElementById('toast');
    const penOnlyToggle = document.getElementById('penOnlyToggle');
    const colorPicker = document.getElementById('native-color-picker');
    const colorIndicator = document.getElementById('color-indicator');
    const sizeSlider = document.getElementById('size-slider');
    const sizeVal = document.getElementById('size-val');
    const uiBar = document.getElementById('ui-bar');
    const uiToggle = document.getElementById('ui-toggle');

    let currentTool = null; // 初期状態ではどのツールも未選択
    let toolColors = {
      pen: '#e53935',
      highlighter: '#ffeb3b'
    };
    let currentColor = toolColors.pen;
    let eraserMode = 'partial'; // 'partial' (通常消しゴム) または 'stroke' (オブジェクト消しゴム)
    let isLineMode = false;
    let isPointerTrailEnabled = true;
    try {
      const savedTrail = localStorage.getItem('beamerplus_pointer_trail');
      if (savedTrail !== null) isPointerTrailEnabled = (savedTrail === 'true');
    } catch (_) {}
    let toolOpacities = {
      pen: 0.85,
      highlighter: 0.28
    };
    let toolSizes = {
      pen: 5,
      highlighter: 45,
      eraser: 25,
      pointer: 18
    };
    let toolRanges = {
      pen: [1, 30],
      highlighter: [5, 160],
      eraser: [5, 100],
      pointer: [5, 60]
    };
    let currentPage = 0;
    let totalPages = 0;
    let isDrawing = false;
    let currentStrokePoints = [];
    let remoteStroke = null;
    let isBlank = false;
    let isSketchbook = false;
    let isSketchVisible = true;
    let sketchbookStrokes = [];
    let slideStrokes = [];
    let currentLinks = [];

    function findLinkAt(nx, ny) {
      if (!currentLinks || isSketchbook) return null;
      for (const l of currentLinks) {
        if (!l.rect || l.rect.length < 4) continue;
        const [x0, y0, x1, y1] = l.rect;
        if (x0 <= nx && nx <= x1 && y0 <= ny && ny <= y1) {
          return l;
        }
      }
      return null;
    }

    // スワイプ検出用状態
    let swipeTouchId = null;
    let swipeStartX = 0;
    let swipeStartY = 0;
    let swipeStartTime = 0;
    let isTrackingSwipe = false;

    function getToolOpacity() {
      return currentTool === 'highlighter' ? (toolOpacities.highlighter || 0.28) : (toolOpacities.pen || 0.85);
    }

    function getCurrentWidthRatio() {
      const tool = currentTool || 'pen';
      const size = toolSizes[tool] !== undefined ? toolSizes[tool] : 5;
      return size * 0.001;
    }

    function updateSizeUI() {
      const tool = currentTool || 'pen';
      const range = toolRanges[tool] || [1, 50];
      sizeSlider.min = range[0];
      sizeSlider.max = range[1];
      const curSize = toolSizes[tool] !== undefined ? toolSizes[tool] : 5;
      sizeSlider.value = curSize;
      sizeVal.innerText = curSize;
    }

    sizeSlider.addEventListener('input', (e) => {
      const val = parseInt(e.target.value, 10);
      const tool = currentTool || 'pen';
      toolSizes[tool] = val;
      sizeVal.innerText = val;
    });

    // 消しゴムモード切替 UI 更新
    function updateEraserModeUI() {
      if (eraserMode === 'stroke') {
        btnEraserMode.innerText = 'オブジェクト';
        btnEraserMode.classList.add('stroke-mode');
        btnEraserMode.title = '消しゴム: オブジェクト（線全体を消去。タップで通常に切替）';
      } else {
        btnEraserMode.innerText = '通常';
        btnEraserMode.classList.remove('stroke-mode');
        btnEraserMode.title = '消しゴム: 通常（なぞった部分のみ消去。タップでオブジェクトに切替）';
      }

      if (currentTool === 'eraser') {
        btnEraserMode.classList.remove('disabled');
      } else {
        btnEraserMode.classList.add('disabled');
      }
    }

    btnEraserMode.addEventListener('click', (e) => {
      e.stopPropagation();
      eraserMode = (eraserMode === 'partial') ? 'stroke' : 'partial';
      updateEraserModeUI();
    });

    // 直線モード UI 更新
    function updateLineModeUI() {
      btnLine.classList.toggle('active', isLineMode);
      const canDrawLine = (currentTool === 'pen' || currentTool === 'highlighter');
      btnLine.classList.toggle('disabled', !canDrawLine);
    }

    btnLine.addEventListener('click', (e) => {
      e.stopPropagation();
      if (currentTool !== 'pen' && currentTool !== 'highlighter') {
        selectTool('pen', false);
        isLineMode = true;
      } else {
        isLineMode = !isLineMode;
      }
      updateLineModeUI();
    });

    // ポインタ軌跡切替 UI 更新
    function updatePointerTrailUI() {
      if (isPointerTrailEnabled) {
        btnPointerTrail.innerText = '軌跡ON';
        btnPointerTrail.classList.remove('off-mode');
        btnPointerTrail.title = 'ポインタ: 軌跡ON（なぞった時に一時的な線を描画。タップでOFFに切替）';
      } else {
        btnPointerTrail.innerText = '軌跡OFF';
        btnPointerTrail.classList.add('off-mode');
        btnPointerTrail.title = 'ポインタ: 軌跡OFF（ドット移動のみ。タップでONに切替）';
      }

      if (currentTool === 'pointer') {
        btnPointerTrail.classList.remove('disabled');
      } else {
        btnPointerTrail.classList.add('disabled');
      }
    }

    btnPointerTrail.addEventListener('click', (e) => {
      e.stopPropagation();
      isPointerTrailEnabled = !isPointerTrailEnabled;
      try {
        localStorage.setItem('beamerplus_pointer_trail', String(isPointerTrailEnabled));
      } catch (_) {}
      if (!isPointerTrailEnabled) {
        currentTrail = [];
        fadingTrails = [];
        pointerCtx.clearRect(0, 0, pointerCanvas.width, pointerCanvas.height);
      }
      updatePointerTrailUI();
    });

    // スケッチブック / ブランク UI 更新
    function updateBlankUI(enabled) {
      isBlank = !!enabled;
      btnBlank.classList.toggle('active', isBlank);
      blankOverlay.classList.toggle('active', isBlank);
    }

    function updateSketchbookUI(enabled, strokes) {
      isSketchbook = !!enabled;
      btnSketchbook.classList.toggle('active', isSketchbook);
      slideWrapper.classList.toggle('sketchbook', isSketchbook);
      if (isSketchbook) {
        if (strokes) sketchbookStrokes = strokes;
        renderAllStrokes(sketchbookStrokes);
      } else {
        if (strokes) slideStrokes = strokes;
        renderAllStrokes(slideStrokes);
      }
    }

    btnSketchbook.addEventListener('click', (e) => {
      e.stopPropagation();
      sendWS({ type: 'toggle_sketchbook' });
    });

    btnBlank.addEventListener('click', (e) => {
      e.stopPropagation();
      sendWS({ type: 'toggle_blank' });
    });

    blankOverlay.addEventListener('click', (e) => {
      e.stopPropagation();
      sendWS({ type: 'toggle_blank' });
    });

    // トースト通知
    let toastTimer = null;
    function showToast(text) {
      if (!toast) return;
      toast.innerText = text;
      toast.classList.add('show');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        toast.classList.remove('show');
      }, 2500);
    }

    // スケッチ表示/非表示 UI 更新
    function updateSketchVisibilityUI(visible) {
      isSketchVisible = !!visible;
      btnVisibility.innerText = isSketchVisible ? '👁️' : '👁️‍🗨️';
      btnVisibility.classList.toggle('active', !isSketchVisible);
      committedCanvas.style.display = isSketchVisible ? 'block' : 'none';
      activeCanvas.style.display = isSketchVisible ? 'block' : 'none';
    }

    btnVisibility.addEventListener('click', (e) => {
      e.stopPropagation();
      sendWS({ type: 'toggle_sketch_visibility' });
    });

    // 全画面表示切替
    function isFullscreen() {
      return !!(document.fullscreenElement || document.webkitFullscreenElement);
    }

    function updateFullscreenUI() {
      const fs = isFullscreen();
      btnFullscreen.innerText = fs ? '🗗' : '⛶';
      btnFullscreen.title = fs ? '全画面表示を解除' : '全画面表示切替';
      btnFullscreen.classList.toggle('active', fs);
    }

    btnFullscreen.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        if (!isFullscreen()) {
          const el = document.documentElement;
          if (el.requestFullscreen) {
            await el.requestFullscreen();
          } else if (el.webkitRequestFullscreen) {
            el.webkitRequestFullscreen();
          } else {
            showToast('ℹ️ お使いの環境は全画面API非対応です');
          }
        } else {
          if (document.exitFullscreen) {
            await document.exitFullscreen();
          } else if (document.webkitExitFullscreen) {
            document.webkitExitFullscreen();
          }
        }
      } catch (err) {
        console.warn('Fullscreen error:', err);
      }
      updateFullscreenUI();
    });

    document.addEventListener('fullscreenchange', updateFullscreenUI);
    document.addEventListener('webkitfullscreenchange', updateFullscreenUI);

    // --- パスコード認証管理 ---
    let currentPasscode = '';

    const authOverlay = document.getElementById('auth-overlay');
    const passcodeInput = document.getElementById('passcode-input');
    const authErrorMsg = document.getElementById('auth-error-msg');
    const btnSubmitPass = document.getElementById('btn-submit-pass');
    const btnTogglePassVisibility = document.getElementById('btn-toggle-pass-visibility');

    if (btnTogglePassVisibility) {
      btnTogglePassVisibility.addEventListener('click', (e) => {
        e.stopPropagation();
        if (passcodeInput.type === 'password') {
          passcodeInput.type = 'text';
          btnTogglePassVisibility.innerText = '👁️‍🗨️';
        } else {
          passcodeInput.type = 'password';
          btnTogglePassVisibility.innerText = '👁️';
        }
      });
    }

    function showAuthOverlay(errorText = '') {
      authOverlay.classList.add('active');
      if (errorText) {
        authErrorMsg.innerText = errorText;
      }
      if (passcodeInput) {
        passcodeInput.value = '';
        passcodeInput.type = 'password';
        if (btnTogglePassVisibility) btnTogglePassVisibility.innerText = '👁️';
        setTimeout(() => passcodeInput.focus(), 150);
      }
    }

    function hideAuthOverlay() {
      authOverlay.classList.remove('active');
      authErrorMsg.innerText = '';
    }

    function submitPasscode(code) {
      currentPasscode = (code || '').trim();
      if (!currentPasscode) {
        authErrorMsg.innerText = 'パスコードを入力してください';
        return;
      }
      authErrorMsg.innerText = '認証中...';
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'auth', passcode: currentPasscode }));
      } else {
        connectWS();
      }
    }

    if (btnSubmitPass) {
      btnSubmitPass.addEventListener('click', () => {
        submitPasscode(passcodeInput.value);
      });
    }

    if (passcodeInput) {
      passcodeInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          submitPasscode(passcodeInput.value);
        }
      });
    }

    // 起動時のパスコード取得（URLクエリパラメータ最優先、次点でsessionStorage）
    const urlParams = new URLSearchParams(window.location.search);
    const urlPasscode = urlParams.get('code') || urlParams.get('passcode') || '';

    if (urlPasscode) {
      currentPasscode = urlPasscode;
      sessionStorage.setItem('beamerplus_passcode', currentPasscode);
    } else {
      currentPasscode = sessionStorage.getItem('beamerplus_passcode') || '';
    }

    // --- WebSocket 接続 ---
    const wsUrl = `ws://${location.hostname}:__WS_PORT__`;
    let ws = null;

    function connectWS() {
      if (!currentPasscode) {
        showAuthOverlay();
        return;
      }

      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        // パスコードで認証要求
        ws.send(JSON.stringify({
          type: 'auth',
          passcode: currentPasscode
        }));
      };

      ws.onclose = () => {
        statusDot.className = '';
        statusDot.title = '切断中 (再接続中...)';
        if (currentPasscode) {
          setTimeout(connectWS, 2000);
        }
      };

      ws.onerror = (e) => {
        ws.close();
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'auth_result') {
            if (msg.success) {
              sessionStorage.setItem('beamerplus_passcode', currentPasscode);
              hideAuthOverlay();
              statusDot.className = 'online';
              statusDot.title = '接続済み';
            } else {
              sessionStorage.removeItem('beamerplus_passcode');
              currentPasscode = '';
              showAuthOverlay(msg.message || 'パスコードが正しくありません');
            }
            return;
          }
          handleServerMessage(msg);
        } catch (e) {
          console.error(e);
        }
      };
    }

    if (currentPasscode) {
      connectWS();
    } else {
      showAuthOverlay();
    }

    function sendWS(data) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
      }
    }

    // サーバーからの通知処理
    function handleServerMessage(msg) {
      if (msg.type === 'state_sync' || msg.type === 'page_changed') {
        currentPage = msg.currentPage !== undefined ? msg.currentPage : msg.page;
        totalPages = msg.totalPages;
        pageIndicator.innerText = `${currentPage + 1} / ${totalPages}`;
        slideStrokes = msg.strokes || [];
        if (msg.sketchbookStrokes !== undefined) {
          sketchbookStrokes = msg.sketchbookStrokes;
        }
        if (msg.links !== undefined) {
          currentLinks = msg.links;
        }
        currentStrokePoints = [];
        clearAllCanvases();
        renderAllStrokes(isSketchbook ? sketchbookStrokes : slideStrokes);
        loadSlideImage(currentPage);
        if (msg.type === 'state_sync') {
          if (msg.color) toolColors.pen = msg.color;
          if (msg.highlighterColor) toolColors.highlighter = msg.highlighterColor;
          if (currentTool === 'highlighter') {
            applyColor(toolColors.highlighter, 'highlighter');
          } else {
            applyColor(toolColors.pen, 'pen');
          }
          if (msg.penOpacity !== undefined) toolOpacities.pen = msg.penOpacity;
          if (msg.highlighterOpacity !== undefined) toolOpacities.highlighter = msg.highlighterOpacity;
          if (msg.toolRanges) Object.assign(toolRanges, msg.toolRanges);
          updateSizeUI();
          updateEraserModeUI();
          updateLineModeUI();
          updatePointerTrailUI();
          if (msg.isBlank !== undefined) updateBlankUI(msg.isBlank);
          if (msg.isSketchVisible !== undefined) updateSketchVisibilityUI(msg.isSketchVisible);
          if (msg.isSketchbook !== undefined) {
            updateSketchbookUI(msg.isSketchbook, msg.isSketchbook ? sketchbookStrokes : slideStrokes);
          }
        }
      } else if (msg.type === 'blank_changed') {
        updateBlankUI(msg.enabled);
      } else if (msg.type === 'sketchbook_changed') {
        updateSketchbookUI(msg.enabled, msg.strokes);
      } else if (msg.type === 'sketch_visibility_changed') {
        updateSketchVisibilityUI(msg.visible);
      } else if (msg.type === 'sketch_saved') {
        showToast(`✓ PC側保存: ${msg.filename}`);
      } else if (msg.type === 'strokes_cleared') {
        clearAllCanvases();
        if (isSketchbook) {
          sketchbookStrokes = [];
        } else {
          slideStrokes = [];
        }
      } else if (msg.type === 'strokes_updated' || msg.type === 'stroke_undone') {
        clearAllCanvases();
        if (msg.strokes) {
          if (isSketchbook) {
            sketchbookStrokes = msg.strokes;
          } else {
            slideStrokes = msg.strokes;
          }
          renderAllStrokes(msg.strokes);
        }
      } else if (msg.type === 'remote_stroke_start') {
        remoteStroke = {
          color: msg.color,
          width_ratio: msg.widthRatio,
          opacity: msg.opacity,
          points: [msg.p0]
        };
        activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
        drawStroke(activeCtx, remoteStroke);
      } else if (msg.type === 'remote_stroke_move') {
        if (remoteStroke) {
          remoteStroke.points.push([msg.normX, msg.normY]);
          activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
          drawStroke(activeCtx, remoteStroke);
        }
      } else if (msg.type === 'remote_stroke_end') {
        if (remoteStroke) {
          activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
          drawStroke(committedCtx, remoteStroke);
          if (isSketchbook) {
            sketchbookStrokes.push(remoteStroke);
          } else {
            slideStrokes.push(remoteStroke);
          }
          remoteStroke = null;
        }
      } else if (msg.type === 'pointer_updated') {
        updateLaserDot(msg.normX, msg.normY, msg.visible, !!msg.isDown);
      } else if (msg.type === 'color_changed') {
        const tool = msg.tool || 'pen';
        if (toolColors[tool] !== undefined) {
          toolColors[tool] = msg.color;
        }
        if (currentTool === tool || (!currentTool && tool === 'pen')) {
          applyColor(msg.color, tool);
        }
      }
    }

    // --- スライド画像の読み込みとリサイズ ---
    function loadSlideImage(page) {
      const codeParam = currentPasscode ? `&code=${encodeURIComponent(currentPasscode)}` : '';
      slideImg.src = `/api/slide?page=${page}${codeParam}&t=${Date.now()}`;
      slideImg.onload = () => {
        adjustLayout();
      };
    }

    function adjustLayout() {
      const stageW = window.innerWidth;
      const stageH = window.innerHeight;

      let imgW = slideImg.naturalWidth;
      let imgH = slideImg.naturalHeight;

      if (!imgW || !imgH) {
        // 画像未読込やスケッチブック時: 16:9 比率をフォールバックとして使用
        imgW = 1600;
        imgH = 900;
      }

      const scale = Math.min(stageW / imgW, stageH / imgH);
      const targetW = Math.max(100, Math.round(imgW * scale));
      const targetH = Math.max(100, Math.round(imgH * scale));

      slideWrapper.style.width = targetW + 'px';
      slideWrapper.style.height = targetH + 'px';

      // Canvas のピクセル解像度を Retina (devicePixelRatio) に合わせる
      const dpr = window.devicePixelRatio || 2;
      [committedCanvas, activeCanvas, pointerCanvas].forEach(c => {
        c.width = targetW * dpr;
        c.height = targetH * dpr;
      });
      [committedCtx, activeCtx, pointerCtx].forEach(ctx => {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.scale(dpr, dpr);
      });

      // Canvas サイズ変更により描画バッファがクリアされるため、保持しているストロークを必ず再描画
      const currentStrokes = isSketchbook ? sketchbookStrokes : slideStrokes;
      renderAllStrokes(currentStrokes);
    }

    window.addEventListener('resize', adjustLayout);

    function clearAllCanvases() {
      committedCtx.clearRect(0, 0, committedCanvas.width, committedCanvas.height);
      activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
      pointerCtx.clearRect(0, 0, pointerCanvas.width, pointerCanvas.height);
    }

    function drawStroke(targetCtx, stroke) {
      if (!stroke.points || stroke.points.length === 0) return;
      const pts = stroke.points;
      const w = slideWrapper.clientWidth;
      const h = slideWrapper.clientHeight;
      const minDim = Math.min(w, h);

      targetCtx.beginPath();
      targetCtx.strokeStyle = stroke.color;
      targetCtx.fillStyle = stroke.color;
      targetCtx.lineCap = 'round';
      targetCtx.lineJoin = 'round';
      targetCtx.globalAlpha = stroke.opacity !== undefined ? stroke.opacity : 0.85;
      const lineWidth = Math.max(1.5, minDim * (stroke.width_ratio || 0.005));
      targetCtx.lineWidth = lineWidth;

      if (pts.length === 1) {
        // 単一タップ時のドット描画
        const px = pts[0][0] * w;
        const py = pts[0][1] * h;
        targetCtx.arc(px, py, lineWidth / 2, 0, Math.PI * 2);
        targetCtx.fill();
        targetCtx.globalAlpha = 1.0;
        return;
      }

      const p0x = pts[0][0] * w;
      const p0y = pts[0][1] * h;
      targetCtx.moveTo(p0x, p0y);

      if (pts.length === 2) {
        targetCtx.lineTo(pts[1][0] * w, pts[1][1] * h);
      } else {
        // 中点二次ベジェスプライン曲線（C1連続）による滑らかな曲線補間
        const p1x = pts[1][0] * w;
        const p1y = pts[1][1] * h;
        targetCtx.lineTo((p0x + p1x) * 0.5, (p0y + p1y) * 0.5);

        for (let i = 1; i < pts.length - 1; i++) {
          const cpx = pts[i][0] * w;
          const cpy = pts[i][1] * h;
          const nextX = pts[i + 1][0] * w;
          const nextY = pts[i + 1][1] * h;
          const midX = (cpx + nextX) * 0.5;
          const midY = (cpy + nextY) * 0.5;
          targetCtx.quadraticCurveTo(cpx, cpy, midX, midY);
        }
        targetCtx.lineTo(pts[pts.length - 1][0] * w, pts[pts.length - 1][1] * h);
      }

      targetCtx.stroke();
      targetCtx.globalAlpha = 1.0;
    }

    function renderAllStrokes(strokes) {
      clearAllCanvases();
      for (const stroke of strokes) {
        drawStroke(committedCtx, stroke);
      }
    }

    function drawActiveStroke() {
      activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
      if (currentStrokePoints.length === 0) return;
      drawStroke(activeCtx, {
        color: currentColor,
        width_ratio: getCurrentWidthRatio(),
        opacity: getToolOpacity(),
        points: currentStrokePoints
      });
    }

    // --- 入力デバイス（Apple Pencil / Galaxy S-Pen / 指タッチ）のクロスプラットフォーム判定 ---
    let lastPointerType = 'touch';
    window.addEventListener('pointerdown', (e) => {
      lastPointerType = e.pointerType;
    }, { capture: true, passive: true });

    window.addEventListener('pointermove', (e) => {
      if (e.buttons !== 0) {
        lastPointerType = e.pointerType;
      }
    }, { capture: true, passive: true });

    window.addEventListener('pointerup', (e) => {
      if (e.pointerType === 'pen') {
        setTimeout(() => {
          if (lastPointerType === 'pen') lastPointerType = 'touch';
        }, 120);
      } else {
        lastPointerType = 'touch';
      }
    }, { capture: true, passive: true });

    window.addEventListener('pointercancel', (e) => {
      lastPointerType = 'touch';
    }, { capture: true, passive: true });

    // --- スタイラス / タッチ座標正規化 ---
    function getNormalizedPos(e) {
      const touch = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]);
      const rect = slideWrapper.getBoundingClientRect();
      const clientX = touch ? touch.clientX : 0;
      const clientY = touch ? touch.clientY : 0;
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      // Apple Pencil (WebKit touchType) または W3C Pointer Events (Android S-Pen 等) のいずれかでペン判定
      const isStylus = (touch && touch.touchType === 'stylus') || (lastPointerType === 'pen');
      return {
        normX: Math.max(0, Math.min(1, rect.width > 0 ? x / rect.width : 0)),
        normY: Math.max(0, Math.min(1, rect.height > 0 ? y / rect.height : 0)),
        touch: touch,
        isStylus: isStylus,
        force: (touch && typeof touch.force === 'number' && touch.force > 0) ? touch.force : 0.6
      };
    }

    // --- スワイプページ送り管理 ---
    function startSwipe(touch) {
      if (!touch) return;
      swipeTouchId = touch.identifier;
      swipeStartX = touch.clientX;
      swipeStartY = touch.clientY;
      swipeStartTime = Date.now();
      isTrackingSwipe = true;
    }

    function checkSwipe(touch) {
      if (!isTrackingSwipe || !touch) return false;
      if (touch.identifier !== swipeTouchId) return false;
      isTrackingSwipe = false;

      const dx = touch.clientX - swipeStartX;
      const dy = touch.clientY - swipeStartY;
      const dt = Date.now() - swipeStartTime;

      // 50ms〜800msの間、水平方向に45px以上、縦ブレよりも横移動が主（1.2倍以上）
      if (dt >= 50 && dt < 800 && Math.abs(dx) >= 45 && Math.abs(dx) > Math.abs(dy) * 1.2) {
        if (dx < 0) {
          sendWS({ type: 'next_page' });
        } else {
          sendWS({ type: 'prev_page' });
        }
        return true;
      }
      return false;
    }

    // タッチ開始
    activeCanvas.addEventListener('touchstart', (e) => {
      const pos = getNormalizedPos(e);
      if (!pos.touch) return;
      const penOnly = penOnlyToggle.checked;

      // スワイプ可能条件:
      // A. ツールが未選択 (currentTool === null) の場合: 指・ペンを問わずスワイプ可能
      // B. ツール選択中だが「Pencilのみ描画」ONかつ指でのタッチ (!pos.isStylus) の場合: 指スワイプ可能
      const canSwipe = (!currentTool) || (!pos.isStylus && penOnly);
      if (canSwipe) {
        startSwipe(pos.touch);
      }

      if (!currentTool) {
        // どのツールも選択されていない場合は描画・消去・ポインタを行わずスワイプ専用とする
        e.preventDefault();
        return;
      }

      if (!pos.isStylus && penOnly) {
        // Pencilのみ描画がONなら指での描画は無視
        e.preventDefault();
        return;
      }

      e.preventDefault(); // スクリブルやスクロールを防止

      if (currentTool === 'pointer') {
        // ポインタ操作は Pointer Events (ホバー＆タッチ対応) で一元処理
        return;
      }

      if (currentTool === 'eraser') {
        sendWS({
          type: 'erase',
          normX: pos.normX,
          normY: pos.normY,
          mode: eraserMode,
          size: toolSizes.eraser || 25
        });
        return;
      }

      // ペン / 蛍光ペン描画（均一な透明度・太さで描画）
      isDrawing = true;
      if (isLineMode) {
        currentStrokePoints = [[pos.normX, pos.normY], [pos.normX, pos.normY]];
      } else {
        currentStrokePoints = [[pos.normX, pos.normY]];
      }
      drawActiveStroke();

      sendWS({
        type: 'stroke_start',
        tool: currentTool,
        color: currentColor,
        size: toolSizes[currentTool] || 5,
        widthRatio: getCurrentWidthRatio(),
        normX: pos.normX,
        normY: pos.normY,
        pressure: 1.0,
        isLine: isLineMode
      });
    }, { passive: false });

    // タッチ移動
    activeCanvas.addEventListener('touchmove', (e) => {
      const pos = getNormalizedPos(e);
      if (!pos.touch) return;
      const penOnly = penOnlyToggle.checked;

      // スワイプ追跡中、または描画処理中はブラウザのデフォルトスクロールを完全に防止
      e.preventDefault();

      if (!currentTool) return;
      if (!pos.isStylus && penOnly) return;

      if (currentTool === 'pointer') {
        // ポインタ操作は Pointer Events (ホバー＆タッチ対応) で一元処理
        return;
      }

      if (currentTool === 'eraser') {
        sendWS({
          type: 'erase',
          normX: pos.normX,
          normY: pos.normY,
          mode: eraserMode,
          size: toolSizes.eraser || 25
        });
        return;
      }

      if (!isDrawing) return;

      if (isLineMode) {
        // 直線モード: 始点から現在位置までの2点ストローク
        let endX = pos.normX;
        let endY = pos.normY;
        if (currentStrokePoints.length > 0) {
          const p0 = currentStrokePoints[0];
          const dx = (endX - p0[0]) * slideWrapper.clientWidth;
          const dy = (endY - p0[1]) * slideWrapper.clientHeight;
          const angleDeg = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI);
          // 水平スナップ (±5度以内)
          if (angleDeg < 5 || angleDeg > 175) {
            endY = p0[1];
          } else if (Math.abs(angleDeg - 90) < 5) {
            // 垂直スナップ (±5度以内)
            endX = p0[0];
          }
          currentStrokePoints = [p0, [endX, endY]];
        }
        drawActiveStroke();
        sendWS({
          type: 'line_update',
          normX: endX,
          normY: endY
        });
        return;
      }

      // 手ブレ・静止時の微小ジッター抑制（約1.5px未満の微小移動は間引く）
      if (currentStrokePoints.length > 0) {
        const last = currentStrokePoints[currentStrokePoints.length - 1];
        const dx = (pos.normX - last[0]) * slideWrapper.clientWidth;
        const dy = (pos.normY - last[1]) * slideWrapper.clientHeight;
        if (dx * dx + dy * dy < 2.25) {
          return;
        }
      }

      currentStrokePoints.push([pos.normX, pos.normY]);
      drawActiveStroke();

      sendWS({
        type: 'stroke_move',
        normX: pos.normX,
        normY: pos.normY,
        pressure: 1.0
      });
    }, { passive: false });

    // タッチ終了
    const onTouchEnd = (e) => {
      const pos = getNormalizedPos(e);
      const touch = pos.touch || (e.changedTouches && e.changedTouches[0]);

      // スワイプ判定を実行
      if (touch && checkSwipe(touch)) {
        if (isDrawing) {
          isDrawing = false;
          currentStrokePoints = [];
          activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
          sendWS({ type: 'stroke_end' });
        }
        return;
      }
      isTrackingSwipe = false;

      // タップによるリンク操作判定（スワイプせず、描画も行われなかった場合）
      if (!isDrawing && touch) {
        const penOnly = penOnlyToggle.checked;
        const canLink = (!currentTool) || (!pos.isStylus && penOnly);
        if (canLink && pos.normX !== undefined && pos.normY !== undefined) {
          const dx = touch.clientX - swipeStartX;
          const dy = touch.clientY - swipeStartY;
          if (Math.hypot(dx, dy) < 15) {
            const link = findLinkAt(pos.normX, pos.normY);
            if (link) {
              if (link.page !== null && link.page !== undefined) {
                sendWS({ type: 'set_page', page: link.page });
              } else if (link.uri) {
                window.open(link.uri, '_blank');
              }
            }
          }
        }
      }

      if (currentTool === 'pointer') {
        // ポインタ操作は Pointer Events (ホバー＆タッチ対応) で一元処理
        return;
      }

      if (isDrawing) {
        isDrawing = false;
        // 終点を確実に反映（フリーハンド時のみ）
        if (!isLineMode && currentStrokePoints.length > 0 && pos.normX !== undefined) {
          const last = currentStrokePoints[currentStrokePoints.length - 1];
          const dx = (pos.normX - last[0]) * slideWrapper.clientWidth;
          const dy = (pos.normY - last[1]) * slideWrapper.clientHeight;
          if (dx * dx + dy * dy >= 1.0) {
            currentStrokePoints.push([pos.normX, pos.normY]);
            sendWS({
              type: 'stroke_move',
              normX: pos.normX,
              normY: pos.normY,
              pressure: 1.0
            });
          }
        }
        activeCtx.clearRect(0, 0, activeCanvas.width, activeCanvas.height);
        if (currentStrokePoints.length > 0) {
          const finishedStroke = {
            color: currentColor,
            width_ratio: getCurrentWidthRatio(),
            opacity: getToolOpacity(),
            points: currentStrokePoints
          };
          drawStroke(committedCtx, finishedStroke);
          if (isSketchbook) {
            sketchbookStrokes.push(finishedStroke);
          } else {
            slideStrokes.push(finishedStroke);
          }
        }
        currentStrokePoints = [];
        sendWS({ type: 'stroke_end' });
      }
    };

    activeCanvas.addEventListener('touchend', onTouchEnd, { passive: false });
    activeCanvas.addEventListener('touchcancel', onTouchEnd, { passive: false });

    // スライド外（stage）タッチ時のスワイプ対応
    stage.addEventListener('touchstart', (e) => {
      if (e.target !== stage) return;
      const touch = e.touches[0];
      if (touch) {
        startSwipe(touch);
        e.preventDefault();
      }
    }, { passive: false });

    stage.addEventListener('touchmove', (e) => {
      if (e.target !== stage) return;
      if (isTrackingSwipe) {
        e.preventDefault();
      }
    }, { passive: false });

    stage.addEventListener('touchend', (e) => {
      if (e.target !== stage) return;
      const touch = e.changedTouches[0];
      if (touch) {
        checkSwipe(touch);
      }
      isTrackingSwipe = false;
    }, { passive: false });

    stage.addEventListener('touchcancel', (e) => {
      if (e.target !== stage) return;
      const touch = e.changedTouches[0];
      if (touch) {
        checkSwipe(touch);
      }
      isTrackingSwipe = false;
    }, { passive: false });

    // UIバー上でのスクロール・タップがスワイプや描画と競合しないよう伝播を止める
    uiBar.addEventListener('touchstart', (e) => e.stopPropagation(), { passive: true });
    uiBar.addEventListener('touchmove', (e) => e.stopPropagation(), { passive: true });
    uiBar.addEventListener('touchend', (e) => e.stopPropagation(), { passive: true });

    // ポインタの一時的な軌跡（トレイル）管理
    let currentTrail = [];
    let fadingTrails = []; // [{ points: [{x, y, t}, ...], releaseTime: number }]
    let pointerIsDown = false;
    let trailAnimId = null;

    function addPointerTrailPoint(nx, ny) {
      currentTrail.push({ x: nx, y: ny, t: Date.now() });
      if (!trailAnimId) {
        trailAnimId = requestAnimationFrame(renderPointerTrail);
      }
    }

    function renderPointerTrail() {
      const now = Date.now();
      const lifetime = 450;      // 0.45秒
      const fadeDuration = 250;  // 0.25秒

      if (currentTrail.length > 0) {
        currentTrail = currentTrail.filter(p => now - p.t < lifetime);
      }
      fadingTrails = fadingTrails.filter(t => now - t.releaseTime < fadeDuration);

      pointerCtx.clearRect(0, 0, pointerCanvas.width, pointerCanvas.height);

      const trailsToDraw = [];

      // 1. 離されたフェードアウト中ストローク
      for (const t of fadingTrails) {
        const elapsed = now - t.releaseTime;
        const alpha = Math.max(0, Math.min(1, 1 - elapsed / fadeDuration));
        if (alpha > 0 && t.points.length >= 2) {
          trailsToDraw.push({ points: t.points, alpha: alpha });
        }
      }

      // 2. 現在ドラッグ中のストローク
      if (currentTrail.length >= 2) {
        trailsToDraw.push({ points: currentTrail, alpha: 1.0 });
      }

      if (trailsToDraw.length > 0) {
        const w = slideWrapper.clientWidth;
        const h = slideWrapper.clientHeight;
        const baseDim = Math.min(w, h);
        const baseW = Math.max(4, baseDim * 0.006);

        pointerCtx.lineCap = 'round';
        pointerCtx.lineJoin = 'round';

        for (const item of trailsToDraw) {
          const pts = item.points;
          const alpha = item.alpha;

          pointerCtx.beginPath();
          const p0x = pts[0].x * w;
          const p0y = pts[0].y * h;
          pointerCtx.moveTo(p0x, p0y);

          if (pts.length === 2) {
            pointerCtx.lineTo(pts[1].x * w, pts[1].y * h);
          } else {
            const p1x = pts[1].x * w;
            const p1y = pts[1].y * h;
            pointerCtx.lineTo((p0x + p1x) * 0.5, (p0y + p1y) * 0.5);

            for (let i = 1; i < pts.length - 1; i++) {
              const cpx = pts[i].x * w;
              const cpy = pts[i].y * h;
              const nextX = pts[i + 1].x * w;
              const nextY = pts[i + 1].y * h;
              const midX = (cpx + nextX) * 0.5;
              const midY = (cpy + nextY) * 0.5;
              pointerCtx.quadraticCurveTo(cpx, cpy, midX, midY);
            }
            const lastX = pts[pts.length - 1].x * w;
            const lastY = pts[pts.length - 1].y * h;
            pointerCtx.lineTo(lastX, lastY);
          }

          // 1. 外側の光彩（グロー）: 一括描画
          pointerCtx.strokeStyle = `rgba(255, 30, 30, ${0.45 * alpha})`;
          pointerCtx.lineWidth = baseW * 2.4;
          pointerCtx.stroke();

          // 2. 中心の明るい線（コア）: 一括描画
          pointerCtx.strokeStyle = `rgba(255, 90, 90, ${0.95 * alpha})`;
          pointerCtx.lineWidth = baseW;
          pointerCtx.stroke();
        }
      }

      if (currentTrail.length > 0 || fadingTrails.length > 0) {
        trailAnimId = requestAnimationFrame(renderPointerTrail);
      } else {
        trailAnimId = null;
      }
    }

    function updateLaserDot(normX, normY, visible, isDown) {
      const prevDown = pointerIsDown;
      pointerIsDown = !!isDown;
      const now = Date.now();

      if (visible) {
        laserDot.style.display = 'block';
        laserDot.style.left = (normX * 100) + '%';
        laserDot.style.top = (normY * 100) + '%';
        if (isDown) {
          if (!prevDown) {
            // 新しいストロークの開始（前のストロークとは完全に分離）
            if (currentTrail.length >= 2) {
              fadingTrails.push({ points: [...currentTrail], releaseTime: now });
            }
            currentTrail = [{ x: normX, y: normY, t: now }];
            if (!trailAnimId) {
              trailAnimId = requestAnimationFrame(renderPointerTrail);
            }
          } else {
            addPointerTrailPoint(normX, normY);
          }
        } else if (prevDown && !isDown) {
          // 離した瞬間
          if (currentTrail.length >= 2) {
            fadingTrails.push({ points: [...currentTrail], releaseTime: now });
          }
          currentTrail = [];
        }
      } else {
        laserDot.style.display = 'none';
        if (prevDown) {
          if (currentTrail.length >= 2) {
            fadingTrails.push({ points: [...currentTrail], releaseTime: now });
          }
          currentTrail = [];
        }
      }
    }

    // --- Apple Pencil ホバー & タッチ対応レーザーポインタ (Pointer Events) ---
    let hoverTimeoutId = null;

    function getPointerNormPos(e) {
      const rect = slideWrapper.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const normX = rect.width > 0 ? x / rect.width : 0;
      const normY = rect.height > 0 ? y / rect.height : 0;
      const isInside = normX >= 0 && normX <= 1 && normY >= 0 && normY <= 1;
      // buttons: 1 (主ボタン/タッチ), pressure > 0 (ペン接触)
      const isDown = (e.buttons & 1) !== 0 || (typeof e.pressure === 'number' && e.pressure > 0);
      return {
        normX: Math.max(0, Math.min(1, normX)),
        normY: Math.max(0, Math.min(1, normY)),
        isInside,
        isDown,
        pointerType: e.pointerType
      };
    }

    activeCanvas.addEventListener('pointermove', (e) => {
      if (currentTool !== 'pointer') return;
      const penOnly = penOnlyToggle.checked;
      if (e.pointerType === 'touch' && penOnly) return;

      if (hoverTimeoutId) {
        clearTimeout(hoverTimeoutId);
        hoverTimeoutId = null;
      }

      const pos = getPointerNormPos(e);
      if (!pos.isInside) {
        updateLaserDot(0, 0, false, false);
        sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
        return;
      }

      const effectiveDown = isPointerTrailEnabled ? pos.isDown : false;
      updateLaserDot(pos.normX, pos.normY, true, effectiveDown);
      sendWS({ type: 'pointer_move', normX: pos.normX, normY: pos.normY, visible: true, isDown: effectiveDown });
    });

    activeCanvas.addEventListener('pointerdown', (e) => {
      if (currentTool !== 'pointer') return;
      const penOnly = penOnlyToggle.checked;
      if (e.pointerType === 'touch' && penOnly) return;

      if (hoverTimeoutId) {
        clearTimeout(hoverTimeoutId);
        hoverTimeoutId = null;
      }

      e.preventDefault();
      const pos = getPointerNormPos(e);
      const effectiveDown = isPointerTrailEnabled;
      updateLaserDot(pos.normX, pos.normY, true, effectiveDown);
      sendWS({ type: 'pointer_move', normX: pos.normX, normY: pos.normY, visible: true, isDown: effectiveDown });
    });

    activeCanvas.addEventListener('pointerup', (e) => {
      if (currentTool !== 'pointer') return;
      const penOnly = penOnlyToggle.checked;
      if (e.pointerType === 'touch' && penOnly) return;

      if (hoverTimeoutId) {
        clearTimeout(hoverTimeoutId);
        hoverTimeoutId = null;
      }

      const pos = getPointerNormPos(e);
      // Apple Pencil 等のスタイラスペンで画面近傍にいる場合:
      // 直後にホバーの pointermove が来ればホバーを維持。
      // ホバー非対応ペン（またはペンを完全に離した場合）は 80ms 以内にイベントが来ないため自動でドットも消去する。
      if (e.pointerType === 'pen' && pos.isInside) {
        updateLaserDot(pos.normX, pos.normY, true, false);
        sendWS({ type: 'pointer_move', normX: pos.normX, normY: pos.normY, visible: true, isDown: false });

        hoverTimeoutId = setTimeout(() => {
          updateLaserDot(0, 0, false, false);
          sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
          hoverTimeoutId = null;
        }, 80);
      } else {
        updateLaserDot(0, 0, false, false);
        sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
      }
    });

    activeCanvas.addEventListener('pointerleave', (e) => {
      if (currentTool !== 'pointer') return;
      const penOnly = penOnlyToggle.checked;
      if (e.pointerType === 'touch' && penOnly) return;

      if (hoverTimeoutId) {
        clearTimeout(hoverTimeoutId);
        hoverTimeoutId = null;
      }

      updateLaserDot(0, 0, false, false);
      sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
    });

    activeCanvas.addEventListener('pointercancel', (e) => {
      if (currentTool !== 'pointer') return;
      if (hoverTimeoutId) {
        clearTimeout(hoverTimeoutId);
        hoverTimeoutId = null;
      }
      updateLaserDot(0, 0, false, false);
      sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
    });

    // --- 完全な色選択・カラーピッカー操作 ---
    function applyColor(hex, targetTool = null) {
      const tool = targetTool || (currentTool === 'highlighter' ? 'highlighter' : 'pen');
      if (toolColors[tool] !== undefined) {
        toolColors[tool] = hex;
      }
      currentColor = hex;
      if (colorPicker) colorPicker.value = hex;
      if (colorIndicator) colorIndicator.style.backgroundColor = hex;
    }

    if (colorPicker) {
      colorPicker.addEventListener('input', (e) => {
        applyColor(e.target.value);
        const tool = (currentTool === 'highlighter') ? 'highlighter' : 'pen';
        sendWS({ type: 'set_color', tool: tool, color: e.target.value });
      });
      colorPicker.addEventListener('change', (e) => {
        applyColor(e.target.value);
        const tool = (currentTool === 'highlighter') ? 'highlighter' : 'pen';
        sendWS({ type: 'set_color', tool: tool, color: e.target.value });
      });
    }

    // --- ツール選択・UI操作 ---
    function selectTool(toolName) {
      if (currentTool === toolName) {
        // 使用しているツールのボタンを押したら選択解除（未選択状態にする）
        if (currentTool === 'pointer') {
          if (hoverTimeoutId) { clearTimeout(hoverTimeoutId); hoverTimeoutId = null; }
          if (laserDot) laserDot.style.display = 'none';
          currentTrail = [];
          fadingTrails = [];
          pointerCtx.clearRect(0, 0, pointerCanvas.width, pointerCanvas.height);
          sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
        }
        currentTool = null;
        isLineMode = false;
      } else {
        if (currentTool === 'pointer' && toolName !== 'pointer') {
          if (hoverTimeoutId) { clearTimeout(hoverTimeoutId); hoverTimeoutId = null; }
          if (laserDot) laserDot.style.display = 'none';
          currentTrail = [];
          fadingTrails = [];
          pointerCtx.clearRect(0, 0, pointerCanvas.width, pointerCanvas.height);
          sendWS({ type: 'pointer_move', normX: 0, normY: 0, visible: false, isDown: false });
        }
        currentTool = toolName;
        if (toolName === 'eraser' || toolName === 'pointer') {
          isLineMode = false;
        }
        if (toolName === 'highlighter') {
          applyColor(toolColors.highlighter, 'highlighter');
        } else if (toolName === 'pen') {
          applyColor(toolColors.pen, 'pen');
        }
      }

      [btnPen, btnHighlighter, btnEraser, btnPointer].forEach(b => {
        b.classList.toggle('active', currentTool !== null && b.dataset.tool === currentTool);
      });
      updateSizeUI();
      updateEraserModeUI();
      updateLineModeUI();
      updatePointerTrailUI();
    }

    [btnPen, btnHighlighter, btnEraser, btnPointer].forEach(btn => {
      btn.addEventListener('click', () => {
        selectTool(btn.dataset.tool);
      });
    });

    // ページ操作ボタン
    btnPrev.addEventListener('click', () => sendWS({ type: 'prev_page' }));
    btnNext.addEventListener('click', () => sendWS({ type: 'next_page' }));
    btnClear.addEventListener('click', () => {
      clearAllCanvases();
      sendWS({ type: 'clear' });
    });

    // UIバーの折りたたみ
    let isBarHidden = false;
    uiToggle.addEventListener('click', () => {
      isBarHidden = !isBarHidden;
      uiBar.style.display = isBarHidden ? 'none' : 'flex';
      uiToggle.innerText = isBarHidden ? 'バーを表示 ▼' : 'バーを隠す ▲';
    });

    // 初期状態の UI 反映（どのツールも未選択の状態）
    selectTool(null);
    updateEraserModeUI();
    updateLineModeUI();
    updatePointerTrailUI();
    adjustLayout();
  </script>
</body>
</html>
"""


# --- Qt シグナルブリッジ ---
class WebBridge(QObject):
    stroke_started = Signal(dict)
    stroke_moved = Signal(dict)
    stroke_ended = Signal()
    pointer_updated = Signal(float, float, bool, bool)
    erase_requested = Signal(dict)
    eraser_mode_command = Signal(str)  # "partial" または "stroke"
    line_mode_command = Signal(bool)    # 直線モード ON/OFF
    line_update_command = Signal(float, float) # 直線終点更新
    page_command = Signal(str, int)  # "next", "prev", "set", page_idx
    tool_command = Signal(str)
    tool_size_command = Signal(str, int)
    color_command = Signal(str, str)
    undo_requested = Signal()
    clear_requested = Signal()
    client_count_changed = Signal(int)
    blank_command = Signal(bool)
    sketchbook_command = Signal(bool)
    toggle_blank_command = Signal()
    toggle_sketchbook_command = Signal()
    sketch_visibility_command = Signal(bool)
    toggle_sketch_visibility_command = Signal()
    save_sketch_requested = Signal()


# --- HTTP リクエストハンドラ ---
class SlideRequestHandler(SimpleHTTPRequestHandler):
    image_provider: Callable[[int], bytes | None] | None = None
    status_provider: Callable[[], dict] | None = None
    ws_port: int = 8765
    passcode: str = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            content = HTML_CONTENT.replace("__WS_PORT__", str(SlideRequestHandler.ws_port))
            self.wfile.write(content.encode("utf-8"))
            return

        elif path == "/api/slide":
            req_code = params.get("code", [""])[0]
            if SlideRequestHandler.passcode and req_code != SlideRequestHandler.passcode:
                self.send_response(401)
                self.end_headers()
                return

            page_str = params.get("page", ["0"])[0]
            try:
                page_idx = int(page_str)
            except ValueError:
                page_idx = 0

            image_data = None
            if SlideRequestHandler.image_provider:
                image_data = SlideRequestHandler.image_provider(page_idx)

            if image_data:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(image_data)
            else:
                self.send_response(404)
                self.end_headers()
            return

        elif path == "/api/status":
            req_code = params.get("code", [""])[0]
            if SlideRequestHandler.passcode and req_code != SlideRequestHandler.passcode:
                self.send_response(401)
                self.end_headers()
                return

            status = {}
            if SlideRequestHandler.status_provider:
                status = SlideRequestHandler.status_provider()
            res = json.dumps(status).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res)
            return

        super().do_GET()

    def log_message(self, format, *args):
        pass  # ターミナルログ抑制


# --- WebServer 統合クラス ---
class WebServer:
    def __init__(
        self,
        bridge: WebBridge,
        image_provider: Callable[[int], bytes | None],
        status_provider: Callable[[], dict],
        http_port: int = 8080,
        ws_port: int = 8765,
        passcode: str | None = None,
    ):
        self.bridge = bridge
        self.image_provider = image_provider
        self.status_provider = status_provider
        self.http_port = http_port
        self.ws_port = ws_port
        self.passcode = str(passcode) if passcode else generate_passcode(8)

        SlideRequestHandler.image_provider = staticmethod(self.image_provider)
        SlideRequestHandler.status_provider = staticmethod(self.status_provider)
        SlideRequestHandler.passcode = self.passcode

        self.http_server: ThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.ws_thread: threading.Thread | None = None
        self.ws_loop: asyncio.AbstractEventLoop | None = None
        self.connected_clients: set = set()
        self.is_running: bool = False

    def set_passcode(self, new_passcode: str):
        """iPad 接続用パスコードを更新"""
        self.passcode = str(new_passcode).strip()
        SlideRequestHandler.passcode = self.passcode

    def start(self):
        if self.is_running:
            return
        self.is_running = True

        # 1. HTTP サーバー起動
        def run_http():
            ThreadingHTTPServer.allow_reuse_address = True
            for port in range(self.http_port, self.http_port + 10):
                try:
                    self.http_server = ThreadingHTTPServer(("0.0.0.0", port), SlideRequestHandler)
                    self.http_port = port
                    self.http_server.serve_forever()
                    break
                except OSError as e:
                    if port == self.http_port + 9:
                        print(f"[BeamerPlus] HTTP Server Error: {e}")

        self.http_thread = threading.Thread(target=run_http, daemon=True)
        self.http_thread.start()

        # 2. WebSocket サーバー起動
        def run_ws():
            self.ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.ws_loop)

            async def ws_handler(websocket):
                authenticated = False
                try:
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                        except Exception:
                            continue

                        msg_type = data.get("type")

                        if not authenticated:
                            if msg_type == "auth":
                                req_code = str(data.get("passcode", "")).strip()
                                if req_code == self.passcode:
                                    authenticated = True
                                    self.connected_clients.add(websocket)
                                    self.bridge.client_count_changed.emit(len(self.connected_clients))

                                    await websocket.send(json.dumps({
                                        "type": "auth_result",
                                        "success": True
                                    }))

                                    # 初期状態送信
                                    try:
                                        init_data = self.status_provider()
                                        init_data["type"] = "state_sync"
                                        await websocket.send(json.dumps(init_data))
                                    except Exception:
                                        pass
                                else:
                                    await websocket.send(json.dumps({
                                        "type": "auth_result",
                                        "success": False,
                                        "message": "パスコードが正しくありません"
                                    }))
                            continue

                        self._handle_client_message(data)

                except (websockets.exceptions.ConnectionClosed, ConnectionResetError, BrokenPipeError):
                    pass
                except Exception:
                    pass
                finally:
                    if authenticated:
                        self.connected_clients.discard(websocket)
                        self.bridge.client_count_changed.emit(len(self.connected_clients))

            async def main_ws():
                for port in range(self.ws_port, self.ws_port + 10):
                    try:
                        async with websockets.serve(ws_handler, "0.0.0.0", port):
                            self.ws_port = port
                            SlideRequestHandler.ws_port = port
                            await asyncio.Future()
                        break
                    except OSError:
                        if port == self.ws_port + 9:
                            raise

            try:
                self.ws_loop.run_until_complete(main_ws())
            except Exception as e:
                print(f"[BeamerPlus] WebSocket Server Error: {e}")

        self.ws_thread = threading.Thread(target=run_ws, daemon=True)
        self.ws_thread.start()

    def _handle_client_message(self, data: dict):
        msg_type = data.get("type")
        if msg_type == "stroke_start":
            self.bridge.stroke_started.emit(data)
        elif msg_type == "stroke_move":
            self.bridge.stroke_moved.emit(data)
        elif msg_type == "stroke_end":
            self.bridge.stroke_ended.emit()
        elif msg_type == "pointer_move":
            self.bridge.pointer_updated.emit(
                float(data.get("normX", 0.0)),
                float(data.get("normY", 0.0)),
                bool(data.get("visible", False)),
                bool(data.get("isDown", False))
            )
        elif msg_type == "erase":
            self.bridge.erase_requested.emit(data)
        elif msg_type == "set_eraser_mode":
            self.bridge.eraser_mode_command.emit(str(data.get("mode", "partial")))
        elif msg_type == "set_line_mode":
            self.bridge.line_mode_command.emit(bool(data.get("isLine", False)))
        elif msg_type == "line_update":
            self.bridge.line_update_command.emit(
                float(data.get("normX", 0.0)),
                float(data.get("normY", 0.0))
            )
        elif msg_type == "prev_page":
            self.bridge.page_command.emit("prev", 0)
        elif msg_type == "next_page":
            self.bridge.page_command.emit("next", 0)
        elif msg_type == "set_page":
            self.bridge.page_command.emit("set", int(data.get("page", 0)))
        elif msg_type == "set_tool":
            self.bridge.tool_command.emit(str(data.get("tool", "pen")))
        elif msg_type == "set_tool_size":
            self.bridge.tool_size_command.emit(
                str(data.get("tool", "pen")),
                int(data.get("size", 5))
            )
        elif msg_type == "set_color":
            self.bridge.color_command.emit(
                str(data.get("tool", "pen")),
                str(data.get("color", "#e53935"))
            )
        elif msg_type == "undo":
            self.bridge.undo_requested.emit()
        elif msg_type == "clear":
            self.bridge.clear_requested.emit()
        elif msg_type == "toggle_blank":
            self.bridge.toggle_blank_command.emit()
        elif msg_type == "set_blank":
            self.bridge.blank_command.emit(bool(data.get("enabled", False)))
        elif msg_type == "toggle_sketchbook":
            self.bridge.toggle_sketchbook_command.emit()
        elif msg_type == "set_sketchbook":
            self.bridge.sketchbook_command.emit(bool(data.get("enabled", False)))
        elif msg_type == "toggle_sketch_visibility":
            self.bridge.toggle_sketch_visibility_command.emit()
        elif msg_type == "set_sketch_visibility":
            self.bridge.sketch_visibility_command.emit(bool(data.get("visible", True)))
        elif msg_type == "save_sketch":
            self.bridge.save_sketch_requested.emit()

    def broadcast(self, message_dict: dict):
        """全接続クライアント（iPad）へメッセージをブロードキャスト"""
        if not self.connected_clients or not self.ws_loop:
            return

        payload = json.dumps(message_dict)

        async def _send():
            tasks = []
            for ws in list(self.connected_clients):
                if ws.state == State.OPEN:
                    tasks.append(ws.send(payload))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(_send(), self.ws_loop)

    def stop(self):
        if self.http_server:
            self.http_server.shutdown()
        if self.ws_loop:
            self.ws_loop.stop()
        self.is_running = False
