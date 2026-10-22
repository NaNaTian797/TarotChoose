"""可从任意工作目录使用显式解释器运行的入口。"""

from tarot_daily.app import main

if __name__ == "__main__":
    raise SystemExit(main())
