import logging
import re
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=True).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


# MySQL automatically creates secondary indexes when a FOREIGN KEY has no
# suitable supporting index. Their generated names (bar_id_2, bar_id_3, ...)
# are reflected by SQLAlchemy but absent from our declarative metadata.
# Suppressing the Alembic "remove_index" suggestion is correct *only* for
# indexes that correspond exactly to a reflected FK's constrained columns.
# Other database indexes must remain visible to autogenerate.
_MYSQL_AUTO_FK_INDEX_NAME = re.compile(r"bar_id_[2-9][0-9]*\\Z")


def is_mysql_implicit_foreign_key_index(index, name):
    if not name or not _MYSQL_AUTO_FK_INDEX_NAME.fullmatch(name):
        return False
    columns = tuple(column.name for column in index.columns)
    return any(
        columns == tuple(element.parent.name for element in fk.elements)
        for fk in index.table.foreign_key_constraints
    )


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    # Copy the extension options instead of mutating them between commands.
    conf_args = dict(current_app.extensions['migrate'].configure_args)
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        if connection.dialect.name == "mysql":
            previous_include_object = conf_args.get("include_object")

            def include_object(obj, name, type_, reflected, compare_to):
                if previous_include_object is not None and not previous_include_object(
                    obj, name, type_, reflected, compare_to
                ):
                    return False
                if (
                    type_ == "index"
                    and reflected
                    and compare_to is None
                    and is_mysql_implicit_foreign_key_index(obj, name)
                ):
                    logger.debug(
                        "Ignoring MySQL-generated FK-support index %s on %s",
                        name, obj.table.name,
                    )
                    return False
                return True

            conf_args["include_object"] = include_object

        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
