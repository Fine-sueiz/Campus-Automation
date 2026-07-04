import os
from pathlib import Path

import uvicorn

from wg_monitor.server import create_app


root = Path(os.getenv("APP_ROOT", ".")).resolve()
app = create_app(root=root)


if __name__ == "__main__":
    uvicorn.run(
        "run_server:app",
        # 默认只监听本机；如需局域网访问（例如手机 PWA），显式设置 HOST=0.0.0.0
        host=os.getenv("HOST", "127.0.0.1"),
        # 8011 是本机监测的约定端口，8000 留给日程安排程序
        port=int(os.getenv("PORT", "8011")),
        reload=os.getenv("RELOAD", "").lower() in {"1", "true", "yes"},
    )
