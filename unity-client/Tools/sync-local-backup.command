#!/bin/zsh
# Fetch committed changes from the working repository into its independent local Git backup.
set -eu
repository_path="${0:A:h:h:h}"
backup_path="${repository_path:h}/noman-backup-20261103"
if [[ ! -d "$backup_path/.git" ]]; then
  print -u2 "未找到本地 Git 备份：$backup_path"
  exit 1
fi
if [[ "$(git -C "$backup_path" branch --show-current)" != '军队配置' ]]; then
  print -u2 '备份当前不在军队配置分支，已停止同步。'
  exit 1
fi
git -C "$backup_path" fetch workspace
git -C "$backup_path" merge --ff-only workspace/军队配置
print 'Git 备份已同步最新本地提交；修改前版本仍保留在 backup/before-barracks-20261103。'
