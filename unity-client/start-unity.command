#!/bin/zsh
# 使用工程指定的 Unity Editor；默认启动作战沙盘。
set -eu
project_path="${0:A:h}"
version=$(awk '/^m_EditorVersion:/{print $2; exit}' "$project_path/ProjectSettings/ProjectVersion.txt")
editor_path="${UNITY_EDITOR_PATH:-/Applications/Unity/Hub/Editor/$version/Unity.app/Contents/MacOS/Unity}"
action="${1:-Run}"
log_dir="$project_path/../.local/unity"
if [[ ! -x "$editor_path" ]]; then
  print -u2 "未找到 Unity $version：$editor_path"
  print -u2 '请先在 Unity Hub 安装对应编辑器，或设置 UNITY_EDITOR_PATH。'
  exit 1
fi
mkdir -p "$log_dir"
case "$action" in
  Open)
    exec "$editor_path" -projectPath "$project_path" -logFile "$log_dir/open-macos.log"
    ;;
  Run)
    exec "$editor_path" -projectPath "$project_path" \
      -executeMethod Qiongtu.Sandbox.Editor.SandboxProjectSetup.RunPreview \
      -logFile "$log_dir/run-macos.log"
    ;;
  Prepare|BuildWeb|BuildWindows)
    method="$action"
    [[ "$action" == Prepare ]] && method=PrepareScene
    args=(-batchmode -quit -projectPath "$project_path" \
      -executeMethod "Qiongtu.Sandbox.Editor.SandboxProjectSetup.$method" \
      -logFile "$log_dir/$action-macos.log")
    [[ "$action" == BuildWeb ]] && args+=(-buildTarget WebGL)
    [[ "$action" == BuildWindows ]] && args+=(-buildTarget Win64)
    exec "$editor_path" "${args[@]}"
    ;;
  *)
    print -u2 '用法：./start-unity.command [Open|Run|Prepare|BuildWeb|BuildWindows]'
    exit 2
    ;;
esac
