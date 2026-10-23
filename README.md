# Digital Clock Overlay

Windows向けの背景透過デジタル時計です。

## 機能

- 時計を常に最前面に表示
- 背景を透過
- ドラッグで位置を変更
- 文字色・文字サイズを変更
- Windows起動時の自動起動を設定

## 操作方法

- 左ドラッグ：移動
- 右クリック：設定メニュー
- Escキー：終了

## Pythonから起動

Tkinterが利用できるPython環境で実行してください。

python digital_clock_overlay.py

## 表示されない場合

次のコマンドで、背景透過を無効にして位置をリセットできます。

python digital_clock_overlay.py --diagnostic --reset-position
