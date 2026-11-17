# -*- coding: utf-8 -*-
"""Servidor waitress (igual a producao) na porta 5001, apontando para o banco
descartavel *_teste. Rodar dentro do container web:
  DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste \
    python tests/pagamentos_v172/servidor_teste.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if not os.environ.get('DATABASE_URL', '').endswith('_teste'):
    sys.exit('Recusado: DATABASE_URL precisa apontar para um banco *_teste')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

from run import app  # noqa: E402

try:
    from waitress import serve
    print('servidor: waitress')
    serve(app, host='0.0.0.0', port=5001, threads=8)
except ImportError:
    # Imagem Docker antiga sem waitress: servidor do Werkzeug com threads, sem debug
    print('servidor: werkzeug (threaded)')
    app.run(host='0.0.0.0', port=5001, threaded=True, debug=False, use_reloader=False)
