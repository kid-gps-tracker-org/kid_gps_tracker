#!/usr/bin/env python3
"""
Git コミット履歴からリリースノートを自動生成
"""
import subprocess
import sys
import re

def get_commits_since_last_tag(current_tag: str) -> list:
    """前回のタグからのコミットを取得"""
    result = subprocess.run(
        ['git', 'describe', '--abbrev=0', '--tags', f'{current_tag}^'],
        capture_output=True, text=True
    )

    prev_tag = result.stdout.strip() if result.returncode == 0 else None

    if prev_tag:
        cmd = ['git', 'log', f'{prev_tag}..{current_tag}', '--pretty=format:%s']
    else:
        cmd = ['git', 'log', current_tag, '--pretty=format:%s']

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip().split('\n') if result.stdout else []

def categorize_commits(commits: list) -> dict:
    """コミットをカテゴリ分け"""
    categories = {
        '新機能': [],
        'バグ修正': [],
        'ドキュメント': [],
        'その他': []
    }

    for commit in commits:
        if commit.startswith(('feat:', 'feature:', '機能:')):
            categories['新機能'].append(commit)
        elif commit.startswith(('fix:', '修正:')):
            categories['バグ修正'].append(commit)
        elif commit.startswith(('docs:', 'ドキュメント:')):
            categories['ドキュメント'].append(commit)
        else:
            categories['その他'].append(commit)

    return categories

def generate_release_notes(tag: str) -> str:
    """マークダウンのリリースノートを生成"""
    commits = get_commits_since_last_tag(tag)
    categories = categorize_commits(commits)

    notes = f"# リリース {tag}\n\n"

    for category, items in categories.items():
        if items:
            notes += f"## {category}\n\n"
            for item in items:
                cleaned = re.sub(r'^(feat|fix|docs|機能|修正):\s*', '', item)
                notes += f"- {cleaned}\n"
            notes += "\n"

    notes += "## インストール方法\n\n"
    notes += "お使いのデバイスに対応するファームウェアをダウンロードしてください：\n\n"
    notes += f"- `kid_gps_tracker_{tag.lstrip('v')}_nrf9151.zip` - nRF9151 DK用\n"
    notes += f"- `kid_gps_tracker_{tag.lstrip('v')}_nrf9160.zip` - nRF9160 DK用\n"
    notes += f"- `kid_gps_tracker_{tag.lstrip('v')}_nrf9161.zip` - nRF9161 DK用\n\n"
    notes += "nRF Cloud ポータルの Firmware Update → Firmware Bundles から"
    notes += "アップロードし、デバイスグループに対してFOTAジョブを作成してください。\n"

    return notes

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: release_notes.py <tag>")
        sys.exit(1)

    tag = sys.argv[1]
    print(generate_release_notes(tag))
