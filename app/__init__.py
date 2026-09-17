"""Flask 应用工厂与配置。"""

from __future__ import annotations

import os

from flask import Flask

from . import db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config.from_mapping(
        DATABASE_PATH=os.environ.get("DATABASE_PATH", "/data/stagecue.db"),
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from .routes import api, pages

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    @app.errorhandler(ApiError)
    def handle_api_error(err: "ApiError"):
        from flask import jsonify
        return jsonify(err.to_dict()), err.status_code

    return app


class ApiError(Exception):
    """统一的 API 错误响应（JSON，而非 HTML 错误页）。"""

    def __init__(self, status_code: int = 400, message: str = "请求有误",
                 payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}

    def to_dict(self) -> dict:
        data = {"error": self.message}
        data.update(self.payload)
        return data
