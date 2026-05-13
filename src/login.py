# -*- coding: utf-8 -*-
"""CLI 入口：手动登录各平台（弹出浏览器扫码）。"""

# ================================
# 导入依赖
# ================================
import sys
import asyncio
import argparse
from pathlib import Path

# ================================
# 确保能找到 src 包
# ================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth import AuthManager


# ================================
# 登录流程
# ================================
async def login_platform(platform: str, fail_fast: bool = False) -> bool:
    """登录指定平台。"""
    print(f"\n{'='*40}")
    print(f"  平台: {platform}")
    print(f"{'='*40}")

    auth = AuthManager()
    success = await auth.login_platform(platform)

    if success:
        cookies = await auth.load_cookies(platform)
        count = len(cookies) if cookies else 0
        print(f"\n📊 已保存 {count} 条 cookie")
        print("✅ 你现在可以运行 daemon.py 启动自动化监控了")
    else:
        print(f"\n💡 提示: 可以重试，或检查网络环境后再次尝试")
        if platform in ("taobao", "pdd"):
            print(f"   {platform} 对自动化浏览器限制较严，可能的解决方案：")
            print(f"   1. 多试几次（有时偶发性成功）")
            print(f"   2. 用手动浏览器打开登录页面，手动复制 cookie 到 cookies/{platform}.json")
            print(f"   3. 如有系统 Chrome，可尝试用 --chrome 参数")

    return success


# ================================
# 主入口
# ================================
async def main():
    parser = argparse.ArgumentParser(description="薅羊毛 - 手动登录平台")
    parser.add_argument(
        "--platform", "-p",
        required=True,
        choices=["jd", "taobao", "pdd", "miniapp"],
        help="要登录的平台",
    )
    args = parser.parse_args()

    success = await login_platform(args.platform)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
