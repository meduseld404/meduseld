from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()


def init_db(app):
    """Initialize database and migrations with the Flask app."""
    db.init_app(app)
    migrate.init_app(app, db)

    with app.app_context():
        # Import models so they're registered with SQLAlchemy
        import models  # noqa: F401

        db.create_all()
