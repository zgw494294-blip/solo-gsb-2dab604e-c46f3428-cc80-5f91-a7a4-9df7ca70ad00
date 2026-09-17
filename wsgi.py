"""gunicorn / flask 启动入口。"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # 本地开发：python wsgi.py
    app.run(host="0.0.0.0", port=8000, debug=False)
