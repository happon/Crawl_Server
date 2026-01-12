PowerShell なら、表示されるパスは **prompt 関数**で自由に短くできます。VS Code のターミナルにもそのまま効きます。

## 1) いちばん短い：末尾フォルダ名だけ表示

1. VS Code の PowerShell ターミナルで、プロファイルを開きます。

   ```powershell
   code $PROFILE
   ```

   ※ ファイルが無いと言われたら作成します：

   ```powershell
   New-Item -Type File -Force $PROFILE
   code $PROFILE
   ```

2. 開いたファイルに以下を追記して保存：

   ```powershell
   function prompt {
     $leaf = Split-Path -Leaf (Get-Location)
     "$leaf> "
   }
   ```

3. 反映（どちらか）

   * ターミナルを開き直す
   * または今のセッションで読み直す：

     ```powershell
     . $PROFILE
     ```

これで `C:\Users\...\project\repo` のような表示が `repo>` になります。

---

## 2) 少し情報を残す：ドライブ名 + 末尾フォルダ

```powershell
function prompt {
  $p = Get-Location
  $drive = $p.Drive.Name
  $leaf = Split-Path -Leaf $p.Path
  "$drive:\$leaf> "
}
```

例：`C:\repo>`

---

## 3) 長いパスを途中省略：先頭 + … + 末尾2階層

```powershell
function prompt {
  $path = (Get-Location).Path
  $parts = $path -split '\\'
  if ($parts.Count -le 4) { return "$path> " }

  $head = ($parts[0..1] -join '\')           # 例: C:\Users
  $tail = ($parts[-2..-1] -join '\')         # 末尾2階層
  "$head\…\$tail> "
}
```

---

## 補足（該当する場合）

* **oh-my-posh / starship** を入れている場合は、上の `prompt` よりもテーマ側の設定が優先されることがあります。その場合は利用中のプロンプトツール名に合わせて設定変更が必要です。

まずは「末尾フォルダ名だけ」の案が一番シンプルです。今のプロンプトが oh-my-posh 等で装飾されている見た目なら、その旨だけ教えてください（それ前提の手順に切り替えます）。
