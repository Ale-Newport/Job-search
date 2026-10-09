from alembic import context
from sqlalchemy import create_engine

engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
with engine.connect() as connection:
    connection.exec_driver_sql('PRAGMA foreign_keys=ON')
    connection.exec_driver_sql('PRAGMA busy_timeout=2000')
    connection.exec_driver_sql('BEGIN IMMEDIATE')
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
engine.dispose()
