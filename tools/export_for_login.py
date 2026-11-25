# -*- coding: utf-8 -*-
"""把 turb 注册的账号导出成 GPT-Login-2FA-Manager 的导入文件。

login 工具的导入格式见其 src/store.js：只认最多三段，多一段直接报错——
    邮箱----取件地址----2FA密钥
取件地址只能是 wdmail 看信链接（login 工具的取件只解析 wdmail 页面），
所以只有能在 wdmail 清单里查到看信链接的账号才导出；查不到的计数跳过，
不生成登不进去的死条目。密码不进导入行：login 工具靠邮箱验证码登录，
账号密码由它自己的「初始化密码」流程在账号上重新设置，不需要旧密码。

用法：
    python tools/export_for_login.py --wdmail D:\\path\\备用邮箱.txt \
        --out D:\\Softwares\\GPT-Login-2FA-Manager\\import-from-turb.txt
    --db 默认取项目根目录的 turb.sqlite3。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WDMAIL_URL_RE = re.compile(r"https://api\.wdmail\.top/\S+")


def load_wdmail_links(path: Path) -> dict[str, str]:
    """解析 wdmail 备用邮箱清单：邮箱行在前，紧随的看信链接归该邮箱。"""
    links: dict[str, str] = {}
    current_email = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        url_match = WDMAIL_URL_RE.search(line)
        if url_match and current_email:
            links.setdefault(current_email, url_match.group(0).strip())
            continue
        email_match = EMAIL_RE.search(line)
        if email_match:
            current_email = email_match.group(0).lower()
    return links


def load_accounts(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # 现行表是 accounts；registered_accounts 是旧版迁移源，两边都兼容。
        table = "accounts" if "accounts" in tables else "registered_accounts"
        rows = conn.execute(f"SELECT payload FROM {table}").fetchall()
    finally:
        conn.close()
    accounts = []
    for (payload,) in rows:
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("email"):
            accounts.append(data)
    return accounts


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 turb 账号给 GPT-Login-2FA-Manager 导入")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "turb.sqlite3"))
    parser.add_argument("--wdmail", required=True, help="wdmail 备用邮箱清单文件路径")
    parser.add_argument("--out", default="import-from-turb.txt")
    args = parser.parse_args()

    links = load_wdmail_links(Path(args.wdmail))
    accounts = load_accounts(Path(args.db))

    lines: list[str] = []
    seen: set[str] = set()
    skipped_no_link = 0
    skipped_dup = 0
    for account in accounts:
        email = str(account.get("email") or "").strip().lower()
        if email in seen:
            skipped_dup += 1
            continue
        seen.add(email)
        url = links.get(email)
        if not url:
            skipped_no_link += 1
            continue
        totp = str(account.get("totp_secret") or "").strip()
        lines.append(f"{email}----{url}----{totp}" if totp else f"{email}----{url}")

    out_path = Path(args.out)
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"账号总数 {len(accounts)}：导出 {len(lines)}，"
          f"无 wdmail 看信链接跳过 {skipped_no_link}，重复跳过 {skipped_dup}")
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
