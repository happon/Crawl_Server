### パッケージのインストール
>pip install パッケージ

### パッケージのアップグレード
>python.exe -m パッケージ install --upgrade パッケージ

### pythonファイルの実行
>python python_file.py


### python仮想環境の作成
作成したいプロジェクトのトップディレクトリで
>python -m venv 仮想環境名

### python仮想環境作成時のエラー
''' txt

PS C:\Users\yufui\Desktop\クローラサーバ> .\CS\Scripts\activate
.\CS\Scripts\activate : このシステムではスクリプトの実行が無効になっているため、ファイル C:\Users\yufui\Desktop\クローラサーバ\CS\Scripts\Activate.ps1 を読み込むことができません。詳
細については、「about_Execution_Policies」(https://go.microsoft.com/fwlink/?LinkID=135170) を参照してください。
発生場所 行:1 文字:1
+ .\CS\Scripts\activate
+ ~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : セキュリティ エラー: (: ) []、PSSecurityException
    + FullyQualifiedErrorId : UnauthorizedAccess

'''

① 一時的に実行ポリシーを緩和（安全）
ターミナル（PowerShell）で以下を実行：
>Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
その後、仮想環境を再度アクティベート：
>.\CS\Scripts\Activate
この方法は「一時的」なので、PCを再起動すると元に戻ります（セキュリティ的に安全）

② 永続的に許可したい場合（自己PCでの開発用）
管理者権限でPowerShellを開き、次を実行：
>Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
⚠️ この方法は自己管理PCなど信頼できる環境でのみ使ってください。