# 見開きPDF分割

横長の見開きページを、左右の単ページの PDF に分割する Mac アプリです。

Apple Silicon（M1以降）、macOS 13 以降で動きます。処理は Mac の中だけで行い、PDF を外部へ送りません。

## アプリを入手する

[Releases](https://github.com/moriyakeiichi/divide-pdf/releases/latest) の `divide-pdf-mac.zip` をダウンロードし、展開して `見開きPDF分割.app` を開きます。

初回に「開発元を確認できない」と出たときは、アプリを右クリックして「開く」を選んでください。Apple の公証は行っていません。

操作、保存先、分割位置、注釈の扱い、コマンド版の使い方は [使い方.md](使い方.md) にまとめています。

## ソースからビルドする

```sh
python3 -m venv .build-env
.build-env/bin/python -m pip install -r requirements-build.txt
.build-env/bin/python -m unittest discover -s tests -v
.build-env/bin/python build_apps.py
```

`divide_pdf.py` は、同じ分割をコマンドから行うスクリプトです。

## ライセンス

アプリは PyMuPDF（AGPL-3.0）と Python を同梱しています。ライセンス原文はアプリ内の `Contents/Resources/Licenses` にあります。このリポジトリが、配布するアプリに対応するソースです。
