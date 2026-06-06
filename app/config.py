import os
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'chave-padrao')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Sessão expira em 1 hora sem atividade
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)
    SESSION_PERMANENT = True