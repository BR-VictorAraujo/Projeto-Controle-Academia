# -*- coding: utf-8 -*-
"""
Sistema de log do app de Biometria.
Grava em arquivos .txt separados por dia, na pasta logs/ do projeto.
"""

import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, 'logs')

_lock = threading.Lock()


def _garantir_pasta():
    os.makedirs(LOG_DIR, exist_ok=True)


def _arquivo_hoje():
    nome = datetime.now().strftime('%Y-%m-%d') + '.txt'
    return os.path.join(LOG_DIR, nome)


def log(categoria, mensagem):
    """
    Grava uma linha de log no arquivo do dia atual.
    categoria: ex. 'RECONHECIMENTO', 'ACESSO', 'ERRO', 'LEITOR'
    mensagem: texto livre
    """
    try:
        _garantir_pasta()
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        linha = f'[{agora}] [{categoria}] {mensagem}\n'
        with _lock:
            with open(_arquivo_hoje(), 'a', encoding='utf-8') as f:
                f.write(linha)
    except Exception:
        # Log nunca deve quebrar a aplicação principal
        pass


# Atalhos por categoria
def log_reconhecimento(msg): log('RECONHECIMENTO', msg)
def log_acesso(msg):         log('ACESSO', msg)
def log_erro(msg):           log('ERRO', msg)
def log_leitor(msg):         log('LEITOR', msg)
def log_cadastro(msg):       log('CADASTRO', msg)