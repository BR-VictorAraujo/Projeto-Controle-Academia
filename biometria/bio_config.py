"""bio_config.py â Salva e carrega configuraÃ§Ãµes do app de biometria."""
import json, os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bio_config.json')

class BioConfig:
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {
            'host': 'localhost', 'port': '5432',
            'dbname': 'academia_db', 'user': 'postgres',
            'password': '', 'simulado': True, 'flask_port': 5000
        }

    @staticmethod
    def save(cfg):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)