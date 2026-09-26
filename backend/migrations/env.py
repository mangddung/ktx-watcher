"""alembic 환경. 앱 로그 설정을 덮어쓰지 않도록 logging.config 는 쓰지 않는다."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  모델을 등록한다
from app.db import Base

config = context.config
target_metadata = Base.metadata


def run() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # SQLite 는 ALTER 가 제한적이라 batch 모드로 마이그레이션한다
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run()
