from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

db = SQLAlchemy()

class ApiService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    image = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(200), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_deployed = db.Column(db.DateTime)
    
    def to_dict(self):
        return {
            'name': self.name,
            'image': self.image,
            'domain': self.domain
        }

def init_db(app):
    # Use absolute path for database
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'services.db'))
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Simplified SQLite configuration
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'check_same_thread': False,  # Allow multi-threading
            'timeout': 30  # Connection timeout in seconds
        }
    }
    
    db.init_app(app)
    
    with app.app_context():
        db.create_all() 