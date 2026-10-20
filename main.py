"""兼容旧启动方式：运行 FilePilot Lite API。"""
from backend.main import app as app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)
