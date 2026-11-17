# -*- coding: utf-8 -*-
"""Encerra o servidor_teste.py (porta 5001) dentro do container (a imagem nao tem pkill)."""
import os
import signal

for pid in os.listdir('/proc'):
    if not pid.isdigit() or int(pid) == os.getpid():
        continue
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            cmd = f.read().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        continue
    if 'servidor_teste.py' in cmd and 'sh -c' not in cmd:
        os.kill(int(pid), signal.SIGTERM)
        print('encerrado', pid)
