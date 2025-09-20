from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import declarative_base

authDb = SQLAlchemy(model_class=declarative_base())
